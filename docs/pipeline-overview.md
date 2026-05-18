# Pipeline Overview

The current pipeline turns public news candidates into structured healthcare
disruption records. The goal is to separate likely operational disruptions
from general healthcare news, then preserve useful metadata for downstream
analysis.

## Main Flow

1. **Seed discovery**
   - `src/GDELT/gdelt_seeds.py` scans GDELT GKG records.
   - It filters by healthcare-related themes, location, subsector, and URL
     quality rules.

2. **Article scraping**
   - `src/GDELT/helpers.py` fetches candidate article pages.
   - It removes common page noise and extracts article body text.

3. **Classification and validation**
   - `src/GDELT/BERT_filter.py` provides a BERT-based classification path for
     fast article triage.
   - `src/GDELT/helpers.py` also supports local LLM validation for active
     operational disruptions.

4. **Field extraction**
   - Confirmed records are mapped to subsectors such as `cyber_attack`,
     `drug_shortage`, `medical_device_shortage`, `natural_disaster`, or
     `other`.
   - Subsector-specific fields are extracted into JSON.

5. **Persistence**
   - `src/GDELT/runner.py` saves intermediate seed, validated, and enriched
     records under `data/raw/gdelt/`.
   - Final records are appended to `data/processed/GDELT.json` by default.

## Typical Command

Run from the repository root:

```bash
python src/GDELT/runner.py --num-files 2 --limit 3
```

For a bounded historical run:

```bash
python src/GDELT/runner.py --start-date 20260101 --end-date 20260131 --subsectors cyber_attack,drug_shortage
```

## Outputs

- `data/raw/gdelt/seeds/`: candidate URLs before validation.
- `data/raw/gdelt/validated/`: records confirmed as disruptions.
- `data/raw/gdelt/enriched/`: records with extracted fields.
- `data/processed/GDELT.json`: final appended output.
- `data/seen_urls.json`: URL history used to avoid duplicate processing.

## Current Supporting Modules

- `src/GDELT/gdelt_seeds.py`: GDELT file discovery, theme matching, subsector
  detection, date bounds, and URL quality filtering.
- `src/GDELT/helpers.py`: article body extraction, LLM validation, and
  subsector field extraction.
- `src/scrapers/bert_scraper.py`: compact article scraper used by the BERT
  classifier.
- `src/GDELT/freeze_data.py` and `src/GDELT/prep_for_vis.py`: offline
  comparison and benchmarking helpers for BERT/LLM evaluation.

## Notes For Contributors

- Use small `--limit` values for smoke tests.
- Keep Ollama running when using the LLM validation and extraction path.
- Prefer adding new pipeline behavior to the existing GDELT helpers and runner
  rather than creating one-off scripts.
