# Developer Workflow

This page is to help contributors set up the project and start working with CORVID.

## Setup

Create a virtual environment from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install documentation and formatting tools:

```bash
python -m pip install -r requirements-dev.txt
```

Ollama is required for LLM validation and extraction paths:

```bash
ollama list
```

Keep the Ollama app or `ollama serve` running before commands that validate or extract article fields. Pull only the model needed for the path you are testing: current shared validation uses `src.shared_utils.AI_MODEL` `llama3.2` or `gemma4:e4b`. Confirm the needed model appears in `ollama list` before relying on LLM-backed validation results. Alternatively users may look at using the `serve_ollama` script.

## Contribution Requirements

All code in this project is formatted with ruff for consistency and readability.
Along with formatting requirements, all tests should pass for a PR to be accepted.


Format Python files before opening a pull request:

```bash
ruff format .
```

Check formatting without changing files:

```bash
python -m ruff format --check .
```

Run lint checks:

```bash
python -m ruff check .
```

Run the test suite:

```bash
python -m pytest -q
```

## Local RAG Chat Setup (Deprecated):

```bash
python -m src.ingest --file data/processed/AHA.json --force
uvicorn src.RAG.server:app --reload
```
***Note: The pipelines no longer output `AHA.json` or any other source specific json files. It is currently untested but you may try pushing either `results.json` or `scooper.json`.***  

Open `http://127.0.0.1:8000` and ask a question.

### Ingestion pipeline (`ingest.py`)

- Input JSON should follow the record shape represented by `src/config/schema.json`.
  The loader expects a top-level `sources: [...]` list.
- Flags:
  - `--file <path>` (required) — JSON file to ingest.
  - `--new_db` (optional) — wipes the existing ChromaDB and starts fresh.
  - `--force` (optional) — skips LLM validation and semantic duplicate checks.
  - `--dup_threshold <float>` (optional) — adjusts semantic duplicate matching.
  - `--use-bert` (optional) — runs BERT before ingestion-time LLM validation.
- Default behavior is additive and checks exact IDs plus semantic duplicates.

### API reference (`src/RAG/server.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/`        | Chat UI (serves `index.html`) |
| `POST` | `/chat`    | Body: `{ "question": "..." }` → `{ answer, sources, model, chunks_retrieved }` |
| `GET`  | `/status`  | Health check — DB readiness + record count |

Source documents in `/chat` responses follow the shape `{ id, title, source_name, direct_link }`.
