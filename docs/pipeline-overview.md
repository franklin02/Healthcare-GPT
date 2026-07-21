# Pipeline Overview

The pipeline turns public news articles into structured healthcare disruption records. Two independent source pipelines (**GDELT** and **HTML scraper**) feed into the same LLM validation and field-extraction layer, then record
results to local JSON/CSV and optionally to Supabase.


## Main Flow

### GDELT Pipeline

1. **Seed Discovery** 
   - `src/GDELT/gdelt_seeds.py` scans GDELT GKG files, filtering by sector themes (configured in `src/GDELT/sector_themes.py`), U.S. location hints, noise themes, and URL quality rules
   - Seeds can be collected across multiple threads (`--gdelt-seed-threads`)

2. **Scraping** 
   - `src/shared_utils.get_body_and_title()` fetches each candidate URL and extracts the article body and title

3. **Validation and Classification** (*shared with the HTML pipeline*)
   - `src/shared_utils.py` calls a local Ollama endpoint to classify the article as an operational disruption and assign a subsector
   - Optionally, `src/GDELT/BERT_filter.py` can be used as a pre-screen before LLM validation

4. **Field Extraction** (*shared with the HTML pipeline*)
   - Confirmed disruptions are given one of the following subsectors:
      - `cyber_attack`: confirmed breach or attack on a named healthcare entity
      - `drug_shortage`: confirmed shortage of a named drug
      - `medical_device_shortage`: confirmed inability to supply a specific device
      - `natural_disaster`: operational shutdown from fire, flood, storm, etc.
      - `other`: disruption that doesn't fit the above categories
   -  `src/shared_utils.extract_fields()` prompts the LLM with a subsector-scoped JSON template to extract subsector-specific info about the disruption
   - Records are built as `Vulnerability` objects (`src/classes/vulnerability.py`) with subsector-specific dataclasses (`DrugShortageData`, `MedicalDeviceShortageData`, `CyberAttackData`, `NaturalDisasterData`, `OtherData`)

5. **Saving results**
   - The runner writes intermediate stage files under `data/raw/gdelt/{seeds,validated,enriched}/`, appends final records to `data/output/results.json` (orchestrator default) or `data/processed/GDELT.json` (runner default), and updates `data/seen_urls.json`
   - If Supabase credentials are present, validated records are also deduplicated and inserted via `src/dedup.py` and `src/supabase_function.py`

6. **Optional retrieval app**
   - `src/ingest.py` loads processed JSON records into ChromaDB
   - `src/RAG/server.py` serves the FastAPI chat app over the local vector store


### HTML Pipeline

1. **Scraping**
   - `src/scrapers/scooper.py` paginates through configured HTML news sites (CyberScoop, StateScoop, FedScoop, AHA, HealthIT News), fetching article bodies and dates
   - New raw rows are appended to `data/raw/scooper_raw.csv`

2. **Validation and Classification** (*shared with the GDELT pipeline*)
   - `src/shared_utils.py` calls a local Ollama endpoint to classify the article as an operational disruption and assign a subsector
   - Optionally, `src/GDELT/BERT_filter.py` can be used as a pre-screen before LLM validation

3. **Field Extraction** (*shared with the GDELT pipeline*)
   - Confirmed disruptions are given one of the following subsectors:
      - `cyber_attack`: confirmed breach or attack on a named healthcare entity
      - `drug_shortage`: confirmed shortage of a named drug
      - `medical_device_shortage`: confirmed inability to supply a specific device
      - `natural_disaster`: operational shutdown from fire, flood, storm, etc.
      - `other`: disruption that doesn't fit the above categories
   - `src/shared_utils.extract_fields()` prompts the LLM with a subsector-scoped JSON template to extract subsector-specific info about the disruption
   - Records are built as `Vulnerability` objects (`src/classes/vulnerability.py`) with subsector-specific dataclasses (`DrugShortageData`, `MedicalDeviceShortageData`, `CyberAttackData`, `NaturalDisasterData`, `OtherData`)

4. **Saving Results**
   - Validated records go to `data/vulnerabilities/scooper_vuln.csv` and `data/processed/scooper.json`
   - Rejected articles go to `data/noise/scooper_noise.csv`


## Typical Commands

Run from the repository root using module execution:

```bash
# GDELT only — quick smoke test (3-seed default cap)
python -m src.orchestrator --skip-html --num-files 2

# GDELT only — bounded historical run
python -m src.orchestrator --skip-html --start-date 20260101 --end-date 20260131

# Both pipelines
python -m src.orchestrator --num-files 2 --limit 3

# HTML-only smoke test
python -m src.orchestrator --skip-gdelt

# Multithreaded parallel run
python -m src.orchestrator --models 2 --threads-per-model 2 --starting-port 11434

# Seeds-only mode (collect seeds without LLM processing)
python -m src.orchestrator --seeds_only
```

After collecting records, index them for the local chat app:

```bash
python -m src.ingest --file data/processed/GDELT.json
```

Then run the app:

```bash
uvicorn src.RAG.server:app --reload
```


## Configuration

- All CLI flags can be set as defaults in `src/config/config.cfg` (copy from `config-template.cfg`)
- Environment variables and `.env` are used for Supabase credentials (`SUPABASE_URL`, `SUPABASE_KEY`)
- The AI model defaults to `llama3.2:latest` and is read from `AI_MODEL` in config


## Outputs

| Path | Description |
|------|-------------|
| `data/raw/gdelt/seeds/` | Candidate URLs before LLM validation |
| `data/raw/gdelt/validated/` | Records confirmed as disruptions |
| `data/raw/gdelt/enriched/` | Records with filled-in fields |
| `data/raw/scooper_raw.csv` | Raw scraped HTML articles |
| `data/output/results.json` | GDELT output (orchestrator default) |
| `data/processed/scooper.json` | HTML scraper output |
| `data/vulnerabilities/scooper_vuln.csv` | Validated HTML scraper records |
| `data/noise/scooper_noise.csv` | Rejected HTML scraper articles |
| `data/seen_urls.json` | URL history for GDELT |
| `data/gdelt_cache/` | Cached GKG zip files |
| `data/logs/` | Per-module log files |
| `chroma_db/` | Local vector store for the RAG app |

## Recovery

The **GDELT** pipeline saves seen URLs, writes completed records, and preserves `data/raw/gdelt/seeds/` for recovery. 

This is state preservation, not automatic resume. To recover from staged GDELT data:

```bash
# Stitch from enriched (default, no LLM needed)
python -m src.GDELT.runner --stitch-stage enriched

# Stitch from validated
python -m src.GDELT.runner --stitch-stage validated

# Stitch from seeds (re-runs scraping + LLM, keep Ollama running)
python -m src.GDELT.runner --stitch-stage seeds
```

## Current Supporting Modules

| Module | Role |
|--------|------|
| `src/orchestrator.py` | Runs GDELT and/or HTML, manages multithreading and progress bars |
| `src/GDELT/runner.py` | GDELT end-to-end: seed collection → scrape → validate → extract → output |
| `src/GDELT/gdelt_seeds.py` | GKG file discovery, theme matching, subsector detection, URL quality |
| `src/GDELT/sector_themes.py` | Sector and subsector theme definitions |
| `src/GDELT/BERT_filter.py` | Optional pre-screen using BERT model before LLM validation |
| `src/GDELT/ollama_filter.py` | Old code; filter for healthcare-related articles using Ollama |
| `src/GDELT/gemma.py` | Filter for healthcare-related articles using Gemma |
| `src/scrapers/scooper.py` | HTML site scraping and LLM classification for configured news sites |
| `src/scrapers/fda_congress_reports.py` | FDA Reports to Congress PDF scraper (drug shortage reports) |
| `src/shared_utils.py` | Utilities used by both GDELT and HTML pipelines (article fetching, LLM validation/extraction, config loading, signal handling) |
| `src/classes/vulnerability.py` | `Vulnerability` dataclass and subsector-specific data classes |
| `src/cli_reporter.py` | Live progress bars and run summaries |
| `src/logging_utils.py` | File-backed module loggers |
| `src/ingest.py` | JSON to ChromaDB ingestion with semantic dedup and chunking |
| `src/RAG/server.py` | FastAPI chat endpoints and web UI |
| `src/dedup.py` | Semantic fingerprint embedding for Supabase dedup |
| `src/supabase_function.py` | Supabase client, reads, and writes |
| `src/data_migration.py` | Generate Supabase-ready SQL from local CSV/JSON files |


