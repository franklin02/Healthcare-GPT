# Healthcare GPT Documentation

```{toctree}
:maxdepth: 2
:caption: Current Documentation

pipeline-overview
bert-classifier
developer-workflow
api
```

## Project Direction

Healthcare GPT is currently focused on an AI-assisted healthcare disruption
detection workflow. The active path collects candidate public articles, filters
for operational healthcare disruptions, extracts structured event fields, and
optionally indexes the resulting records for local retrieval.

The current work emphasizes:

- GDELT-based discovery of candidate healthcare disruption news.
- Local Ollama validation for active operational disruptions.
- Structured JSON records wrapped in a top-level `sources` list.
- Optional BERT pre-screening for faster triage when the classifier model is
  available.
- ChromaDB ingestion and a FastAPI chat interface for retrieval over processed
  records.

## What Is Current

- `src/GDELT/` contains the main GDELT pipeline and BERT classifier work.
- `src/orchestrator.py` is the top-level command for running GDELT followed by
  all configured HTML scrapers.
- `src/GDELT/runner.py` coordinates seed collection, article scraping,
  validation, extraction, intermediate saves, and final JSON output.
- `src/GDELT/BERT_filter.py` contains the zero-shot classifier workflow used
  to identify likely healthcare disruption articles when BERT screening is
  enabled.
- `src/ingest.py` loads processed JSON records, chunks them, detects
  duplicates, and writes them to ChromaDB.
- `src/main.py` serves the local FastAPI chat app over the vector store.
- `docs/api.rst` publishes API documentation from selected Python docstrings.

## What Is Legacy

Older source-pack, prompt-pack, and meeting-question documents are still in the
repository for historical context. They are intentionally not linked from this
Sphinx home page because they do not describe the active implementation path.

## Data Handling

Keep the repository clean:

- Use public sources only unless explicitly approved otherwise.
- Do not commit restricted, sensitive, PCII, or FOUO information.
- Preserve source URLs and enough provenance to trace each generated record.
