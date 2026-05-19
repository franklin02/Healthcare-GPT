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
   - `src/GDELT/helpers.py` fetches candidate article pages.
   - It removes common page noise and extracts article body text.

3. **Classification and validation**
   - `src/GDELT/helpers.py` calls a local Ollama endpoint to validate active
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
   - `src/main.py` serves the FastAPI chat app over the local vector store.

## Typical Command

Run from the repository root:

```bash
python src/GDELT/runner.py --num-files 2 --limit 3
```

For a bounded historical run:

```bash
python src/GDELT/runner.py --start-date 20260101 --end-date 20260131 --subsectors cyber_attack,drug_shortage
```

After reviewing processed records, index them for the local chat app:

```bash
python src/ingest.py --file data/processed/GDELT.json
```

Then run the app:

```bash
cd src
uvicorn main:app --reload
```

## Outputs

- `data/raw/gdelt/seeds/`: candidate URLs before validation.
- `data/raw/gdelt/validated/`: records confirmed as disruptions.
- `data/raw/gdelt/enriched/`: records with extracted fields.
- `data/processed/GDELT.json`: final appended output.
- `data/seen_urls.json`: URL history used to avoid duplicate processing.
- `chroma_db/`: local vector store created by `src/ingest.py`.

## Current Supporting Modules

- `src/GDELT/gdelt_seeds.py`: GDELT file discovery, theme matching, subsector
  detection, date bounds, and URL quality filtering.
- `src/GDELT/helpers.py`: article body extraction, LLM validation, and
  subsector field extraction.
- `src/GDELT/ollama_filter.py`: focused URL filter for healthcare cyberattack
  article experiments.
- `src/scrapers/bert_scraper.py`: compact article scraper used by the BERT
  classifier.
- `src/ingest.py`: JSON loading, chunking, duplicate detection, and ChromaDB
  indexing.
- `src/main.py`: FastAPI endpoints and local chat UI.

## Notes For Contributors

- Use small `--limit` values for smoke tests.
- Keep Ollama running when using the LLM validation and extraction path.
- Prefer adding new pipeline behavior to the existing GDELT helpers and runner
  rather than creating one-off scripts.
- Treat prompt-pack and source-pack documents as historical unless they are
  explicitly pulled into the current Sphinx toctree.
