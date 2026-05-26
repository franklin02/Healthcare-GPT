import pytest
import json
import sys
import types
import subprocess
from unittest.mock import patch, MagicMock
import requests
import src.shared_utils as helpers


@pytest.fixture(autouse=True)
def clear_ollama_model_cache():
    helpers._VERIFIED_OLLAMA_MODELS.clear()
    yield
    helpers._VERIFIED_OLLAMA_MODELS.clear()


class TestEnsureOllamaModelAvailable:
    """Test suite for Ollama startup model checks."""

    @patch("src.shared_utils.subprocess.run")
    def test_installed_model_passes(self, mock_run):
        """Installed model should pass and cache the successful check."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "NAME            ID              SIZE      MODIFIED\n"
                "llama3.2        abc123          2.0 GB    2 days ago\n"
                "gemma4:e4b      def456          4.0 GB    1 day ago\n"
            ),
            stderr="",
        )

        helpers.ensure_model_available("gemma4:e4b")
        helpers.ensure_model_available("gemma4:e4b")

        mock_run.assert_called_once_with(
            ["ollama", "list"],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )

    @patch("src.shared_utils.subprocess.run")
    def test_missing_model_exits_with_pull_guidance(self, mock_run):
        """Missing model should fail with exact pull guidance."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NAME            ID              SIZE      MODIFIED\nllama3.2 abc 2 GB now\n",
            stderr="",
        )

        with pytest.raises(SystemExit) as exc:
            helpers.ensure_model_available("gemma4:e4b")

        assert str(exc.value) == (
            "[ERROR] Model 'gemma4:e4b' not found in Ollama. Make sure Ollama "
            "is installed, on PATH, and running.\n"
            "Run: ollama pull gemma4:e4b"
        )

    @patch("src.shared_utils.subprocess.run", side_effect=FileNotFoundError)
    def test_ollama_command_missing_exits_with_setup_guidance(self, mock_run):
        """Missing ollama executable should fail with setup guidance."""
        with pytest.raises(SystemExit) as exc:
            helpers.ensure_model_available("gemma4:e4b")

        assert str(exc.value) == (
            "[ERROR] Model 'gemma4:e4b' not found in Ollama. Make sure Ollama "
            "is installed, on PATH, and running.\n"
            "Run: ollama pull gemma4:e4b"
        )

    @patch(
        "src.shared_utils.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["ollama", "list"], timeout=15),
    )
    def test_ollama_list_timeout_exits_with_setup_guidance(self, mock_run):
        """Timed out ollama list should fail with setup guidance."""
        with pytest.raises(SystemExit) as exc:
            helpers.ensure_model_available("gemma4:e4b")

        assert str(exc.value) == (
            "[ERROR] Model 'gemma4:e4b' not found in Ollama. Make sure Ollama "
            "is installed, on PATH, and running.\n"
            "Run: ollama pull gemma4:e4b"
        )

    @patch("src.shared_utils.subprocess.run")
    def test_ollama_list_nonzero_exits_with_running_guidance(self, mock_run):
        """Nonzero ollama list should fail with running/setup guidance."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: could not connect to ollama app",
        )

        with pytest.raises(SystemExit) as exc:
            helpers.ensure_model_available("gemma4:e4b")

        assert str(exc.value) == (
            "[ERROR] Model 'gemma4:e4b' not found in Ollama. Make sure Ollama "
            "is installed, on PATH, and running.\n"
            "Details: Error: could not connect to ollama app\n"
            "Run: ollama pull gemma4:e4b"
        )


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
            "Ransomware hits hospital", "Body text"
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

        is_threat, detail = helpers.ai_check_validation("Policy news", "Body text")
        assert is_threat is False
        assert detail == "This is just policy news"

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

        is_threat, detail = helpers.ai_check_validation("Title", "Body")
        assert is_threat is False

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_json_parse_error(self, mock_post):
        """Test handling of invalid JSON response"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "not valid json"}
        mock_post.return_value = mock_response

        is_threat, detail = helpers.ai_check_validation("Title", "Body")
        assert is_threat is False
        assert detail == "Parsing Error"

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_request_exception(self, mock_post):
        """Test handling of request exceptions"""
        mock_post.side_effect = requests.RequestException("Connection error")

        is_threat, detail = helpers.ai_check_validation("Title", "Body")
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

        is_threat, detail = helpers.ai_check_validation("Drug shortage", "Body")
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

        is_threat, detail = helpers.ai_check_validation("Device shortage", "Body")
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

        is_threat, detail = helpers.ai_check_validation("Hurricane", "Body")
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

        helpers.ai_check_validation("Title", "Body")
        call_args = mock_post.call_args
        assert call_args[0][0] == helpers.AI_URL

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

        helpers.ai_check_validation("Title", "Body")
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["model"] == helpers.AI_MODEL


class TestExtractFields:
    """Test suite for extract_fields function"""

    def test_extract_fields_invalid_subsector(self):
        """Test with invalid subsector exits."""
        with pytest.raises(SystemExit):
            helpers.extract_fields("invalid_subsector", "Title", "Body")

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
            "drug_shortage", "Drug shortage", "Body"
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

        _, subsector_data = helpers.extract_fields("cyber_attack", "Ransomware", "Body")
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
            "medical_device_shortage", "Device shortage", "Body"
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
            "natural_disaster", "Hurricane", "Body"
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

        _, subsector_data = helpers.extract_fields("other", "Staff shortage", "Body")
        assert subsector_data["event_type"] == "Staff shortage"
        assert subsector_data["severity"] == "High"

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_request_exception(self, mock_post):
        """Test handling of request exceptions."""
        mock_post.side_effect = requests.RequestException("Connection error")

        sector_data, subsector_data = helpers.extract_fields(
            "drug_shortage", "Title", "Body"
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
            "drug_shortage", "Title", "Body"
        )
        assert all(v is None for v in sector_data.values())
        assert all(v is None for v in subsector_data.values())

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_uses_json_format(self, mock_post):
        """Test that JSON format is requested."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": json.dumps({"drug_name": None})}
        mock_post.return_value = mock_response

        helpers.extract_fields("drug_shortage", "Title", "Body")
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["format"] == "json"

    @patch("src.shared_utils.requests.post")
    def test_extract_fields_low_temperature(self, mock_post):
        """Test that low temperature is used for deterministic extraction."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": json.dumps({"drug_name": None})}
        mock_post.return_value = mock_response

        helpers.extract_fields("drug_shortage", "Title", "Body")
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["options"]["temperature"] == 0.0


class TestRunBertAndUseBert:
    """Test suite for _run_bert and ai_check_validation(use_bert=True)."""

    def test_run_bert_delegates_to_run_bert_inference(self, monkeypatch):
        """Test that _run_bert imports and calls run_bert_inference with mock data."""
        fake_module = types.ModuleType("src.GDELT.BERT_filter")
        mock_run_bert_inference = MagicMock(return_value="cyber_attack")
        fake_module.run_bert_inference = mock_run_bert_inference
        monkeypatch.setitem(sys.modules, "src.GDELT.BERT_filter", fake_module)

        result = helpers._run_bert("Ransomware hits hospital", "Body text")

        assert result == "cyber_attack"
        mock_run_bert_inference.assert_called_once_with(
            {"title": "Ransomware hits hospital", "body": "Body text"}
        )

    @patch("src.shared_utils.requests.post")
    def test_ai_check_validation_use_bert_rejects_none_without_llm(
        self, mock_post, monkeypatch
    ):
        """Test that use_bert returns early when BERT rejects the article."""
        monkeypatch.setattr(helpers, "_run_bert", MagicMock(return_value="none"))

        is_threat, detail = helpers.ai_check_validation(
            "Policy news", "Body text", use_bert=True
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
            "Ransomware hits hospital", "Body text", use_bert=True
        )

        assert is_threat is True
        assert detail == "cyber_attack"
        mock_post.assert_called_once()
