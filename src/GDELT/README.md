# GDELT Healthcare Disruption Pipeline

This pipeline ingests GDELT Global Knowledge Graph (GKG) data, filters for
U.S.-based healthcare disruption candidates, and uses a local LLM to validate
events and extract structured metadata.


## Stage Overview

- **Master File List**: Fetches the latest index of GDELT GKG files.

- **GKG Zips**: Downloads and extracts recent records.

- **Filtering**: Applies theme matching, location checks, and URL regex rules to isolate valuable links.

- **AI Validation**: Scrapes article bodies and prompts an LLM to confirm the presence of an active disruption to healthcare operations.

- **Extraction**: Prompts the LLM to pull subsector-specific metadata (e.g. drug names, downtime days, ransom amounts) into JSON.

- **JSON Output**: Appends finalized records to a master data file.


## End-to-End Run Command

Run the pipeline from the repository root. Keep Ollama running locally before
commands that perform LLM validation or extraction. The current shared
validation model is configured in `src/shared_utils.py` as `AI_MODEL`.

Use module execution (`python -m src.GDELT.runner ...`) for pipeline entrypoints.
Direct file execution such as `python src/GDELT/runner.py` may work in some
local environments, but it is not the documented or tested execution path.

Basic run (to process 2 recent GDELT files, test with 3 URLs):

`python -m src.GDELT.runner --num-files 2 --limit 3`

Targeted historical run (Jan 01 - 31, cyberattacks and drug shortages):

`python -m src.GDELT.runner --start-date 20260101 --end-date 20260131 --subsectors cyber_attack,drug_shortage`

Command arguments:

- `-n`, `--num-files`: Number of recent GDELT files to scan.

- `-l`, `--limit`: Maximum number of seeds to process.

- `-s`, `--subsectors`: Which subsectors to filter for. Comma-separated list (`cyber_attack`, `drug_shortage`, `medical_device_shortage`, `natural_disaster`, `all`). Defaults to `all`.

- `--start-date` / `--end-date`: Date bounds for historical scraping.

- `-o`, `--output-path`: Path for the final JSON output. Defaults to `data/processed/GDELT.json`.

- `--seen-urls-file`: Path for the JSON file of processed URLs. Defaults to `data/seen_urls.json`.

- `--stitch-staged`: Recover the final output from records already saved in `data/raw/gdelt/enriched/` without fetching, scraping, or calling the LLM.


## Subsector Detection

GDELT themes are only discovery hints.

- One article can match more than one supported subsector.
- Raw seed records keep `subsector` as the primary label.
- Raw seed records may also include `detected_subsectors` with all GDELT theme matches.
- Final validated records still have one `subsector`, chosen by the LLM.
- `subsector_data` uses the schema for that final subsector.


## Subsector Detection

GDELT themes are only discovery hints.

- One article can match more than one supported subsector.
- Raw seed records keep `subsector` as the primary label.
- Raw seed records may also include `detected_subsectors` with all GDELT theme matches.
- Final validated records still have one `subsector`, chosen by the LLM.
- `subsector_data` uses the schema for that final subsector.


## Single-Stage Debugging

If you need to isolate stages for testing / benchmarking without running the full end-to-end pipeline:

Check GDELT seed filtering without scraping:
- Import `backfill_cyber_seeds()` from `src.GDELT.gdelt_seeds` in a Python
  shell or scratch script to test the regex and theme filters without running
  article scraping or LLM validation.
- For a GDELT module smoke test through the supported CLI path, run
  `python -m src.GDELT.runner --num-files 1 --limit 0`.

Test URL scraping and AI validation without GDELT:
- Import `filter_with_gemma()` from `src/GDELT/gemma.py` and pass a
  small list of URLs from a Python shell or scratch script.

Test BERT classification directly:
- Import `run_bert_inference()` from `src/GDELT/BERT_filter.py` and pass a
  dictionary with `title` and `body` keys.


## Output File Locations

Intermediate and final data structures are saved to track pipeline progress and prevent data loss. For final output and URL history, paths can be changed with commands but default to the following.

Final output:
- `data/processed/GDELT.json`

URL history (list of processed URLs to prevent future duplicates):
- `data/seen_urls.json`

Raw seeds (candidate URLs before scraping): 
- `data/raw/gdelt/seeds/`
- May include `detected_subsectors` when one article matches multiple subsectors.

Validated data (articles confirmed as threats by the LLM):
- `data/raw/gdelt/validated/`

Extracted data (articles with fully extracted JSON metadata):
- `data/raw/gdelt/enriched/`

## Crash Recovery

If the pipeline crashes after records are enriched but before the final output
is written, stitch the staged enriched records into the final JSON file:

`python -m src.GDELT.runner --stitch-staged`

Use `--output-path` with `--stitch-staged` to recover into a custom file or
directory. Stitching leaves staging files in place.

## Graceful Interrupts

During GDELT seed processing, press `Ctrl-C` to stop the run cleanly. The runner
marks the GDELT stage as paused, saves `data/seen_urls.json`, writes any
completed records to the configured GDELT output file, and preserves
`data/raw/gdelt/seeds/` so later recovery or stitching work can inspect the
remaining staged seeds.

This is a graceful stop with state preservation, not an automatic resume. When
run through the orchestrator, a paused GDELT stage also prevents later pipeline
stages from starting.
