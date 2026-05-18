# Healthcare GPT

## Overview
Healthcare GPT is a proof-of-concept project to support INL’s new AI tasking: build an AI-assisted workflow that collects authoritative public data on healthcare-sector disruptions and produces (1) a compact dashboard JSON payload and (2) a 1-page executive summary with citations and confidence metadata.

We are starting with a focused pilot scope (medical device disruptions + hospital cyberattacks) to demonstrate feasibility before scaling.

## Pilot Scope (POC)
**Primary focus**
- Medical device disruption events (cyber incidents + natural hazard events)
- Medical device shortages (when directly linked to disruptions)

**Secondary focus**
- Hospital cyberattacks and related operational disruptions
- Pharmaceutical manufacturing events (as capacity allows)

**Data sources**
- Public/authoritative sources only (no restricted/PCII/FOUO content in this repo)

## Key Outputs
1) **Disruption Event Dataset (CSV)**  
   Structured event records with provenance: URL, accessed date, evidence snippet, and confidence.
2) **Mitigation KPI Dataset (CSV)**  
   KPI tracking table used to populate dashboard mitigation metrics.
3) **Compact Dashboard JSON**  
   Deterministic schema-based JSON output for dashboard rendering (sector → subsector risk + top incidents + KPIs + sources).
4) **Executive Summary**  
   One-page summary derived from retrieved evidence (with citations + confidence).

## Repository Structure
- `docs/` — project documentation (plan, meeting questions, data dictionary)
- `data/templates/` — CSV templates used by students/agents
- `data/collected/` — collected data (CSV files) or pointers to shared storage
- `schemas/` — JSON schema(s) for dashboard payload
- `src/` — scripts (future): crawlers/agents, parsing, validation, JSON generation

## Security / Data Handling
- Use **public sources only** unless INL explicitly provides approved restricted feeds.
- Do **not** paste restricted or sensitive information into public AI tools.
- Every data entry must include provenance fields (URL, accessed date, evidence snippet, confidence).

## Getting Started
1) Review `docs/index.md`
2) Download and use templates in `data/templates/`
3) Add collected events to `data/collected/` (or link to shared storage if large)
4) Add at least two technical questions to `docs/meeting-questions.md` for the next INL meeting

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

- `Ruff Format` runs on every pull request and fails if Python files are not
  formatted with Ruff.
- `Sphinx Docs` runs on every push to `main`, builds the docs, and publishes
  them to GitHub Pages.

The workflow files live in `.github/workflows/`. GitHub starts running them
automatically after they are merged.

Repo admins still need to finish two settings in GitHub:

1) In Settings -> Branches, require the Ruff status check before merging to
   `main`.
2) In Settings -> Pages, set the publishing source to GitHub Actions.

## Contacts / Roles
- Repo lead: Yang (repo admin + structure)
- All team members: data collection + questions + documentation updates
