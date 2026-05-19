import pytest
from unittest.mock import Mock, patch, MagicMock
import datetime
import uuid
from bs4 import BeautifulSoup
from src.scrapers import html_engine
from src.classes import Vulnerability


class TestFetchHtmlPage:
    """Tests for the fetch_html_page function"""

    @patch("src.scrapers.html_engine.get_page")
    def test_fetch_html_page_success(self, mock_get_page):
        """Test successful fetching of articles from a page"""
        # Setup mock responses
        index_html = """
        <html>
            <li class="search-results__item">
                <a class="post-item__title-link" href="https://example.com/article1">Article 1</a>
            </li>
        </html>
        """
        article_html = """
        <html>
            <div class="single-article__content">Article content here</div>
            <time datetime="2024-01-01T00:00:00Z"></time>
        </html>
        """

        mock_response_index = Mock()
        mock_response_index.content = index_html.encode()
        mock_response_article = Mock()
        mock_response_article.content = article_html.encode()

        mock_get_page.side_effect = [mock_response_index, mock_response_article]

        site_config = html_engine.HTML_SITES[0]
        result = html_engine.fetch_html_page(site_config, site_config["url"])

        assert len(result) == 1
        assert result[0]["title"] == "Article 1"
        assert result[0]["link"] == "https://example.com/article1"
        assert "Article content here" in result[0]["body"]

    @patch("src.scrapers.html_engine.get_page")
    def test_fetch_html_page_missing_container_selector(self, mock_get_page):
        """Test handling of missing container selector"""
        mock_response = Mock()
        mock_response.content = b"<html><body>No matching containers</body></html>"
        mock_get_page.return_value = mock_response

        site_config = {
            "name": "TestSite",
            "url": "https://example.com",
            "map": {
                "container": "div.nonexistent",
                "link_selector": "a",
                "body_selector": "div.content",
                "date_selector": "time",
            },
        }

        result = html_engine.fetch_html_page(site_config, "https://example.com")

        assert result == []

    @patch("src.scrapers.html_engine.get_page")
    def test_fetch_html_page_missing_body_selector(self, mock_get_page):
        """Test handling of missing body selector in article"""
        index_html = """
        <html>
            <li class="search-results__item">
                <a class="post-item__title-link" href="https://example.com/article1">Article 1</a>
            </li>
        </html>
        """
        article_html = """
        <html>
            <div class="wrong-selector">Article content</div>
        </html>
        """

        mock_response_index = Mock()
        mock_response_index.content = index_html.encode()
        mock_response_article = Mock()
        mock_response_article.content = article_html.encode()

        mock_get_page.side_effect = [mock_response_index, mock_response_article]

        site_config = html_engine.HTML_SITES[0]
        result = html_engine.fetch_html_page(site_config, site_config["url"])

        assert len(result) == 0

    @patch("src.scrapers.html_engine.get_page")
    def test_fetch_html_page_relative_urls(self, mock_get_page):
        """Test handling of relative URLs"""
        index_html = """
        <html>
            <li class="search-results__item">
                <a class="post-item__title-link" href="/article1">Article 1</a>
            </li>
        </html>
        """
        article_html = """
        <html>
            <div class="single-article__content">Article content</div>
        </html>
        """

        mock_response_index = Mock()
        mock_response_index.content = index_html.encode()
        mock_response_article = Mock()
        mock_response_article.content = article_html.encode()

        mock_get_page.side_effect = [mock_response_index, mock_response_article]

        site_config = {
            "name": "TestSite",
            "url": "https://example.com/search",
            "map": {
                "container": "li.search-results__item",
                "link_selector": "a.post-item__title-link",
                "body_selector": "div.single-article__content",
                "date_selector": "time",
            },
        }

        result = html_engine.fetch_html_page(site_config, site_config["url"])

        assert len(result) == 1
        assert result[0]["link"] == "https://example.com/article1"

    @patch("src.scrapers.html_engine.get_page")
    def test_fetch_html_page_duplicate_urls(self, mock_get_page):
        """Test deduplication of URLs"""
        index_html = """
        <html>
            <li class="search-results__item">
                <a class="post-item__title-link" href="https://example.com/article1">Article 1</a>
            </li>
            <li class="search-results__item">
                <a class="post-item__title-link" href="https://example.com/article1">Article 1 Duplicate</a>
            </li>
        </html>
        """
        article_html = """
        <html>
            <div class="single-article__content">Article content</div>
        </html>
        """

        mock_response_index = Mock()
        mock_response_index.content = index_html.encode()
        mock_response_article = Mock()
        mock_response_article.content = article_html.encode()

        mock_get_page.side_effect = [mock_response_index, mock_response_article]

        site_config = html_engine.HTML_SITES[0]
        result = html_engine.fetch_html_page(site_config, site_config["url"])

        assert len(result) == 1

    @patch("src.scrapers.html_engine.get_page")
    def test_fetch_html_page_network_error(self, mock_get_page):
        """Test handling of network errors during article fetch"""
        index_html = """
        <html>
            <li class="search-results__item">
                <a class="post-item__title-link" href="https://example.com/article1">Article 1</a>
            </li>
        </html>
        """

        mock_response_index = Mock()
        mock_response_index.content = index_html.encode()

        mock_get_page.side_effect = [mock_response_index, Exception("Network error")]

        site_config = html_engine.HTML_SITES[0]
        result = html_engine.fetch_html_page(site_config, site_config["url"])

        assert len(result) == 0

    @patch("src.scrapers.html_engine.get_page")
    def test_fetch_html_page_link_selector_found_no_anchor(self, mock_get_page):
        """When a link_selector is provided but no anchor is found, it should skip (covers lines 128-132)."""
        index_html = """
        <html>
            <li class="item"><span>No anchor here</span></li>
        </html>
        """
        mock_response_index = Mock()
        mock_response_index.content = index_html.encode()
        mock_get_page.return_value = mock_response_index

        site_config = {
            "name": "TestSite",
            "url": "https://example.com",
            "map": {
                "container": "li",
                "link_selector": "a.post-item__title-link",
                "body_selector": "div.content",
                "date_selector": "time",
            },
        }

        result = html_engine.fetch_html_page(site_config, site_config["url"])
        assert result == []

    @patch("src.scrapers.html_engine.get_page")
    def test_fetch_html_page_container_is_anchor_without_link_selector(
        self, mock_get_page
    ):
        """When container items are anchors and no link_selector provided, use el itself (covers line 125)."""
        index_html = """
        <html>
            <a class="post-item__title-link" href="https://example.com/article1">Article 1</a>
        </html>
        """
        article_html = '<html><div class="single-article__content">Body</div></html>'

        mock_response_index = Mock()
        mock_response_index.content = index_html.encode()
        mock_response_article = Mock()
        mock_response_article.content = article_html.encode()

        mock_get_page.side_effect = [mock_response_index, mock_response_article]

        site_config = {
            "name": "TestSite",
            "url": "https://example.com",
            "map": {
                "container": "a",
                "title": None,
                # no link_selector here
                "body_selector": "div.single-article__content",
                "date_selector": "time",
            },
        }

        result = html_engine.fetch_html_page(site_config, site_config["url"])
        assert len(result) == 1
        assert result[0]["link"] == "https://example.com/article1"

    @patch("src.scrapers.html_engine.get_page")
    def test_fetch_html_page_anchor_missing_href(self, mock_get_page):
        """If the anchor has no href attribute it should be skipped (covers line 135)."""
        index_html = """
        <html>
            <li class="search-results__item">
                <a class="post-item__title-link">No href</a>
            </li>
        </html>
        """
        mock_response_index = Mock()
        mock_response_index.content = index_html.encode()
        mock_get_page.return_value = mock_response_index

        site_config = html_engine.HTML_SITES[0]
        # reuse a standard site_config but the anchor lacks href so should skip
        result = html_engine.fetch_html_page(site_config, site_config["url"])
        assert result == []

    @patch("src.scrapers.html_engine.get_page")
    def test_fetch_html_page_uses_title_selector_when_present(self, mock_get_page):
        """If a title selector is configured, it should be used to set title_text (covers lines 148-149)."""
        index_html = """
        <html>
            <li class="item">
                <a href="https://example.com/article1">Link text</a>
                <h2 class="headline">Explicit Title</h2>
            </li>
        </html>
        """
        article_html = '<html><div class="single-article__content">Body</div></html>'

        mock_response_index = Mock()
        mock_response_index.content = index_html.encode()
        mock_response_article = Mock()
        mock_response_article.content = article_html.encode()
        mock_get_page.side_effect = [mock_response_index, mock_response_article]

        site_config = {
            "name": "TestSite",
            "url": "https://example.com",
            "map": {
                "container": "li",
                "title": "h2.headline",
                "link_selector": "a",
                "body_selector": "div.single-article__content",
                "date_selector": "time",
            },
        }

        result = html_engine.fetch_html_page(site_config, site_config["url"])
        assert len(result) == 1
        assert result[0]["title"] == "Explicit Title"

    @patch("src.scrapers.html_engine.fetch_html_page")
    @patch("src.scrapers.html_engine.check_valid_file")
    @patch("src.scrapers.html_engine.ai_check_validation")
    @patch("src.scrapers.html_engine.find_subsector_fields")
    @patch("src.scrapers.html_engine.json_output")
    @patch("src.scrapers.html_engine.vuln_output")
    @patch("src.scrapers.html_engine.noise_output")
    def test_run_html_scraper_pagination_fallback_builds_page_url(
        self,
        mock_noise_output,
        mock_vuln_output,
        mock_json_output,
        mock_find_fields,
        mock_ai_check,
        mock_check_valid,
        mock_fetch,
    ):
        """When pagination_url is missing the fallback constructs page_url (covers lines 219-221)."""
        articles = [
            {
                "title": "Article",
                "link": "https://example.com/article1",
                "body": "Content",
                "date": "2024-01-01",
            }
        ]
        # first call returns articles, second call returns empty to stop loop
        mock_fetch.side_effect = [articles, []]
        mock_ai_check.return_value = (False, "not_threat")

        site_config = {
            "name": "TestSite",
            # include a '?' so sep becomes '&' to exercise that logic
            "url": "https://example.com/search?q=health",
            "map": {
                "container": "li",
                "link_selector": "a",
                "body_selector": "div",
                "date_selector": "time",
                "starting_page": 1,
                "cap": -1,
            },
        }

        html_engine.run_html_scraper(site_config)

        # Ensure fetch called twice and the second call used constructed page URL with '&page=2'
        assert mock_fetch.call_count == 2
        second_call_page = mock_fetch.call_args_list[1][0][1]
        assert second_call_page == "https://example.com/search?q=health&page=2"


class TestRunHtmlScraper:
    """Tests for the run_html_scraper function"""

    @patch("src.scrapers.html_engine.noise_output")
    @patch("src.scrapers.html_engine.vuln_output")
    @patch("src.scrapers.html_engine.json_output")
    @patch("src.scrapers.html_engine.find_subsector_fields")
    @patch("src.scrapers.html_engine.ai_check_validation")
    @patch("src.scrapers.html_engine.fetch_html_page")
    @patch("src.scrapers.html_engine.check_valid_file")
    def test_run_html_scraper_no_articles(
        self,
        mock_check_valid,
        mock_fetch,
        mock_ai_check,
        mock_find_fields,
        mock_json_output,
        mock_vuln_output,
        mock_noise_output,
    ):
        """Test scraper when no articles are found"""
        mock_fetch.return_value = []

        site_config = html_engine.HTML_SITES[0]
        html_engine.run_html_scraper(site_config)

        mock_check_valid.assert_called_once()
        mock_fetch.assert_called_once()
        mock_json_output.assert_not_called()

    @patch("src.scrapers.html_engine.noise_output")
    @patch("src.scrapers.html_engine.vuln_output")
    @patch("src.scrapers.html_engine.json_output")
    @patch("src.scrapers.html_engine.find_subsector_fields")
    @patch("src.scrapers.html_engine.ai_check_validation")
    @patch("src.scrapers.html_engine.fetch_html_page")
    @patch("src.scrapers.html_engine.check_valid_file")
    def test_run_html_scraper_threat_detected(
        self,
        mock_check_valid,
        mock_fetch,
        mock_ai_check,
        mock_find_fields,
        mock_json_output,
        mock_vuln_output,
        mock_noise_output,
    ):
        """Test scraper when a threat is detected"""
        articles = [
            {
                "title": "Healthcare Cyber Attack",
                "link": "https://example.com/article1",
                "body": "Attack on hospital systems",
                "date": "2024-01-01",
            }
        ]
        mock_fetch.return_value = articles
        mock_ai_check.return_value = (True, "cyber_attack")
        mock_find_fields.return_value = {"details": "test"}

        site_config = {
            "name": "TestSite",
            "url": "https://example.com",
            "map": {
                "container": "li",
                "link_selector": "a",
                "body_selector": "div",
                "date_selector": "time",
                "starting_page": 1,
                "cap": 1,
            },
        }
        html_engine.run_html_scraper(site_config)

        assert mock_json_output.call_count == 1
        assert mock_vuln_output.call_count == 1
        mock_noise_output.assert_not_called()

    @patch("src.scrapers.html_engine.noise_output")
    @patch("src.scrapers.html_engine.vuln_output")
    @patch("src.scrapers.html_engine.json_output")
    @patch("src.scrapers.html_engine.find_subsector_fields")
    @patch("src.scrapers.html_engine.ai_check_validation")
    @patch("src.scrapers.html_engine.fetch_html_page")
    @patch("src.scrapers.html_engine.check_valid_file")
    def test_run_html_scraper_no_threat(
        self,
        mock_check_valid,
        mock_fetch,
        mock_ai_check,
        mock_find_fields,
        mock_json_output,
        mock_vuln_output,
        mock_noise_output,
    ):
        """Test scraper when no threat is detected (noise)"""
        articles = [
            {
                "title": "Healthcare General News",
                "link": "https://example.com/article1",
                "body": "General news about healthcare",
                "date": "2024-01-01",
            }
        ]
        mock_fetch.return_value = articles
        mock_ai_check.return_value = (False, "not_threat")

        site_config = {
            "name": "TestSite",
            "url": "https://example.com",
            "map": {
                "container": "li",
                "link_selector": "a",
                "body_selector": "div",
                "date_selector": "time",
                "starting_page": 1,
                "cap": 1,
            },
        }
        html_engine.run_html_scraper(site_config)

        assert mock_noise_output.call_count == 1
        mock_json_output.assert_not_called()

    @patch("src.scrapers.html_engine.noise_output")
    @patch("src.scrapers.html_engine.vuln_output")
    @patch("src.scrapers.html_engine.json_output")
    @patch("src.scrapers.html_engine.find_subsector_fields")
    @patch("src.scrapers.html_engine.ai_check_validation")
    @patch("src.scrapers.html_engine.fetch_html_page")
    @patch("src.scrapers.html_engine.check_valid_file")
    def test_run_html_scraper_respects_cap(
        self,
        mock_check_valid,
        mock_fetch,
        mock_ai_check,
        mock_find_fields,
        mock_json_output,
        mock_vuln_output,
        mock_noise_output,
    ):
        """Test that scraper respects page cap"""
        # Return articles for pages 1 and 2, then empty for page 3 onwards
        articles = [
            {
                "title": "Article",
                "link": "https://example.com/article1",
                "body": "Content",
                "date": "2024-01-01",
            }
        ]
        mock_fetch.side_effect = [articles, articles, []]
        mock_ai_check.return_value = (False, "not_threat")

        site_config = {
            "name": "TestSite",
            "url": "https://example.com",
            "pagination_url": "https://example.com/page/{page}",
            "map": {
                "container": "li",
                "link_selector": "a",
                "body_selector": "div",
                "date_selector": "time",
                "starting_page": 1,
                "cap": 2,
            },
        }

        html_engine.run_html_scraper(site_config)

        # Should call for pages 1 and 2 (cap=2 means stop when page > 2)
        assert mock_fetch.call_count == 2

    @patch("src.scrapers.html_engine.noise_output")
    @patch("src.scrapers.html_engine.vuln_output")
    @patch("src.scrapers.html_engine.json_output")
    @patch("src.scrapers.html_engine.find_subsector_fields")
    @patch("src.scrapers.html_engine.ai_check_validation")
    @patch("src.scrapers.html_engine.fetch_html_page")
    @patch("src.scrapers.html_engine.check_valid_file")
    def test_run_html_scraper_invalid_subsector(
        self,
        mock_check_valid,
        mock_fetch,
        mock_ai_check,
        mock_find_fields,
        mock_json_output,
        mock_vuln_output,
        mock_noise_output,
    ):
        """Test scraper with invalid subsector returned from AI"""
        articles = [
            {
                "title": "Healthcare News",
                "link": "https://example.com/article1",
                "body": "News content",
                "date": "2024-01-01",
            }
        ]
        mock_fetch.return_value = articles
        mock_ai_check.return_value = (True, "invalid_subsector")

        site_config = html_engine.HTML_SITES[0]
        html_engine.run_html_scraper(site_config)

        # Should skip the article due to invalid subsector
        mock_json_output.assert_not_called()
        mock_vuln_output.assert_not_called()

    @patch("src.scrapers.html_engine.fetch_html_page")
    @patch("src.scrapers.html_engine.check_valid_file")
    def test_run_html_scraper_fetch_error(self, mock_check_valid, mock_fetch):
        """Test scraper error handling when fetch_html_page fails"""
        mock_fetch.side_effect = Exception("Fetch error")

        site_config = html_engine.HTML_SITES[0]

        # Should not raise an exception, just print error
        html_engine.run_html_scraper(site_config)

        mock_fetch.assert_called_once()


class TestSubsectorFields:
    """Tests for subsector field constants"""

    def test_subsector_fields_exist(self):
        """Test that all expected subsector fields are defined"""
        expected_fields = [
            "drug_shortage",
            "medical_device_shortage",
            "cyber_attack",
            "natural_disaster",
            "other",
        ]

        assert html_engine.SUBSECTOR_FIELDS == expected_fields

    def test_html_sites_configured(self):
        """Test that HTML_SITES are properly configured"""
        assert len(html_engine.HTML_SITES) > 0

        for site in html_engine.HTML_SITES:
            assert "name" in site
            assert "url" in site
            assert "map" in site

            map_config = site["map"]
            assert "container" in map_config
            assert "link_selector" in map_config
            assert "body_selector" in map_config
            assert "starting_page" in map_config
            assert "cap" in map_config


class TestVulnerabilityIntegration:
    """Integration tests for Vulnerability object creation"""

    @patch("src.scrapers.html_engine.get_page")
    @patch("src.scrapers.html_engine.find_subsector_fields")
    @patch("src.scrapers.html_engine.ai_check_validation")
    def test_vulnerability_creation(
        self,
        mock_ai_check,
        mock_find_fields,
        mock_get_page,
    ):
        """Test that Vulnerability objects are created correctly"""
        index_html = """
        <html>
            <li class="search-results__item">
                <a class="post-item__title-link" href="https://example.com/article1">Test Article</a>
            </li>
        </html>
        """
        article_html = """
        <html>
            <div class="single-article__content">Detailed content</div>
            <time datetime="2024-01-01T00:00:00Z"></time>
        </html>
        """

        mock_response_index = Mock()
        mock_response_index.content = index_html.encode()
        mock_response_article = Mock()
        mock_response_article.content = article_html.encode()

        mock_get_page.side_effect = [mock_response_index, mock_response_article]
        mock_ai_check.return_value = (True, "cyber_attack")
        mock_find_fields.return_value = {"severity": "high"}

        site_config = html_engine.HTML_SITES[0]

        with patch("src.scrapers.html_engine.json_output") as mock_json_output:
            with patch("src.scrapers.html_engine.check_valid_file"):
                with patch("src.scrapers.html_engine.vuln_output"):
                    html_engine.run_html_scraper(site_config)

        # Verify json_output was called (meaning Vulnerability was created)
        mock_json_output.assert_called_once()

        # Get the Vulnerability object that was passed to json_output
        vuln_arg = mock_json_output.call_args[0][0]
        assert isinstance(vuln_arg, Vulnerability)
        assert vuln_arg.title == "Test Article"
        assert vuln_arg.subsector == "cyber_attack"
