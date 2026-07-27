# Classification

This project relies on locally ran models to handle classification, field extraction, and vulnerability assessment.

Early prototypes used LLama 3.2:3b based on developer hardware access however it is recommeneded to use Gemma4:e4b or higher for production use cases.

Testing of both models showed that Llama 3.2 had a significant halucination and misclassification rate favoring `natural_disaster` subsectors more often than not.
Testing was also done to looking at quality based on parameter count there was no noticable change between running Gemma:e4b and Gemma:31b for our usecases.

***Note: Neither model is shipped with this repository you must download it from ollama or use some other API endpoint***

## Model configuration

In line with guidelines from Google this system uses the following model parameters:

For Gemma4:e4b
  "temperature": 1
  "top_k": 64
  "top_p": 0.95
  "num_ctx": 4096

# BERT Classifier (Deprecated)
During protoyping a BERT model was finetuned to classify subsectors as a method of reducing overall compute costs.
This feature is now deprecated and can be run optionally however is not recommended outside of development.

## Purpose

News feeds contain many articles that mention healthcare but do not describe
active operational disruptions. The classifier helps reduce that noise before
more expensive validation and extraction steps run.

### Implementation

`src/GDELT/BERT_filter.py` uses Hugging Face Transformers for zero-shot
classification. It first looks for a local fine-tuned model at:

```text
models/healthcare_bert_v2
```

When that model exists, the classifier returns one of the supported pipeline
subsectors:

- `drug_shortage`
- `medical_device_shortage`
- `cyber_attack`
- `natural_disaster`
- `other`
- `none`

When the fine-tuned model is not present, the classifier falls back to:

```text
typeform/distilbert-base-uncased-mnli
```

The fallback model uses these candidate labels:

- `cyber attack or data breach`
- `hospital system failure`
- `medical supply shortage`
- `unrelated news`

Fallback inference returns:

- `potential_hit` when a disruption label clears the threshold and beats the
  unrelated-news score.
- `none` when the article does not look like a disruption candidate.

The module selects CUDA, Apple Silicon MPS, or CPU depending on the available
runtime.

### Article Scraping

`src/scrapers/bert_scraper.py` is the scraper used by the BERT workflow. It
fetches a URL, extracts the page title, removes common page noise, and limits
the body text to the first 300 words for classification.

The scraper returns a dictionary with:

- `title`
- `body`

This keeps BERT inputs small and consistent across news sites.

### Current Entry Points

- `run_bert_inference({"title": "...", "body": "..."})` classifies one article.
- `src/shared_utils.py` can call BERT before LLM validation when
  `ai_check_validation(..., use_bert=True)` is used.
- `python -m src.ingest --use-bert` enables the same pre-screen before ingestion-time
  LLM validation.