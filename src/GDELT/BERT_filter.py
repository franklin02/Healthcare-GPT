"""Classify healthcare-related news items using a zero-shot BERT model.

This module is a library that provides helpers to run zero-shot classification
on article text in raw text format (headline + excerpt). It is intended to be
called via `run_bert_inference` from the pipeline's `ask_llm` module.

Dependencies:
    transformers (pipeline)
    torch

Example:
    from BERT_filter import load_model, run_bert_inference

    classifier = load_model()
    result = run_bert_inference({"title": "Hospital breach", "body": "..."}, classifier)
"""

from pathlib import Path

import torch
import sys
import os
from transformers import pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.logging_utils import get_file_logger  # noqa: E402

_MODULE_DIR = Path(__file__).resolve().parent
LOG_FILE = _MODULE_DIR.parent.parent / "data" / "logs" / "bert_filter.log"
LOGGER = get_file_logger(__name__, LOG_FILE)
FINETUNE_BERT_PATH = _MODULE_DIR.parent / "models" / "healthcare_bert_v2"
FALLBACK_MODEL_ID = "typeform/distilbert-base-uncased-mnli"

_FINETUNED_CANDIDATES = [
    "a drug or pharmaceutical shortage",
    "a medical device shortage",
    "a cyber attack or data breach",
    "a natural disaster affecting healthcare",
    "another healthcare disruption",
    "no significant healthcare threat",
]
_CANDIDATE_TO_SUBSECTOR = {
    "a drug or pharmaceutical shortage": "drug_shortage",
    "a medical device shortage": "medical_device_shortage",
    "a cyber attack or data breach": "cyber_attack",
    "a natural disaster affecting healthcare": "natural_disaster",
    "another healthcare disruption": "other",
    "no significant healthcare threat": "none",
}
_FALLBACK_CANDIDATES = [
    "cyber attack or data breach",
    "hospital system failure",
    "medical supply shortage",
    "unrelated news",
]
_FALLBACK_THREAT_LABELS = [
    "cyber attack or data breach",
    "hospital system failure",
    "medical supply shortage",
]


def get_device():
    """Detect the best available device for model inference.

    Returns:
        int | str: 0 for CUDA, "mps" for Apple Silicon, or -1 for CPU.
    """
    if torch.cuda.is_available():
        return 0
    elif torch.backends.mps.is_available():
        return "mps"
    else:
        return -1


def _device_label(device) -> str:
    if device == 0:
        return "cuda"
    if device == "mps":
        return "mps"
    return "cpu"


def _selected_model_id():
    return FINETUNE_BERT_PATH if FINETUNE_BERT_PATH.exists() else FALLBACK_MODEL_ID


def describe_model() -> tuple[str, str]:
    """Return the BERT model identifier and device label without loading weights."""
    return str(_selected_model_id()), _device_label(get_device())


def _quiet_transformers_output() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    try:
        from huggingface_hub.utils import disable_progress_bars

        disable_progress_bars()
    except Exception:
        pass
    try:
        from transformers.utils import logging as transformers_logging

        transformers_logging.set_verbosity_error()
    except Exception:
        pass


def load_model(verbose: bool = False):
    """Load the zero-shot classification pipeline.

    Checks for a local finetuned model at FINETUNE_BERT_PATH. If found,
    loads it; otherwise warns and falls back to FALLBACK_MODEL_ID.

    Returns:
        transformers.Pipeline: Loaded zero-shot classification pipeline.
    """
    if not verbose:
        _quiet_transformers_output()

    device = get_device()
    device_label = _device_label(device)
    if verbose:
        print(f"[DEBUG] Looking for finetuned model at: {FINETUNE_BERT_PATH}")
    MODEL_ID = _selected_model_id()
    if MODEL_ID == FALLBACK_MODEL_ID and verbose:
        print("[WARN] Finetuned model not found, reverting to base model.")

    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        tokenizer.model_input_names = ["input_ids", "attention_mask"]
        model = pipeline(
            "zero-shot-classification",
            model=MODEL_ID,
            tokenizer=tokenizer,
            device=device,
        )
        if verbose:
            print(f"[INFO] BERT model loaded from {MODEL_ID} using {device_label}")
        LOGGER.info("BERT model loaded from %s using %s", MODEL_ID, device_label)
        return model
    except Exception as e:
        if verbose:
            print(f"[WARN] Failed to load BERT model: {e}")
        LOGGER.warning("Failed to load BERT model: %s", e)
        return None


def run_bert_inference(data: dict, classifier=None, verbose: bool = False) -> str:
    """Classify a single article as a potential healthcare-related hit.

    If the finetuned model is present, uses the model's trained candidate
    labels and returns the predicted subsector directly. If only the base
    model is available, falls back to a threshold-based approach and returns
    "potential_hit" or "none".

    Args:
        data (dict): Article payload. Expected keys:
            - "title" (str): Article headline. Missing/None treated as "".
            - "body" (str): Article body/text. Missing/None treated as "".
        classifier: Loaded transformers pipeline instance for inference.
            - If None, load_model() is called automatically (recommended
            for most callers). Pass explicitly only to override the default
            model, during testing or experimentation.

    Returns:
        str: If finetuned model is present, one of: "drug_shortage",
            "medical_device_shortage", "cyber_attack", "natural_disaster",
            "other", or "none". If falling back to base model, one of:
            "potential_hit" or "none".
    """
    if classifier is None:
        classifier = load_model(verbose=verbose)
        if classifier is None:
            return "none"

    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    text = f"Headline: {title}. Details: {body[:500]}".replace("\n", " ")

    if FINETUNE_BERT_PATH.exists():
        res = classifier(
            text,
            _FINETUNED_CANDIDATES,
            multi_label=False,
            hypothesis_template="This healthcare news involves {}.",
        )

        top_label = res["labels"][0]
        top_score = res["scores"][0]

        if verbose:
            print(f"[BERT] top label: '{top_label}' (score: {top_score:.2f})")

        if top_score < 0.15:
            return "none"

        return _CANDIDATE_TO_SUBSECTOR.get(top_label, "none")
    else:
        if verbose:
            print(
                "[WARN] Running inference with base model, results may be less accurate."
            )
        LOGGER.warning(
            "Running inference with base model, results may be less accurate for title %s",
            title,
        )
        res = classifier(
            text,
            _FALLBACK_CANDIDATES,
            multi_label=True,
            hypothesis_template="This healthcare news involves a {}.",
        )
        scores = dict(zip(res["labels"], res["scores"]))
        unrelated_score = scores.get("unrelated news", 0)
        for label in _FALLBACK_THREAT_LABELS:
            if scores[label] > 0.60 and scores[label] > unrelated_score:
                return "potential_hit"
        return "none"
