"""Tests for src/dedup.py duplicate-counter wiring."""

import io
from unittest.mock import patch

from src.classes import Vulnerability
from src.cli_reporter import CliReporter, PipelineStats
from src.dedup import handle_vuln


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


def test_duplicate_branch_increments_counter_and_emits_detail():
    """Same-subsector close neighbor: writes to duplicates, bumps stats, prints [DUPLICATE]."""
    stream = io.StringIO()
    reporter = CliReporter(verbose=True, stream=stream)
    stats = PipelineStats("test")

    with (
        patch("src.dedup.embed_vulnerability", return_value=[0.0] * 384),
        patch(
            "src.supabase_function.find_nearest_vulnerability",
            return_value=("existing-uuid", "drug_shortage", 0.1),
        ),
        patch("src.supabase_function.insert_duplicate") as mock_insert_dup,
        patch("src.supabase_function.insert_vuln") as mock_insert_vuln,
    ):
        handle_vuln(_make_vuln(), reporter=reporter, stats=stats)

    assert mock_insert_dup.called
    assert not mock_insert_vuln.called
    assert stats.duplicates == 1
    assert "[DUPLICATE] Test Article" in stream.getvalue()


def test_subsector_mismatch_does_not_increment_counter():
    """Close neighbor but different subsector: inserts as new canonical row, counter stays 0."""
    stats = PipelineStats("test")

    with (
        patch("src.dedup.embed_vulnerability", return_value=[0.0] * 384),
        patch(
            "src.supabase_function.find_nearest_vulnerability",
            return_value=("existing-uuid", "cyber_attack", 0.1),
        ),
        patch("src.supabase_function.insert_duplicate") as mock_insert_dup,
        patch("src.supabase_function.insert_vuln") as mock_insert_vuln,
    ):
        handle_vuln(_make_vuln(subsector="drug_shortage"), stats=stats)

    assert not mock_insert_dup.called
    assert mock_insert_vuln.called
    assert stats.duplicates == 0


def test_empty_table_does_not_increment_counter():
    """find_nearest_vulnerability returns None: first canonical row, counter stays 0."""
    stats = PipelineStats("test")

    with (
        patch("src.dedup.embed_vulnerability", return_value=[0.0] * 384),
        patch(
            "src.supabase_function.find_nearest_vulnerability",
            return_value=None,
        ),
        patch("src.supabase_function.insert_duplicate") as mock_insert_dup,
        patch("src.supabase_function.insert_vuln") as mock_insert_vuln,
    ):
        handle_vuln(_make_vuln(), stats=stats)

    assert not mock_insert_dup.called
    assert mock_insert_vuln.called
    assert stats.duplicates == 0


def test_far_neighbor_does_not_increment_counter():
    """Distance above threshold: inserts as new canonical row, counter stays 0."""
    stats = PipelineStats("test")

    with (
        patch("src.dedup.embed_vulnerability", return_value=[0.0] * 384),
        patch(
            "src.supabase_function.find_nearest_vulnerability",
            return_value=("existing-uuid", "drug_shortage", 0.9),
        ),
        patch("src.supabase_function.insert_duplicate") as mock_insert_dup,
        patch("src.supabase_function.insert_vuln") as mock_insert_vuln,
    ):
        handle_vuln(_make_vuln(), stats=stats)

    assert not mock_insert_dup.called
    assert mock_insert_vuln.called
    assert stats.duplicates == 0


def test_duplicate_branch_without_reporter_or_stats_still_inserts():
    """Calling without reporter/stats (default args) still writes to duplicates table."""
    with (
        patch("src.dedup.embed_vulnerability", return_value=[0.0] * 384),
        patch(
            "src.supabase_function.find_nearest_vulnerability",
            return_value=("existing-uuid", "drug_shortage", 0.1),
        ),
        patch("src.supabase_function.insert_duplicate") as mock_insert_dup,
        patch("src.supabase_function.insert_vuln") as mock_insert_vuln,
    ):
        handle_vuln(_make_vuln())

    assert mock_insert_dup.called
    assert not mock_insert_vuln.called
