"""Pytest configuration for Healthcare-GPT tests."""

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
