from unittest.mock import patch

import pytest

from src.cli_reporter import PipelineStats
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
    """Verbose flag should be forwarded to the HTML scraper."""
    sites = [{"name": "TestSite"}]
    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.scrapers.scooper.HTML_SITES", sites),
        patch("src.scrapers.scooper.run_html_scraper") as mock_scraper,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        mock_scraper.return_value = PipelineStats("TestSite")
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
        patch("src.orchestrator.backfill_cyber_seeds", return_value=[]),
        patch("src.GDELT.runner.run", side_effect=pause_gdelt) as mock_run,
        patch("src.scrapers.scooper.run_html_scraper") as mock_scraper,
        patch("src.cli_reporter.CliReporter.summary") as mock_summary,
    ):
        result = orchestrator.main([])

    assert result == 0
    mock_run.assert_called_once()
    mock_scraper.assert_not_called()
    mock_summary.assert_called_once()


def test_orchestrator_skips_remaining_html_sites_after_html_pause():
    """A paused HTML site should prevent later HTML sites from running."""
    sites = [{"name": "SiteOne"}, {"name": "SiteTwo"}]
    paused_stats = PipelineStats("SiteOne", paused=True)

    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.orchestrator.backfill_cyber_seeds", return_value=[]),
        patch("src.GDELT.runner.run"),
        patch("src.scrapers.scooper.HTML_SITES", sites),
        patch(
            "src.scrapers.scooper.run_html_scraper",
            return_value=paused_stats,
        ) as mock_scraper,
        patch("src.cli_reporter.CliReporter.summary") as mock_summary,
    ):
        result = orchestrator.main([])

    assert result == 0
    assert mock_scraper.call_count == 1
    assert mock_scraper.call_args.kwargs["stats"].name == "SiteOne"
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
        patch("src.scrapers.scooper.run_html_scraper") as mock_scraper,
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
