from unittest.mock import patch

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
