from unittest.mock import patch

import pytest

from src.cli_reporter import PipelineStats
from src import orchestrator


def test_orchestrator_forwards_verbose_to_gdelt_runner():
    with (
        patch("src.GDELT.runner.run") as mock_run,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        result = orchestrator.main(["--skip-html", "--verbose"])

    assert result == 0
    assert mock_run.call_args.kwargs["verbose"] is True


def test_orchestrator_forwards_verbose_to_html_scraper():
    sites = [{"name": "TestSite"}]
    with (
        patch("src.scrapers.html_engine.HTML_SITES", sites),
        patch("src.scrapers.html_engine.run_html_scraper") as mock_scraper,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        mock_scraper.return_value = PipelineStats("TestSite")
        result = orchestrator.main(["--skip-gdelt", "--verbose"])

    assert result == 0
    assert mock_scraper.call_args.kwargs["verbose"] is True


def test_orchestrator_forwards_html_limit_overrides():
    sites = [{"name": "TestSite"}]
    with (
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
    with pytest.raises(SystemExit) as exc_info:
        orchestrator.main(["--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--html-start-page" in help_text
    assert "--html-page-cap" in help_text


def test_orchestrator_detects_equals_style_gdelt_options():
    with (
        patch("src.GDELT.runner.run") as mock_run,
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        result = orchestrator.main(["--skip-html", "--num-files=5"])

    assert result == 0
    assert mock_run.call_args.kwargs["num_files"] == 5
    assert mock_run.call_args.kwargs["limit"] is None
