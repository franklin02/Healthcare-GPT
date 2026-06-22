import pytest
import json
import sys
import types
import subprocess
from unittest.mock import patch, MagicMock
import requests
import src.shared_utils as helpers


LONG_BODY = (
    "This is a sufficiently long article body used to exercise the shared LLM "
    "validation path. It contains enough characters to clear the new minimum "
    "threshold and should still behave like a normal article excerpt for tests."
)
TEST_OLLAMA_PORT = 11434


class TestGetBody:
    """Test suite for get_body function"""

    def test_get_body_empty_url(self):
        """Test with empty URL returns empty string"""
        assert helpers.get_body("") == ""

    def test_get_body_none_url(self):
        """Test with None URL returns empty string"""
        assert helpers.get_body(None) == ""

    @patch("src.shared_utils.requests.get")
    def test_get_body_url_normalization(self, mock_get):
        """Test that URLs without protocol get https:// prepended"""
        mock_response = MagicMock()
        mock_response.text = "<body><p>Test content</p></body>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        helpers.get_body("example.com")
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert args[0] == "https://example.com"

    @patch("src.shared_utils.requests.get")
    def test_get_body_http_protocol_preserved(self, mock_get):
        """Test that explicit http:// protocol is preserved"""
        mock_response = MagicMock()
        mock_response.text = "<body><p>Test content</p></body>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        helpers.get_body("http://example.com")
        args, kwargs = mock_get.call_args
        assert args[0] == "http://example.com"

    @patch("src.shared_utils.requests.get")
    def test_get_body_successful_extraction(self, mock_get):
        """Test successful extraction of article body"""
        mock_response = MagicMock()
        mock_response.text = "<html><body><article><p>Paragraph 1</p><p>Paragraph 2</p></article></body></html>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = helpers.get_body("https://example.com")
        assert "Paragraph 1" in result
        assert "Paragraph 2" in result

    @patch("src.shared_utils.requests.get")
    def test_get_body_removes_script_tags(self, mock_get):
        """Test that script tags are removed"""
        mock_response = MagicMock()
        mock_response.text = """
            <body>
                <article>
                    <p>Real content</p>
                    <script>alert('ads')</script>
                </article>
            </body>
        """
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = helpers.get_body("https://example.com")
        assert "alert" not in result
        assert "Real content" in result

    @patch("src.shared_utils.requests.get")
    def test_get_body_removes_noise_classes(self, mock_get):
        """Test that noise elements by class are removed"""
        mock_response = MagicMock()
        mock_response.text = """
            <body>
                <article>
                    <p>Real content</p>
                    <div class="sidebar">Ad stuff</div>
                    <div class="related">Related articles</div>
                </article>
            </body>
        """
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = helpers.get_body("https://example.com")
        assert "Ad stuff" not in result
        assert "Related articles" not in result
        assert "Real content" in result

    @patch("src.shared_utils.requests.get")
    def test_get_body_request_exception(self, mock_get):
        """Test handling of network errors"""
        mock_get.side_effect = requests.RequestException("Connection failed")

        result = helpers.get_body("https://example.com")
        assert result == ""

    @patch("src.shared_utils.requests.get")
    def test_get_body_timeout(self, mock_get):
        """Test handling of timeout"""
        mock_get.side_effect = requests.Timeout("Request timeout")

        result = helpers.get_body("https://example.com")
        assert result == ""

    @patch("src.shared_utils.requests.get")
    def test_get_body_uses_headers(self, mock_get):
        """Test that proper User-Agent header is used"""
        mock_response = MagicMock()
        mock_response.text = "<body><p>Test</p></body>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        helpers.get_body("https://example.com")
        call_kwargs = mock_get.call_args[1]
        assert "headers" in call_kwargs
        assert "User-Agent" in call_kwargs["headers"]

    @patch("src.shared_utils.requests.get")
    def test_get_body_prefers_paragraphs(self, mock_get):
        """Test that paragraph text is preferred over raw text"""
        mock_response = MagicMock()
        mock_response.text = """
            <body>
                <article>
                    <p>Paragraph 1</p>
                    <p>Paragraph 2</p>
                </article>
            </body>
        """
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = helpers.get_body("https://example.com")
        # Should join paragraphs with double newline
        assert "Paragraph 1" in result
        assert "Paragraph 2" in result

    @patch("src.shared_utils.requests.get")
    def test_get_body_strips_noise_by_id(self, mock_get):
        """Ensure elements matched by id regex are removed (covers line 173)."""
        mock_response = MagicMock()
        mock_response.text = """
            <body>
                <article>
                    <p>Real content</p>
                    <div id="ad-banner">Buy now</div>
                </article>
            </body>
        """
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = helpers.get_body("https://example.com")
        assert "Real content" in result
        assert "Buy now" not in result

    @patch("src.shared_utils.requests.get")
    def test_get_body_no_body_found_prints_and_returns_empty(
        self, mock_get, capsys=None
    ):
        """Trigger the main is None branch and return empty string (covers lines 189-190)."""
        mock_response = MagicMock()
        mock_response.text = "<html><body></body></html>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = helpers.get_body("https://example.com")
        assert result == ""

    @patch("src.shared_utils.requests.get")
    def test_get_body_falls_back_to_main_text_when_no_paragraphs(self, mock_get):
        """Return raw main text when no <p> tags exist (covers line 199)."""
        mock_response = MagicMock()
        mock_response.text = (
            "<body><article>Some plain text without p tags</article></body>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = helpers.get_body("https://example.com")
        assert "Some plain text without p tags" in result

    @patch("src.shared_utils.requests.get")
    def test_get_body_filters_boilerplate_only_pages(self, mock_get):
        """Return empty string when the page is mostly navigation and footer chrome."""
        mock_response = MagicMock()
        mock_response.text = """
            <html>
                <body>
                    <div>Skip to main content</div>
                    <div>Close</div>
                    <div>© Copyright 2026 Post Register | Terms of Use | Privacy Policy</div>
                    <div>Powered by BLOX Content Management System from BLOX Digital</div>
                </body>
            </html>
        """
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = helpers.get_body("https://example.com")
        assert result == ""


class TestGetTitle:
    """Test suite for get_title function"""

    def test_empty_url_returns_empty_string(self):
        """Test with empty URL returns empty string"""
        assert helpers.get_title("") == ""

    def test_none_url_returns_none(self):
        """Test with None URL returns None"""
        assert helpers.get_title(None) is None

    @patch("src.shared_utils.requests.get")
    def test_extracts_title_from_title_tag(self, mock_get):
        """Test that the text of the <title> tag is returned"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Hospital Ransomware Attack</title></head>"
            "<body><article><p>Content</p></article></body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        assert helpers.get_title("https://example.com") == "Hospital Ransomware Attack"

    @patch("src.shared_utils.requests.get")
    def test_strips_pipe_site_suffix(self, mock_get):
        """Test that ' | Site' suffix is stripped from the title"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Hospital Ransomware Attack | Reuters</title></head>"
            "<body></body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        title = helpers.get_title("https://example.com")
        assert title == "Hospital Ransomware Attack"
        assert "Reuters" not in title

    @patch("src.shared_utils.requests.get")
    def test_strips_dash_site_suffix(self, mock_get):
        """Test that ' - Site' suffix is stripped from the title"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Hospital Ransomware Attack - NBC News</title></head>"
            "<body></body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        title = helpers.get_title("https://example.com")
        assert title == "Hospital Ransomware Attack"
        assert "NBC News" not in title

    @patch("src.shared_utils.requests.get")
    def test_strips_em_dash_site_suffix(self, mock_get):
        """Test that ' – Site' suffix is stripped from the title"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Drug Shortage – BBC Health</title></head>"
            "<body></body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        assert helpers.get_title("https://example.com") == "Drug Shortage"

    @patch("src.shared_utils.requests.get")
    def test_strips_only_last_separator_segment(self, mock_get):
        """Test that only the last separator segment is stripped, leaving earlier parts intact"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Attack | Full Story | Site</title></head>"
            "<body></body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        assert helpers.get_title("https://example.com") == "Attack | Full Story"

    @patch("src.shared_utils.requests.get")
    def test_falls_back_to_url_when_no_title_tag(self, mock_get):
        """Test that the raw URL is returned when no <title> tag exists"""
        mock_response = MagicMock()
        mock_response.text = "<html><head></head><body><p>Content</p></body></html>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        url = "https://example.com/article"
        assert helpers.get_title(url) == url

    @patch("src.shared_utils.requests.get")
    def test_falls_back_to_url_when_title_tag_empty(self, mock_get):
        """Test that the raw URL is returned when the <title> tag is empty"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title></title></head><body><p>Content</p></body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        url = "https://example.com/article"
        assert helpers.get_title(url) == url

    @patch("src.shared_utils.requests.get")
    def test_network_error_returns_url(self, mock_get):
        """Test that the raw URL is returned on network failure"""
        mock_get.side_effect = requests.RequestException("Connection failed")

        url = "https://example.com"
        assert helpers.get_title(url) == url

    @patch("src.shared_utils.requests.get")
    def test_normalizes_url_without_scheme(self, mock_get):
        """Test that URLs without a scheme get https:// prepended before fetching"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Test</title></head><body></body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        helpers.get_title("example.com")
        args, _ = mock_get.call_args
        assert args[0] == "https://example.com"


class TestGetBodyAndTitle:
    """Test suite for get_body_and_title function"""

    @patch("src.shared_utils.requests.get")
    def test_returns_body_and_title(self, mock_get):
        """Test that both body and title are extracted from a single request"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Hospital Ransomware Attack | Reuters</title></head>"
            "<body><article><p>Paragraph 1</p><p>Paragraph 2</p></article></body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        body, title = helpers.get_body_and_title("https://example.com")

        assert "Paragraph 1" in body
        assert "Paragraph 2" in body
        assert title == "Hospital Ransomware Attack"
        assert "Reuters" not in title

    @patch("src.shared_utils.requests.get")
    def test_single_request(self, mock_get):
        """Test that only one HTTP request is made (not two)"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Test</title></head>"
            "<body><article><p>Content</p></article></body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        helpers.get_body_and_title("https://example.com")
        assert mock_get.call_count == 1

    def test_empty_url(self):
        """Test with empty URL returns empty body and empty title"""
        body, title = helpers.get_body_and_title("")
        assert body == ""
        assert title == ""

    def test_none_url(self):
        """Test with None URL returns empty body and empty title"""
        body, title = helpers.get_body_and_title(None)
        assert body == ""
        assert title == ""

    @patch("src.shared_utils.requests.get")
    def test_network_error(self, mock_get):
        """Test that network errors return empty body and URL as title"""
        mock_get.side_effect = requests.RequestException("Connection failed")

        body, title = helpers.get_body_and_title("https://example.com")
        assert body == ""
        assert title == "https://example.com"

    @patch("src.shared_utils.requests.get")
    def test_empty_body_valid_title(self, mock_get):
        """Test page with title but no meaningful body content"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Valid Title</title></head>"
            "<body>"
            "<div>Skip to main content</div>"
            "<div>Close</div>"
            "<div>© Copyright 2026 | Terms of Use | Privacy Policy</div>"
            "<div>Powered by BLOX Content Management System</div>"
            "</body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        body, title = helpers.get_body_and_title("https://example.com")
        assert body == ""
        assert title == "Valid Title"

    @patch("src.shared_utils.requests.get")
    def test_normalizes_url_without_scheme(self, mock_get):
        """Test that URLs without a scheme get https:// prepended"""
        mock_response = MagicMock()
        mock_response.text = (
            "<html><head><title>Test</title></head>"
            "<body><article><p>Content</p></article></body></html>"
        )
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        helpers.get_body_and_title("example.com")
        args, _ = mock_get.call_args
        assert args[0] == "https://example.com"


class TestAiCheckValidation:
    """Test suite for ai_check_validation function"""

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_valid_threat(self, mock_post):
        """Test valid threat identification"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "analysis": "Hospital affected by ransomware",
                    "is_operational_disruption": True,
                    "subsector": "cyber_attack",
                }
            )
        }
        mock_post.return_value = mock_response

        is_threat, detail = helpers.ai_check_validation(
            "Ransomware hits hospital", LONG_BODY
        )
        assert is_threat is True
        assert detail == "cyber_attack"

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_no_threat(self, mock_post):
        """Test non-threat identification"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "analysis": "This is just policy news",
                    "is_operational_disruption": False,
                    "subsector": "none",
                }
            )
        }
        mock_post.return_value = mock_response

        is_threat, detail = helpers.ai_check_validation("Policy news", LONG_BODY)
        assert is_threat is False
        assert detail == "This is just policy news"

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_short_body_skips_llm(self, mock_post):
        """Test that short bodies return early without calling the LLM"""
        body = "Short body text that is clearly below the minimum threshold."

        is_threat, detail = helpers.ai_check_validation("Short article", body)

        assert is_threat is False
        assert detail == "Body too short for LLM review"
        mock_post.assert_not_called()

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_string_no_response(self, mock_post):
        """Test handling of string 'NO' response"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "analysis": "Not a disruption",
                    "is_operational_disruption": "NO",
                    "subsector": "none",
                }
            )
        }
        mock_post.return_value = mock_response

        is_threat, detail = helpers.ai_check_validation("Title", LONG_BODY)
        assert is_threat is False

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_json_parse_error(self, mock_post):
        """Test handling of invalid JSON response"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "not valid json"}
        mock_post.return_value = mock_response

        is_threat, detail = helpers.ai_check_validation("Title", LONG_BODY)
        assert is_threat is False
        assert detail == "Parsing Error"

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_request_exception(self, mock_post):
        """Test handling of request exceptions"""
        mock_post.side_effect = requests.RequestException("Connection error")

        is_threat, detail = helpers.ai_check_validation("Title", LONG_BODY)
        assert is_threat is False
        assert detail == "Parsing Error"

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_drug_shortage(self, mock_post):
        """Test drug shortage subsector classification"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "analysis": "Drug shortage identified",
                    "is_operational_disruption": True,
                    "subsector": "drug_shortage",
                }
            )
        }
        mock_post.return_value = mock_response

        is_threat, detail = helpers.ai_check_validation("Drug shortage", LONG_BODY)
        assert is_threat is True
        assert detail == "drug_shortage"

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_medical_device(self, mock_post):
        """Test medical device shortage subsector"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "analysis": "Device shortage",
                    "is_operational_disruption": True,
                    "subsector": "medical_device_shortage",
                }
            )
        }
        mock_post.return_value = mock_response

        is_threat, detail = helpers.ai_check_validation("Device shortage", LONG_BODY)
        assert detail == "medical_device_shortage"

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_natural_disaster(self, mock_post):
        """Test natural disaster subsector"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "analysis": "Hurricane hits hospital",
                    "is_operational_disruption": True,
                    "subsector": "natural_disaster",
                }
            )
        }
        mock_post.return_value = mock_response

        is_threat, detail = helpers.ai_check_validation("Hurricane", LONG_BODY)
        assert detail == "natural_disaster"

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_posts_correct_url(self, mock_post):
        """Test that correct AI URL is used"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "is_operational_disruption": False,
                    "subsector": "none",
                    "analysis": "test",
                }
            )
        }
        mock_post.return_value = mock_response

        helpers.ai_check_validation("Title", LONG_BODY, port=TEST_OLLAMA_PORT)
        call_args = mock_post.call_args
        assert call_args[0][0] == f"http://localhost:{TEST_OLLAMA_PORT}/api/generate"

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_uses_correct_model(self, mock_post):
        """Test that correct AI model is specified"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "is_operational_disruption": False,
                    "subsector": "none",
                    "analysis": "test",
                }
            )
        }
        mock_post.return_value = mock_response

        helpers.ai_check_validation("Title", LONG_BODY)
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["model"] == helpers.AI_MODEL


class TestExtractFields:
    """Test suite for extract_fields function"""

    def test_extract_fields_invalid_subsector(self):
        """Test with invalid subsector raises a recoverable error."""
        with pytest.raises(helpers.MissingSubsectorFieldsError):
            helpers.extract_fields(
                "invalid_subsector", "Title", "Body", TEST_OLLAMA_PORT
            )

    def test_extract_fields_empty_subsector_fields(self, monkeypatch):
        """Test a configured subsector with no fields raises a recoverable error."""
        monkeypatch.setitem(helpers.SUBSECTOR_FIELDS, "empty_subsector", [])

        with pytest.raises(helpers.MissingSubsectorFieldsError):
            helpers.extract_fields("empty_subsector", "Title", "Body", TEST_OLLAMA_PORT)

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_drug_shortage(self, mock_post):
        """Test extraction of drug shortage fields."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "exec_summary": "Shortage affects hospitals.",
                    "geography_scope": "Northeast",
                    "drug_name": "Penicillin",
                    "generic_name": "penicillin",
                    "manufacturer": "Pfizer",
                    "dosage_form": "tablet",
                    "shortage_reason": "Factory closure",
                    "estimated_resolution_date": "2026-06-01",
                    "affected_regions": ["Northeast", "Southeast"],
                    "domestic_vs_foreign_dependency": "Foreign",
                }
            )
        }
        mock_post.return_value = mock_response

        sector_data, subsector_data = helpers.extract_fields(
            "drug_shortage", "Drug shortage", "Body", TEST_OLLAMA_PORT
        )
        assert sector_data["exec_summary"] == "Shortage affects hospitals."
        assert sector_data["geography_scope"] == "Northeast"
        assert subsector_data["drug_name"] == "Penicillin"
        assert subsector_data["manufacturer"] == "Pfizer"

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_cyber_attack(self, mock_post):
        """Test extraction of cyber attack fields."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "attack_type": "ransomware",
                    "threat_actor": "Unknown",
                    "individuals_affected": 50000,
                    "data_types_exposed": ["patient records"],
                    "systems_affected": ["EHR"],
                    "ransom_demanded_usd": 500000,
                    "ransom_paid": 250000,
                    "downtime_days": 3,
                    "services_disrupted": ["Surgery"],
                    "law_enforcement_involved": True,
                    "hhs_breach_portal_listed": True,
                }
            )
        }
        mock_post.return_value = mock_response

        _, subsector_data = helpers.extract_fields(
            "cyber_attack", "Ransomware", "Body", TEST_OLLAMA_PORT
        )
        assert subsector_data["attack_type"] == "ransomware"
        assert subsector_data["ransom_demanded_usd"] == 500000

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_medical_device(self, mock_post):
        """Test extraction of medical device shortage fields."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "device_name": "Ventilator",
                    "device_category": "Respiratory",
                    "manufacturer": "Philips",
                    "manufacturer_country": "Netherlands",
                    "shortage_reason": "Supply chain",
                    "fda_recall_number": "FDA12345",
                    "recall_class": "Class II",
                    "affected_specialties": ["ICU"],
                    "alternatives_available": False,
                    "estimated_resolution_date": "2026-07-01",
                    "domestic_vs_foreign_dependency": "Foreign",
                }
            )
        }
        mock_post.return_value = mock_response

        _, subsector_data = helpers.extract_fields(
            "medical_device_shortage", "Device shortage", "Body", TEST_OLLAMA_PORT
        )
        assert subsector_data["device_name"] == "Ventilator"
        assert subsector_data["recall_class"] == "Class II"

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_natural_disaster(self, mock_post):
        """Test extraction of natural disaster fields."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "disaster_type": "Hurricane",
                    "disaster_name": "Hurricane Ian",
                    "fema_declaration_id": "FEMA4672",
                    "category_magnitude": "Category 4",
                    "affected_facilities_count": 15,
                    "evacuation_ordered": True,
                    "field_hospitals": 2,
                    "beds_offline": 500,
                    "facility_status": "Operational",
                    "estimated_damage_usd": 2000000,
                    "infrastructure_damage": "Roof, generators",
                    "services_disrupted": ["Surgery", "Emergency"],
                }
            )
        }
        mock_post.return_value = mock_response

        _, subsector_data = helpers.extract_fields(
            "natural_disaster", "Hurricane", "Body", TEST_OLLAMA_PORT
        )
        assert subsector_data["disaster_type"] == "Hurricane"
        assert subsector_data["beds_offline"] == 500

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_other(self, mock_post):
        """Test extraction of other event fields."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "event_type": "Staff shortage",
                    "event_description": "Mass resignation",
                    "severity": "High",
                    "departments_affected": ["Emergency"],
                    "staff_type_affected": ["Nurses"],
                    "beds_offline": 100,
                    "services_disrupted": ["Emergency care"],
                    "regulatory_response": "Declared state of emergency",
                }
            )
        }
        mock_post.return_value = mock_response

        _, subsector_data = helpers.extract_fields(
            "other", "Staff shortage", "Body", TEST_OLLAMA_PORT
        )
        assert subsector_data["event_type"] == "Staff shortage"
        assert subsector_data["severity"] == "High"

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_request_exception(self, mock_post):
        """Test handling of request exceptions."""
        mock_post.side_effect = requests.RequestException("Connection error")

        sector_data, subsector_data = helpers.extract_fields(
            "drug_shortage", "Title", "Body", TEST_OLLAMA_PORT
        )
        assert all(v is None for v in sector_data.values())
        assert all(v is None for v in subsector_data.values())

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_json_parse_error(self, mock_post):
        """Test handling of JSON parse errors."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "not valid json"}
        mock_post.return_value = mock_response

        sector_data, subsector_data = helpers.extract_fields(
            "drug_shortage", "Title", "Body", TEST_OLLAMA_PORT
        )
        assert all(v is None for v in sector_data.values())
        assert all(v is None for v in subsector_data.values())

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_uses_json_format(self, mock_post):
        """Test that JSON format is requested."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": json.dumps({"drug_name": None})}
        mock_post.return_value = mock_response

        helpers.extract_fields("drug_shortage", "Title", "Body", TEST_OLLAMA_PORT)
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["format"] == "json"

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_low_temperature(self, mock_post):
        """Test that low temperature is used for deterministic extraction."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": json.dumps({"drug_name": None})}
        mock_post.return_value = mock_response

        helpers.extract_fields("drug_shortage", "Title", "Body", TEST_OLLAMA_PORT)
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["options"]["temperature"] == 0.0

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_handles_explicit_negative_boolean(self, mock_post):
        """An explicitly negated boolean is requested and returned as false."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps({"ransom_paid": False})
        }
        mock_post.return_value = mock_response

        _, subsector_data = helpers.extract_fields(
            "cyber_attack",
            "Ransomware",
            "The hospital did not pay the ransom.",
            TEST_OLLAMA_PORT,
        )
        prompt = mock_post.call_args[1]["json"]["prompt"]
        assert "false for an explicit negative statement" in prompt
        assert subsector_data["ransom_paid"] is False

    def test_build_extraction_prompt_includes_only_selected_subsector_guidance(self):
        """Prompt should include guidance for the requested subsector only."""
        prompt = helpers.build_extraction_prompt(
            "cyber_attack",
            "Ransomware disrupts hospital",
            "Hospital systems were offline for three days.",
        )

        assert "FIELD-SPECIFIC GUIDANCE (cyber_attack fields)" in prompt
        assert '"attack_type": stated kind of cyber incident' in prompt
        assert '"drug_name": brand, marketed, or named drug product' not in prompt
        assert "A field name does not need to appear verbatim" in prompt

    def test_parse_extraction_response_splits_sector_and_subsector_fields(self):
        """Parser should split raw LLM JSON without making an Ollama request."""
        raw_response = json.dumps(
            {
                "exec_summary": "Hospital ransomware disrupted records.",
                "geography_scope": "Ohio",
                "start_date": "2026-06-01",
                "end_date": None,
                "resilience_or_mitigation_observed": (
                    "Staff diverted ambulances to nearby facilities"
                ),
                "attack_type": "ransomware",
                "threat_actor": "Unknown",
                "systems_affected": ["electronic health records"],
                "unexpected_key": "ignored",
            }
        )

        sector_data, subsector_data = helpers.parse_extraction_response(
            raw_response,
            "cyber_attack",
            "Ransomware disrupts hospital",
            (
                "Staff diverted ambulances to nearby facilities while electronic "
                "health records were offline."
            ),
        )

        assert set(sector_data) == set(helpers.LLM_SECTOR_FIELDS)
        assert set(subsector_data) == set(helpers.SUBSECTOR_FIELDS["cyber_attack"])
        assert sector_data["exec_summary"] == "Hospital ransomware disrupted records."
        assert sector_data["geography_scope"] == "Ohio"
        assert subsector_data["attack_type"] == "ransomware"
        assert subsector_data["systems_affected"] == ["electronic health records"]
        assert "unexpected_key" not in sector_data
        assert "unexpected_key" not in subsector_data


class TestRunBertAndUseBert:
    """Test suite for _run_bert and ai_check_validation(use_bert=True)."""

    def test_run_bert_delegates_to_run_bert_inference(self, monkeypatch):
        """Test that _run_bert imports and calls run_bert_inference with mock data."""
        fake_module = types.ModuleType("src.GDELT.BERT_filter")
        mock_classifier = MagicMock(name="mock_classifier")
        mock_load_model = MagicMock(return_value=mock_classifier)
        mock_run_bert_inference = MagicMock(return_value="cyber_attack")
        fake_module.load_model = mock_load_model
        fake_module.run_bert_inference = mock_run_bert_inference
        monkeypatch.setitem(sys.modules, "src.GDELT.BERT_filter", fake_module)
        monkeypatch.setattr(helpers, "_classifier", None)

        result = helpers._run_bert("Ransomware hits hospital", LONG_BODY)

        assert result == "cyber_attack"
        mock_load_model.assert_called_once()
        mock_run_bert_inference.assert_called_once_with(
            {"title": "Ransomware hits hospital", "body": LONG_BODY},
            mock_classifier,
            verbose=False,
        )

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_use_bert_rejects_none_without_llm(
        self, mock_post, monkeypatch
    ):
        """Test that use_bert returns early when BERT rejects the article."""
        monkeypatch.setattr(helpers, "_run_bert", MagicMock(return_value="none"))

        is_threat, detail = helpers.ai_check_validation(
            "Policy news", LONG_BODY, use_bert=True
        )

        assert is_threat is False
        assert detail == "BERT: unrelated news"
        mock_post.assert_not_called()

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_use_bert_forwards_to_llm_when_flagged(
        self, mock_post, monkeypatch
    ):
        """Test that use_bert still calls the LLM when BERT flags the article."""
        monkeypatch.setattr(
            helpers, "_run_bert", MagicMock(return_value="cyber_attack")
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "analysis": "Hospital affected by ransomware",
                    "is_operational_disruption": True,
                    "subsector": "cyber_attack",
                }
            )
        }
        mock_post.return_value = mock_response

        is_threat, detail = helpers.ai_check_validation(
            "Ransomware hits hospital", LONG_BODY, use_bert=True
        )

        assert is_threat is True
        assert detail == "cyber_attack"
        mock_post.assert_called_once()


@pytest.fixture(autouse=True)
def clear_ollama_model_cache():
    helpers.checked_ollama_models.clear()
    yield
    helpers.checked_ollama_models.clear()


class TestEnsureOllamaModelAvailable:
    """Test suite for Ollama startup model checks."""

    @staticmethod
    def ollama_list_output(*models):
        rows = ["NAME            ID              SIZE      MODIFIED"]
        rows.extend(f"{model} abc123          2.0 GB    now" for model in models)
        return "\n".join(rows) + "\n"

    @patch("src.shared_utils.subprocess.run")
    def test_installed_model_passes_and_caches_success(self, mock_run):
        """Installed model should pass and cache the successful check."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.ollama_list_output(helpers.AI_MODEL),
            stderr="",
        )

        helpers.ensure_model_available()
        helpers.ensure_model_available()

        mock_run.assert_called_once_with(
            ["ollama", "list"],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )

    @patch("src.shared_utils.subprocess.run")
    def test_cache_is_per_model(self, mock_run):
        """Checking one cached model should not skip checks for another model."""
        alternate_model = f"{helpers.AI_MODEL}-alternate"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.ollama_list_output(helpers.AI_MODEL, alternate_model),
            stderr="",
        )

        helpers.ensure_model_available()
        helpers.ensure_model_available(alternate_model)

        assert mock_run.call_count == 2

    @patch("src.shared_utils.subprocess.run")
    def test_missing_model_raises_with_pull_guidance_and_is_not_cached(self, mock_run):
        """Missing model should fail with exact pull guidance."""
        missing_model = f"{helpers.AI_MODEL}-missing"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.ollama_list_output(helpers.AI_MODEL),
            stderr="",
        )

        with pytest.raises(helpers.model_unavailable_error) as exc:
            helpers.ensure_model_available(missing_model)

        assert (
            f"[ERROR] Model '{missing_model}' not found in Ollama. Make sure Ollama "
            in str(exc.value)
        )
        assert f"Run: ollama pull {missing_model}" in str(exc.value)
        assert missing_model not in helpers.checked_ollama_models

    @patch("src.shared_utils.subprocess.run")
    def test_ollama_cli_missing_raises_readable_error(self, mock_run):
        """Missing ollama CLI should raise an error to help users install it."""
        mock_run.side_effect = FileNotFoundError

        with pytest.raises(helpers.model_unavailable_error) as exc:
            helpers.ensure_model_available()
        assert "Ollama CLI not found" in str(exc.value)

    @patch("src.shared_utils.subprocess.run")
    def test_ollama_list_timeout_raises_readable_error(self, mock_run):
        """Timeout on ollama list should raise an error to help users install it."""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["ollama", "list"],
            timeout=15,
        )

        with pytest.raises(helpers.model_unavailable_error) as exc:
            helpers.ensure_model_available()
        assert "Could not query Ollama models" in str(exc.value)

    @patch("src.shared_utils.subprocess.run")
    def test_ollama_list_nonzero_returncode_raises_readable_error(self, mock_run):
        """A real failed `ollama list` returns nonzero because check=False is used."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="could not connect to ollama app",
        )

        with pytest.raises(helpers.model_unavailable_error) as exc:
            helpers.ensure_model_available()
        assert "Could not query Ollama models" in str(exc.value)
        assert f"Run: ollama pull {helpers.AI_MODEL}" not in str(exc.value)
        assert helpers.AI_MODEL not in helpers.checked_ollama_models


class TestGetExtractionTemplate:
    """Tests for get_extraction_template().

    1. Field-set correctness — every known subsector returns exactly the 5 base
       fields (LLM_SECTOR_FIELDS) plus its own subsector fields, nothing more.
    2. Type-string mapping — list → "list of strings", bool → "boolean",
       int/float → "integer", everything else → "string".
    3. Unknown subsector — only the 5 base fields returned, all "string".
    4. Missing annotation (inner else branch) — a field present in
       SUBSECTOR_FIELDS but absent from the dataclass __annotations__ dict
       must default to "string".
    5. No-dataclass path (outer else branch) — when SUBSECTOR_DATA_CLASSES has
       no entry for a subsector, every subsector field defaults to "string".
    """

    _BASE_FIELDS = frozenset(helpers.LLM_SECTOR_FIELDS)

    # ── 1. Field-set correctness per known subsector ──────────────────────────

    @pytest.mark.parametrize("subsector", list(helpers.SUBSECTOR_FIELDS.keys()))
    def test_exact_field_set_per_subsector(self, subsector):
        """Each known subsector returns exactly base + its own fields.

        Verifies no foreign fields are injected and no declared fields are
        dropped, for every subsector defined in SUBSECTOR_FIELDS.
        """
        expected = self._BASE_FIELDS | set(helpers.SUBSECTOR_FIELDS[subsector])
        result = helpers.get_extraction_template(subsector)
        assert set(result.keys()) == expected

    # ── 2. Unknown subsector → only base fields ───────────────────────────────

    def test_unknown_subsector_returns_only_base_fields(self):
        """An unrecognised subsector yields exactly the 5 base fields."""
        result = helpers.get_extraction_template("totally_unknown_subsector")
        assert set(result.keys()) == self._BASE_FIELDS

    def test_unknown_subsector_all_values_are_string(self):
        """Every value for an unknown subsector must be 'string'."""
        result = helpers.get_extraction_template("totally_unknown_subsector")
        assert all(v == "string" for v in result.values())

    # ── 3 & 4. Type-string mapping + missing annotation (inner else) ──────────
    #
    #  A synthetic subsector 'test_sub' is injected via monkeypatch so that the
    #  mapping logic is exercised in isolation, independent of real dataclasses.
    #  The dataclass deliberately omits one field to trigger the inner else branch.

    @pytest.fixture()
    def typed_template(self, monkeypatch):
        """Return get_extraction_template('test_sub') with a controlled dataclass.

        The fake dataclass covers all four annotation branches:
          - list[str]  → "list of strings"
          - bool       → "boolean"
          - int        → "integer"
          - float      → "integer"
          - str        → "string"
        Plus 'absent_field', which has no annotation, triggering the inner else.
        """

        class _TypedCls:
            list_field: list[str]
            bool_field: bool
            int_field: int
            float_field: float
            str_field: str
            # 'absent_field' is intentionally omitted — exercises the inner else

        fake_fields = [
            "list_field",
            "bool_field",
            "int_field",
            "float_field",
            "str_field",
            "absent_field",
        ]
        monkeypatch.setitem(helpers.SUBSECTOR_DATA_CLASSES, "test_sub", _TypedCls)
        monkeypatch.setitem(helpers.SUBSECTOR_FIELDS, "test_sub", fake_fields)
        return helpers.get_extraction_template("test_sub")

    def test_list_annotation_maps_to_list_of_strings(self, typed_template):
        assert typed_template["list_field"] == "list of strings"

    def test_bool_annotation_maps_to_boolean(self, typed_template):
        assert typed_template["bool_field"] == "boolean"

    def test_int_annotation_maps_to_integer(self, typed_template):
        assert typed_template["int_field"] == "integer"

    def test_float_annotation_maps_to_integer(self, typed_template):
        """float sits in the same elif branch as int, so it maps to 'integer'."""
        assert typed_template["float_field"] == "integer"

    def test_str_annotation_maps_to_string(self, typed_template):
        assert typed_template["str_field"] == "string"

    def test_base_fields_always_string_even_with_dataclass(self, typed_template):
        """LLM_SECTOR_FIELDS are seeded as 'string' and must never be overwritten."""
        for field in helpers.LLM_SECTOR_FIELDS:
            assert typed_template[field] == "string"

    def test_missing_annotation_defaults_to_string(self, typed_template):
        """A field listed in SUBSECTOR_FIELDS but absent from __annotations__
        falls through the inner else and must become 'string'."""
        assert typed_template["absent_field"] == "string"

    # ── 5. No-dataclass path (outer else branch) ──────────────────────────────

    def test_no_dataclass_all_subsector_fields_are_string(self, monkeypatch):
        """When no dataclass is registered the outer else assigns 'string' to
        every subsector field without inspecting any annotations."""
        fake_fields = ["alpha", "beta", "gamma"]
        # Add to SUBSECTOR_FIELDS only — SUBSECTOR_DATA_CLASSES is left untouched
        # so .get("orphan_sub") returns None and triggers the outer else branch.
        monkeypatch.setitem(helpers.SUBSECTOR_FIELDS, "orphan_sub", fake_fields)
        result = helpers.get_extraction_template("orphan_sub")
        for field in fake_fields:
            assert result[field] == "string"

    def test_no_dataclass_base_fields_still_present(self, monkeypatch):
        """Even without a registered dataclass the 5 base fields must appear."""
        monkeypatch.setitem(helpers.SUBSECTOR_FIELDS, "orphan_sub", ["alpha"])
        result = helpers.get_extraction_template("orphan_sub")
        assert self._BASE_FIELDS.issubset(set(result.keys()))
