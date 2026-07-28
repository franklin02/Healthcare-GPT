# Pipeline Overview

The pipeline turns public news articles into structured healthcare disruption records. Two independent source pipelines (**GDELT** and **HTML scraper**) feed into the same LLM validation and field-extraction layer, then record results to local JSON/CSV and optionally to Supabase.


## Main Flow

### GDELT Pipeline

1. **Seed Discovery** 
   - `src/GDELT/gdelt_seeds.py` scans GDELT GKG files, filtering by sector themes (configured in `src/GDELT/sector_themes.py`)
   - Seeds can be collected across multiple threads (`--gdelt-seed-threads`)

2. **Scraping** 
   - `src/shared_utils.get_body_and_title()` fetches each candidate URL and extracts the article body and title

3. **Validation and Classification** (*shared with the HTML pipeline*)
   - `src/shared_utils.py` calls a local Ollama endpoint to classify the article as an operational disruption and assign a subsector
   - Optionally, `src/GDELT/BERT_filter.py` can be used as a pre-screen before LLM validation however this functionality is deprecated

4. **Field Extraction** (*shared with the HTML pipeline*)
   - Confirmed disruptions are given one of the following subsectors:
      - `cyber_attack`
      - `drug_shortage`
      - `medical_device_shortage`
      - `natural_disaster`
      - `other`: a catch all to capture disruptions that don't fit the above categories
   - `src/shared_utils.extract_fields()` prompts the LLM with a subsector-scoped JSON template to extract subsector-specific info about the disruption
   - Records are built as `Vulnerability` objects (`src/classes/vulnerability.py`) with subsector-specific dataclasses (`DrugShortageData`, `MedicalDeviceShortageData`, `CyberAttackData`, `NaturalDisasterData`, `OtherData`)

5. **Saving results**
   - The runner writes intermediate stage files under `data/raw/gdelt/{seeds,validated,enriched}/`, appends final records to `data/output/results.json` (orchestrator default) or `data/processed/GDELT.json` (runner default), and updates `data/seen_urls.json`


### HTML Pipeline

1. **Scraping**
   - `src/scrapers/scooper.py` paginates through configured HTML news sites (CyberScoop, StateScoop, FedScoop, AHA, HealthIT News), fetching article bodies and dates
   - New raw rows are appended to `data/raw/scooper_raw.csv`

2. **Validation and Classification** (*shared with the GDELT pipeline*)
   - `src/shared_utils.py` calls a local Ollama endpoint to classify the article as an operational disruption and assign a subsector
   - Optionally, `src/GDELT/BERT_filter.py` can be used as a pre-screen before LLM validation

3. **Field Extraction** (*shared with the GDELT pipeline*)
   - Confirmed disruptions are given one of the following subsectors:
      - `cyber_attack`
      - `drug_shortage`
      - `medical_device_shortage`
      - `natural_disaster`
      - `other`: a catch all to capture disruptions that don't fit the above categories
   - `src/shared_utils.extract_fields()` prompts the LLM with a subsector-scoped JSON template to extract subsector-specific info about the disruption
   - Records are built as `Vulnerability` objects (`src/classes/vulnerability.py`) with subsector-specific dataclasses (`DrugShortageData`, `MedicalDeviceShortageData`, `CyberAttackData`, `NaturalDisasterData`, `OtherData`)

4. **Saving Results**
   - HTML results will output in `data/processed/scooper.json` 


## Recommendations

Each pipeline was designed to be able to run standalone using its own command line interfaces; however, it is highly recommended to fill out and use the `config-template.cfg` as it covers the same settings that can be passed as arguments.  


## Deprecated features

This project also contains a deprecated RAG pipeline and frontend to query records. It can be used with the following commands:

```bash
python -m src.ingest --file data/processed/GDELT.json
```

Then run the app:

```bash
uvicorn src.RAG.server:app --reload
```

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


## GDELT Recovery 

In the case of an unrecoverable pipeline state, the **GDELT** pipeline saves records in stages all preserved in `data/raw/gdelt/seeds/`. 

To recover from staged GDELT data:

```bash
# Stitch from enriched data
python -m src.GDELT.runner --stitch-stage enriched

# Stitch from validated data
python -m src.GDELT.runner --stitch-stage validated

# Stitch from seeds (re-runs scraping + LLM)
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


## Note

- The `data/gdelt_cache/` directory is not auto-cleared unless the `--clean` flag is used or set in your config; you can manually delete the contents of the directory to clear disk space or force fresh downloads