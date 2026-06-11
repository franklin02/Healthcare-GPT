"""Tests for the NoiseCollector class and --debug noise integration."""

import json
from pathlib import Path

import pytest

from src.shared_utils import NoiseCollector


class TestNoiseCollector:
    """Unit tests for NoiseCollector."""

    def test_add_records_one_entry(self, tmp_path):
        """Adding a record stores it in the internal list."""
        collector = NoiseCollector(tmp_path / "noise.json")
        collector.add(
            url="https://example.com/article",
            title="Test Article",
            source="GDELT",
            reason="Not a disruption",
            body_preview="Some body text",
            stage="validation",
        )
        assert len(collector.records) == 1
        rec = collector.records[0]
        assert rec["url"] == "https://example.com/article"
        assert rec["title"] == "Test Article"
        assert rec["source"] == "GDELT"
        assert rec["reason"] == "Not a disruption"
        assert rec["body_preview"] == "Some body text"
        assert rec["stage"] == "validation"
        assert "timestamp" in rec

    def test_add_multiple_records(self, tmp_path):
        """Multiple adds accumulate records in order."""
        collector = NoiseCollector(tmp_path / "noise.json")
        for i in range(5):
            collector.add(
                url=f"https://example.com/{i}",
                title=f"Article {i}",
                source="GDELT",
                reason="rejected",
            )
        assert len(collector.records) == 5
        assert collector.records[0]["url"] == "https://example.com/0"
        assert collector.records[4]["url"] == "https://example.com/4"

    def test_body_preview_truncated_to_250_chars(self, tmp_path):
        """Body preview is capped at 250 characters."""
        collector = NoiseCollector(tmp_path / "noise.json")
        long_body = "A" * 500
        collector.add(
            url="https://example.com",
            title="Title",
            source="GDELT",
            reason="rejected",
            body_preview=long_body,
        )
        assert len(collector.records[0]["body_preview"]) == 250

    def test_flush_writes_json(self, tmp_path):
        """Flush writes records to the output file."""
        out = tmp_path / "noise.json"
        collector = NoiseCollector(out)
        collector.add(
            url="https://example.com",
            title="Title",
            source="GDELT",
            reason="rejected",
        )
        result_path = collector.flush()
        assert result_path == out
        assert out.exists()

        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["total"] == 1
        assert len(data["noise_records"]) == 1
        assert data["noise_records"][0]["url"] == "https://example.com"

    def test_flush_creates_parent_directories(self, tmp_path):
        """Flush creates intermediate directories when they don't exist."""
        out = tmp_path / "nested" / "deep" / "noise.json"
        collector = NoiseCollector(out)
        collector.add(
            url="https://example.com",
            title="Title",
            source="GDELT",
            reason="rejected",
        )
        collector.flush()
        assert out.exists()

    def test_flush_returns_none_when_empty(self, tmp_path):
        """Flush with no records returns None and writes nothing."""
        out = tmp_path / "noise.json"
        collector = NoiseCollector(out)
        result = collector.flush()
        assert result is None
        assert not out.exists()

    def test_flush_writes_correct_total(self, tmp_path):
        """Total field matches the number of records."""
        out = tmp_path / "noise.json"
        collector = NoiseCollector(out)
        for i in range(10):
            collector.add(
                url=f"https://example.com/{i}",
                title=f"Title {i}",
                source="GDELT",
                reason="rejected",
            )
        collector.flush()

        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["total"] == 10
        assert len(data["noise_records"]) == 10

    def test_default_body_preview_and_stage(self, tmp_path):
        """Body preview and stage default to empty strings."""
        collector = NoiseCollector(tmp_path / "noise.json")
        collector.add(
            url="https://example.com",
            title="Title",
            source="GDELT",
            reason="rejected",
        )
        rec = collector.records[0]
        assert rec["body_preview"] == ""
        assert rec["stage"] == ""

    def test_flush_produces_valid_utf8_json(self, tmp_path):
        """Non-ASCII content is preserved correctly."""
        out = tmp_path / "noise.json"
        collector = NoiseCollector(out)
        collector.add(
            url="https://example.com",
            title="Hôpital attaqué — données exposées",
            source="GDELT",
            reason="rejected",
        )
        collector.flush()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "Hôpital" in data["noise_records"][0]["title"]
