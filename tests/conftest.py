"""Pytest configuration for Healthcare-GPT tests."""

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def skip_model_availability_check():
    """Skip model availability check during tests."""
    with patch("src.GDELT.runner.ensure_model_available"):
        yield


@pytest.fixture
def mock_requests_get():
    """Mock requests.get in shared_utils."""
    with patch("src.shared_utils.requests.get") as mock:
        yield mock


@pytest.fixture
def mock_requests_post():
    """Mock requests.post in shared_utils."""
    with patch("src.shared_utils.requests.post") as mock:
        yield mock


@pytest.fixture
def mock_subprocess_run():
    """Mock subprocess.run in shared_utils."""
    with patch("src.shared_utils.subprocess.run") as mock:
        yield mock


@pytest.fixture
def mock_save_json():
    """Mock save_json in runner."""
    with patch("src.GDELT.runner.save_json") as mock:
        yield mock


@pytest.fixture
def mock_runner_dirs(tmp_path):
    """Patch directories in runner to use a temporary directory."""
    seeds_dir = tmp_path / "seeds"
    validated_dir = tmp_path / "validated"
    enriched_dir = tmp_path / "enriched"

    seeds_dir.mkdir(exist_ok=True)
    validated_dir.mkdir(exist_ok=True)
    enriched_dir.mkdir(exist_ok=True)

    with (
        patch("src.GDELT.runner.SEEDS_DIR", seeds_dir),
        patch("src.GDELT.runner.VALIDATED_DIR", validated_dir),
        patch("src.GDELT.runner.ENRICHED_DIR", enriched_dir),
    ):
        yield {
            "tmp_path": tmp_path,
            "seeds": seeds_dir,
            "validated": validated_dir,
            "enriched": enriched_dir,
        }

import pytest
import src.shared_utils as shared_utils

@pytest.fixture(autouse=True)

def isolate_config(monkeypatch):
    monkeypatch.setattr(shared_utils, "_CONFIG", {})

def test_example(monkeypatch):
    monkeypatch.setattr(shared_utils, "_CONFIG", {
        "SKIP_HTML": "false",
        "MODELS": "2",
    })
