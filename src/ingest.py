import json
import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


DEFAULT_CHROMA_DIR = str(Path(__file__).parent.parent / "chroma_db")
EMBED_MODEL  = "all-MiniLM-L6-v2"
COLLECTION   = "agentic_data"
DEFAULT_DUP_THRESHOLD = 0.44 #this is the least aggressive threshold that detects all duplicates
DUP_LOG_FILENAME = "duplication_log.txt"



'''
This function opens and loads the JSON file into a dictionary with its corresponding key value pair. 
This will only work if the JSON file has a 'sources' key with a list of records. Anything else will 
raise an error. 
'''
def load_document(filepath: str) -> list[dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a JSON object with a 'sources' key in {filepath}, "
            f"but got {type(raw).__name__}"
        )

    if "sources" in raw and isinstance(raw["sources"], list):
        print(f"  Found {len(raw['sources'])} records")
        return raw["sources"]

    raise ValueError(
        f"Expected a 'sources' key wrapping the records in {filepath}, "
        f"but found top-level keys: {list(raw.keys())}"
    )



'''
This function turns a single JSON object into a readable text chunck that our LLM can use to reason about.
Each known field is appended and added to a list of lines. The list also scans for unknonw fields and grabs
then rather than silently dropping them (This was used in the old schema, but I just kept it for now). At the 
end all lines are joined and returned.

'''
def record_to_text(record: dict) -> str:
    lines = []
    known_fields = [
        ("id", "ID"),
        ("title", "Title"),
        ("source_name", "Source"),
        ("direct_link", "Link"),
        ("subsector", "Subsector"),
        ("date_published", "Date Published"),
        ("date_accessed", "Date Accessed"),
        ("content", "Body of the article"),
        ("exec_summary", "Executive Summary"),
    ]

    seen_keys = set()
    for key, label in known_fields:
        if key in record and record[key]:
            val = record[key]
            if isinstance(val, (dict, list)):
                val = json.dumps(val)
            lines.append(f"{label}: {val}")
            seen_keys.add(key)

    #This block flattens the subsector_data to make it easier for an LLM to read.
    #NOTE: It skips any empty fields, empty fields should either be "" or []
    if "subsector_data" in record and isinstance(record["subsector_data"], dict):
        lines.append("\nSubsector Details:")
        for key, val in record["subsector_data"].items():
            if val is None or val == "" or val == []:
                continue
            label = key.replace("_", " ").title()
            if isinstance(val, (dict, list)):
                val = json.dumps(val)
            lines.append(f"  {label}: {val}")
        seen_keys.add("subsector_data")

    #This will catch any unexpected fields that do not follow the schema. This should not happen
    #but if it does, we will print a warning and continue rather than silently dropping the field.
    for key, val in record.items():
        if key in seen_keys:
            continue
        if val is None or val == "" or val == []:
            continue
        label = key.replace("_", " ").title()
        if isinstance(val, (dict, list)):
            val = json.dumps(val)
        lines.append(f"{label}: {val}")
        print(f"WARNING: Unexpected field found: {label}: {val}")
    return "\n".join(lines)



'''
This functions creates a single document for each JSON object in the file. This uses record_to_text 
to create the text chunk and then adds the metadata to the document. If the record_to_text returns an 
empty string, the document is skipped. We also stash the full source record JSON under the 'raw' metadata
key so we can reconstruct the original object when logging or merging near-duplicates later.
'''
def build_documents(records: list[dict]) -> list[Document]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=160,
        separators=["\n\n", "\n", " ", ""],
    )

    final_docs = []
    for record in records:
        text = record_to_text(record)
        if not text.strip():
            continue

        metadata = {
            "id": str(record.get("id", "")),
            "title": str(record.get("title", "")),
            "source_name": str(record.get("source_name", "")),
            "subsector": str(record.get("subsector", "")),
            "raw": json.dumps(record, ensure_ascii=False), # might be overkill
        }

        for chunk in text_splitter.split_text(text):
            final_docs.append(Document(page_content=chunk, metadata=metadata))

    return final_docs



'''
Resolves which directory to use for the Chroma DB. If --diff_dir was passed, it must exist and be
a directory; otherwise we exit with a clean error. When --diff_dir is not passed we use DEFAULT_CHROMA_DIR
(which Chroma will create on first write).
'''
def resolve_chroma_dir(diff_dir: str | None) -> str:
    if diff_dir is None:
        return DEFAULT_CHROMA_DIR

    path = Path(diff_dir)
    if not path.exists() or not path.is_dir():
        print(f"[ERROR] --diff_dir path not found or not a directory: {diff_dir}")
        exit(1)
    return str(path.resolve())



'''
Deep-merges two source records into a single record. This is only called when 
the two records already passed the subsector-match check. Merge rules:
  - id: keep the existing record's id (merged record replaces it in the DB).
  - title / content / exec_summary: concatenate with a separator (skip empties / duplicates).
  - source_name / direct_link: join with " | " when distinct.
  - date_published / date_accessed: keep the latest (ISO strings sort lexically).
  - subsector: kept as-is from existing (precondition: equal to new's).
  - subsector_data: deep-merge. list values -> extend + dedupe; scalars -> existing wins unless empty.
  - Unknown fields: same policy as subsector_data scalars.
'''
def merge_records(existing: dict, new: dict) -> dict:
    def _concat(a, b, sep="\n\n---\n\n"):
        a = (a or "").strip()
        b = (b or "").strip()
        if not a:
            return b
        if not b:
            return a
        if a == b:
            return a
        return f"{a}{sep}{b}"

    def _join(a, b, sep=" | "):
        a = (a or "").strip() if isinstance(a, str) else a
        b = (b or "").strip() if isinstance(b, str) else b
        if not a:
            return b
        if not b:
            return a
        if a == b:
            return a
        return f"{a}{sep}{b}"

    def _latest_date(a, b):
        if not a:
            return b
        if not b:
            return a
        return a if a >= b else b

    def _merge_subsector_data(a: dict, b: dict) -> dict:
        out = dict(a or {})
        for k, v_new in (b or {}).items():
            if k not in out or out[k] in (None, "", []):
                out[k] = v_new
                continue
            v_old = out[k]
            if isinstance(v_old, list) and isinstance(v_new, list):
                combined = list(v_old)
                for item in v_new:
                    if item not in combined:
                        combined.append(item)
                out[k] = combined
            elif isinstance(v_old, dict) and isinstance(v_new, dict):
                out[k] = _merge_subsector_data(v_old, v_new)
        return out

    merged: dict = {}
    merged["id"] = existing.get("id") #id already in the db 
    merged["title"] = _concat(existing.get("title", ""), new.get("title", ""))
    merged["source_name"] = _join(existing.get("source_name", ""), new.get("source_name", ""))
    merged["direct_link"] = _join(existing.get("direct_link", ""), new.get("direct_link", ""))
    merged["subsector"] = existing.get("subsector", new.get("subsector", ""))
    merged["date_accessed"] = _latest_date(existing.get("date_accessed", ""), new.get("date_accessed", ""))
    merged["date_published"] = _latest_date(existing.get("date_published", ""), new.get("date_published", ""))
    merged["content"] = _concat(existing.get("content", ""), new.get("content", ""))
    merged["exec_summary"] = _concat(existing.get("exec_summary", ""), new.get("exec_summary", ""))
    merged["subsector_data"] = _merge_subsector_data(
        existing.get("subsector_data", {}) or {},
        new.get("subsector_data", {}) or {},
    )

    known = set(merged.keys())
    for source in (existing, new):
        for k, v in source.items():
            if k in known:
                continue
            if k not in merged or merged[k] in (None, "", []):
                merged[k] = v
            elif isinstance(merged[k], list) and isinstance(v, list):
                merged[k] = merged[k] + [x for x in v if x not in merged[k]]

    return merged



'''
Looks up the most similar record already in the DB. Returns (existing_record_dict, distance) if a hit
scores at/under the threshold and is a DIFFERENT record (by id), else None. Returns None when the DB
is empty as well. Uses cosine distance (Chroma default) so lower == closer.
'''
def find_duplicate(
    db: Chroma,
    record: dict,
    threshold: float,
) -> tuple[dict, float] | None:
    try:
        existing_count = db._collection.count()
    except Exception:
        existing_count = 0
    if existing_count == 0:
        return None

    query_text = record_to_text(record)
    if not query_text.strip():
        return None

    hits = db.similarity_search_with_score(query_text, k=1)
    if not hits:
        return None #no hits found

    doc, distance = hits[0]
    if distance > threshold:
        return None #closest hit is too far away

    hit_subsector = doc.metadata.get("subsector", "")
    incoming_subsector = str(record.get("subsector", "")).strip().lower()
    hit_subsector_str = str(hit_subsector).strip().lower()
    if hit_subsector_str != incoming_subsector:
        print(f"Subsector mismatch: {hit_subsector_str} != {incoming_subsector}")
        return None

    raw_json = doc.metadata.get("raw")
    if not raw_json:
        return None
    try:
        existing_record = json.loads(raw_json)
    except json.JSONDecodeError:
        return None

    return existing_record, float(distance)



'''
Appends a human-readable block to <chroma_dir>/duplication_log.txt describing a near-duplicate event.
The log entry contains the existing record, the incoming record and the merged/proposed record so that
a reviewer can copy/paste either side back into a {"sources": [...]} file and re-ingest with --force
if the merge turns out to be wrong.
'''
def log_duplicate(
    chroma_dir: str,
    existing: dict,
    new: dict,
    merged: dict,
    distance: float,
    threshold: float,
    action: str,
) -> None:
    Path(chroma_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(chroma_dir) / DUP_LOG_FILENAME

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    subsector_match = "yes" if existing.get("subsector") == new.get("subsector") else "no"

    existing_json = json.dumps(existing, indent=2, ensure_ascii=False)
    new_json = json.dumps(new, indent=2, ensure_ascii=False)
    merged_json = json.dumps(merged, indent=2, ensure_ascii=False)

    block = (
        f"=== DUPLICATE DETECTED {ts} ===\n"
        f"distance: {distance:.4f}   threshold: {threshold:.4f}   "
        f"subsector_match: {subsector_match}   action: {action}\n"
        f"--- Existing record ---\n{existing_json}\n"
        f"--- New record ---\n{new_json}\n"
        f"--- Combined record ---\n{merged_json}\n\n"
    )

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(block)

def _is_valid_disruption(record: dict, *, use_bert: bool) -> bool:
    """Return True if the record describes an active healthcare disruption.

    Calls ``ai_check_validation`` from ask_llm with the ``use_bert`` flag so
    the caller controls which pipeline (LLM-only vs BERT) is used.
    Skips classification and returns True when ask_llm is not importable 
    (e.g. Ollama not running), so ingestion can still proceed.
    """
    import sys
    scrapers_dir = str(Path(__file__).resolve().parent / "scrapers")
    if scrapers_dir not in sys.path:
        sys.path.insert(0, scrapers_dir)

    try:
        from ask_llm import ai_check_validation  # type: ignore[import]
    except ImportError:
        print("         [WARN] ask_llm not found — skipping classification gate")
        return True
 
    title = str(record.get("title", ""))
    body = str(record.get("content", record.get("exec_summary", "")))
 
    is_threat, detail = ai_check_validation(title, body, use_bert=use_bert)
 
    if not is_threat:
        print(f"         [SKIP] classifier rejected: {detail}")
    return is_threat



'''
Main ingestion pipeline. Loads records, optionally resets the DB, then for each record either inserts
it directly (--force) or first checks the DB for a semantically similar record. When a duplicate is
found and the subsectors match, the existing record's chunks are deleted and the merged record is
inserted in its place. When a duplicate is found but subsectors disagree, we log the near-duplicate
and ingest the new record as a separate entry.
'''
def ingest(
    filepath: str,
    *,
    new_db: bool = False,
    diff_dir: str | None = None,
    force: bool = False,
    dup_threshold: float = DEFAULT_DUP_THRESHOLD,
    use_bert: bool = False,
) -> None:
    print(f"\n{'-'*55}")
    print("Ingestion Pipeline")
    print(f"{'-'*55}\n")

    chroma_dir = resolve_chroma_dir(diff_dir)

    print(f"(1/4) Loading JSON from: {filepath}")
    records = load_document(filepath)
    print(f"-----> {len(records):,} records loaded")

    print(f"(2/4) Loading embedding model: {EMBED_MODEL}")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    print(f"(3/4) Preparing vector store at: {chroma_dir}")
    if os.path.exists(chroma_dir) and new_db:
        print("----->  --new_db flag set, are you sure you want to delete the existing database? [y/n]")
        confirm = input()
        if confirm == "y":
            shutil.rmtree(chroma_dir)
            print("-----> Database deleted")
        else:
            print("-----> Database not deleted, exiting...")
            exit(1)

    db = Chroma(
        persist_directory=chroma_dir,
        embedding_function=embeddings,
        collection_name=COLLECTION,
    )

    existing = db.get(include=["metadatas"])
    existing_ids = {m["id"] for m in existing["metadatas"] if m.get("id")}
    print(f"-----> {len(existing_ids)} records currently in DB")

    print(f"(4/4) Ingesting records (force={force}, threshold={dup_threshold})")

    if force:
        docs = build_documents(records)
        before = len(docs)
        docs = [d for d in docs if d.metadata.get("id") not in existing_ids]
        skipped = before - len(docs)
        print(f"-----> {skipped} chunks skipped by exact-id match, {len(docs)} new chunks")
        if not docs:
            print("-----> Nothing new to ingest. Exiting.")
            return

        BATCH = 500
        for start in range(0, len(docs), BATCH):
            batch = docs[start : start + BATCH]
            db.add_documents(batch)

        print("\n\n\nIngestion complete!")
        print(f"-----> {len(docs):,} chunks indexed in {chroma_dir}")
        print("----->  You can now start the server: uvicorn main:app --reload\n")
        return

    stats = {"new": 0, "id_skipped": 0, "merged": 0, "logged_only": 0, "rejected": 0}
    for idx, record in enumerate(records, start=1):
        rec_id = str(record.get("id", ""))
        print(f"-----> [{idx}/{len(records)}] record id={rec_id!r}")

        # Skip exact id matches
        if rec_id and rec_id in existing_ids:
            print("         [WARN] exact id already in DB, skipping: " + rec_id)
            stats["id_skipped"] += 1
            continue

        # Classification gate
        if not _is_valid_disruption(record, use_bert=use_bert):
            stats["rejected"] += 1
            continue

        dup = find_duplicate(db, record, dup_threshold)
        if dup is None:
            new_docs = build_documents([record])
            if new_docs:
                db.add_documents(new_docs)
                existing_ids.add(rec_id)
                stats["new"] += 1
                print(f"         no duplicate, inserted {len(new_docs)} chunks")
            continue

        existing_record, distance = dup
        existing_subsector = existing_record.get("subsector", "")
        new_subsector = record.get("subsector", "")

        #semantically similar but different subsector, logs this but inserts as new (users should manually check and merge if needed)
        if existing_subsector != new_subsector:
            merged_preview = {**existing_record, **record}
            log_duplicate(
                chroma_dir,
                existing_record,
                record,
                merged_preview,
                distance,
                dup_threshold,
                action="logged_only_subsector_mismatch",
            )
            new_docs = build_documents([record])
            if new_docs:
                db.add_documents(new_docs)
                existing_ids.add(rec_id)
            stats["logged_only"] += 1
            print(
                f"         near-duplicate (distance={distance:.4f}) but subsector mismatch "
                f"({existing_subsector!r} vs {new_subsector!r}); logged and ingested as new"
            )
            continue

        merged = merge_records(existing_record, record)
        existing_id = str(existing_record.get("id", ""))
        try:
            db._collection.delete(where={"id": existing_id})
        except Exception as e:
            print(f"         [WARN] failed to delete old chunks for id={existing_id}: {e}")

        merged_docs = build_documents([merged])
        if merged_docs:
            db.add_documents(merged_docs)

        log_duplicate(
            chroma_dir,
            existing_record,
            record,
            merged,
            distance,
            dup_threshold,
            action="merged",
        )
        stats["merged"] += 1
        print(
            f"         duplicate (distance={distance:.4f}) merged with existing id={existing_id} "
            f"({len(merged_docs)} chunks replaced)"
        )

    print("\n\n\nIngestion complete!")
    print(
        f"-----> new={stats['new']}  merged={stats['merged']}  "
        f"logged_only={stats['logged_only']}  id_skipped={stats['id_skipped']}"
    )
    print(f"-----> chroma dir: {chroma_dir}")
    print(f"-----> dup log:    {Path(chroma_dir) / DUP_LOG_FILENAME}")
    print("----->  You can now start the server: uvicorn main:app --reload\n")


'''
This is the main function for the script. It parses the arguments and calls the ingest function to begin.
    --file: Path to the JSON file (required)
    --new_db: When on, will overwrite the existing database
    --diff_dir: Override the default chroma_db directory (must exist). Useful for testing.
    --force: Skip the semantic duplicate check and behave like the original pipeline.
    --dup_threshold: Cosine distance upper bound for duplicate detection (default: 0.44).
'''
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Agentic JSON file into ChromaDB")
    parser.add_argument("--file", required=True, help="Path to JSON file")
    parser.add_argument("--new_db", action="store_true", help="When on, will overwrite the existing database")
    parser.add_argument(
        "--diff_dir",
        default=None,
        help="Override the default chroma_db directory (must exist). Helpful when testing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the semantic duplicate check and ingest records directly.",
    )
    parser.add_argument(
        "--dup_threshold",
        type=float,
        default=DEFAULT_DUP_THRESHOLD,
        help=f"Cosine distance upper bound for duplicate detection (default: {DEFAULT_DUP_THRESHOLD}).",
    )
    parser.add_argument(
        "--use-bert",
        action="store_true",
        default=False,
        help=(
            "Enable BERT pre-screening before each LLM validation call. "
            "Articles rejected by BERT skip the LLM entirely, reducing ingestion time."
        )
    )


    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"[ERROR] File not found: {args.file}")
        exit(1)

    ingest(
        args.file,
        new_db=args.new_db,
        diff_dir=args.diff_dir,
        force=args.force,
        dup_threshold=args.dup_threshold,
        use_bert=args.use_bert,
    )
