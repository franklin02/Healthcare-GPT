import pytest
from unittest.mock import MagicMock, patch
from src.GDELT import BERT_filter


@pytest.fixture(autouse=True)
def pipeline_mock(monkeypatch):
    """Mock transformers.pipeline in all tests to prevent real model loading."""
    mock_pipeline = MagicMock(name="mock_pipeline")
    monkeypatch.setattr(BERT_filter, "pipeline", mock_pipeline)
    return mock_pipeline


def test_get_device_returns_0_when_cuda_available():
    """Test that get_device returns 0 when CUDA is available."""
    with patch("src.GDELT.BERT_filter.torch.cuda.is_available", return_value=True):
        result = BERT_filter.get_device()
        assert result == 0


def test_get_device_returns_mps_when_only_mps_available():
    """Test that get_device returns 'mps' when CUDA is unavailable but MPS is available."""
    with (
        patch("src.GDELT.BERT_filter.torch.cuda.is_available", return_value=False),
        patch(
            "src.GDELT.BERT_filter.torch.backends.mps.is_available", return_value=True
        ),
    ):
        result = BERT_filter.get_device()
        assert result == "mps"


def test_get_device_returns_minus_1_when_no_devices_available():
    """Test that get_device returns -1 when neither CUDA nor MPS is available."""
    with (
        patch("src.GDELT.BERT_filter.torch.cuda.is_available", return_value=False),
        patch(
            "src.GDELT.BERT_filter.torch.backends.mps.is_available", return_value=False
        ),
    ):
        result = BERT_filter.get_device()
        assert result == -1


def test_load_model_uses_finetuned_model_when_present(pipeline_mock):
    """Test that load_model prefers the local finetuned model when present."""
    # Use mocks only; this test must not load a real transformer model.
    mock_finetuned_path = MagicMock(name="finetuned_path")
    mock_finetuned_path.exists.return_value = True
    mock_tokenizer = MagicMock(name="mock_tokenizer")
    with (
        patch.object(BERT_filter, "FINETUNE_BERT_PATH", mock_finetuned_path),
        patch("src.GDELT.BERT_filter.get_device", return_value=-1) as mock_get_device,
        patch("transformers.AutoTokenizer") as mock_auto_tokenizer,
        patch("builtins.print"),
    ):
        mock_auto_tokenizer.from_pretrained.return_value = mock_tokenizer
        mock_classifier = MagicMock(name="mock_classifier")
        pipeline_mock.return_value = mock_classifier

        result = BERT_filter.load_model()

        assert result is mock_classifier
        mock_get_device.assert_called_once()
        pipeline_mock.assert_called_once_with(
            "zero-shot-classification",
            model=BERT_filter.FINETUNE_BERT_PATH,
            tokenizer=mock_tokenizer,
            device=-1,
        )


def test_load_model_falls_back_to_base_model_when_finetuned_missing(pipeline_mock):
    """Test that load_model falls back to the base model when the finetuned model is missing."""
    # Use mocks only; this test must not load a real transformer model.
    mock_finetuned_path = MagicMock(name="finetuned_path")
    mock_finetuned_path.exists.return_value = False
    mock_tokenizer = MagicMock(name="mock_tokenizer")
    with (
        patch.object(BERT_filter, "FINETUNE_BERT_PATH", mock_finetuned_path),
        patch("src.GDELT.BERT_filter.get_device", return_value=0) as mock_get_device,
        patch("builtins.print") as mock_print,
        patch("transformers.AutoTokenizer") as mock_auto_tokenizer,
    ):
        mock_auto_tokenizer.from_pretrained.return_value = mock_tokenizer
        mock_classifier = MagicMock(name="mock_classifier")
        pipeline_mock.return_value = mock_classifier

        result = BERT_filter.load_model()

        assert result is mock_classifier
        mock_get_device.assert_called_once()
        mock_print.assert_any_call(
            "[WARN] Finetuned model not found, reverting to base model."
        )
        pipeline_mock.assert_called_once_with(
            "zero-shot-classification",
            model=BERT_filter.FALLBACK_MODEL_ID,
            tokenizer=mock_tokenizer,
            device=0,
        )
