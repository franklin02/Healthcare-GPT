import io
from unittest.mock import patch

import pytest

from src.cli_reporter import CliReporter, PipelineStats
from src.shared_utils import model_unavailable_error
import src.scrapers.scooper as scooper

# scooper's Supabase helpers (load_cite, handle_vuln, ...) only exist when
# src.supabase_function imported successfully; without its dependencies the
# tests that patch them can't run, so they skip instead of erroring.
requires_supabase = pytest.mark.skipif(
    not hasattr(scooper, "load_cite"),
    reason="scooper Supabase helpers unavailable (optional deps missing)",
)


@pytest.fixture(autouse=True)
def _disable_supabase(monkeypatch):
    monkeypatch.setattr(scooper, "SUPABASE_AVAILABLE", False)


def test_run_html_scraper_counts_validated_and_rejected_articles():
    """One valid and one rejected article should update stats and outputs."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com",
        "map": {
            "starting_page": 1,
            "cap": 1,
        },
    }
    articles = [
        {
            "title": "Hospital breach",
            "link": "https://example.com/valid",
            "body": "Confirmed breach",
            "date": "2026-01-01",
        },
        {
            "title": "Policy news",
            "link": "https://example.com/noise",
            "body": "Not a disruption",
            "date": "2026-01-02",
        },
    ]

    with (
        patch("src.scrapers.scooper.ensure_model_available"),
        patch("src.scrapers.scooper.check_valid_file"),
        patch("src.scrapers.scooper.fetch_html_page", return_value=(articles, True)),
        patch(
            "src.scrapers.scooper.ai_check_validation",
            side_effect=[(True, "cyber_attack"), (False, "No impact")],
        ),
        patch(
            "src.scrapers.scooper.extract_fields",
            return_value=({"exec_summary": "Breach confirmed"}, {}),
        ),
        patch("src.scrapers.scooper.prepend_vuln_csv") as mock_vuln_csv,
        patch("src.scrapers.scooper.prepend_noise_csv") as mock_noise_csv,
        patch("src.scrapers.scooper.prepend_json_sources") as mock_json,
    ):
        stats = scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    assert stats.discovered == 2
    assert stats.processed == 2
    assert stats.validated == 1
    assert stats.rejected == 1
    assert stats.output_records == 1
    mock_vuln_csv.assert_called_once()
    mock_noise_csv.assert_called_once()
    mock_json.assert_called_once()


def test_run_html_scraper_handles_missing_subsector_fields():
    """Missing extraction fields should skip the article without stopping the run."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com",
        "map": {"starting_page": 1, "cap": 1},
    }
    articles = [
        {
            "title": "Hospital breach",
            "link": "https://example.com/valid",
            "body": "Confirmed breach",
            "date": "2026-01-01",
        }
    ]

    with (
        patch("src.scrapers.scooper.ensure_model_available"),
        patch("src.scrapers.scooper.check_valid_file"),
        patch("src.scrapers.scooper.fetch_html_page", return_value=(articles, True)),
        patch(
            "src.scrapers.scooper.ai_check_validation",
            return_value=(True, "cyber_attack"),
        ),
        patch(
            "src.scrapers.scooper.extract_fields",
            side_effect=scooper.MissingSubsectorFieldsError("No fields found"),
        ),
        patch("src.scrapers.scooper.prepend_vuln_csv") as mock_vuln_csv,
        patch("src.scrapers.scooper.prepend_noise_csv"),
        patch("src.scrapers.scooper.prepend_json_sources"),
    ):
        stats = scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    assert stats.validated == 0
    assert stats.skipped == 1
    assert stats.warnings == 1
    assert stats.output_records == 0
    mock_vuln_csv.assert_called_once()
    assert mock_vuln_csv.call_args.args[1] == []


def test_run_html_scraper_pause_flushes_buffered_outputs():
    """Ctrl-C during HTML processing should flush accepted and rejected rows."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com",
        "map": {
            "starting_page": 1,
            "cap": 1,
        },
    }
    articles = [
        {
            "title": "Hospital breach",
            "link": "https://example.com/valid",
            "body": "Confirmed breach",
            "date": "2026-01-01",
        },
        {
            "title": "Policy news",
            "link": "https://example.com/noise",
            "body": "Not a disruption",
            "date": "2026-01-02",
        },
        {
            "title": "Interrupted article",
            "link": "https://example.com/interrupted",
            "body": "Still processing",
            "date": "2026-01-03",
        },
    ]

    with (
        patch("src.scrapers.scooper.ensure_model_available"),
        patch("src.scrapers.scooper.check_valid_file"),
        patch("src.scrapers.scooper.fetch_html_page", return_value=(articles, True)),
        patch(
            "src.scrapers.scooper.ai_check_validation",
            side_effect=[
                (True, "cyber_attack"),
                (False, "No impact"),
                KeyboardInterrupt(),
            ],
        ),
        patch(
            "src.scrapers.scooper.extract_fields",
            return_value=({"exec_summary": "Breach confirmed"}, {}),
        ),
        patch("src.scrapers.scooper.prepend_vuln_csv") as mock_vuln_csv,
        patch("src.scrapers.scooper.prepend_noise_csv") as mock_noise_csv,
        patch("src.scrapers.scooper.prepend_json_sources") as mock_json,
    ):
        stats = scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    assert stats.paused is True
    assert stats.discovered == 3
    assert stats.processed == 3
    assert stats.validated == 1
    assert stats.rejected == 1
    assert stats.output_records == 1
    mock_vuln_csv.assert_called_once()
    mock_noise_csv.assert_called_once()
    mock_json.assert_called_once()
    assert len(mock_vuln_csv.call_args.args[1]) == 1
    assert len(mock_noise_csv.call_args.args[1]) == 1
    assert len(mock_json.call_args.args[1]) == 1


def test_run_html_scraper_pause_during_fetch_flushes_empty_outputs():
    """Ctrl-C during page fetch should mark pause and still use output helpers."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com",
        "map": {
            "starting_page": 1,
            "cap": 1,
        },
    }

    with (
        patch("src.scrapers.scooper.ensure_model_available"),
        patch("src.scrapers.scooper.check_valid_file"),
        patch(
            "src.scrapers.scooper.fetch_html_page",
            side_effect=KeyboardInterrupt(),
        ),
        patch("src.scrapers.scooper.prepend_vuln_csv") as mock_vuln_csv,
        patch("src.scrapers.scooper.prepend_noise_csv") as mock_noise_csv,
        patch("src.scrapers.scooper.prepend_json_sources") as mock_json,
    ):
        stats = scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    assert stats.paused is True
    assert stats.output_records == 0
    mock_vuln_csv.assert_called_once_with("TestSite", [])
    mock_noise_csv.assert_called_once_with("TestSite", [])
    mock_json.assert_called_once_with("TestSite", [])


def test_run_html_scraper_allows_page_cap_override():
    """A page cap of 2 should fetch page 1 and page 2."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com/page-1",
        "pagination_url": "https://example.com/page-{page}",
        "map": {
            "starting_page": 1,
            "cap": 2,
        },
    }
    article = {
        "title": "Routine update",
        "link": "https://example.com/article",
        "body": "No disruption",
        "date": "2026-01-01",
    }

    with (
        patch("src.scrapers.scooper.ensure_model_available"),
        patch("src.scrapers.scooper.check_valid_file"),
        patch(
            "src.scrapers.scooper.fetch_html_page",
            return_value=([article], False),
        ) as mock_fetch,
        patch(
            "src.scrapers.scooper.ai_check_validation",
            return_value=(False, "No impact"),
        ),
        patch("src.scrapers.scooper.prepend_vuln_csv"),
        patch("src.scrapers.scooper.prepend_noise_csv"),
        patch("src.scrapers.scooper.prepend_json_sources"),
        patch("src.scrapers.scooper.time.sleep"),
    ):
        scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    assert mock_fetch.call_count == 2
    first_url = mock_fetch.call_args_list[0].args[1]
    second_url = mock_fetch.call_args_list[1].args[1]
    assert first_url == "https://example.com/page-1"
    assert second_url == "https://example.com/page-2"


def test_run_html_scraper_start_page_override_can_skip_run():
    """An HTML_START_PAGE override past the cap should exit without fetching."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com/page-1",
        "map": {
            "starting_page": 1,
            "cap": 1,
        },
    }

    with (
        patch("src.scrapers.scooper.ensure_model_available"),
        patch("src.scrapers.scooper.check_valid_file"),
        patch("src.scrapers.scooper.get_config_int", return_value=2),
        patch("src.scrapers.scooper.fetch_html_page") as mock_fetch,
        patch("src.scrapers.scooper.prepend_vuln_csv"),
        patch("src.scrapers.scooper.prepend_noise_csv"),
        patch("src.scrapers.scooper.prepend_json_sources"),
    ):
        scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    mock_fetch.assert_not_called()


def test_run_html_scraper_logs_model_failure_before_setup_or_fetching():
    """Model availability check should fail before any scraping."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com",
        "map": {
            "starting_page": 1,
            "cap": 1,
        },
    }

    with (
        patch(
            "src.scrapers.scooper.ensure_model_available",
            side_effect=model_unavailable_error("model unavailable"),
        ) as mock_model_check,
        patch("src.scrapers.scooper.LOGGER.error") as mock_log_error,
        patch("src.scrapers.scooper.check_valid_file") as mock_check_file,
        patch("src.scrapers.scooper.fetch_html_page") as mock_fetch,
    ):
        with pytest.raises(model_unavailable_error):
            scooper.run_html_scraper(
                site_config,
                reporter=CliReporter(stream=io.StringIO()),
                stats=PipelineStats("TestSite"),
            )

    mock_model_check.assert_called_once_with()
    mock_log_error.assert_called_once_with(
        "Model availability check failed: %s",
        mock_model_check.side_effect,
    )
    mock_check_file.assert_not_called()
    mock_fetch.assert_not_called()


@requires_supabase
def test_run_html_scraper_sb_only_skips_local_writes():
    """sb_only mode routes to Supabase and never touches the local corpus."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com",
        "map": {
            "starting_page": 1,
            "cap": 1,
        },
    }
    articles = [
        {
            "title": "Hospital breach",
            "link": "https://example.com/valid",
            "body": "Confirmed breach",
            "date": "2026-01-01",
        },
        {
            "title": "Policy news",
            "link": "https://example.com/noise",
            "body": "Not a disruption",
            "date": "2026-01-02",
        },
    ]

    with (
        patch("src.scrapers.scooper.SUPABASE_AVAILABLE", True),
        patch("src.scrapers.scooper.ensure_model_available"),
        patch("src.scrapers.scooper.check_valid_file") as mock_check_file,
        patch("src.scrapers.scooper.load_cite", return_value=[]),
        patch("src.scrapers.scooper.is_known_db", return_value=False),
        patch("src.scrapers.scooper.fetch_html_page", return_value=(articles, True)),
        patch(
            "src.scrapers.scooper.ai_check_validation",
            side_effect=[(True, "cyber_attack"), (False, "No impact")],
        ),
        patch(
            "src.scrapers.scooper.extract_fields",
            return_value=({"exec_summary": "Breach confirmed"}, {}),
        ),
        patch("src.scrapers.scooper.handle_vuln") as mock_handle_vuln,
        patch("src.scrapers.scooper.insert_noise") as mock_insert_noise,
        patch("src.scrapers.scooper.prepend_vuln_csv") as mock_vuln_csv,
        patch("src.scrapers.scooper.prepend_noise_csv") as mock_noise_csv,
        patch("src.scrapers.scooper.prepend_json_sources") as mock_json,
    ):
        stats = scooper.run_html_scraper(
            site_config,
            sb_only=True,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    # Validated -> Supabase, rejected -> Supabase noise
    mock_handle_vuln.assert_called_once()
    mock_insert_noise.assert_called_once()
    # Local corpus is never seeded or written
    mock_check_file.assert_not_called()
    mock_vuln_csv.assert_not_called()
    mock_noise_csv.assert_not_called()
    mock_json.assert_not_called()
    assert stats.validated == 1
    assert stats.rejected == 1
    assert stats.output_records == 1


@requires_supabase
def test_run_html_scraper_local_mode_skips_supabase():
    """Local mode must not call Supabase helpers even when creds are available."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com",
        "map": {
            "starting_page": 1,
            "cap": 1,
        },
    }
    articles = [
        {
            "title": "Hospital breach",
            "link": "https://example.com/valid",
            "body": "Confirmed breach",
            "date": "2026-01-01",
        },
        {
            "title": "Policy news",
            "link": "https://example.com/noise",
            "body": "Not a disruption",
            "date": "2026-01-02",
        },
    ]

    with (
        patch("src.scrapers.scooper.SUPABASE_AVAILABLE", True),
        patch("src.scrapers.scooper.ensure_model_available"),
        patch("src.scrapers.scooper.check_valid_file"),
        patch("src.scrapers.scooper.fetch_html_page", return_value=(articles, True)),
        patch(
            "src.scrapers.scooper.ai_check_validation",
            side_effect=[(True, "cyber_attack"), (False, "No impact")],
        ),
        patch(
            "src.scrapers.scooper.extract_fields",
            return_value=({"exec_summary": "Breach confirmed"}, {}),
        ),
        patch("src.scrapers.scooper.load_cite") as mock_load_cite,
        patch("src.scrapers.scooper.handle_vuln") as mock_handle_vuln,
        patch("src.scrapers.scooper.insert_noise") as mock_insert_noise,
        patch("src.scrapers.scooper.prepend_vuln_csv") as mock_vuln_csv,
        patch("src.scrapers.scooper.prepend_noise_csv") as mock_noise_csv,
        patch("src.scrapers.scooper.prepend_json_sources") as mock_json,
    ):
        # sb_only defaults to False -> local path
        scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    # No Supabase reads or writes
    mock_load_cite.assert_not_called()
    mock_handle_vuln.assert_not_called()
    mock_insert_noise.assert_not_called()
    # Local writers are used instead
    mock_vuln_csv.assert_called_once()
    mock_noise_csv.assert_called_once()
    mock_json.assert_called_once()
