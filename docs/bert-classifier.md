# BERT Classifier

The BERT classifier work is the fast triage layer for deciding whether an
article is likely to describe a healthcare disruption.

## Purpose

News feeds contain many articles that mention healthcare but do not describe
active operational disruptions. The classifier helps reduce that noise before
more expensive validation and extraction steps run.

## Implementation

`src/GDELT/BERT_filter.py` uses Hugging Face Transformers with the model:

```text
typeform/distilbert-base-uncased-mnli
```

The classifier runs zero-shot classification over a short article prompt built
from the title and first part of the article body.

Current candidate labels are:

- `cyber attack or data breach`
- `hospital system failure`
- `medical supply shortage`
- `unrelated news`

The classifier returns:

- `potential_hit` when a disruption label clears the threshold and beats the
  unrelated-news score.
- `none` when the article does not look like a disruption candidate.

## Article Scraping

`src/scrapers/bert_scraper.py` is the scraper used by the BERT workflow. It
fetches a URL, extracts the page title, removes common page noise, and limits
the body text to the first 300 words for classification.

The scraper returns a dictionary with:

- `title`
- `body`

This keeps BERT inputs small and consistent across news sites.

## Offline Evaluation

The classifier can be run against a CSV of article examples:

```bash
python src/GDELT/BERT_filter.py path/to/articles.csv
```

Expected CSV columns:

- `url`
- `title`
- `body`
- optional `llama_hit`

When `llama_hit` is present, the script prints agreement statistics comparing
BERT-flagged URLs against LLM-confirmed hits.

## Benchmarking Helpers

Two helper scripts support classifier evaluation:

- `src/GDELT/freeze_data.py` re-runs BERT and LLM checks on historical URLs,
  compares decisions, records timing, and writes `race_results_<n>.csv`.
- `src/GDELT/prep_for_vis.py` benchmarks BERT inference on frozen samples and
  writes `bert_benchmark_results.csv` for visualization or further analysis.

## Where It Fits

BERT is intended to be the lightweight classifier stage in the broader
pipeline. Local LLM calls can still be used for validation and structured field
extraction when the pipeline needs richer reasoning or JSON metadata.
