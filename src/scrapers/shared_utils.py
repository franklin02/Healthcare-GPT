"""Provide shared utility functions for file validation, data processing, and URL handling in the application.

This module provides utility functions and constants that assist in processing and managing data for the application.
It includes functions for fetching news articles, extracting key information, and classifying them into different categories.
Including page fetching, validation of files, JSON and CSV outputs, and URL construction.
The shared utilities aim to simplify and streamline repetitive tasks or operations across the project.

Attributes:
    - `_sys`: System-related functionality or constant.
    - `_PROJECT_ROOT`: Specifies the project's root directory.
    - `READY_FOR_RAG_DIR`: Directory designated for resources ready for retrieval-augmented generation (RAG).
    - `NOISE_DIR`: Directory for storing noise data.
    - `VULNERABILITIES_DIR`: Directory for storing vulnerabilities data.
    - `HEADERS`: Headers for HTTP-related tasks.
    - `VULN_CSV_HEADER`: Header for the vulnerabilities CSV file.
    - `NOISE_CSV_HEADER`: Header for the noise CSV file.


Functions:
    - `get_page`: Retrieves web page content for a given URL, handling HTTP requests.
    - `_site_filename`: Generates or retrieves specific filename associated with a site.
    - `check_valid_file`: Validates files against specific criteria.
    - `json_output`: Outputs data in JSON format.
    - `vuln_output`: Processes and generates output related to vulnerabilities.
    - `noise_output`: Processes and generates output related to noise.
    - `build_page_url`: Constructs URLs for web pages based on given parameters.
"""

import json
import csv
import os
import tempfile
import requests
from pathlib import Path
import sys as _sys

# Anchor to the project root so this works both as `scrapers.shared_utils`
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))
from src.classes import Vulnerability


READY_FOR_RAG_DIR = _PROJECT_ROOT / "data" / "processed"
NOISE_DIR = _PROJECT_ROOT / "data" / "noise"
VULNERABILITIES_DIR = _PROJECT_ROOT / "data" / "vulnerabilities"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


VULN_CSV_HEADER = [
    "date_accessed",
    "date_published",
    "source_name",
    "subsector",
    "title",
    "direct_link",
    "exec_summary",
    "content_preview",
]

NOISE_CSV_HEADER = [
    "date_accessed",
    "source_name",
    "title",
    "url",
    "reason",
    "body_preview",
]


def get_page(url):
    """
    Fetches the content of a web page for the given URL.

    Parameters:
        url (str): The URL of the web page to fetch.

    Returns:
        requests.Response: The response object containing the web page content.

    Raises:
        requests.exceptions.HTTPError: If the HTTP request returned an unsuccessful status code.
        requests.exceptions.RequestException: For any issues during the HTTP request such as timeouts or connection errors.
    """
    resp = requests.get(url, timeout=15, headers=HEADERS)
    resp.raise_for_status()
    return resp


def _site_filename(site_name: str) -> str:
    """
    Generates a sanitized site filename by trimming leading and trailing whitespace from the given site name.

    Parameters:
        site_name (str): The name of the site as a string.

    Returns:
        str: The trimmed site name with whitespace removed.
    """
    return site_name.strip()


def check_valid_file(site_name):
    """
    Checks for the existence of required files and directories for the given site name. If the required files do not exist, creates them with appropriate initial content.

    Parameters:
        site_name (str): The name of the site used to generate file names and structure.

    Function Logic:
        - Ensures the directories READY_FOR_RAG_DIR, NOISE_DIR, and VULNERABILITIES_DIR exist by creating them if necessary.
        - Constructs a file stem using the supplied site_name with the help of the `_site_filename` function.
        - Checks if a .json file for the site exists in READY_FOR_RAG_DIR. If not, creates the file with a default JSON structure.
        - Checks if a .csv file for the site exists in NOISE_DIR. If not, creates an empty file with a header row defined by NOISE_CSV_HEADER.
        - Checks if a .csv file for the site exists in VULNERABILITIES_DIR. If not, creates an empty file with a header row defined by VULN_CSV_HEADER.
        - Prints messages to indicate the creation of new files when applicable.

    """
    READY_FOR_RAG_DIR.mkdir(parents=True, exist_ok=True)
    NOISE_DIR.mkdir(parents=True, exist_ok=True)
    VULNERABILITIES_DIR.mkdir(parents=True, exist_ok=True)

    stem = _site_filename(site_name)

    json_path = READY_FOR_RAG_DIR / f"{stem}.json"
    if not json_path.exists():
        json_path.write_text(json.dumps({"sources": []}, indent=4), encoding="utf-8")
        print(f"Created {json_path}")

    noise_path = NOISE_DIR / f"{stem}.csv"
    if not noise_path.exists():
        with open(noise_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(NOISE_CSV_HEADER)
        print(f"Created {noise_path}")

    vulnerabilities_path = VULNERABILITIES_DIR / f"{stem}.csv"
    if not vulnerabilities_path.exists():
        with open(vulnerabilities_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(VULN_CSV_HEADER)
        print(f"Created {vulnerabilities_path}")


def _content_preview(body: str | None) -> str:
    return (body or "")[:250].replace("\n", " ")


def _top_row_matches(
    csv_path: Path,
    title: str,
    body_snippet: str,
    preview_column: str,
) -> bool:
    if not csv_path.exists():
        return False

    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        try:
            first_row = next(reader)
        except StopIteration:
            return False

    if first_row.get("title", "") != title:
        return False

    incoming_preview = _content_preview(body_snippet)
    if first_row.get(preview_column, "") != incoming_preview:
        print(
            f"[WARN] Title matched but body preview differs for {title!r} "
            f"— stopping anyway"
        )
    return True



def is_known_article(site_name: str, title: str, body_snippet: str) -> bool:
    site = _site_filename(site_name)
    if _top_row_matches(
        VULNERABILITIES_DIR / f"{site}.csv", title, body_snippet, "content_preview"
    ):
        return True
    if _top_row_matches(
        NOISE_DIR / f"{site}.csv", title, body_snippet, "body_preview"
    ):
        return True
    return False



def prepend_vuln_csv(site_name: str, new_rows: list[list[str]]) -> None:
    if not new_rows:
        return

    csv_path = VULNERABILITIES_DIR / f"{_site_filename(site_name)}.csv"
    existing_data_rows: list[list[str]] = []
    if csv_path.exists():
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                next(reader)  # drop the existing header
            except StopIteration:
                pass
            existing_data_rows = list(reader)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{_site_filename(site_name)}.",
        suffix=".csv.tmp",
        dir=str(VULNERABILITIES_DIR),
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(VULN_CSV_HEADER)
            writer.writerows(new_rows)
            writer.writerows(existing_data_rows)
        os.replace(tmp_path, csv_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise



def prepend_noise_csv(site_name: str, new_rows: list[list[str]]) -> None:
    if not new_rows:
        return

    csv_path = NOISE_DIR / f"{_site_filename(site_name)}.csv"
    existing_data_rows: list[list[str]] = []
    if csv_path.exists():
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                next(reader)  # drop the existing header
            except StopIteration:
                pass
            existing_data_rows = list(reader)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{_site_filename(site_name)}.",
        suffix=".csv.tmp",
        dir=str(NOISE_DIR),
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(NOISE_CSV_HEADER)
            writer.writerows(new_rows)
            writer.writerows(existing_data_rows)
        os.replace(tmp_path, csv_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise



def prepend_json_sources(site_name: str, new_vulns: list[Vulnerability]) -> None:
    if not new_vulns:
        return

    json_path = READY_FOR_RAG_DIR / f"{_site_filename(site_name)}.json"
    if json_path.exists():
        data = json.loads(json_path.read_text(encoding="utf-8"))
    else:
        data = {"sources": []}

    new_dicts = [v.to_dict() for v in new_vulns]
    data["sources"] = new_dicts + data.get("sources", [])

    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{_site_filename(site_name)}.",
        suffix=".json.tmp",
        dir=str(READY_FOR_RAG_DIR),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, json_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


