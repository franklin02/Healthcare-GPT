import io
from unittest.mock import patch

from src.cli_reporter import CliReporter, PipelineStats
from src.scrapers import html_engine


def test_run_html_scraper_counts_validated_and_rejected_articles():
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
