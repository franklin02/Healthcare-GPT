"""Tests for src/dedup.py duplicate-counter wiring."""

import io
from unittest.mock import patch
import pytest

from src.classes import Vulnerability
from src.cli_reporter import CliReporter, PipelineStats
from src.dedup import handle_vuln


@pytest.fixture
def mock_embed_vulnerability():
    with patch("src.dedup.embed_vulnerability") as mock:
        mock.return_value = [0.0] * 384
        yield mock


@pytest.fixture
def mock_find_nearest_vulnerability():
    with patch("src.supabase_function.find_nearest_vulnerability") as mock:
        yield mock


@pytest.fixture
def mock_insert_dup():
    with patch("src.supabase_function.insert_duplicate") as mock:
        yield mock


@pytest.fixture
def mock_insert_vuln():
    with patch("src.supabase_function.insert_vuln") as mock:
        yield mock


def _make_vuln(subsector: str = "drug_shortage") -> Vulnerability:
    return Vulnerability(
        id="abc123",
        title="Test Article",
        source_name="test",
        direct_link="https://example.com/1",
        subsector=subsector,
        date_accessed="2024-01-01 00:00",
        date_published="2023-05-15",
        content="content",
    )


def test_duplicate_branch_increments_counter_and_emits_detail(
    mock_embed_vulnerability,
    mock_find_nearest_vulnerability,
    mock_insert_dup,
    mock_insert_vuln,
):
    """Same-subsector close neighbor: writes to duplicates, bumps stats, prints [DUPLICATE]."""
    stream = io.StringIO()
    reporter = CliReporter(verbose=True, stream=stream)
    stats = PipelineStats("test")

    mock_find_nearest_vulnerability.return_value = (
        "existing-uuid",
        "drug_shortage",
        0.1,
    )

    handle_vuln(_make_vuln(), reporter=reporter, stats=stats)

    assert mock_insert_dup.called
    assert not mock_insert_vuln.called
    assert stats.duplicates == 1
    assert "[DUPLICATE] Test Article" in stream.getvalue()


def test_subsector_mismatch_does_not_increment_counter(
    mock_embed_vulnerability,
    mock_find_nearest_vulnerability,
    mock_insert_dup,
    mock_insert_vuln,
):
    """Close neighbor but different subsector: inserts as new canonical row, counter stays 0."""
    stats = PipelineStats("test")

    mock_find_nearest_vulnerability.return_value = (
        "existing-uuid",
        "cyber_attack",
        0.1,
    )

    handle_vuln(_make_vuln(subsector="drug_shortage"), stats=stats)

    assert not mock_insert_dup.called
    assert mock_insert_vuln.called
    assert stats.duplicates == 0


def test_empty_table_does_not_increment_counter(
    mock_embed_vulnerability,
    mock_find_nearest_vulnerability,
    mock_insert_dup,
    mock_insert_vuln,
):
    """find_nearest_vulnerability returns None: first canonical row, counter stays 0."""
    stats = PipelineStats("test")

    mock_find_nearest_vulnerability.return_value = None

    handle_vuln(_make_vuln(), stats=stats)

    assert not mock_insert_dup.called
    assert mock_insert_vuln.called
    assert stats.duplicates == 0


def test_far_neighbor_does_not_increment_counter(
    mock_embed_vulnerability,
    mock_find_nearest_vulnerability,
    mock_insert_dup,
    mock_insert_vuln,
):
    """Distance above threshold: inserts as new canonical row, counter stays 0."""
    stats = PipelineStats("test")

    mock_find_nearest_vulnerability.return_value = (
        "existing-uuid",
        "drug_shortage",
        0.9,
    )

    handle_vuln(_make_vuln(), stats=stats)

    assert not mock_insert_dup.called
    assert mock_insert_vuln.called
    assert stats.duplicates == 0


def test_duplicate_branch_without_reporter_or_stats_still_inserts(
    mock_embed_vulnerability,
    mock_find_nearest_vulnerability,
    mock_insert_dup,
    mock_insert_vuln,
):
    """Calling without reporter/stats (default args) still writes to duplicates table."""
    mock_find_nearest_vulnerability.return_value = (
        "existing-uuid",
        "drug_shortage",
        0.1,
    )

    handle_vuln(_make_vuln())

    assert mock_insert_dup.called
    assert not mock_insert_vuln.called
