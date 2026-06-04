# Healthcare GPT

## Overview
Healthcare GPT is a proof-of-concept project for an AI-assisted workflow that
collects public healthcare-disruption signals, validates operational impact,
and produces structured incident records for analysis and retrieval.

The current implementation centers on a GDELT discovery pipeline, local
Ollama-backed validation/extraction, optional BERT pre-screening, ChromaDB
ingestion, and a FastAPI chat interface over processed records.

## Pilot Scope
**Primary focus**
- Medical device disruption events, including cyber incidents and natural hazard events
- Medical device shortages when directly linked to disruptions

**Secondary focus**
- Hospital cyberattacks and related operational disruptions
- Pharmaceutical manufacturing events as capacity allows

**Data sources**
- Public and authoritative sources

## Key Outputs
1. **Processed disruption JSON**
   Structured records in `data/processed/*.json`, wrapped in a top-level
   `sources` list with provenance and subsector metadata.
2. **Raw GDELT staging files**
   Seed, validated, and enriched records under `data/raw/gdelt/` for pipeline
   inspection.
3. **Local retrieval index**
   A ChromaDB vector store created by `src/ingest.py`.
4. **FastAPI chat app**
   A local UI and `/chat` API served from `src/RAG/server.py`.

## Repository Structure
- `docs/` - project documentation and API reference sources
- `data/processed/` - processed JSON records ready for ingestion
- `src/GDELT/` - GDELT seed discovery, validation, extraction, and runner code
- `src/scrapers/` - shared scraper and LLM helper utilities
- `src/config/schema.json` - structured output schema reference
- `src/` - FastAPI app, ingestion pipeline, classifier, scraper, and data models

## Documentation
The published Sphinx documentation is available at:

https://franklin02.github.io/Healthcare-GPT/index.html

## Getting Started
1. Review the published documentation or `docs/index.md`.
2. Install the development tools listed below.
3. Run a small GDELT smoke test or docs build before opening a pull request.

## Optional Supabase Setup
Supabase is used as an optional persistence and deduplication store. When
`SUPABASE_URL` and `SUPABASE_KEY` are set, the GDELT and HTML pipelines can
write accepted vulnerabilities, rejected noise articles, and duplicate records
to Supabase. When those variables are missing, database writes are disabled and
the local JSON/CSV outputs still work.

1. Create a Supabase project and open the SQL editor.
2. Run the setup SQL files in this order:

```sql
-- src/config/schema.sql
-- src/config/duplicate.sql
-- src/config/dedup_rpc.sql
```

3. Add local credentials in a gitignored `.env` file:

```bash
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_KEY=your-service-role-key
```

Use the service-role key only for local/private pipeline runs and never commit
it. The schema enables row-level security, so an anon key will need explicit
policies before it can insert or query rows.

The main tables are:
- `vulnerabilities` - accepted disruption records with optional embeddings
- `noise` - rejected articles used to avoid reprocessing known noise
- `duplicates` - duplicate records linked back to their original vulnerability

## Developer Tooling
Install the development tools with:

```bash
python -m pip install -r requirements-dev.txt
```

Format Python files with Ruff before opening a pull request:

```bash
ruff format .
```

Check formatting without changing files:

```bash
ruff format --check .
```

Build the Sphinx documentation locally:

```bash
sphinx-build -b html -E docs docs/_build/html
```

## GitHub Actions
This repository uses GitHub Actions for two automation checks:

- `CI` runs on pull requests to `main` and fails if pytest, Ruff lint, or Ruff
  formatting checks fail.
- `Sphinx Docs` runs on pull requests and pushes to `main`. Pushes to `main`
  also publish the built docs to GitHub Pages.

The workflow files live in `.github/workflows/`. GitHub starts running them
automatically after they are merged.

Repo admins still need to finish two settings in GitHub:

1. In Settings -> Branches, require the CI status check before merging to
   `main`.
2. In Settings -> Pages, set the publishing source to GitHub Actions.
