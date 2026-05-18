import pytest
import json
from unittest.mock import patch, MagicMock
import requests
from src.GDELT import helpers


class TestGetBody:
    """Test suite for get_body function"""

    def test_get_body_empty_url(self):
        """Test with empty URL returns empty string"""
        assert helpers.get_body("") == ""

    def test_get_body_none_url(self):
        """Test with None URL returns empty string"""
        assert helpers.get_body(None) == ""

    @patch("src.GDELT.helpers.requests.get")
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

    @patch("src.GDELT.helpers.requests.get")
    def test_get_body_http_protocol_preserved(self, mock_get):
        """Test that explicit http:// protocol is preserved"""
        mock_response = MagicMock()
        mock_response.text = "<body><p>Test content</p></body>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        helpers.get_body("http://example.com")
        args, kwargs = mock_get.call_args
        assert args[0] == "http://example.com"

    @patch("src.GDELT.helpers.requests.get")
    def test_get_body_successful_extraction(self, mock_get):
        """Test successful extraction of article body"""
        mock_response = MagicMock()
        mock_response.text = "<html><body><article><p>Paragraph 1</p><p>Paragraph 2</p></article></body></html>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = helpers.get_body("https://example.com")
        assert "Paragraph 1" in result
        assert "Paragraph 2" in result

    @patch("src.GDELT.helpers.requests.get")
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

    @patch("src.GDELT.helpers.requests.get")
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

    @patch("src.GDELT.helpers.requests.get")
    def test_get_body_request_exception(self, mock_get):
        """Test handling of network errors"""
        mock_get.side_effect = requests.RequestException("Connection failed")

        result = helpers.get_body("https://example.com")
        assert result == ""

    @patch("src.GDELT.helpers.requests.get")
    def test_get_body_timeout(self, mock_get):
        """Test handling of timeout"""
        mock_get.side_effect = requests.Timeout("Request timeout")

        result = helpers.get_body("https://example.com")
        assert result == ""

    @patch("src.GDELT.helpers.requests.get")
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

    @patch("src.GDELT.helpers.requests.get")
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


class TestAiCheckValidation:
    """Test suite for ai_check_validation function"""

    @patch("src.GDELT.helpers.requests.post")
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
            "Ransomware hits hospital", "Body text"
        )
        assert is_threat is True
        assert detail == "cyber_attack"

    @patch("src.GDELT.helpers.requests.post")
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

        is_threat, detail = helpers.ai_check_validation("Policy news", "Body text")
        assert is_threat is False
        assert detail == "This is just policy news"

    @patch("src.GDELT.helpers.requests.post")
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

        is_threat, detail = helpers.ai_check_validation("Title", "Body")
        assert is_threat is False

    @patch("src.GDELT.helpers.requests.post")
    def test_ai_check_validation_json_parse_error(self, mock_post):
        """Test handling of invalid JSON response"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "not valid json"}
        mock_post.return_value = mock_response

        is_threat, detail = helpers.ai_check_validation("Title", "Body")
        assert is_threat is False
        assert detail == "Parsing Error"

    @patch("src.GDELT.helpers.requests.post")
    def test_ai_check_validation_request_exception(self, mock_post):
        """Test handling of request exceptions"""
        mock_post.side_effect = requests.RequestException("Connection error")

        is_threat, detail = helpers.ai_check_validation("Title", "Body")
        assert is_threat is False
        assert detail == "Parsing Error"

    @patch("src.GDELT.helpers.requests.post")
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

        is_threat, detail = helpers.ai_check_validation("Drug shortage", "Body")
        assert is_threat is True
        assert detail == "drug_shortage"

    @patch("src.GDELT.helpers.requests.post")
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

        is_threat, detail = helpers.ai_check_validation("Device shortage", "Body")
        assert detail == "medical_device_shortage"

    @patch("src.GDELT.helpers.requests.post")
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

        is_threat, detail = helpers.ai_check_validation("Hurricane", "Body")
        assert detail == "natural_disaster"

    @patch("src.GDELT.helpers.requests.post")
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

        helpers.ai_check_validation("Title", "Body")
        call_args = mock_post.call_args
        assert call_args[0][0] == helpers.AI_URL

    @patch("src.GDELT.helpers.requests.post")
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

        helpers.ai_check_validation("Title", "Body")
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["model"] == helpers.AI_MODEL


class TestFindSubsectorFields:
    """Test suite for find_subsector_fields function"""

    def test_find_subsector_fields_invalid_subsector(self):
        """Test with invalid subsector returns empty dict"""
        result = helpers.find_subsector_fields("invalid_subsector", "Title", "Body")
        assert result == {}

    @patch("src.GDELT.helpers.requests.post")
    def test_find_subsector_fields_drug_shortage(self, mock_post):
        """Test extraction of drug shortage fields"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
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

        result = helpers.find_subsector_fields("drug_shortage", "Drug shortage", "Body")
        assert result["drug_name"] == "Penicillin"
        assert result["manufacturer"] == "Pfizer"

    @patch("src.GDELT.helpers.requests.post")
    def test_find_subsector_fields_cyber_attack(self, mock_post):
        """Test extraction of cyber attack fields"""
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

        result = helpers.find_subsector_fields("cyber_attack", "Ransomware", "Body")
        assert result["attack_type"] == "ransomware"
        assert result["ransom_demanded_usd"] == 500000

    @patch("src.GDELT.helpers.requests.post")
    def test_find_subsector_fields_medical_device(self, mock_post):
        """Test extraction of medical device shortage fields"""
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

        result = helpers.find_subsector_fields(
            "medical_device_shortage", "Device shortage", "Body"
        )
        assert result["device_name"] == "Ventilator"
        assert result["recall_class"] == "Class II"

    @patch("src.GDELT.helpers.requests.post")
    def test_find_subsector_fields_natural_disaster(self, mock_post):
        """Test extraction of natural disaster fields"""
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

        result = helpers.find_subsector_fields("natural_disaster", "Hurricane", "Body")
        assert result["disaster_type"] == "Hurricane"
        assert result["beds_offline"] == 500

    @patch("src.GDELT.helpers.requests.post")
    def test_find_subsector_fields_other(self, mock_post):
        """Test extraction of other event fields"""
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

        result = helpers.find_subsector_fields("other", "Staff shortage", "Body")
        assert result["event_type"] == "Staff shortage"
        assert result["severity"] == "High"

    @patch("src.GDELT.helpers.requests.post")
    def test_find_subsector_fields_request_exception(self, mock_post):
        """Test handling of request exceptions"""
        mock_post.side_effect = requests.RequestException("Connection error")

        result = helpers.find_subsector_fields("drug_shortage", "Title", "Body")
        # Should return dict with all fields set to None
        assert all(v is None for v in result.values())

    @patch("src.GDELT.helpers.requests.post")
    def test_find_subsector_fields_json_parse_error(self, mock_post):
        """Test handling of JSON parse errors"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "not valid json"}
        mock_post.return_value = mock_response

        result = helpers.find_subsector_fields("drug_shortage", "Title", "Body")
        # Should return dict with all fields set to None
        assert all(v is None for v in result.values())

    @patch("src.GDELT.helpers.requests.post")
    def test_find_subsector_fields_uses_json_format(self, mock_post):
        """Test that JSON format is requested"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": json.dumps({"drug_name": None})}
        mock_post.return_value = mock_response

        helpers.find_subsector_fields("drug_shortage", "Title", "Body")
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["format"] == "json"

    @patch("src.GDELT.helpers.requests.post")
    def test_find_subsector_fields_low_temperature(self, mock_post):
        """Test that low temperature is used for deterministic extraction"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": json.dumps({"drug_name": None})}
        mock_post.return_value = mock_response

        helpers.find_subsector_fields("drug_shortage", "Title", "Body")
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["options"]["temperature"] == 0.0
