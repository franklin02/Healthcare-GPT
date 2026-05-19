import pytest
from unittest.mock import MagicMock, patch
from src.GDELT import BERT_filter


def test_load_model_uses_finetuned_model_when_present():
	"""Test that load_model prefers the local finetuned model when present."""
	# Use mocks only; this test must not load a real transformer model.
	mock_finetuned_path = MagicMock(name="finetuned_path")
	mock_finetuned_path.exists.return_value = True
	with patch.object(BERT_filter, "FINETUNE_BERT_PATH", mock_finetuned_path), patch(
		"src.GDELT.BERT_filter.get_device", return_value=-1
	) as mock_get_device, patch("src.GDELT.BERT_filter.pipeline") as mock_pipeline:
		mock_classifier = MagicMock(name="mock_classifier")
		mock_pipeline.return_value = mock_classifier

		result = BERT_filter.load_model()

		assert result is mock_classifier
		mock_get_device.assert_called_once()
		mock_pipeline.assert_called_once_with(
			"zero-shot-classification",
			model=BERT_filter.FINETUNE_BERT_PATH,
			device=-1,
		)


def test_load_model_falls_back_to_base_model_when_finetuned_missing():
	"""Test that load_model falls back to the base model when the finetuned model is missing."""
	# Use mocks only; this test must not load a real transformer model.
	mock_finetuned_path = MagicMock(name="finetuned_path")
	mock_finetuned_path.exists.return_value = False
	with patch.object(BERT_filter, "FINETUNE_BERT_PATH", mock_finetuned_path), patch(
		"src.GDELT.BERT_filter.get_device", return_value=0
	) as mock_get_device, patch("src.GDELT.BERT_filter.pipeline") as mock_pipeline, patch(
		"builtins.print"
	) as mock_print:
		mock_classifier = MagicMock(name="mock_classifier")
		mock_pipeline.return_value = mock_classifier

		result = BERT_filter.load_model()

		assert result is mock_classifier
		mock_get_device.assert_called_once()
		mock_print.assert_called_once_with(
			"[WARN] Finetuned model not found, reverting to base model."
		)
		mock_pipeline.assert_called_once_with(
			"zero-shot-classification",
			model=BERT_filter.FALLBACK_MODEL_ID,
			device=0,
		)

