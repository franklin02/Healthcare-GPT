import asyncio
import sys
import os
from pathlib import Path
import pandas as pd
from transformers import pipeline

current_dir = Path(__file__).resolve().parent
scraper_dir = current_dir.parent / "scrapers"

if str(scraper_dir) not in sys.path:
    sys.path.append(str(scraper_dir))

try:
    from bert_scraper import bert_scraper
except ImportError:
    print(f"Error: Could not find bert_scraper.py in {scraper_dir}")


MODEL_ID = "typeform/distilbert-base-uncased-mnli"
classifier = pipeline("zero-shot-classification", model=MODEL_ID, device=0)

CONCURRENT_REQUESTS = 10 

def run_bert_inference(data: dict) -> str:
    title = str(data.get('title') or "").strip()
    body = str(data.get('body') or "").strip()
    
    # Clean text: remove extra whitespace and newlines
    text = f"Headline: {title}. Details: {body[:500]}".replace("\n", " ")

    # These MUST match the labels checked below
    candidate_labels = [
        "cyber attack or data breach", 
        "hospital system failure",
        "medical supply shortage",
        "unrelated news"
    ]
    
    res = classifier(
        text, 
        candidate_labels, 
        multi_label=True,
        hypothesis_template="This healthcare news involves a {}."
    )
    
    scores = dict(zip(res['labels'], res['scores']))

    # Only check the high-priority threats
    threat_labels = [
        "cyber attack or data breach", 
        "hospital system failure",
        "medical supply shortage"
    ]

    # LOGIC: Flag if any threat is > 0.60 AND it scores higher than 'unrelated news'
    unrelated_score = scores.get("unrelated news", 0)
    
    for label in threat_labels:
        if scores[label] > 0.60 and scores[label] > unrelated_score:
            return "potential_hit"
            
    return "none"

def print_comparison_stats(bert_results: list, llama_hits: list):
    """Handles all the summary and comparison bloat in one place."""
    bert_set = set(url for url in bert_results if url)
    llama_set = set(url for url in llama_hits if url)
    
    intersection = llama_set.intersection(bert_set)
    bert_only = bert_set - llama_set
    llama_only = llama_set - bert_set

    print(f"\n{'='*20} PIPELINE SUMMARY {'='*20}")
    print(f"Ollama (LLM) Hits: {len(llama_set)}")
    print(f"BERT Filter Hits:   {len(bert_set)}")
    print(f"Agreement:          {len(intersection)}")
    print(f"BERT Over-flags:    {len(bert_only)}")
    print(f"BERT Misses:        {len(llama_only)}")

    if llama_only:
        print("\n[!] BERT missed these LLM-confirmed hits:")
        for url in llama_only:
            print(f"- {url}")
    print(f"{'='*49}\n")

async def process_link (url, sem):
    async with sem:
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, bert_scraper, url)
            
            if not data or not data.get('body'):
                return {"url": url, "title": "", "body": "", "status": "SKIP"}

            subsector = run_bert_inference(data)
            return {
                "url": url, 
                "title": data.get('title', ''), 
                "body": data.get('body', ''), 
                "subsector": subsector,
                "status": "YES" if subsector != "none" else "NO"
            }
        except Exception as e:
            return {"url": url, "title": "", "body": "", "status": f"ERROR: {e}"}

async def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith('.csv') else None
    all_results = []
    llama_confirmed_urls = []

    if csv_path and os.path.exists(csv_path):
        print(f"--- OFFLINE MODE: Filtering {csv_path} ---")
        df = pd.read_csv(csv_path).fillna("") 
        
        for _, row in df.iterrows():
            data = {"title": row['title'], "body": row['body']}
            subsector = run_bert_inference(data)
            
            all_results.append({"url": row['url'], "subsector": subsector})
            
            if row.get('llama_hit') == 1:
                llama_confirmed_urls.append(row['url'])
    
    bert_confirmed_urls = [r['url'] for r in all_results if r['subsector'] != "none"]
    print_comparison_stats(bert_confirmed_urls, llama_confirmed_urls)
    
    return bert_confirmed_urls

if __name__ == "__main__":
    asyncio.run(main())