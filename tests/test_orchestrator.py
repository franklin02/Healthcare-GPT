from unittest.mock import patch

import pytest

from src.cli_reporter import CliReporter, PipelineStats
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
    with patch(
        "src.orchestrator.get_config_bool", side_effect=lambda _, d=False: d
    ) as mock:
        yield mock


@pytest.fixture
def mock_get_config_int():
    with patch(
        "src.orchestrator.get_config_int", side_effect=lambda _, d=None: d
    ) as mock:
        yield mock


@pytest.fixture
def mock_get_config_value():
    with patch(
        "src.orchestrator.get_config_value", side_effect=lambda _, d=None: d
    ) as mock:
        yield mock


@pytest.fixture
def mock_setup_scooper():
    with patch("src.scrapers.scooper.setup_scooper") as mock:
        yield mock


@pytest.fixture
def mock_run_scooper():
    with patch("src.scrapers.scooper.run_scooper") as mock:
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
    mock_runner_run.return_value = (PipelineStats("GDELT"), [])

    result = orchestrator.main(["--skip-html", "--verbose"])

    assert result == 0
    assert mock_runner_run.call_args.kwargs["verbose"] is True


def test_orchestrator_forwards_verbose_to_html_scraper(
    mock_ensure_model_available,
    mock_setup_scooper,
    mock_run_scooper,
    mock_cli_summary,
):
    """Verbose flag should be forwarded to run_scooper."""
    # The main branch API now expects a tuple return value
    mock_run_scooper.return_value = (PipelineStats("Scooper"), [], None, None)

    result = orchestrator.main(["--skip-gdelt", "--verbose"])

    assert result == 0
    # Verify the verbose flag was forwarded properly
    assert mock_run_scooper.call_args.kwargs.get("verbose") is True


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
    mock_runner_run.return_value = (PipelineStats("GDELT"), [])

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
    mock_run_scooper,
    mock_cli_summary,
):
    """A paused GDELT run should stop later orchestrator stages cleanly."""

    def pause_gdelt(*args, **kwargs):
        stats = kwargs["stats"]
        stats.paused = True
        return stats, []

    mock_runner_run.side_effect = pause_gdelt

    result = orchestrator.main([])

    assert result == 0
    mock_runner_run.assert_called_once()
    mock_run_scooper.assert_not_called()
    mock_cli_summary.assert_called_once()


def test_orchestrator_handles_html_pause(
    mock_ensure_model_available,
    mock_setup_scooper,
    mock_run_scooper,
    mock_cli_summary,
):
    """A paused run_scooper result should summarize once and exit cleanly."""
    paused_stats = PipelineStats("Scooper", paused=True)
    mock_run_scooper.return_value = (paused_stats, [], None, None)

    result = orchestrator.main(["--skip-gdelt"])

    assert result == 0
    mock_run_scooper.assert_called_once()
    mock_cli_summary.assert_called_once()


def test_orchestrator_logs_model_availability_failure_before_pipelines(
    mock_ensure_model_available,
    mock_logger_error,
    mock_runner_run,
    mock_run_scooper,
):
    """Model availability check should fail before any scraping."""
    mock_ensure_model_available.side_effect = model_unavailable_error(
        "model unavailable"
    )

    result = orchestrator.main([])

    assert result == 1
    mock_ensure_model_available.assert_called_once_with()
    mock_logger_error.assert_called_once()
    assert mock_logger_error.call_args.args == (
        "Model availability check failed: %s",
        mock_ensure_model_available.side_effect,
    )
    mock_runner_run.assert_not_called()
    mock_run_scooper.assert_not_called()


def test_orchestrator_skips_model_check_when_all_model_pipelines_are_skipped(
    mock_ensure_model_available,
):
    """Model availability check should be skipped when all pipelines are skipped."""
    result = orchestrator.main(["--skip-gdelt", "--skip-html"])

    assert result == 0
    mock_ensure_model_available.assert_not_called()


def test_orchestrator_gdelt_multi_worker_stats_merge_correctly(
    mock_ensure_model_available,
    mock_backfill_cyber_seeds,
    mock_runner_run,
    mock_cli_summary,
):
    """Each GDELT worker should get its own PipelineStats; merged totals must be consistent."""

    def fake_run(*args, **kwargs):
        stats = kwargs["stats"]
        seeds = kwargs.get("raw_seeds", [])
        stats.discovered = len(seeds)
        stats.processed = len(seeds)
        stats.validated = len(seeds)
        return stats, []

    seeds = [{"url": f"https://example.com/{i}", "source": "test"} for i in range(4)]

    mock_backfill_cyber_seeds.return_value = seeds
    mock_runner_run.side_effect = fake_run

    result = orchestrator.main(
        ["--skip-html", "--models", "2", "--threads-per-model", "1"]
    )

    assert result == 0
    mock_cli_summary.assert_called_once()
    summaries = mock_cli_summary.call_args[0][0]
    gdelt_stats = summaries[0]
    assert gdelt_stats.discovered >= gdelt_stats.processed
    assert gdelt_stats.discovered == 4
    assert gdelt_stats.processed == 4
    assert gdelt_stats.validated == 4


def test_collect_gdelt_seeds_splits_date_range_across_threads():
    with (
        patch(
            "src.orchestrator.fetch_gkg_links",
            return_value=["a", "b", "c", "d"],
        ) as mock_fetch,
        patch("src.orchestrator.backfill_cyber_seeds") as mock_backfill,
    ):
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
    mock_fetch.assert_called_once_with(
        num_files=2,
        start_date="2026-01-31",
        end_date="2026-01-01",
    )
    assert mock_backfill.call_count == 2
    assert mock_backfill.call_args_list[0].kwargs["links"] == ["a", "b"]
    assert mock_backfill.call_args_list[1].kwargs["links"] == ["c", "d"]


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
        patch(
            "src.GDELT.runner.run",
            return_value=(PipelineStats("GDELT"), []),
        ),
        patch("src.cli_reporter.CliReporter.summary"),
    ):
        result = orchestrator.main(["--skip-html", "--gdelt-seed-threads=3"])

    assert result == 0
    assert mock_collect.call_args.kwargs["seed_threads"] == 3
