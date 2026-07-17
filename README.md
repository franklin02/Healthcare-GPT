# Healthcare GPT

## What is it?
**Healthcare GPT** is a data pipeline and research tool that collects public data and classifies it into vulnerability events affecting critical infrastructure sectors.
It is currently a proof of concept tool. 

## Table of Contents
- [Main Features](#main-features)
- [Dependencies](#dependencies)
- [Configuration](#configuration)
- [License](#license)

## Main Features:
- Automated source collection via [The GDELT Project](https://www.gdeltproject.org/)
- Custom source collection support for priority information sources
- Parallel inference across a configurable number of models and threads 
- LLM-based classification of articles into vulnererability events
- Deduplication of articles with prioritization on data quality
- Structured JSON output per infrastructure sector

Some features have been deprecated and may or may not return:
- BERT pre-screening to reduce inference load
- ChromaDB ingestion and RAG frontend

## Dependencies


## Repository Structure
- `docs/` - project documentation and API reference sources
- `data/processed/` - processed JSON records ready for ingestion
- `src/GDELT/` - GDELT seed discovery, validation, extraction, and runner code
- `src/scrapers/` - shared scraper and LLM helper utilities
- `src/config/schema.json` - structured output schema reference
- `src/` - FastAPI app, ingestion pipeline, classifier, scraper, and data models
- `scripts/` - opt-in maintenance/validation scripts run on demand (not part of
  the pytest suite); e.g. `scripts/validate_schemas.py` validates that every
  schema populates every field. See CONTRIBUTING.md §7.

## Documentation
The published Sphinx documentation is available at:

https://franklin02.github.io/Healthcare-GPT/index.html

## Getting Started
1. Review the published documentation or `docs/index.md`.
2. Install the development tools listed below.
3. Run a small GDELT smoke test or docs build before opening a pull request.

