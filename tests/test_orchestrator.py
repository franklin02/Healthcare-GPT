from unittest.mock import patch

import pytest

from src.cli_reporter import PipelineStats
from src.shared_utils import model_unavailable_error
from src import orchestrator


@pytest.fixture
def mock_ensure_model_available():
    with patch("src.orchestrator.ensure_model_available") as mock:
        yield mock


@pytest.fixture
def mock_backfill_cyber_seeds():
    with patch("src.orchestrator.backfill_cyber_seeds") as mock:
        mock.return_value = []
        yield mock


@pytest.fixture
def mock_runner_run():
    with patch("src.GDELT.runner.run") as mock:
        yield mock


@pytest.fixture
def mock_cli_summary():
    with patch("src.cli_reporter.CliReporter.summary") as mock:
        yield mock


@pytest.fixture
def mock_get_config_bool():
    with patch("src.orchestrator.get_config_bool", side_effect=lambda _, d=False: d) as mock:
        yield mock


@pytest.fixture
def mock_get_config_int():
    with patch("src.orchestrator.get_config_int", side_effect=lambda _, d=None: d) as mock:
        yield mock


@pytest.fixture
def mock_get_config_value():
    with patch("src.orchestrator.get_config_value", side_effect=lambda _, d=None: d) as mock:
        yield mock


@pytest.fixture
def mock_run_html_scraper():
    with patch("src.scrapers.scooper.run_html_scraper") as mock:
        yield mock


@pytest.fixture
def mock_logger_error():
    with patch("src.orchestrator.LOGGER.error") as mock:
        yield mock


def test_orchestrator_forwards_verbose_to_gdelt_runner(
    mock_ensure_model_available,
    mock_backfill_cyber_seeds,
    mock_runner_run,
    mock_cli_summary,
):
    """Verbose flag should be forwarded to the GDELT runner."""
    result = orchestrator.main(["--skip-html", "--verbose"])

    assert result == 0
    assert mock_runner_run.call_args.kwargs["verbose"] is True


def test_orchestrator_forwards_verbose_to_html_scraper(
    monkeypatch,
    mock_ensure_model_available,
    mock_get_config_bool,
    mock_get_config_int,
    mock_get_config_value,
    mock_run_html_scraper,
    mock_cli_summary,
):
    """Verbose flag should be forwarded to the HTML scraper."""
    sites = [{"name": "TestSite"}]
    monkeypatch.setattr("src.scrapers.scooper.HTML_SITES", sites)
    
    mock_run_html_scraper.return_value = PipelineStats("TestSite")
    result = orchestrator.main(["--skip-gdelt", "--verbose"])

    assert result == 0
    assert mock_run_html_scraper.call_args.kwargs["verbose"] is True


def test_orchestrator_help_documents_html_limit_overrides(capsys):
    """CLI help should list the HTML pagination override flags."""
    with pytest.raises(SystemExit) as exc_info:
        orchestrator.main(["--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--html-start-page" in help_text
    assert "--html-page-cap" in help_text


def test_orchestrator_detects_equals_style_gdelt_options(
    mock_ensure_model_available,
    mock_backfill_cyber_seeds,
    mock_runner_run,
    mock_cli_summary,
):
    """--num-files=5 should count as an explicit GDELT option."""
    result = orchestrator.main(["--skip-html", "--num-files=5"])

    assert result == 0
    assert mock_runner_run.call_args.kwargs["num_files"] == 5
    assert mock_runner_run.call_args.kwargs["limit"] is None


def test_orchestrator_skips_html_after_gdelt_pause(
    mock_ensure_model_available,
    mock_get_config_bool,
    mock_get_config_int,
    mock_get_config_value,
    mock_backfill_cyber_seeds,
    mock_runner_run,
    mock_run_html_scraper,
    mock_cli_summary,
):
    """A paused GDELT run should stop later orchestrator stages cleanly."""

    def pause_gdelt(*args, **kwargs):
        kwargs["stats"].paused = True
        return []

    mock_runner_run.side_effect = pause_gdelt

    result = orchestrator.main([])

    assert result == 0
    mock_runner_run.assert_called_once()
    mock_run_html_scraper.assert_not_called()
    mock_cli_summary.assert_called_once()


def test_orchestrator_skips_remaining_html_sites_after_html_pause(
    monkeypatch,
    mock_ensure_model_available,
    mock_backfill_cyber_seeds,
    mock_runner_run,
    mock_run_html_scraper,
    mock_cli_summary,
):
    """A paused HTML site should prevent later HTML sites from running."""
    sites = [{"name": "SiteOne"}, {"name": "SiteTwo"}]
    monkeypatch.setattr("src.scrapers.scooper.HTML_SITES", sites)
    
    paused_stats = PipelineStats("SiteOne", paused=True)
    mock_run_html_scraper.return_value = paused_stats

    result = orchestrator.main([])

    assert result == 0
    assert mock_run_html_scraper.call_count == 1
    assert mock_run_html_scraper.call_args.kwargs["stats"].name == "SiteOne"
    mock_cli_summary.assert_called_once()


def test_orchestrator_logs_model_availability_failure_before_pipelines(
    mock_ensure_model_available,
    mock_logger_error,
    mock_runner_run,
    mock_run_html_scraper,
):
    """Model availability check should fail before any scraping."""
    mock_ensure_model_available.side_effect = model_unavailable_error("model unavailable")

    result = orchestrator.main([])

    assert result == 1
    mock_ensure_model_available.assert_called_once_with()
    mock_logger_error.assert_called_once()
    assert mock_logger_error.call_args.args == (
        "Model availability check failed: %s",
        mock_ensure_model_available.side_effect,
    )
    mock_runner_run.assert_not_called()
    mock_run_html_scraper.assert_not_called()


def test_orchestrator_skips_model_check_when_all_model_pipelines_are_skipped(
    mock_ensure_model_available,
):
    """Model availability check should be skipped when all pipelines are skipped."""
    result = orchestrator.main(["--skip-gdelt", "--skip-html"])

    assert result == 0
    mock_ensure_model_available.assert_not_called()
