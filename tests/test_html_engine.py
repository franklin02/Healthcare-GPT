import io
from unittest.mock import patch

from src.cli_reporter import CliReporter, PipelineStats
from src.scrapers import html_engine


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
        patch("src.scrapers.html_engine.check_valid_file"),
        patch(
            "src.scrapers.html_engine.fetch_html_page", return_value=(articles, True)
        ),
        patch(
            "src.scrapers.html_engine.ai_check_validation",
            side_effect=[(True, "cyber_attack"), (False, "No impact")],
        ),
        patch(
            "src.scrapers.html_engine.extract_fields",
            return_value=({"exec_summary": "Breach confirmed"}, {}),
        ),
        patch("src.scrapers.html_engine.prepend_vuln_csv") as mock_vuln_csv,
        patch("src.scrapers.html_engine.prepend_noise_csv") as mock_noise_csv,
        patch("src.scrapers.html_engine.prepend_json_sources") as mock_json,
    ):
        stats = html_engine.run_html_scraper(
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


def test_run_html_scraper_allows_page_cap_override():
    """A page_cap override of 2 should fetch page 1 and page 2."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com/page-1",
        "pagination_url": "https://example.com/page-{page}",
        "map": {
            "starting_page": 1,
            "cap": 1,
        },
    }
    article = {
        "title": "Routine update",
        "link": "https://example.com/article",
        "body": "No disruption",
        "date": "2026-01-01",
    }

    with (
        patch("src.scrapers.html_engine.check_valid_file"),
        patch(
            "src.scrapers.html_engine.fetch_html_page",
            return_value=([article], False),
        ) as mock_fetch,
        patch(
            "src.scrapers.html_engine.ai_check_validation",
            return_value=(False, "No impact"),
        ),
        patch("src.scrapers.html_engine.prepend_vuln_csv"),
        patch("src.scrapers.html_engine.prepend_noise_csv"),
        patch("src.scrapers.html_engine.prepend_json_sources"),
        patch("src.scrapers.html_engine.time.sleep"),
    ):
        html_engine.run_html_scraper(
            site_config,
            page_cap=2,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    assert mock_fetch.call_count == 2
    first_url = mock_fetch.call_args_list[0].args[1]
    second_url = mock_fetch.call_args_list[1].args[1]
    assert first_url == "https://example.com/page-1"
    assert second_url == "https://example.com/page-2"


def test_run_html_scraper_start_page_override_can_skip_run():
    """Starting after the cap should exit without fetching any pages."""
    site_config = {
        "name": "TestSite",
        "url": "https://example.com/page-1",
        "map": {
            "starting_page": 1,
            "cap": 1,
        },
    }

    with (
        patch("src.scrapers.html_engine.check_valid_file"),
        patch("src.scrapers.html_engine.fetch_html_page") as mock_fetch,
        patch("src.scrapers.html_engine.prepend_vuln_csv"),
        patch("src.scrapers.html_engine.prepend_noise_csv"),
        patch("src.scrapers.html_engine.prepend_json_sources"),
    ):
        html_engine.run_html_scraper(
            site_config,
            starting_page=2,
            reporter=CliReporter(stream=io.StringIO()),
            stats=PipelineStats("TestSite"),
        )

    mock_fetch.assert_not_called()
