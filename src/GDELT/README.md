# GDELT Healthcare Disruption Pipeline
This pipeline ingests GDELT Global Knowledge Graph (GKG) data, filters for US-based healthcare supply chain and operational disruptions, and uses a local LLM to extract structured metadata.


## Stage Overview

- Master File List: Fetches the latest index of GDELT GKG files.

- GKG Zips: Downloads and extracts recent CSV records.

- Filtering: Applies theme matching, location checks, and URL regex rules to isolate valuable links.

- AI Validation: Scrapes article bodies and prompts an LLM to confirm the presence of an active disruption to healthcare operations.

- Extraction: Prompts the LLM to pull subsector-specific metadata (e.g. drug names, downtime days, ransom amounts) into JSON.

- JSON Output: Appends finalized records to a master data file.


## End-to-End Run Command
Run the pipeline from the root directory using `runner.py`. You must have Ollama running locally with the llama3.2 model (ollama serve).

Basic run (to process 2 recent GDELT files, test with 3 URLs):

`python runner.py --num-files 2 --limit 3 --subsectors all`

Targeted historical run (Jan 01 - 31, cyberattacks and drug shortages):

`python runner.py --start-date 20260101 --end-date 20260131 --subsectors cyber_attack,drug_shortage`

**Command arguments:**

- `-n`, `--num-files`: Number of recent GDELT files to scan.

- `-l`, `--limit`: Maximum number of seeds to process.

- `-s`, `--subsectors`: Which subsectors to filter for. Comma-separated list (`cyber_attack`, `drug_shortage`, `medical_device_shortage`, `natural_disaster`, `all`).

- `--start-date` / `--end-date`: Date bounds for historical scraping.

- `-o`, `--output-path`: Path for the final JSON output.


