import csv
import io
from unittest.mock import patch

import pytest

from src.cli_reporter import CliReporter, PipelineStats
from src.shared_utils import VULN_CSV_HEADER, model_unavailable_error
import src.scrapers.scooper as scooper


@pytest.fixture(autouse=True)
def _disable_supabase(monkeypatch):
    monkeypatch.setattr(scooper, "SUPABASE_AVAILABLE", False)


@pytest.fixture(autouse=True)
def _isolated_csvs(monkeypatch, tmp_path):
    """Point the consolidated CSV paths at empty tmp files so reads never touch
    the real corpus. Individual tests can write to these paths to seed rows."""
    monkeypatch.setattr(scooper, "VULN_CSV_PATH", tmp_path / "vulnerabilities.csv")
    monkeypatch.setattr(scooper, "NOISE_CSV_PATH", tmp_path / "noise.csv")


def test_run_html_scraper_counts_validated_and_rejected_articles():
    """One valid and one rejected article should update stats and the dfs."""
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
        patch("src.scrapers.scooper.fetch_html_page", return_value=articles),
        patch(
            "src.scrapers.scooper.ai_check_validation",
            side_effect=[(True, "cyber_attack"), (False, "No impact")],
        ),
        patch(
            "src.scrapers.scooper.extract_fields",
            return_value=({"exec_summary": "Breach confirmed"}, {}),
        ),
    ):
        stats, vuln_df, noise_df = scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    assert stats.discovered == 2
    assert stats.processed == 2
    assert stats.validated == 1
    assert stats.rejected == 1
    assert stats.output_records == 1
    assert len(vuln_df) == 1
    assert len(noise_df) == 1


def test_run_html_scraper_pause_keeps_buffered_rows_in_dfs():
    """Ctrl-C during HTML processing should retain accepted and rejected rows."""
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
        patch("src.scrapers.scooper.fetch_html_page", return_value=articles),
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
    ):
        stats, vuln_df, noise_df = scooper.run_html_scraper(
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
    assert len(vuln_df) == 1
    assert len(noise_df) == 1


def test_run_html_scraper_pause_during_fetch_returns_empty_dfs():
    """Ctrl-C during page fetch should mark pause and return empty dfs."""
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
        patch(
            "src.scrapers.scooper.fetch_html_page",
            side_effect=KeyboardInterrupt(),
        ),
    ):
        stats, vuln_df, noise_df = scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    assert stats.paused is True
    assert stats.output_records == 0
    assert len(vuln_df) == 0
    assert len(noise_df) == 0


def test_run_html_scraper_skips_known_article_without_stopping_pagination():
    """A previously-seen (source_name, link) is skipped but pagination continues."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com",
        "map": {
            "starting_page": 1,
            "cap": 1,
        },
    }
    # Seed the consolidated vuln CSV with a row matching the first article's link.
    known_link = "https://example.com/known"
    with open(scooper.VULN_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(VULN_CSV_HEADER)
        writer.writerow(
            [
                "2026-01-01 00:00",
                "2026-01-01",
                "TestSite",
                "cyber_attack",
                "Known breach",
                known_link,
                "Already collected",
                "preview",
            ]
        )

    articles = [
        {
            "title": "Known breach",
            "link": known_link,
            "body": "Already collected",
            "date": "2026-01-01",
        },
        {
            "title": "Policy news",
            "link": "https://example.com/fresh",
            "body": "Not a disruption",
            "date": "2026-01-02",
        },
    ]

    with (
        patch("src.scrapers.scooper.ensure_model_available"),
        patch("src.scrapers.scooper.fetch_html_page", return_value=articles),
        patch(
            "src.scrapers.scooper.ai_check_validation",
            # Only the fresh article should ever reach validation.
            side_effect=[(False, "No impact")],
        ) as mock_validate,
    ):
        stats, vuln_df, noise_df = scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    # The known article is skipped before validation; the fresh one is processed.
    assert mock_validate.call_count == 1
    assert stats.processed == 2
    assert stats.skipped == 1
    assert stats.rejected == 1
    # Loaded row is preserved (skip, not stop), fresh noise row recorded.
    assert len(vuln_df) == 1
    assert len(noise_df) == 1


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
        patch(
            "src.scrapers.scooper.fetch_html_page",
            return_value=[article],
        ) as mock_fetch,
        patch(
            "src.scrapers.scooper.ai_check_validation",
            return_value=(False, "No impact"),
        ),
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
        patch("src.scrapers.scooper.get_config_int", return_value=2),
        patch("src.scrapers.scooper.fetch_html_page") as mock_fetch,
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
    mock_fetch.assert_not_called()


def test_run_html_scraper_sb_only_skips_local_writes():
    """sb_only mode routes to Supabase and returns the (empty) loaded corpus."""
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
        patch("src.scrapers.scooper.load_cite", return_value=[]),
        patch("src.scrapers.scooper.is_known_db", return_value=False),
        patch("src.scrapers.scooper.fetch_html_page", return_value=articles),
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
    ):
        stats, vuln_df, noise_df = scooper.run_html_scraper(
            site_config,
            sb_only=True,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    # Validated -> Supabase, rejected -> Supabase noise
    mock_handle_vuln.assert_called_once()
    mock_insert_noise.assert_called_once()
    # No new local rows are collected; dfs are just the (empty) loaded corpus.
    assert len(vuln_df) == 0
    assert len(noise_df) == 0
    assert stats.validated == 1
    assert stats.rejected == 1
    assert stats.output_records == 1


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
        patch("src.scrapers.scooper.fetch_html_page", return_value=articles),
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
    ):
        # sb_only defaults to False -> local path
        stats, vuln_df, noise_df = scooper.run_html_scraper(
            site_config,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    # No Supabase reads or writes
    mock_load_cite.assert_not_called()
    mock_handle_vuln.assert_not_called()
    mock_insert_noise.assert_not_called()
    # Rows land in the returned dfs instead
    assert len(vuln_df) == 1
    assert len(noise_df) == 1
