# Pipeline Overview

The current pipeline turns public news candidates into structured healthcare
disruption records. The goal is to separate likely operational disruptions
from general healthcare news, then preserve useful metadata for downstream
analysis and retrieval.

## Main Flow

1. **Seed discovery**
   - `src/GDELT/gdelt_seeds.py` scans GDELT GKG records.
   - It filters by healthcare themes, supported subsectors, U.S. location
     hints, noise themes, and URL quality rules.

2. **Article scraping**
   - `src/shared_utils.py` fetches candidate article pages.
   - It removes common page noise and extracts article body text.

3. **Classification and validation**
   - `src/shared_utils.py` calls a local Ollama endpoint to validate active
     operational disruptions.
   - `src/GDELT/BERT_filter.py` can be used as an optional pre-screen before
     LLM validation.

4. **Field extraction**
   - Confirmed records are mapped to subsectors such as `cyber_attack`,
     `drug_shortage`, `medical_device_shortage`, `natural_disaster`, or
     `other`.
   - Subsector-specific fields are extracted into JSON.

5. **Persistence**
   - `src/GDELT/runner.py` saves intermediate seed, validated, and enriched
     records under `data/raw/gdelt/`.
   - Final records are appended to `data/processed/GDELT.json` by default,
     wrapped in a top-level `sources` list.
   - `data/seen_urls.json` tracks URLs that have already reached LLM
     validation.

6. **Optional retrieval app**
   - `src/ingest.py` loads processed JSON records into ChromaDB.
   - `src/RAG/server.py` serves the FastAPI chat app over the local vector store.

## Typical Command

Run from the repository root:

```bash
python -m src.GDELT.runner --num-files 2 --limit 3
```

Run both active pipelines through the orchestrator:

```bash
python -m src.orchestrator --num-files 2 --limit 3
```

Run a small HTML-only pagination smoke test:

```bash
python -m src.orchestrator --skip-gdelt --html-start-page 1 --html-page-cap 0 --verbose
```

For a bounded historical run:

```bash
python -m src.GDELT.runner --start-date 20260101 --end-date 20260131 --subsectors cyber_attack,drug_shortage
```

After reviewing processed records, index them for the local chat app:

```bash
python src/ingest.py --file data/processed/GDELT.json
```

Then run the app:

```bash
uvicorn src.RAG.server:app --reload
```

## Outputs

- `data/raw/gdelt/seeds/`: candidate URLs before validation.
- `data/raw/gdelt/validated/`: records confirmed as disruptions.
- `data/raw/gdelt/enriched/`: records with extracted fields.
- `data/processed/GDELT.json`: final appended output.
- `data/seen_urls.json`: URL history used to avoid duplicate processing.
- `chroma_db/`: local vector store created by `src/ingest.py`.

## HTML Pagination Controls

Configured HTML sources keep their selector and pagination defaults in
`src/scrapers/html_engine.py` because each site starts and paginates
differently. The orchestrator exposes `--html-start-page` and
`--html-page-cap` as run-time overrides so larger HTML runs do not require
source edits. When those arguments are omitted, each source uses its configured
`starting_page` and `cap`, preserving the previous behavior. A page cap of
`-1` means unlimited pagination, matching the HTML scraper's direct CLI.

The overrides are intentionally global across HTML sites. That keeps the
orchestrator interface small and mirrors the GDELT runner's coarse controls:
operators can choose a quick smoke test, a bounded scan, or an unrestricted
backfill without needing to know each site's internal config shape.

## Current Supporting Modules

- `src/GDELT/gdelt_seeds.py`: GDELT file discovery, theme matching, subsector
  detection, date bounds, and URL quality filtering.
- `src/shared_utils.py`: article body extraction, LLM validation, and
  subsector field extraction.
- `src/GDELT/gemma.py`: focused Gemma URL filter for healthcare cyberattack
  article experiments.
- `src/GDELT/BERT_filter.py`: optional BERT classifier used to pre-screen
  candidate articles before LLM validation.
- `src/orchestrator.py`: top-level command that runs GDELT first, then all
  configured HTML scrapers.
- `src/ingest.py`: JSON loading, chunking, duplicate detection, and ChromaDB
  indexing.
- `src/RAG/server.py`: FastAPI endpoints and local chat UI.

## Notes For Contributors

- Use small `--limit` values for smoke tests.
- Keep Ollama running when using the LLM validation and extraction path.
- Prefer adding new pipeline behavior to `src/shared_utils.py` and the GDELT runner
  rather than creating one-off scripts.
- Treat prompt-pack and source-pack documents as historical unless they are
  explicitly pulled into the current Sphinx toctree.
