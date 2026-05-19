import csv
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.classes.vulnerability import Vulnerability
from src.scrapers import shared_utils


def test_get_page_fetches_with_project_headers():
    """Test that get_page calls requests.get with the shared headers and timeout."""
    mock_response = MagicMock(name="response")
    mock_response.raise_for_status.return_value = None
    with patch(
        "src.scrapers.shared_utils.requests.get", return_value=mock_response
    ) as mock_get:
        result = shared_utils.get_page("https://example.com/article")

        assert result is mock_response
        mock_get.assert_called_once_with(
            "https://example.com/article",
            timeout=15,
            headers=shared_utils.HEADERS,
        )


def test_site_filename_strips_whitespace():
    """Test that _site_filename trims leading and trailing whitespace."""
    assert shared_utils._site_filename("  HealthITSecurity  ") == "HealthITSecurity"


def test_check_valid_file_creates_expected_files(tmp_path, monkeypatch):
    """Test that check_valid_file creates the json and csv files with headers."""
    ready_for_rag_dir = tmp_path / "processed"
    noise_dir = tmp_path / "noise"
    vulnerabilities_dir = tmp_path / "vulnerabilities"
    monkeypatch.setattr(shared_utils, "READY_FOR_RAG_DIR", ready_for_rag_dir)
    monkeypatch.setattr(shared_utils, "NOISE_DIR", noise_dir)
    monkeypatch.setattr(shared_utils, "VULNERABILITIES_DIR", vulnerabilities_dir)

    shared_utils.check_valid_file("  Example Site  ")

    json_path = ready_for_rag_dir / "Example Site.json"
    noise_path = noise_dir / "Example Site.csv"
    vuln_path = vulnerabilities_dir / "Example Site.csv"

    assert json.loads(json_path.read_text(encoding="utf-8")) == {"sources": []}
    assert list(csv.reader(noise_path.read_text(encoding="utf-8").splitlines())) == [
        shared_utils.NOISE_CSV_HEADER
    ]
    assert list(csv.reader(vuln_path.read_text(encoding="utf-8").splitlines())) == [
        shared_utils.VULN_CSV_HEADER
    ]


def test_json_output_appends_vulnerability_payload(tmp_path, monkeypatch):
    """Test that json_output appends a vulnerability dict into the target file."""
    ready_for_rag_dir = tmp_path / "processed"
    ready_for_rag_dir.mkdir(parents=True)
    monkeypatch.setattr(shared_utils, "READY_FOR_RAG_DIR", ready_for_rag_dir)

    vuln = Vulnerability(
        id="1",
        title="Hospital breach",
        source_name="Example Site",
        direct_link="https://example.com/article",
        subsector="cyber_attack",
        date_accessed="2026-05-19",
        date_published="2026-05-18",
        content="Article body",
        exec_summary="Summary",
    )
    json_path = ready_for_rag_dir / "Example Site.json"
    json_path.write_text(json.dumps({"sources": []}), encoding="utf-8")

    shared_utils.json_output(vuln)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["sources"] == [vuln.to_dict()]


def test_vuln_output_writes_expected_csv_row(tmp_path, monkeypatch):
    """Test that vuln_output writes a single CSV row with a truncated preview."""
    vulnerabilities_dir = tmp_path / "vulnerabilities"
    vulnerabilities_dir.mkdir(parents=True)
    monkeypatch.setattr(shared_utils, "VULNERABILITIES_DIR", vulnerabilities_dir)

    vuln = Vulnerability(
        id="1",
        title="Device shortage",
        source_name="Example Site",
        direct_link="https://example.com/device",
        subsector="medical_device_shortage",
        date_accessed="2026-05-19",
        date_published="2026-05-18",
        content="x" * 300 + "\nrest",
        exec_summary="Summary",
    )

    shared_utils.vuln_output(vuln)

    rows = list(
        csv.reader(
            (vulnerabilities_dir / "Example Site.csv")
            .read_text(encoding="utf-8")
            .splitlines()
        )
    )
    assert rows == [
        [
            "2026-05-19",
            "2026-05-18",
            "Example Site",
            "medical_device_shortage",
            "Device shortage",
            "https://example.com/device",
            "Summary",
            "x" * 250,
        ]
    ]


def test_noise_output_writes_expected_csv_row(tmp_path, monkeypatch):
    """Test that noise_output writes a row with a timestamped preview."""
    noise_dir = tmp_path / "noise"
    noise_dir.mkdir(parents=True)
    monkeypatch.setattr(shared_utils, "NOISE_DIR", noise_dir)

    mock_now = MagicMock()
    mock_now.strftime.return_value = "2026-05-19 10:30"
    with patch("src.scrapers.shared_utils.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = mock_now
        shared_utils.noise_output(
            "Example Site",
            "Ignored article",
            "https://example.com",
            "body\ntext",
            "irrelevant",
        )

    rows = list(
        csv.reader(
            (noise_dir / "Example Site.csv").read_text(encoding="utf-8").splitlines()
        )
    )
    assert rows == [
        [
            "2026-05-19 10:30",
            "Example Site",
            "Ignored article",
            "https://example.com",
            "irrelevant",
            "body text",
        ]
    ]


def test_build_page_url_uses_base_url_for_starting_page():
    """Test that build_page_url returns the base URL for the starting page."""
    assert (
        shared_utils.build_page_url(
            {"url": "https://example.com", "map": {}}, 1, 1, "page"
        )
        == "https://example.com"
    )


def test_build_page_url_uses_mapped_page_param():
    """Test that build_page_url uses the configured page param when present."""
    assert (
        shared_utils.build_page_url(
            {"url": "https://example.com/articles", "map": {"page_param": "p"}},
            3,
            1,
            "page",
        )
        == "https://example.com/articles?p=3"
    )


def test_build_page_url_falls_back_to_default_page_param():
    """Test that build_page_url falls back to the default page parameter."""
    assert (
        shared_utils.build_page_url(
            {"url": "https://example.com/articles", "map": {}}, 2, 1, "page"
        )
        == "https://example.com/articles?page=2"
    )
