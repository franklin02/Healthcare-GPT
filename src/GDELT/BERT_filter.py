"""Filter and classify healthcare-related news items using a zero-shot BERT model.

This module provides helpers to run zero-shot classification on article
text (headline + excerpt), an async wrapper to scrape pages and classify them,
and utilities to compare BERT results with LLM-confirmed hits.

Dependencies:
- transformers (pipeline)
- pandas
- a local `bert_scraper(url)` function in `src/scrapers/bert_scraper.py`

Typical CLI usage:
    python src/GDELT/BERT_filter.py path/to/articles.csv
"""

import asyncio
import sys
import os
from pathlib import Path
import pandas as pd
from transformers import pipeline
import torch

current_dir = Path(__file__).resolve().parent
scraper_dir = current_dir.parent / "scrapers"

if str(scraper_dir) not in sys.path:
    sys.path.append(str(scraper_dir))

try:
    from bert_scraper import bert_scraper
except ImportError:
    print(f"Error: Could not find bert_scraper.py in {scraper_dir}")
    sys.exit(1)


FINETUNE_BERT_PATH = current_dir.parent.parent / "models" / "healthcare_bert_v2"
FALLBACK_MODEL_ID = "typeform/distilbert-base-uncased-mnli"


def get_device():
    if torch.cuda.is_available():
        return 0
    elif torch.backends.mps.is_available():
        return "mps"
    else:
        return -1


def load_model():
    device = get_device()
    if FINETUNE_BERT_PATH.exists():
        MODEL_ID = FINETUNE_BERT_PATH
    else:
        print("[WARN] Finetuned model not found, reverting to base model.")
        MODEL_ID = FALLBACK_MODEL_ID
    return pipeline("zero-shot-classification", model=MODEL_ID, device=device)


CONCURRENT_REQUESTS = 10


def run_bert_inference(data: dict, classifier) -> str:
    """Classify a single article as a potential healthcare-related hit.

    The function composes a short prompt from the article title and the first
    500 characters of the body, then runs a zero-shot classifier with a set
    of candidate labels. A result is considered a "potential_hit" when any of
    the threat labels scores above 0.60 and is higher than the "unrelated
    news" score.

    Args:
        data (dict): Article payload. Expected keys:
            - "title" (str): Article headline. Missing/None treated as "".
            - "body" (str): Article body/text. Missing/None treated as "".
        classifier: Loaded transformers pipeline instance for inference.

    Returns:
        str: One of:
            - "potential_hit": a threat label passed the threshold.
            - "none": no threat labels passed the threshold.
    """
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()

    text = f"Headline: {title}. Details: {body[:500]}".replace("\n", " ")

    candidate_labels = [
        "cyber attack or data breach",
        "hospital system failure",
        "medical supply shortage",
        "unrelated news",
    ]

    res = classifier(
        text,
        candidate_labels,
        multi_label=True,
        hypothesis_template="This healthcare news involves a {}.",
    )

    scores = dict(zip(res["labels"], res["scores"]))

    threat_labels = [
        "cyber attack or data breach",
        "hospital system failure",
        "medical supply shortage",
    ]

    unrelated_score = scores.get("unrelated news", 0)

    for label in threat_labels:
        if scores[label] > 0.60 and scores[label] > unrelated_score:
            return "potential_hit"

    return "none"


def print_comparison_stats(bert_results: list, llama_hits: list):
    """Print a summary comparing BERT-flagged URLs with LLM-confirmed hits.

    Args:
        bert_results (list[str]): Iterable of URLs flagged by the BERT filter.
        llama_hits (list[str]): Iterable of URLs confirmed by the LLM/ollama pipeline.

    Returns:
        None: Prints counts and disagreement lists to stdout for human inspection.
    """
    bert_set = set(url for url in bert_results if url)
    llama_set = set(url for url in llama_hits if url)

    intersection = llama_set.intersection(bert_set)
    bert_only = bert_set - llama_set
    llama_only = llama_set - bert_set

    print(f"\n{'=' * 20} PIPELINE SUMMARY {'=' * 20}")
    print(f"Ollama (LLM) Hits: {len(llama_set)}")
    print(f"BERT Filter Hits:   {len(bert_set)}")
    print(f"Agreement:          {len(intersection)}")
    print(f"BERT Over-flags:    {len(bert_only)}")
    print(f"BERT Misses:        {len(llama_only)}")

    if llama_only:
        print("\n[!] BERT missed these LLM-confirmed hits:")
        for url in llama_only:
            print(f"- {url}")
    print(f"{'=' * 49}\n")


async def process_link(url, sem, classifier):
    """Asynchronously scrape a URL and run BERT classification on the content.

    This coroutine calls the blocking `bert_scraper(url)` in a threadpool
    executor so that many scrapes can run concurrently under asyncio. The
    provided semaphore is used to bound parallelism.

    Args:
        url (str): Target page URL to scrape.
        sem (asyncio.Semaphore): Semaphore to limit concurrent scrapes.
        classifier: Loaded transformers pipeline instance passed through
            to run_bert_inference.

    Returns:
        dict: Result with keys:
            - "url" (str): The input URL.
            - "title" (str): Scraped title or empty string.
            - "body" (str): Scraped body text or empty string.
            - "subsector" (str): Classification result ("potential_hit" or "none").
            - "status" (str): One of "YES", "NO", "SKIP", or "ERROR: <msg>".
    """
    async with sem:
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, bert_scraper, url)

            if not data or not data.get("body"):
                return {"url": url, "title": "", "body": "", "status": "SKIP"}

            subsector = run_bert_inference(data, classifier)
            return {
                "url": url,
                "title": data.get("title", ""),
                "body": data.get("body", ""),
                "subsector": subsector,
                "status": "YES" if subsector != "none" else "NO",
            }
        except Exception as e:
            return {"url": url, "title": "", "body": "", "status": f"ERROR: {e}"}


async def main():
    """CLI entry point: run offline CSV filtering and print comparison stats.

    If the first command-line argument is a path to a CSV file, the CSV is
    loaded with pandas and each row is classified with `run_bert_inference`.
    The function collects BERT-confirmed URLs, compares them to any
    `llama_hit` flags in the CSV, prints a summary via
    `print_comparison_stats`, and returns the list of URLs flagged by BERT.

    Expected CSV columns:
        - "url" (str)
        - "title" (str)
        - "body" (str)
        - optional "llama_hit" (int, where 1 indicates an LLM-confirmed hit)

    Returns:
        list[str]: URLs where BERT reported a hit ("potential_hit").

    Side effects:
        - Prints pipeline summary to stdout.
        - Reads `sys.argv[1]` for CSV path when invoked as a script.
    """
    classifier = load_model()
    csv_path = (
        sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".csv") else None
    )
    all_results = []
    llama_confirmed_urls = []

    if csv_path and os.path.exists(csv_path):
        print(f"--- OFFLINE MODE: Filtering {csv_path} ---")
        df = pd.read_csv(csv_path).fillna("")

        for _, row in df.iterrows():
            data = {"title": row["title"], "body": row["body"]}
            subsector = run_bert_inference(data, classifier)  # fixed

            all_results.append({"url": row["url"], "subsector": subsector})

            if row.get("llama_hit") == 1:
                llama_confirmed_urls.append(row["url"])

    bert_confirmed_urls = [r["url"] for r in all_results if r["subsector"] != "none"]
    print_comparison_stats(bert_confirmed_urls, llama_confirmed_urls)

    return bert_confirmed_urls


if __name__ == "__main__":
    asyncio.run(main())
