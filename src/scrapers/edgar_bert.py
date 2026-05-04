import argparse
import json
import time
from pathlib import Path

from transformers import pipeline

try:
    import torch
except ImportError:
    torch = None

from ask_llm import SUBSECTOR_FIELDS, find_subsector_fields

# This script takes the output from a json, and uses the BERT model to classify each into a subsector and decide if it should be included in the RAG dataset. 
# Usage: python edgar_bert_to_rag.py --input sec_edgar_filings.json --output ready_for_rag/sec_edgar_filings.json --threshold 0.60 --subsector-data

READY_FOR_RAG_DIR = Path(__file__).parent.parent / "data" / "Ready_for_RAG"
DEFAULT_INPUT = Path("sec_edgar_filings.json")
DEFAULT_OUTPUT = READY_FOR_RAG_DIR / "sec_edgar_filings.json"
MODEL_ID = "typeform/distilbert-base-uncased-mnli"
REQUEST_DELAY_SECONDS = 0.1
FILTER_TEXT_WINDOW = 500
FILTER_SCORE_THRESHOLD = 0.60

LABELS = [
    "drug shortage",
    "medical device shortage",
    "cyber attack",
    "natural disaster",
    "other",
    "unrelated",
]

LABEL_TO_SUBSECTOR = {
    "drug shortage": "drug_shortage",
    "medical device shortage": "medical_device_shortage",
    "cyber attack": "cyber_attack",
    "natural disaster": "natural_disaster",
    "other": "other",
    "unrelated": "none",
}


def _select_device() -> int:
    if torch is None:
        return -1
    return 0 if torch.cuda.is_available() else -1

# The input JSON is expected to have a structure like: {"sources": [ { "id": ..., "title": ..., "content": ..., ... }, ... ] }
def load_sources(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Input JSON not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    sources = raw.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("Input JSON must contain a 'sources' list")
    return sources

# Classify the subsector of a filing using the BERT zero-shot classifier. Returns the subsector, the top score, and the top label. If the top label is "unrelated" or below the threshold, returns "none" for the subsector.
def classify_subsector(classifier, title: str, content: str, threshold: float) -> tuple[str, float, str]:
    text = f"Headline: {title}. Details: {content[:FILTER_TEXT_WINDOW]}".replace("\n", " ")
    result = classifier(
        text,
        LABELS,
        multi_label=True,
        hypothesis_template="This filing is about {}.",
    )

    top_label = result["labels"][0]
    top_score = float(result["scores"][0])
    unrelated_score = 0.0
    if "unrelated" in result["labels"]:
        unrelated_score = float(result["scores"][result["labels"].index("unrelated")])
    subsector = LABEL_TO_SUBSECTOR.get(top_label, "none")
    if top_label == "unrelated" or top_score < threshold or top_score <= unrelated_score:
        return "none", top_score, top_label
    return subsector, top_score, top_label

# Build the RAG record by taking the original source data, adding the "source_name" (defaulting to "SEC EDGAR" if not present), and adding the classified "subsector" and any extracted "subsector_data".
def build_rag_record(source: dict, subsector: str, subsector_data: dict) -> dict:
    record = dict(source)
    record["source_name"] = record.get("source_name") or "SEC EDGAR"
    record["subsector"] = subsector
    record["subsector_data"] = subsector_data
    return record


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify EDGAR JSON filings with BERT and write Ready_for_RAG output."
    )
    parser.add_argument(
        "-i",
        "--input",
        default=str(DEFAULT_INPUT),
        help="Path to the EDGAR filings JSON (default: sec_edgar_filings.json).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path to the Ready_for_RAG JSON output.",
    )
    parser.add_argument(
        "-n",
        "--max-entries",
        type=int,
        default=None,
        help="Maximum number of entries to process.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=FILTER_SCORE_THRESHOLD,
        help="Minimum BERT score to accept a subsector label.",
    )
    parser.add_argument(
        "--subsector-data",
        action="store_true",
        help="Extract subsector_data using the LLM for classified subsectors.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Loading EDGAR filings from {input_path}")
    sources = load_sources(input_path)
    if args.max_entries is not None:
        sources = sources[: args.max_entries]

    device = _select_device()
    print(f"Loading BERT model ({MODEL_ID}) on device {device}")
    classifier = pipeline("zero-shot-classification", model=MODEL_ID, device=device)

    results = []
    skipped = 0
    # Process each source, classify it, and build the RAG record if it's relevant. Keep track of how many are kept vs skipped, and print progress every 25 entries.
    for idx, source in enumerate(sources, start=1):
        title = str(source.get("title") or "").strip()
        content = str(source.get("content") or "").strip()

        subsector, score, label = classify_subsector(classifier, title, content, args.threshold)

        if subsector == "none":
            skipped += 1
        else:
            subsector_data = {}
            if args.subsector_data and subsector in SUBSECTOR_FIELDS:
                try:
                    subsector_data = find_subsector_fields(subsector, title, content)
                except Exception as exc:
                    print(f"Subsector data failed for {source.get('id', idx)}: {exc}")

            results.append(build_rag_record(source, subsector, subsector_data))

        if idx % 25 == 0 or idx == len(sources):
            print(
                f"Processed {idx}/{len(sources)}: kept {len(results)}, skipped {skipped}"
            )

        time.sleep(REQUEST_DELAY_SECONDS)

    output_path.write_text(json.dumps({"sources": results}, indent=4), encoding="utf-8")
    print(f"Wrote {len(results)} records to {output_path}")


if __name__ == "__main__":
    main()
