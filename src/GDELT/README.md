# GDELT Healthcare Disruption Pipeline
This pipeline ingests GDELT Global Knowledge Graph (GKG) data, filters for US-based healthcare supply chain and operational disruptions, and uses a local LLM to extract structured metadata.


## Stage Overview

- Master File List: Fetches the latest index of GDELT GKG files.

- GKG Zips: Downloads and extracts recent CSV records.

- Filtering: Applies theme matching, location checks, and URL regex rules to isolate valuable links.

- AI Validation: Scrapes article bodies and prompts an LLM to confirm the presence of an active disruption to healthcare operations.

- Extraction: Prompts the LLM to pull subsector-specific metadata (e.g. drug names, downtime days, ransom amounts) into JSON.

- JSON Output: Appends finalized records to a master data file.


