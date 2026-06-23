from unittest.mock import patch
import datetime

import pytest

from src.cli_reporter import CliReporter, PipelineStats
from src.shared_utils import model_unavailable_error
from src import orchestrator


def test_orchestrator_forwards_verbose_to_gdelt_runner():
    """Verbose flag should be forwarded to the GDELT runner."""
    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.orchestrator.backfill_cyber_seeds", return_value=[]),
        patch("src.GDELT.runner.run") as mock_run,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        result = orchestrator.main(["--skip-html", "--verbose"])

    assert result == 0
    assert mock_run.call_args.kwargs["verbose"] is True


def test_orchestrator_forwards_verbose_to_html_scraper():
    """Verbose flag should be forwarded to run_scooper."""
    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.scrapers.scooper.setup_scooper"),
        patch("src.scrapers.scooper.run_scooper") as mock_scraper,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        mock_scraper.return_value = (PipelineStats("Scooper"), [], None, None)
        result = orchestrator.main(["--skip-gdelt", "--verbose"])

    assert result == 0
    assert mock_scraper.call_args.kwargs["verbose"] is True


def test_orchestrator_help_documents_html_limit_overrides(capsys):
    """CLI help should list the HTML pagination override flags."""
    with pytest.raises(SystemExit) as exc_info:
        orchestrator.main(["--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--html-start-page" in help_text
    assert "--html-page-cap" in help_text


def test_orchestrator_detects_equals_style_gdelt_options():
    """--num-files=5 should count as an explicit GDELT option."""
    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.orchestrator.backfill_cyber_seeds", return_value=[]),
        patch("src.GDELT.runner.run") as mock_run,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        result = orchestrator.main(["--skip-html", "--num-files=5"])

    assert result == 0
    assert mock_run.call_args.kwargs["num_files"] == 5
    assert mock_run.call_args.kwargs["limit"] is None


def test_orchestrator_skips_html_after_gdelt_pause():
    """A paused GDELT run should stop later orchestrator stages cleanly."""

    def pause_gdelt(*args, **kwargs):
        kwargs["stats"].paused = True
        return []

    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.orchestrator.get_config_bool", side_effect=lambda _, d=False: d),
        patch("src.orchestrator.get_config_int", side_effect=lambda _, d=None: d),
        patch("src.orchestrator.get_config_value", side_effect=lambda _, d=None: d),
        patch("src.orchestrator.backfill_cyber_seeds", return_value=[]),
        patch("src.GDELT.runner.run", side_effect=pause_gdelt) as mock_run,
        patch("src.scrapers.scooper.run_scooper") as mock_scraper,
        patch("src.cli_reporter.CliReporter.summary") as mock_summary,
    ):
        result = orchestrator.main([])

    assert result == 0
    mock_run.assert_called_once()
    mock_scraper.assert_not_called()
    mock_summary.assert_called_once()


def test_orchestrator_handles_html_pause():
    """A paused run_scooper result should summarize once and exit cleanly."""
    paused_stats = PipelineStats("Scooper", paused=True)
    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.scrapers.scooper.setup_scooper"),
        patch(
            "src.scrapers.scooper.run_scooper",
            return_value=(paused_stats, [], None, None),
        ) as mock_scraper,
        patch("src.cli_reporter.CliReporter.summary") as mock_summary,
    ):
        result = orchestrator.main(["--skip-gdelt"])

    assert result == 0
    mock_scraper.assert_called_once()
    mock_summary.assert_called_once()


def test_orchestrator_logs_model_availability_failure_before_pipelines():
    """Model availability check should fail before any scraping."""
    with (
        patch(
            "src.orchestrator.ensure_model_available",
            side_effect=model_unavailable_error("model unavailable"),
        ) as mock_model_check,
        patch("src.orchestrator.LOGGER.error") as mock_log_error,
        patch("src.GDELT.runner.run") as mock_run,
        patch("src.scrapers.scooper.run_scooper") as mock_scraper,
    ):
        result = orchestrator.main([])

    assert result == 1
    mock_model_check.assert_called_once_with()
    mock_log_error.assert_called_once()
    assert mock_log_error.call_args.args == (
        "Model availability check failed: %s",
        mock_model_check.side_effect,
    )
    mock_run.assert_not_called()
    mock_scraper.assert_not_called()


def test_orchestrator_skips_model_check_when_all_model_pipelines_are_skipped():
    """Model availability check should be skipped when all pipelines are skipped."""
    with patch("src.orchestrator.ensure_model_available") as mock_model_check:
        result = orchestrator.main(["--skip-gdelt", "--skip-html"])

    assert result == 0
    mock_model_check.assert_not_called()


def test_dedupe_seeds_preserves_first_seen_order():
    seeds = [
        {"url": "https://a.example", "file": "1"},
        {"url": "https://b.example", "file": "2"},
        {"url": "https://a.example", "file": "3"},
    ]
    assert orchestrator._dedupe_seeds(seeds) == [
        {"url": "https://a.example", "file": "1"},
        {"url": "https://b.example", "file": "2"},
    ]


def test_collect_gdelt_seeds_splits_date_range_across_threads():
    with (
        patch("src.orchestrator.backfill_cyber_seeds") as mock_backfill,
        patch("src.orchestrator._split_date") as mock_split,
    ):
        mock_split.return_value = [
            (datetime.date(2026, 1, 16), datetime.date(2026, 1, 1)),
            (datetime.date(2026, 1, 31), datetime.date(2026, 1, 17)),
        ]
        mock_backfill.side_effect = [
            [{"url": "https://a.example"}],
            [{"url": "https://b.example"}],
        ]
        reporter = CliReporter(verbose=False)
        result = orchestrator._collect_gdelt_seeds(
            num_files=2,
            start_date="2026-01-31",
            end_date="2026-01-01",
            cache_dir=orchestrator.GDELT_CACHE_DIR,
            reporter=reporter,
            stats=PipelineStats("GDELT"),
            seed_threads=2,
        )

    assert result == [
        {"url": "https://a.example"},
        {"url": "https://b.example"},
    ]
    assert mock_backfill.call_count == 2
    mock_split.assert_called_once_with(
        datetime.date(2026, 1, 1),
        datetime.date(2026, 1, 31),
        2,
    )
    assert mock_backfill.call_args_list[0].kwargs["start_date"] == "20260101"
    assert mock_backfill.call_args_list[0].kwargs["end_date"] == "20260116"
    assert mock_backfill.call_args_list[1].kwargs["start_date"] == "20260117"
    assert mock_backfill.call_args_list[1].kwargs["end_date"] == "20260131"


def test_collect_gdelt_seeds_chunks_links_without_full_date_range():
    with (
        patch("src.orchestrator.fetch_gkg_links", return_value=["a", "b", "c", "d"]),
        patch("src.orchestrator.backfill_cyber_seeds") as mock_backfill,
    ):
        mock_backfill.side_effect = [
            [{"url": "https://a.example"}],
            [{"url": "https://b.example"}],
        ]
        reporter = CliReporter(verbose=False)
        result = orchestrator._collect_gdelt_seeds(
            num_files=4,
            start_date=None,
            end_date=None,
            cache_dir=orchestrator.GDELT_CACHE_DIR,
            reporter=reporter,
            stats=PipelineStats("GDELT"),
            seed_threads=2,
        )

    assert result == [
        {"url": "https://a.example"},
        {"url": "https://b.example"},
    ]
    assert mock_backfill.call_count == 2
    assert mock_backfill.call_args_list[0].kwargs["links"] == ["a", "b"]
    assert mock_backfill.call_args_list[1].kwargs["links"] == ["c", "d"]


def test_orchestrator_uses_gdelt_seed_threads_argument():
    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.orchestrator.get_config_bool", side_effect=lambda _, d=False: d),
        patch("src.orchestrator._collect_gdelt_seeds", return_value=[]) as mock_collect,
        patch("src.GDELT.runner.run"),
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        result = orchestrator.main(["--skip-html", "--gdelt-seed-threads=3"])

    assert result == 0
    assert mock_collect.call_args.kwargs["seed_threads"] == 3
