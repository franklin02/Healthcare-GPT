# Healthcare GPT Documentation

```{toctree}
:maxdepth: 2
:caption: Current Documentation

pipeline-overview
bert-classifier
api
```

## Project Direction

Healthcare GPT is currently focused on a healthcare disruption detection
pipeline. The active direction is to collect candidate articles, classify
whether they describe operational healthcare disruptions, and turn confirmed
events into structured records for later analysis.

The current work emphasizes:

- GDELT-based discovery of candidate healthcare disruption news.
- BERT-based classification for fast article triage.
- Local LLM validation and field extraction where structured metadata is
  needed.
- JSON outputs that preserve source links, subsectors, article content, and
  extracted disruption fields.

## What Is Current

- `src/GDELT/` contains the main GDELT pipeline and BERT classifier work.
- `src/GDELT/runner.py` coordinates seed collection, article scraping,
  validation, extraction, intermediate saves, and final JSON output.
- `src/GDELT/BERT_filter.py` contains the zero-shot classifier workflow used
  to identify likely healthcare disruption articles.
- `docs/api.rst` publishes API documentation from selected Python docstrings.

## What Is Legacy

Older source-pack and meeting-question documents are still in the repository
for historical context, but they are no longer the main documentation path.
They are intentionally not linked from this Sphinx home page.

## Data Handling

Keep the repository clean:

- Use public sources only unless explicitly approved otherwise.
- Do not commit restricted, sensitive, PCII, or FOUO information.
- Preserve source URLs and enough provenance to trace each generated record.
