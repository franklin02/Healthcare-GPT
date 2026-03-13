"""
ingest.py — FDA Drug Shortage RAG Ingestion Script
----------------------------------------------------
Reads the FDA drug shortage JSON, converts each record into a
human-readable text chunk, embeds it with a local sentence-transformer,
and stores everything in a local ChromaDB vector store.

Run once (or whenever your data updates):
    python ingest.py --file your_fda_file.json
"""

import json
import argparse
import os
from pathlib import Path

from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

# ── Config ────────────────────────────────────────────────────────────────────
CHROMA_DIR   = "./chroma_db"          # where the vector index is saved
EMBED_MODEL  = "all-MiniLM-L6-v2"    # small, fast, free, runs on CPU
COLLECTION   = "fda_shortages"
# ─────────────────────────────────────────────────────────────────────────────


def load_fda_json(filepath: str) -> list[dict]:
    """Load the FDA JSON file. Handles both a bare list and a wrapped object."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # The FDA API typically wraps records under a 'results' key
    if isinstance(raw, dict):
        for key in ("results", "data", "records", "shortages"):
            if key in raw and isinstance(raw[key], list):
                print(f"  Found records under key: '{key}'")
                return raw[key]
        # If no known wrapper, return all values that are lists
        for v in raw.values():
            if isinstance(v, list) and len(v) > 0:
                return v
        # Last resort: wrap single record in a list
        return [raw]

    if isinstance(raw, list):
        return raw

    raise ValueError(f"Unexpected JSON structure in {filepath}")


def record_to_text(record: dict) -> str:
    """
    Convert a single FDA shortage record into a readable text chunk
    that the LLM can reason about.

    This function is intentionally flexible — it walks the record's
    fields and formats them, so it works even if the exact schema varies
    between FDA export versions.
    """
    lines = []

    # ── Priority fields (render first if they exist) ──────────────────────────
    priority_fields = [
        ("generic_name",            "Drug (generic)"),
        ("brand_name",              "Brand name"),
        ("status",                  "Shortage status"),
        ("shortage_reason",         "Reason for shortage"),
        ("availability",            "Availability"),
        ("update_date",                 "Last updated"),
        ("revision_date",           "Revision date"),
        ("therapeutic_category",    "Therapeutic category"),
        ("presentation",            "Presentation / form"),
        ("company_name",            "Manufacturer"),
        ("contact",                 "Contact"),
        ("description",             "Description"),
    ]

    seen_keys = set()
    for key, label in priority_fields:
        if key in record and record[key]:
            val = record[key]
            if isinstance(val, (dict, list)):
                val = json.dumps(val)
            lines.append(f"{label}: {val}")
            seen_keys.add(key)

    # ── Remaining fields (catch-all for unknown schema variations) ────────────
    for key, val in record.items():
        if key in seen_keys:
            continue
        if val is None or val == "" or val == []:
            continue
        label = key.replace("_", " ").title()
        if isinstance(val, (dict, list)):
            val = json.dumps(val)
        lines.append(f"{label}: {val}")

    return "\n".join(lines)


def build_documents(records: list[dict]) -> list[Document]:
    """Turn raw records into LangChain Document objects."""
    docs = []
    for i, record in enumerate(records):
        text = record_to_text(record)
        if not text.strip():
            continue

        # Store key metadata so we can surface it in answers
        # metadata = {
        #     "record_index": i,
        #     "generic_name":  str(record.get("generic_name", "")),
        #     "brand_name":    str(record.get("brand_name", "")),
        #     #"status":        str(record.get("status", "")),
        #     "status":        str(record.get("availability", "")),
        #     "updated":       str(record.get("update_date", "")),
        # }
        metadata = {
            "record_index": i,
            "generic_name": str(record.get("generic_name", "")),
            "brand_name":   str(record.get("openfda", {}).get("brand_name", [""])[0]),
            "status":       str(record.get("availability", record.get("status", ""))),
            "updated":      str(record.get("update_date", "")),
        }
        docs.append(Document(page_content=text, metadata=metadata))

    return docs


def ingest(filepath: str) -> None:
    print(f"\n{'='*55}")
    print("  FDA Drug Shortage RAG — Ingestion Pipeline")
    print(f"{'='*55}\n")

    # 1. Load
    print(f"[1/4] Loading JSON from: {filepath}")
    records = load_fda_json(filepath)
    print(f"      → {len(records):,} records loaded")

    # 2. Build documents
    print("[2/4] Converting records to text chunks...")
    docs = build_documents(records)
    print(f"      → {len(docs):,} documents created")

    # 3. Embeddings
    print(f"[3/4] Loading embedding model: {EMBED_MODEL}")
    print("      (First run downloads ~90 MB — cached after that)")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    # 4. Build & persist ChromaDB
    print(f"[4/4] Building vector store → {CHROMA_DIR}")
    if os.path.exists(CHROMA_DIR):
        print("      (Existing DB found — overwriting)")
        import shutil
        shutil.rmtree(CHROMA_DIR)

    # Batch in chunks of 500 to avoid memory spikes on large files
    BATCH = 500
    db = None
    for start in range(0, len(docs), BATCH):
        batch = docs[start : start + BATCH]
        end   = min(start + BATCH, len(docs))
        print(f"      Embedding records {start+1}–{end} of {len(docs)}…")
        if db is None:
            db = Chroma.from_documents(
                batch,
                embeddings,
                persist_directory=CHROMA_DIR,
                collection_name=COLLECTION,
            )
        else:
            db.add_documents(batch)

    print(f"\n✅ Ingestion complete!")
    print(f"   {len(docs):,} records indexed in {CHROMA_DIR}")
    print(f"   You can now start the server: uvicorn main:app --reload\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest FDA JSON into ChromaDB")
    parser.add_argument("--file", required=True, help="Path to the FDA JSON file")
    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"❌ File not found: {args.file}")
        exit(1)

    ingest(args.file)