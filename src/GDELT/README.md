# GDELT Healthcare Disruption Pipeline

This pipeline ingests GDELT Global Knowledge Graph (GKG) data, filters for US-based healthcare  disruptions, and uses a local LLM to extract structured metadata.


## Stage Overview

- **Master File List**: Fetches the latest index of GDELT GKG files.

- **GKG Zips**: Downloads and extracts recent records.

- **Filtering**: Applies theme matching, location checks, and URL regex rules to isolate valuable links.

- **AI Validation**: Scrapes article bodies and prompts an LLM to confirm the presence of an active disruption to healthcare operations.

- **Extraction**: Prompts the LLM to pull subsector-specific metadata (e.g. drug names, downtime days, ransom amounts) into JSON.

- **JSON Output**: Appends finalized records to a master data file.


## End-to-End Run Command

Run the pipeline from the root directory using `runner.py`. You must have Ollama running locally with the llama3.2 model (ollama serve).

Basic run (to process 2 recent GDELT files, test with 3 URLs):

`python runner.py --num-files 2 --limit 3`

Targeted historical run (Jan 01 - 31, cyberattacks and drug shortages):

`python runner.py --start-date 20260101 --end-date 20260131 --subsectors cyber_attack,drug_shortage`

Command arguments:

- `-n`, `--num-files`: Number of recent GDELT files to scan.

- `-l`, `--limit`: Maximum number of seeds to process.

- `-s`, `--subsectors`: Which subsectors to filter for. Comma-separated list (`cyber_attack`, `drug_shortage`, `medical_device_shortage`, `natural_disaster`, `all`). Defaults to `all`.

- `--start-date` / `--end-date`: Date bounds for historical scraping.

- `-o`, `--output-path`: Path for the final JSON output. Defaults to `src/data/Ready_for_RAG/GDELT.json`.

- `--seen-urls-file`: Path for the JSON file of processed URLs. Defaults to `src/data/seen_urls.json`.


## Single-Stage Debugging

If you need to isolate stages for testing / benchmarking without running the full end-to-end pipeline:

Check GDELT seed filtering without scraping:
- Run `python gdelt_seeds.py` to test the regex and theme filters. It will print the matched URLs to the console without scraping their content or triggering the LLM.

Test URL scraping and AI validation without GDELT:
- Use `ollama_filter.py` by passing a hardcoded list of URLs to `filter_with_ollama()`.

Compare Ollama vs BERT validation:
- Run `python freeze_data.py` to benchmark the two models' accuracy and speed using an existing .csv of URLs (`frozen_race_data.csv`).


## Output File Locations

Intermediate and final data structures are saved to track pipeline progress and prevent data loss. For final output and URL history, paths can be changed with commands but default to the following.

Final output:
- `src/data/Ready_for_RAG/GDELT.json`

URL history (list of processed URLs to prevent future duplicates):
- `src/data/seen_urls.json`

Raw seeds (candidate URLs before scraping): 
- `data/raw/gdelt/seeds/`

Validated data (articles confirmed as threats by the LLM):
- `data/raw/gdelt/validated/`

Extracted data (articles with fully extracted JSON metadata):
- `data/raw/gdelt/enriched/`

---
*TODO: update output locations in `runner.py` and above to match `/data`'s new file structure.*
