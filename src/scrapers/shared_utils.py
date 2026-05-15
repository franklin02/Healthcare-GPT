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
import datetime
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

# CSV column orders. Vulnerabilities = "real" disruptions; Noise = everything
# the AI rejected. Kept intentionally small so we can scan them easier
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


def json_output(vuln: Vulnerability) -> None:
    """
    Writes a new vulnerability entry to a JSON file and logs the processed vulnerability.

    Parameters:
        vuln (Vulnerability): The vulnerability object to be added, containing required metadata.

    Functionality:
        - Computes the target JSON file path based on the source name of the vulnerability.
        - Reads the existing data from the JSON file.
        - Converts the vulnerability object into a dictionary format and appends it to the "sources" list in the JSON data.
        - Writes the updated JSON data back to the file with proper indentation for readability.
        - Prints a log message containing the validity, subsector, and title of the vulnerability.

    Dependencies:
        - Assumes the directory READY_FOR_RAG_DIR and its file path exists for reading and writing.
        - Requires the Vulnerability object to implement a "to_dict" method.
    """
    json_path = READY_FOR_RAG_DIR / f"{_site_filename(vuln.source_name)}.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["sources"].append(vuln.to_dict())
    json_path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    print(f"[VALID] ({vuln.subsector}): {vuln.title}")


def vuln_output(vuln: Vulnerability) -> None:
    """
    Writes vulnerability data to a CSV file. Each row in the file represents a single vulnerability entry.

    Args:
        vuln: A Vulnerability object containing details such as date accessed, date published, source name, subsector, title, direct link, executive summary, and content.

    The function determines the output file path based on the vulnerability's source name. It creates or appends the vulnerability data as a row in a CSV file. A preview of the content is included in the CSV file, limited to 250 characters with newlines removed.
    """
    csv_path = VULNERABILITIES_DIR / f"{_site_filename(vuln.source_name)}.csv"
    content_preview = (vuln.content or "")[:250].replace("\n", " ")
    row = [
        vuln.date_accessed,
        vuln.date_published,
        vuln.source_name,
        vuln.subsector,
        vuln.title,
        vuln.direct_link,
        vuln.exec_summary,
        content_preview,
    ]
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


def noise_output(site_name: str, title: str, url: str, body: str, reason: str) -> None:
    """
    Logs details about a site and its associated noise to a CSV file.

    Parameters:
        site_name (str): Name of the site being logged.
        title (str): Title of the content or issue being noted.
        url (str): URL associated with the site or issue.
        body (str): Content body or description, truncated to 250 characters.
        reason (str): Reason for categorizing the content or issue as noise.

    Writes a row in a CSV file specific to the site, containing the current timestamp, site name, title, URL, reason, and a preview of the content body.
    """
    csv_path = NOISE_DIR / f"{_site_filename(site_name)}.csv"
    body_preview = (body or "")[:250].replace("\n", " ")
    row = [
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        site_name,
        title,
        url,
        reason,
        body_preview,
    ]
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


def build_page_url(site_config, current_page, starting_page, default_page_param):
    """
    Builds a complete page URL based on the site configuration, current page, starting page, and default page parameter.

    Parameters:
        site_config: A dictionary containing site-specific configurations including the base URL and parameter mappings.
        current_page: An integer representing the current page number.
        starting_page: An integer representing the starting page number.
        default_page_param: A string representing the default parameter name for the page value if not specified in site_config.

    Returns:
        A string containing the full URL for the current page. If the current page is the same as the starting page, returns the base URL without any page parameter.
    """
    if current_page == starting_page:
        return site_config["url"]

    page_param = site_config["map"].get("page_param", default_page_param)
    return f"{site_config['url']}?{page_param}={current_page}"
