# CORVID Documentation

```{toctree}
:maxdepth: 1

pipeline-overview
classification
dev-workflow
api
```

## What is it?

**C.O.R.V.I.D.** (Classification of Open Reporting into Vulnerability Incident Data) is a data pipeline and research tool that collects public data and classifies it into vulnerability events affecting critical infrastructure sectors.

It is currently a proof of concept tool. 

## Main Features:
- Automated source collection via [The GDELT Project](https://www.gdeltproject.org/)
- Custom source collection support for priority information sources
- Parallel inference across a configurable number of models and threads 
- LLM-based classification of articles into vulnerability events
- Deduplication of articles with prioritization on data quality
- Structured JSON output per infrastructure sector

Some features have been deprecated and may or may not return:
- BERT pre-screening to reduce inference load
- ChromaDB ingestion and RAG frontend

## Quick Start Guide
1. Clone the repository
```bash
gh repo clone franklin02/Healthcare-GPT
```
2. Setup environment
```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```
3. Use the orchestrator to make your first requests
```bash
python -m src.orchestrator
```