from unittest.mock import patch

import pytest

from src.cli_reporter import PipelineStats
from src.shared_utils import model_unavailable_error
from src import orchestrator


def test_orchestrator_forwards_verbose_to_gdelt_runner():
    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.GDELT.runner.run") as mock_run,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        result = orchestrator.main(["--skip-html", "--verbose"])

    assert result == 0
    assert mock_run.call_args.kwargs["verbose"] is True


def test_orchestrator_forwards_verbose_to_html_scraper():
    sites = [{"name": "TestSite"}]
    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.scrapers.html_engine.HTML_SITES", sites),
        patch("src.scrapers.html_engine.run_html_scraper") as mock_scraper,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        mock_scraper.return_value = PipelineStats("TestSite")
        result = orchestrator.main(["--skip-gdelt", "--verbose"])

    assert result == 0
    assert mock_scraper.call_args.kwargs["verbose"] is True


def test_orchestrator_forwards_html_limit_overrides():
    """HTML limit flags should be forwarded as scraper pagination overrides."""
    sites = [{"name": "TestSite"}]
    with (
        patch("src.orchestrator.ensure_model_available"),
        patch("src.scrapers.html_engine.HTML_SITES", sites),
        patch("src.scrapers.html_engine.run_html_scraper") as mock_scraper,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        mock_scraper.return_value = PipelineStats("TestSite")
        result = orchestrator.main(
            [
                "--skip-gdelt",
                "--html-start-page",
                "4",
                "--html-page-cap",
                "20",
            ]
        )

    assert result == 0
    assert mock_scraper.call_args.kwargs["starting_page"] == 4
    assert mock_scraper.call_args.kwargs["page_cap"] == 20


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
        patch("src.GDELT.runner.run") as mock_run,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        result = orchestrator.main(["--skip-html", "--num-files=5"])

    assert result == 0
    assert mock_run.call_args.kwargs["num_files"] == 5
    assert mock_run.call_args.kwargs["limit"] is None


def test_orchestrator_logs_model_availability_failure_before_pipelines():
    with (
        patch(
            "src.orchestrator.ensure_model_available",
            side_effect=model_unavailable_error("model unavailable"),
        ) as mock_model_check,
        patch("src.orchestrator.LOGGER.error") as mock_log_error,
        patch("src.GDELT.runner.run") as mock_run,
        patch("src.scrapers.html_engine.run_html_scraper") as mock_scraper,
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
    with patch("src.orchestrator.ensure_model_available") as mock_model_check:
        result = orchestrator.main(["--skip-gdelt", "--skip-html"])

    assert result == 0
    mock_model_check.assert_not_called()
