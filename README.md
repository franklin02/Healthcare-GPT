# Healthcare GPT

## Overview
Healthcare GPT is a proof-of-concept project for an AI-assisted workflow that
collects authoritative public data on healthcare-sector disruptions and
produces structured incident outputs, citations, and confidence metadata.

The current project direction emphasizes using BERT as a classifier within a
larger pipeline for identifying and organizing healthcare disruption events.

## Pilot Scope
**Primary focus**
- Medical device disruption events, including cyber incidents and natural hazard events
- Medical device shortages when directly linked to disruptions

**Secondary focus**
- Hospital cyberattacks and related operational disruptions
- Pharmaceutical manufacturing events as capacity allows

**Data sources**
- Public and authoritative sources

## Key Outputs
1. **Disruption Event Dataset (CSV)**  
   Structured event records with provenance, evidence snippets, and confidence.
2. **Mitigation KPI Dataset (CSV)**  
   KPI tracking table used to populate dashboard mitigation metrics.
3. **Compact Dashboard JSON**  
   Deterministic schema-based JSON output for dashboard rendering.
4. **Executive Summary**  
   One-page summary derived from retrieved evidence with citations and confidence.

## Repository Structure
- `docs/` - project documentation and API reference sources
- `data/templates/` - CSV templates for structured data collection
- `data/collected/` - collected data files
- `schemas/` - JSON schemas for structured outputs
- `src/` - application, classifier, scraper, and pipeline code

## Documentation
The published Sphinx documentation is available at:

https://franklin02.github.io/Healthcare-GPT/index.html

## Getting Started
1. Review the published documentation or `docs/index.md`.
2. Install the development tools listed below.
3. Run the local smoke checks before opening a pull request.

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

1. In Settings -> Branches, require the Ruff status check before merging to
   `main`.
2. In Settings -> Pages, set the publishing source to GitHub Actions.
