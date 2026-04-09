import json
import argparse
import os
from pathlib import Path

from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

CHROMA_DIR   = "./chroma_db"
EMBED_MODEL  = "all-MiniLM-L6-v2"
COLLECTION   = "agentic_data"



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
empty string, the document is skipped. 
'''
def build_documents(records: list[dict]) -> list[Document]:
    docs = []
    for i, record in enumerate(records):
        text = record_to_text(record)
        if not text.strip():
            continue
        
        metadata = {
            "id": str(record.get("id", "")),
            "title": str(record.get("title", "")),
            "source_name": str(record.get("source_name", "")),
            "direct_link": str(record.get("direct_link", "")),
            "subsector": str(record.get("subsector", "")),
            "date_published": str(record.get("date_published", "")),
        }        
        docs.append(Document(page_content=text, metadata=metadata))

    return docs



def ingest(filepath: str, new_db: bool = False) -> None:
    print(f"\n{'-'*55}") 
    print("Ingestion Pipeline")
    print(f"{'-'*55}\n")

    # call load_document to load the JSON file (entire file is loaded)
    print(f"(1/4) Loading JSON from: {filepath}")
    records = load_document(filepath)
    print(f"-----> {len(records):,} records loaded")

    # builds the documents for each record found in the file 
    print("(2/4) Converting records to text chunks...")
    docs = build_documents(records)
    print(f"-----> {len(docs):,} documents created")

    # 3. Embeddings
    print(f"(3/4) Loading embedding model: {EMBED_MODEL}")
    print("-----> (First run downloads ~90 MB — cached after that)")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    # either delete the db or add to the existing one (double check with user @ runtime)
    print(f"(4/4) Storing in vector store: {CHROMA_DIR}")
    if os.path.exists(CHROMA_DIR) and new_db:
        print("----->  --new_db flag set, are you sure you want to delete the existing database? [y/n]")
        confirm = input() 
        if confirm == "y":
            import shutil
            shutil.rmtree(CHROMA_DIR)
            print("-----> Database deleted")
        else:
            print("-----> Database not deleted, exiting...")
            exit(1)

    elif os.path.exists(CHROMA_DIR):
        print("-----> Existing DB found — new records will be added)")
        #
        # TODO: find how to edit a chroma db 
        #

    # batched in 500 chunks to avoid memory spikes on large files
    # NOTE: This might need to be tweaked in the future, depending on future testing/ avg lenth of JSON objects
    BATCH = 500
    db = None
    for start in range(0, len(docs), BATCH):
        batch = docs[start : start + BATCH]
        end = min(start + BATCH, len(docs))
        print(f"-----> Embedding records {start+1}–{end} of {len(docs)}…")
        if db is None:
            db = Chroma.from_documents(
                batch,
                embeddings,
                persist_directory=CHROMA_DIR,
                collection_name=COLLECTION,
            )
        else:
            db.add_documents(batch)

    print(f"\n\n\nIngestion complete!")
    print(f"-----> {len(docs):,} records indexed in {CHROMA_DIR}")
    print(f"----->  You can now start the server: uvicorn main:app --reload\n")


'''
This is the main function for the script. It parses the arguments and calls the ingest function to begin.
There are only 2 arguments:
    --file: Path to the JSON file
    --new_db: When on, will overwrite the existing database
This should be automated and --new_db will not run by default.
'''
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Agentic JSON file into ChromaDB")
    parser.add_argument("--file", required=True, help="Path to JSON file")
    parser.add_argument("--new_db", action="store_true", help="When on, will overwrite the existing database")

    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"❌ File not found: {args.file}")
        exit(1)

    ingest(args.file, new_db=args.new_db)