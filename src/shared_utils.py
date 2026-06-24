"""Provide shared utility functions for file validation, data processing, and URL handling in the application.

This module provides utility functions and constants that assist in processing and managing data for the application.
It includes functions for fetching news articles, extracting key information, and classifying them into different categories.
Including page fetching, validation of files, JSON and CSV outputs, and URL construction.
The shared utilities aim to simplify and streamline repetitive tasks or operations across the project.

Attributes:
    - `AI_MODEL`: The specific model that the AI will use for processing.
    - `_PROJECT_ROOT`: Specifies the project's root directory.
    - `READY_FOR_RAG_DIR`: Directory designated for resources ready for retrieval-augmented generation (RAG).
    - `NOISE_DIR`: Directory for storing noise data.
    - `VULNERABILITIES_DIR`: Directory for storing vulnerabilities data.
    - `HEADERS`: Headers for HTTP-related tasks.
    - `VULN_CSV_HEADER`: Header for the vulnerabilities CSV file.
    - `NOISE_CSV_HEADER`: Header for the noise CSV file.
    - `SUBSECTOR_FIELDS`: A dictionary that maps subsectors to their specific fields.

Functions:
    - `get_config_value`: Retrieves a configuration value by name, with an optional default.
    - `get_config_bool`: Retrieves a boolean configuration value by name, with an optional default.
    - `get_config_int`: Retrieves an integer configuration value by name, with an optional default.
    - `get_config_date`: Retrieves an ISO-formatted date configuration value by name, with an optional default.
    - `get_page`: Retrieves web page content for a given URL, handling HTTP requests.
    - `_site_filename`: Generates or retrieves specific filename associated with a site.
    - `check_valid_file`: Validates files against specific criteria.
    - `json_output`: Outputs data in JSON format.
    - `vuln_output`: Processes and generates output related to vulnerabilities.
    - `noise_output`: Processes and generates output related to noise.
    - `build_page_url`: Constructs URLs for web pages based on given parameters.
    - `ai_check_validation`: Parses and verifies whether a healthcare-related article describes an ongoing operational disruption or confirmed breach
       at a named healthcare entity based on strict, predefined criteria.
    - `get_extraction_template`: Builds a typed JSON extraction template scoped to a single subsector, mapping each field to its expected primitive type.
    - `extract_fields`: Extracts subsector-specific metadata from a confirmed disruption article by prompting the LLM with a typed, subsector-scoped template.


Possible subsectors:
        - "drug_shortage": A confirmed shortage of a named drug patients need now.
        - "medical_device_shortage": A confirmed inability to supply a specific named medical device.
        - "cyber_attack": A confirmed breach or attack involving a named healthcare entity.
        - "natural_disaster": Operational shutdowns due to fire, flood, storm, or other physical events.
        - "other": Other confirmed operational disruptions that do not fit the previous categories.
        - "none": Used when no operational disruption or breach is confirmed.

"""

import csv
import datetime
import json
import os
import re
import subprocess
import tempfile
import shutil
import pandas as pd
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .classes import SUBSECTOR_DATA_CLASSES, Vulnerability
from .logging_utils import get_file_logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "src" / "config" / "config.cfg"


def _load_config_file() -> dict[str, str]:
    """
    Load configuration values from the config.cfg file into a dictionary.

    Returns:
        dict[str, str]: A dictionary containing configuration key-value pairs. If the config file does not exist, returns an empty dictionary.
    """
    values: dict[str, str] = {}
    if not _CONFIG_PATH.exists():
        return values

    for raw_line in _CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    return values


_CONFIG = _load_config_file()


def get_config_value(name: str, default: str | None = None) -> str | None:
    """
    Retrieve a configuration value by name, with an optional default.

    Parameters:
        name (str): The name of the configuration variable to retrieve.
        default (str | None): An optional default value to return if the configuration variable is not set or is empty. Defaults to None.

    Returns:
        str | None: The value of the configuration variable if it exists and is not empty; otherwise, returns the provided default value.
    """
    value = _CONFIG.get(name)
    if value is None or value == "":
        return default
    return value


def get_config_bool(name: str, default: bool = False) -> bool:
    """
    Retrieve a boolean configuration value by name, with an optional default.

    Parameters:
        name (str): The name of the configuration variable to retrieve.
        default (bool): An optional default value to return if the configuration variable is not set or is empty. Defaults to False.

    Returns:
        bool: The boolean value of the configuration variable if it exists and is not empty; otherwise, returns the provided default value. The function interprets "true" and "yes" (case-insensitive) as True, and any other non-empty value as False.
    """
    value = get_config_value(name)
    if value is None:
        return default
    return value.lower() in {"true", "yes"}


def get_config_int(name: str, default: int | None = None) -> int | None:
    """
    Retrieve an integer configuration value by name, with an optional default.

    Parameters:
        name (str): The name of the configuration variable to retrieve.
        default (int | None): An optional default value to return if the configuration variable is not set or is empty. Defaults to None.

    Returns:
        int | None: The integer value of the configuration variable if it exists and is not empty; otherwise, returns the provided default value.
    """
    value = get_config_value(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_config_date(
    name: str, default: datetime.date | None = None
) -> datetime.date | None:
    """
    Retrieve an ISO-formatted date configuration value by name, with an optional default.

    Parameters:
        name (str): The name of the configuration variable to retrieve.
        default (datetime.date | None): An optional default value to return if the configuration variable is not set, empty, or not a valid ISO date. Defaults to None.

    Returns:
        datetime.date | None: The parsed date if the configuration variable exists and is a valid ``YYYY-MM-DD`` string; otherwise, returns the provided default value.
    """
    value = get_config_value(name)
    if value is None:
        return default
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return default


AI_URL = get_config_value("AI_URL", "http://localhost:11434/api/generate")
AI_MODEL = get_config_value("AI_MODEL", "llama3.2:latest")
MIN_BODY_CHARS_FOR_LLM = get_config_int("MIN_BODY_CHARS_FOR_LLM", 150) or 150
BODY_CHAR_LIMIT = get_config_int("BODY_CHAR_LIMIT", 4000) or 4000

LOG_FILE = _PROJECT_ROOT / "data" / "logs" / "shared_utils.log"
LOGGER = get_file_logger(__name__, LOG_FILE)

# temporary for now, to be removed later
READY_FOR_RAG_DIR = _PROJECT_ROOT / "data" / "processed"
NOISE_DIR = _PROJECT_ROOT / "data" / "noise"
VULNERABILITIES_DIR = _PROJECT_ROOT / "data" / "vulnerabilities"

_NOISE_PATTERNS = (
    "ad",
    "advert",
    "promo",
    "sidebar",
    "related",
    "newsletter",
    "subscribe",
    "comment",
    "social",
    "share",
    "cookie",
)
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)
_NOISE_SELECTOR = ",".join(f'[class*="{p}"]' for p in _NOISE_PATTERNS)
_BODY_BOILERPLATE_PATTERNS = re.compile(
    r"skip to main content|copyright|terms of use|privacy policy|powered by|"
    r"content management system|blox digital|subscribe|sign in|log in|search|menu|close",
    re.IGNORECASE,
)
_TITLE_SITE_SUFFIX_RE = re.compile(r"(?:\s*[|–—]\s+[^|–—]+|\s-\s+[^-]+)$")

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

LLM_SECTOR_FIELDS = [
    "exec_summary",
    "geography_scope",
    "start_date",
    "end_date",
    "resilience_or_mitigation_observed",
]

SUBSECTOR_FIELDS = {
    "drug_shortage": [
        "drug_name",
        "generic_name",
        "manufacturer",
        "dosage_form",
        "shortage_reason",
        "estimated_resolution_date",
        "affected_regions",
        "domestic_vs_foreign_dependency",
    ],
    "medical_device_shortage": [
        "device_name",
        "device_category",
        "manufacturer",
        "manufacturer_country",
        "shortage_reason",
        "fda_recall_number",
        "recall_class",
        "affected_specialties",
        "alternatives_available",
        "estimated_resolution_date",
        "domestic_vs_foreign_dependency",
    ],
    "cyber_attack": [
        "attack_type",
        "threat_actor",
        "individuals_affected",
        "data_types_exposed",
        "systems_affected",
        "ransom_demanded_usd",
        "ransom_paid",
        "downtime_days",
        "services_disrupted",
        "law_enforcement_involved",
        "hhs_breach_portal_listed",
    ],
    "natural_disaster": [
        "disaster_type",
        "disaster_name",
        "fema_declaration_id",
        "category_magnitude",
        "affected_facilities_count",
        "evacuation_ordered",
        "field_hospitals",
        "beds_offline",
        "facility_status",
        "estimated_damage_usd",
        "infrastructure_damage",
        "services_disrupted",
    ],
    "other": [
        "event_type",
        "event_description",
        "severity",
        "departments_affected",
        "staff_type_affected",
        "beds_offline",
        "services_disrupted",
        "regulatory_response",
    ],
}

SUBSECTOR_FIELD_GUIDANCE = {
    "drug_shortage": [
        '- "drug_name": brand, marketed, or named drug product stated as being in shortage; null if not stated.',
        '- "generic_name": non-brand drug name, generic name, chemical name, or active ingredient name stated for the drug. If the article says one drug name is sold under another brand name, put the sold-under name in "generic_name" and the brand name in "drug_name"; null if not stated.',
        '- "manufacturer": company stated as making, producing, halting production of, recalling, or supplying the drug; null if not stated.',
        '- "dosage_form": stated formulation, strength, route, or presentation of the drug product; null if not stated.',
        '- "shortage_reason": stated cause or reason for the shortage; null if not stated.',
        '- "estimated_resolution_date": date when the article says supply is expected to recover, resume, resolve, or return; null if not stated.',
        '- "affected_regions": list of article-stated places, regions, states, territories, or markets affected by the shortage; null if not stated.',
        '- "domestic_vs_foreign_dependency": whether the article states supply depends on domestic sources, foreign sources, or both; null if not stated.',
    ],
    "medical_device_shortage": [
        '- "device_name": named medical device, product, model, or system stated as being in shortage, recall, or supply disruption; null if not stated.',
        '- "device_category": stated clinical or product category for the device; null if not stated.',
        '- "manufacturer": company stated as making, producing, recalling, or supplying the device; null if not stated.',
        '- "manufacturer_country": country where the article says the product or device is manufactured or made; null if not stated.',
        '- "shortage_reason": stated cause or reason for the device shortage, recall, or supply disruption; null if not stated.',
        '- "fda_recall_number": formal recall identifier stated by the article or regulator; null if not stated.',
        '- "recall_class": formal recall classification stated by the article or regulator; null if not stated.',
        '- "affected_specialties": list of article-stated clinical specialties, departments, or care areas affected by the device issue; null if not stated.',
        '- "alternatives_available": true only when the article explicitly states alternatives are available, false only when it explicitly states they are not, and null if not stated.',
        '- "estimated_resolution_date": date when the article says corrected devices, supply, shipments, or availability are expected to resume or resolve; null if not stated.',
        '- "domestic_vs_foreign_dependency": whether the article states supply depends on domestic sources, foreign sources, or both; null if not stated.',
    ],
    "cyber_attack": [
        '- "attack_type": stated kind of cyber incident, such as the article-stated attack, breach, intrusion, outage, or unauthorized-access type; null if not stated.',
        '- "threat_actor": named or described actor, group, or party the article states carried out, claimed, caused, or was attributed to the incident; null if not stated.',
        '- "individuals_affected": raw number of people, patients, members, employees, individuals, or records the article says were affected or exposed; null if not stated.',
        '- "data_types_exposed": list of article-stated data categories exposed, accessed, stolen, compromised, or included in affected data; null if not stated.',
        '- "systems_affected": list of article-stated technology, operational, clinical, payment, imaging, billing, network, or administrative systems affected; null if not stated.',
        '- "ransom_demanded_usd": raw dollar amount the article says was demanded, requested, or sought as ransom; null if not stated.',
        '- "ransom_paid": true only when the article explicitly states a ransom was paid, false only when it explicitly states a ransom was not paid, and null if not stated.',
        '- "downtime_days": raw number of days systems or services were offline, unavailable, down, or disrupted; null if not stated.',
        '- "services_disrupted": list of article-stated care, business, payment, pharmacy, claims, scheduling, laboratory, imaging, emergency, or operational services disrupted; null if not stated.',
        '- "law_enforcement_involved": true only when the article explicitly states law enforcement was notified, contacted, involved, or investigating, false only when it explicitly states no involvement, and null if not stated.',
        '- "hhs_breach_portal_listed": true only when the article explicitly states the breach was listed, posted, or reported on the HHS breach portal, false only when it explicitly states it was not, and null if not stated.',
    ],
    "natural_disaster": [
        '- "disaster_type": stated type of natural or physical disaster causing the healthcare impact; use the disaster kind, not its proper name; null if not stated.',
        '- "disaster_name": formal name of the disaster or event stated in the article; null if not stated.',
        '- "fema_declaration_id": formal disaster declaration identifier stated in the article; null if not stated.',
        '- "category_magnitude": stated category, magnitude, scale, intensity, classification, or severity level of the disaster; null if not stated.',
        '- "affected_facilities_count": raw number of healthcare facilities the article says were affected; null if not stated.',
        '- "evacuation_ordered": true only when the article explicitly states an evacuation was ordered, false only when it explicitly states none was ordered, and null if not stated.',
        '- "field_hospitals": raw number of field hospitals, temporary care sites, or alternate care facilities stated in the article; null if not stated.',
        '- "beds_offline": raw number of beds the article says were offline, unavailable, closed, or removed from service; null if not stated.',
        '- "facility_status": article-stated operational status of affected healthcare facilities; null if not stated.',
        '- "estimated_damage_usd": raw dollar amount of damage to healthcare facilities or operations stated in the article; null if not stated.',
        '- "infrastructure_damage": list of article-stated physical, utility, building, equipment, or infrastructure damage affecting healthcare operations; null if not stated.',
        '- "services_disrupted": list of article-stated healthcare, clinical, pharmacy, emergency, transport, or operational services disrupted; null if not stated.',
    ],
    "other": [
        '- "event_type": stated type of non-drug, non-device, non-cyber, non-disaster healthcare disruption; null if not stated.',
        '- "event_description": article-stated factual description of the disruptive event; null if not stated.',
        '- "severity": article-stated severity, impact level, emergency status, or seriousness of the event; null if not stated.',
        '- "departments_affected": list of article-stated departments, units, specialties, or care areas affected; null if not stated.',
        '- "staff_type_affected": article-stated staff role, workforce group, or personnel type affected; null if not stated.',
        '- "beds_offline": raw number of beds the article says were offline, unavailable, closed, or removed from service; null if not stated.',
        '- "services_disrupted": list of article-stated healthcare, clinical, administrative, transport, or operational services disrupted; null if not stated.',
        '- "regulatory_response": article-stated response, order, investigation, notice, or action by a regulator or public authority; null if not stated.',
    ],
}


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
    LOGGER.debug("Successfully fetched URL: %s", url)
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
        LOGGER.debug("Created JSON file for site %s at %s", site_name, json_path)

    noise_path = NOISE_DIR / f"{stem}.csv"
    if not noise_path.exists():
        with open(noise_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(NOISE_CSV_HEADER)
        LOGGER.debug("Created noise CSV file for site %s at %s", site_name, noise_path)

    vulnerabilities_path = VULNERABILITIES_DIR / f"{stem}.csv"
    if not vulnerabilities_path.exists():
        with open(vulnerabilities_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(VULN_CSV_HEADER)
        LOGGER.debug(
            "Created vulnerabilities CSV file for site %s at %s",
            site_name,
            vulnerabilities_path,
        )


def _content_preview(body: str | None) -> str:
    """
    Parameter:
        body (str): the entire body of an article

    Returns:
        First 250 characters of the article (success)
    """
    return (body or "")[:250].replace("\n", " ")


def _top_row_matches(
    csv_path: Path,
    title: str,
    body_snippet: str,
    preview_column: str,
) -> bool:
    """
    Not documenting on purpose, this function will likely be deleted in the near future
    """

    if not csv_path.exists():
        LOGGER.debug("CSV file %s does not exist, cannot match top row", csv_path)
        return False

    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        try:
            first_row = next(reader)
        except StopIteration:
            LOGGER.debug("CSV file %s is empty, cannot match top row", csv_path)
            return False

    if first_row.get("title", "") != title:
        LOGGER.debug(
            "Top row title %s does not match incoming title %s in file %s",
            first_row.get("title", ""),
            title,
            csv_path,
        )
        return False

    incoming_preview = _content_preview(body_snippet)
    if first_row.get(preview_column, "") != incoming_preview:
        LOGGER.warning("Body preview differs for title %s", title)
    return True


def is_known_article(site_name: str, title: str, body_snippet: str) -> bool:
    """
    Not documenting on purpose, this function will likely be deleted in the near future
    """
    site = _site_filename(site_name)
    if _top_row_matches(
        VULNERABILITIES_DIR / f"{site}.csv", title, body_snippet, "content_preview"
    ):
        return True
    if _top_row_matches(NOISE_DIR / f"{site}.csv", title, body_snippet, "body_preview"):
        return True
    return False


def prepend_vuln_csv(site_name: str, new_rows: list[list[str]]) -> None:
    """
    Not documenting on purpose, this function will likely be deleted in the near future
    """
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
        LOGGER.error(
            "Failed to prepend to vulnerabilities CSV for site %s: %s",
            site_name,
            exc_info=True,
        )
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def prepend_noise_csv(site_name: str, new_rows: list[list[str]]) -> None:
    """
    Not documenting on purpose, this function will likely be deleted in the near future
    """
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
        LOGGER.error(
            "Failed to prepend to noise CSV for site %s: %s", site_name, exc_info=True
        )
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def prepend_json_sources(site_name: str, new_vulns: list[Vulnerability]) -> None:
    """
    Not documenting on purpose, this function will likely be deleted in the near future
    """
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
        LOGGER.error(
            "Failed to prepend to JSON sources for site %s: %s",
            site_name,
            exc_info=True,
        )
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _extract_title_from_soup(soup: BeautifulSoup, fallback_url: str) -> str:
    """Extract and clean the page title from a parsed BeautifulSoup tree.

    Looks for the HTML ``<title>`` tag, strips common site-name suffixes
    (e.g. " | Reuters", " - NBC News"), and falls back to *fallback_url*
    when no usable title is found.

    Args:
        soup: A parsed BeautifulSoup document.
        fallback_url: The value returned when no ``<title>`` tag is present
            or when the tag text is empty.

    Returns:
        str: Cleaned page title, or *fallback_url*.
    """
    title_tag = soup.find("title")
    if title_tag:
        raw = title_tag.get_text(strip=True)
        if raw:
            cleaned = _TITLE_SITE_SUFFIX_RE.sub("", raw).strip()
            return cleaned if cleaned else raw
    return fallback_url


def _extract_body_from_soup(soup: BeautifulSoup, url: str) -> str:
    """Extract the main article text from a parsed BeautifulSoup tree.

    Removes common non-content tags and noisy selectors (ads, sidebars,
    footers), then returns the concatenated paragraph text when available.

    Args:
        soup: A parsed BeautifulSoup document.  **Note:** this function
            mutates the tree by decomposing non-content elements.
        url: The original URL (used only for log messages).

    Returns:
        str: Cleaned article text, or an empty string if no body content
            is found.
    """
    # Strip non-content tags
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "iframe",
            "form",
            "header",
            "footer",
            "nav",
            "aside",
        ]
    ):
        tag.decompose()

    # Strip noise by class
    for el in soup.select(_NOISE_SELECTOR):
        el.decompose()

    # Strip noise by id
    for el in soup.find_all(id=_NOISE_RE):
        el.decompose()

    # Pick the best content container, in order of preference
    main = None
    for candidate in (
        soup.find("article"),
        soup.find("main"),
        soup.find(attrs={"role": "main"}),
        soup.body,
        soup,
    ):
        if candidate and candidate.get_text(strip=True):
            main = candidate
            break

    if main is None:
        LOGGER.warning("No body found for URL %s", url)
        return ""

    def _filtered_text_chunks(node) -> list[str]:
        """
        Extract text chunks from a BeautifulSoup node, filtering out empty strings and boilerplate.

        Parameters:
            node (BeautifulSoup): The BeautifulSoup node to extract text from.

        Returns:
            list[str]: A list of filtered text chunks.
        """
        chunks: list[str] = []
        # Use stripped_strings to get text without extra whitespace, then filter out empty and boilerplate chunks
        for chunk in node.stripped_strings:
            normalized = chunk.strip()
            if not normalized:
                continue
            if _BODY_BOILERPLATE_PATTERNS.search(normalized):
                continue
            chunks.append(normalized)
        LOGGER.debug(
            "Extracted %d filtered text chunks from node in URL %s", len(chunks), url
        )
        return chunks

    # Prefer paragraph text (gives cleaner article body across sites.)
    # Fall back to filtered descendant text when no <p> tags are present.
    content_chunks = [
        p.get_text(" ", strip=True)
        for p in main.find_all("p")
        if p.get_text(" ", strip=True)
        and not _BODY_BOILERPLATE_PATTERNS.search(p.get_text(" ", strip=True))
    ]

    if not content_chunks:
        content_chunks = _filtered_text_chunks(main)

    if content_chunks:
        LOGGER.debug(
            "Extracted %d content chunks from URL %s", len(content_chunks), url
        )
        return "\n\n".join(content_chunks)

    LOGGER.debug("No content chunks found after filtering for URL %s", url)
    return ""


def get_title(url: str) -> str:
    """Fetch and return the page title for a URL.

    Extracts the HTML <title> tag and strips common site-name suffixes
    (e.g. " | Reuters", " - NBC News").  Falls back to the raw URL if no
    usable ``<title>`` tag is found or the request fails.

    Args:
        url (str): The URL to fetch.  ``https://`` is prepended if the scheme
            is missing.

    Returns:
        str: Cleaned page title, or the URL on failure.
    """
    if not url:
        return url

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = requests.get(url, timeout=30, headers=HEADERS)
        resp.raise_for_status()
    except requests.RequestException as e:
        LOGGER.warning("Failed to fetch URL %s: %s", url, e)
        return url

    soup = BeautifulSoup(resp.text, "html.parser")
    return _extract_title_from_soup(soup, url)


def get_body(url: str) -> str:
    """Fetch and return the main article text for a URL.

    The function performs a simple HTML scrape using `requests` and
    `BeautifulSoup`, removes common non-content tags and noisy selectors
    (ads, sidebars, footers), and returns the concatenated paragraph text
    when available. Network errors or missing content return an empty string.

    Args:
        url (str): The URL to fetch. If the scheme is missing, `https://` is
            prepended.

    Returns:
        str: Cleaned article text, or an empty string on error or if no body
            content is found.

    Notes:
        - This is a heuristic extractor and may not work for all sites.
        - The returned body may be long; callers should truncate if needed.
    """
    if not url:
        LOGGER.warning("Empty URL provided to get_body()")
        return ""

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Fetch
    try:
        resp = requests.get(url, timeout=30, headers=HEADERS)
        resp.raise_for_status()
    except requests.RequestException as e:
        LOGGER.warning("Failed to fetch URL %s: %s", url, e)
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")
    return _extract_body_from_soup(soup, url)


def get_body_and_title(url: str) -> tuple[str, str]:
    """Fetch a URL once and return both the article body and cleaned title.

    Combines the work of :func:`get_body` and :func:`get_title` into a
    single HTTP request, halving outbound traffic when both values are
    needed for the same page.

    The title is extracted **before** the body because
    :func:`_extract_body_from_soup` mutates the soup tree by decomposing
    non-content elements (which could remove the ``<title>`` tag).

    Args:
        url: The URL to fetch.  ``https://`` is prepended when the scheme
            is missing.

    Returns:
        A ``(body, title)`` tuple.  On network errors the body is ``""``
        and the title falls back to the (normalised) URL.  On empty /
        missing content the body is ``""`` while the title may still be
        valid.
    """
    if not url:
        LOGGER.warning("Empty URL provided to get_body_and_title()")
        return "", url or ""

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Fetch once
    try:
        resp = requests.get(url, timeout=30, headers=HEADERS)
        resp.raise_for_status()
    except requests.RequestException as e:
        LOGGER.warning("Failed to fetch URL %s: %s", url, e)
        return "", url

    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract title first — _extract_body_from_soup mutates the tree.
    title = _extract_title_from_soup(soup, url)
    body = _extract_body_from_soup(soup, url)

    return body, title


_classifier = None


def _run_bert(title: str, body: str, verbose: bool = False) -> str:
    """
    Run BERT inference on an article. Returns the predicted subsector string or "none".
    Loads the classifier once on first call and reuses it for subsequent articles.
    "potential_hit" from the base model fallback is normalized to "other".
    """
    global _classifier
    try:
        from src.GDELT.BERT_filter import run_bert_inference, load_model
    except ImportError as exc:
        LOGGER.error("Failed to import src.GDELT.BERT_filter module: %s", exc)
        raise RuntimeError("src.GDELT.BERT_filter module not found") from exc

    if _classifier is None:
        _classifier = load_model(verbose=verbose)
        if _classifier is None:
            LOGGER.warning("BERT classifier failed to load, skipping")
            return "none"

    try:
        result = run_bert_inference(
            {"title": title, "body": body}, _classifier, verbose=verbose
        )
    except Exception as e:
        LOGGER.warning("BERT inference failed: %s", e)
        return "none"

    if result == "potential_hit":
        return "other"
    return result


def ai_check_validation(
    title, body, use_bert=False, verbose: bool = False, port: int = 11434
) -> tuple[bool, str]:
    """
    Parses and verifies whether a healthcare-related article describes an ongoing operational disruption or confirmed breach at a named healthcare entity based on strict, predefined criteria.

    Parameters:
        title (str): The title of the article being analyzed.
        body (str): The main content or excerpt of the article.
        use_bert (bool): False by default, calls bert before calling the llm to save time
        port (int): The port on which the ollama server is running

    Returns: A tuple:
        - A boolean indicating whether the article is flagged as a threat (True if operational disruption or confirmed breach).
        - A string providing further details: the subsector if flagged as a disruption or the reason for rejection if not flagged.

    This function sends the article's title and body to an AI system for evaluation. The AI follows explicit rules to assess disruptions or breaches in healthcare. If an operational disruption is identified, the response will specify the subsector such as 'cyber_attack', 'drug_shortage', etc. If not, the output will explain why the article was rejected.

    Exceptions:
    If an error occurs during the request or response parsing, the function catches the error, logs it, and returns False with "Parsing Error".
    """

    body_text = body or ""
    if len(body_text) < MIN_BODY_CHARS_FOR_LLM:
        LOGGER.info(
            "Skipping LLM validation for title %s because body length %d is below %d characters",
            title,
            len(body_text),
            MIN_BODY_CHARS_FOR_LLM,
        )
        return False, "Body too short for LLM review"

    if use_bert:
        bert_subsector = _run_bert(title, body, verbose=verbose)
        if bert_subsector == "none":
            LOGGER.info("BERT rejected article with title %s", title)
            return False, "BERT: unrelated news"
        LOGGER.info("BERT flagged article with title %s as %s", title, bert_subsector)

    prompt = f"""
        You are a strict Healthcare Operations Auditor. Your ONLY job is to flag articles that describe a REAL, ALREADY-OCCURRING healthcare disruption or a CONFIRMED breach at a named healthcare entity.

        DEFAULT TO NO. Reject the article unless the evidence is explicit, named, and concrete. The vast majority of healthcare news is NOT a disruption.

        ===== ACCEPT (mark YES) ONLY IF (A) OR (B) IS TRUE =====

        (A) ACTIVE CARE DISRUPTION — the article states that a NAMED facility (hospital, clinic, pharmacy, lab, healthcare network) is CURRENTLY or RECENTLY:
            - Diverting ambulances, cancelling surgeries, or turning patients away
            - Operating on downtime / paper procedures because EHR is offline
            - Suspending services or evacuating due to fire, flood, storm, or other physical event
            - Physically out of a specific drug or medical device that patients need now (real supply outage, not pricing or formulary debate)
            - Cut off from operations by a workforce strike, power outage, or other concrete event

        (B) CONFIRMED HEALTHCARE BREACH / CYBERATTACK — this rule has TWO parts; if BOTH are true, the article is YES.
            Part 1: The victim is a NAMED healthcare entity. ALL of these qualify as healthcare entities for this rule:
                * hospitals, clinics, health systems, physician groups
                * pharmacies (retail or hospital)
                * health insurers / payers / PBMs
                * MEDICAL DEVICE MANUFACTURERS (e.g. Stryker, TriMed, Medtronic) — yes, they count
                * pharma manufacturers
                * healthcare-specific software / EHR / billing vendors (e.g. Epic, Change Healthcare)
                * clinical labs and diagnostic companies
            Part 2: The incident has ALREADY HAPPENED — confirmed by the entity, a regulator, an HHS breach notice, an SEC 8-K, or a public breach disclosure. Any ONE of the following counts:
                * ransomware / intrusion / unauthorized access confirmed
                * PHI or patient records exposed, exfiltrated, or encrypted
                * Data security incident formally disclosed by the entity
                * Operational systems impacted by the attack
            If Part 1 AND Part 2 are both true → YES, subsector "cyber_attack". This applies even if the article does NOT describe care being stopped. A confirmed PHI breach at a healthcare entity IS the disruption.

        ===== CONCRETE YES EXAMPLES (these MUST be marked YES) =====

        - "Signature Healthcare diverts ambulances amid cyberattack" — named hospital, current diversion -> YES, cyber_attack
        - "TriMed (orthopedic implant maker) confirms data breach exposing patient PHI" — named device manufacturer, confirmed breach with PHI -> YES, cyber_attack
        - "Acme Pharma halts production of injectable epinephrine; pharmacies report shortage" — named drug, real supply outage -> YES, drug_shortage
        - "Hurricane evacuates Memorial Hospital; ER closed" — named facility, current closure -> YES, natural_disaster
        - "Nurse strike at St. Jude shuts down elective surgeries" — named facility, current stoppage -> YES, other

        ===== REJECT (mark NO) — these are ALL noise =====

        - Funding rounds, valuations, IPOs, M&A, partnerships, commercial deals, earnings
        - Product launches, AI tool / chatbot debuts, software releases, roadmaps, strategy announcements
        - Surveys, statistics, annual / quarterly trend reports (e.g. "FBI IC3 annual report", "burnout survey", "AI adoption survey")
        - Government policy, legislation, regulation, payment-rate changes, prior-auth rules, value-based-care models
        - Lawsuits, court rulings, legal opinions, settlements — UNLESS the article describes an actual ongoing care stoppage caused by them
        - Research, clinical trials, drug discovery, efficacy comparisons (e.g. "drug X is healthier than drug Y", "GLP-1 helps migraines")
        - Drug pricing, formulary changes, access programs, TrumpRx / Medicare deals — without an actual supply outage
        - Cyber THREATS / advisories / vulnerabilities not yet exploited against a named victim ("CISA warns…", "researchers discover bug", "hardening guidance")
        - Cyber attacks on entities OUTSIDE healthcare (generic router malware, espionage campaigns, non-healthcare ransomware)
        - Workforce / burnout / compensation trends without a current named-facility care stoppage
        - Crimes, accidents, or arrests that merely INVOLVE a healthcare object or location but do NOT stop care or operations at a named facility — e.g. a stolen ambulance, theft of medical equipment, a car crash involving an ambulance, a shooting or assault in a hospital parking lot, vandalism. The word "ambulance" or "hospital" appearing in the article is NOT enough; the article must describe care or operations actually being halted.
        - Individual human-interest or patient stories (one person's illness, death, long wait, or recovery) without a named facility halting operations
        - Interviews, executive profiles, conferences, op-eds, opinion pieces
        - Anything hedged with "potential", "could", "may affect", "future risk", "expected to"

        ===== CONCRETE NO EXAMPLES (these MUST be marked NO) =====

        - "Man steals an ambulance and leads police on a chase" — a crime involving a vehicle; no named facility stopped care -> NO
        - "Shooting in hospital parking lot; operations continue normally" — crime at a location, care not disrupted -> NO
        - "Nurses vote to authorize a possible strike; no date set" — hedged future threat, no current stoppage -> NO
        - "Family mourns father who died awaiting a transplant" — individual patient story, no facility disruption -> NO

        ===== JSON OUTPUT CONTRACT — FOLLOW EXACTLY =====

        Respond with EXACTLY this JSON shape and nothing else:
        {{
          "analysis": "One factual sentence: name the entity and the impact, OR state the reason for rejection.",
          "is_operational_disruption": boolean,
          "subsector": "drug_shortage" | "medical_device_shortage" | "cyber_attack" | "natural_disaster" | "other" | "none"
        }}

        HARD RULES on subsector:
        - If is_operational_disruption is false → subsector MUST be "none".
        - If is_operational_disruption is true → subsector MUST be EXACTLY one of: "drug_shortage", "medical_device_shortage", "cyber_attack", "natural_disaster", "other". NEVER "none". NEVER any other string.
        - "other" is reserved for confirmed disruptions that genuinely don't fit the four named categories (e.g. workforce strike at a named hospital, power outage shutting down a named facility). Do NOT use "other" as a fallback for marginal articles — when in doubt, mark NO.

        DECISION CHECK before you answer:
        If your analysis sentence describes a confirmed cyberattack, ransomware, breach, PHI exposure, drug shortage, device shortage, evacuation, or care stoppage at a NAMED healthcare entity, you MUST set is_operational_disruption to true and pick a non-"none" subsector. Your boolean MUST match the facts in your analysis sentence — never say "confirmed breach" in analysis and false in the boolean.

        TITLE: {title}
        EXCERPT: {body}

    """

    try:
        url = f"http://localhost:{port}/api/generate"
        resp = requests.post(
            url,
            json={
                "model": AI_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1, "num_ctx": 4096},
            },
            timeout=60,
        )
        LOGGER.debug("HTTP status %d", resp.status_code)
        resp.raise_for_status()
        raw_response = resp.json().get("response", "{}")
        LOGGER.debug(
            "Validation LLM raw response title=%s raw_response=%s",
            title,
            raw_response,
        )
        data = json.loads(raw_response)
        LOGGER.debug("Parsed JSON for title %s: %s", title, data)
        is_threat = data.get("is_operational_disruption", False)

        # Use subsector if it's a threat, otherwise use the analysis as the "reason"
        if isinstance(is_threat, str):
            is_threat = is_threat.upper() != "NO"
        else:
            is_threat = bool(is_threat)

        detail = (
            data.get("subsector", "none")
            if is_threat
            else data.get("analysis", "No impact detected")
        )
        return is_threat, detail
    # used for timeout / connection refused / HTTP errors
    # we should wrap all ai_check_validation calls in a try/catch loop
    # to re try on these vs handling them as noise
    except requests.exceptions.RequestException as e:
        LOGGER.warning("LLM call failed for %s: %s", title, e)
        raise LLMUnavailableError(f"Ollama validation call failed: {e}") from e

    except Exception as e:
        LOGGER.warning("Error parsing AI response for title %s: %s", title, e)
        return False, "Parsing Error"


def get_extraction_template(subsector: str) -> dict:
    """Builds a typed JSON extraction template for the LLM prompt.

    Inspects the dataclass annotations for the given subsector and maps
    each field to a stringified type hint (e.g., "string", "boolean", "integer",
    "list of strings") to constrain the LLM output and prevent type hallucination.

    Parameters:
        subsector: The classification name of the healthcare subsector.

    Returns:
        dict: A mapping of required field names to their expected primitive types.
    """
    template = {f: "string" for f in LLM_SECTOR_FIELDS}

    subsector_cls = SUBSECTOR_DATA_CLASSES.get(subsector)
    subsector_fields = SUBSECTOR_FIELDS.get(subsector, [])

    LOGGER.debug(
        "get_extraction_template subsector=%s dataclass=%s fields=%s",
        subsector,
        subsector_cls,
        subsector_fields,
    )

    if subsector_cls:
        annotations = subsector_cls.__annotations__
        LOGGER.debug("get_extraction_template annotations=%s", annotations)
        for field in subsector_fields:
            if field in annotations:
                type_str = str(annotations[field]).lower()
                if "list" in type_str:
                    template[field] = "list of strings"
                elif "bool" in type_str:
                    template[field] = "boolean"
                elif "int" in type_str or "float" in type_str:
                    template[field] = "integer"
                else:
                    template[field] = "string"
            else:
                LOGGER.debug(
                    "get_extraction_template field=%s not in annotations, defaulting to string",
                    field,
                )
                template[field] = "string"
    else:
        LOGGER.debug(
            "get_extraction_template no dataclass for subsector=%s, all fields default to string",
            subsector,
        )
        for field in subsector_fields:
            template[field] = "string"

    LOGGER.debug("get_extraction_template final template=%s", template)
    return template


class MissingSubsectorFieldsError(ValueError):
    """Raised when a subsector has no configured extraction fields."""


def _get_subsector_fields_or_raise(subsector: str) -> list[str]:
    """Return configured fields for a subsector or raise a recoverable error.

    Parameters:
        subsector: The classification name of the healthcare subsector.

    Returns:
        The configured field names for the requested subsector.

    Raises:
        MissingSubsectorFieldsError: If the subsector is unknown or has no
            configured extraction fields.
    """
    try:
        subsector_fields = SUBSECTOR_FIELDS[subsector]
    except KeyError as exc:
        message = f"No fields found for subsector {subsector!r}"
        LOGGER.error(message)
        raise MissingSubsectorFieldsError(message) from exc

    if not subsector_fields:
        message = f"No fields found for subsector {subsector!r}"
        LOGGER.error(message)
        raise MissingSubsectorFieldsError(message)

    return subsector_fields


def build_extraction_prompt(subsector, title, body) -> str:
    """Build the field extraction prompt for a validated article.

    Parameters:
        subsector: Subsector returned by ``ai_check_validation``.
        title: Title of the current article.
        body: Full body text or excerpt to include in the extraction prompt.

    Returns:
        A prompt instructing the LLM to return typed, subsector-scoped JSON.

    Raises:
        MissingSubsectorFieldsError: If the subsector is unknown or has no
            configured extraction fields.
    """
    _get_subsector_fields_or_raise(subsector)

    template_dict = get_extraction_template(subsector)
    template_json = json.dumps(template_dict, indent=2)
    subsector_guidance = "\n        ".join(SUBSECTOR_FIELD_GUIDANCE.get(subsector, []))
    LOGGER.debug("extract_fields subsector=%s template=%s", subsector, template_json)

    return f"""
        You are a Healthcare Data Extractor. Extract specific metadata from a confirmed healthcare disruption article. Be conservative -- when in doubt, return null.

        STRICT RULES:
        1. Only extract values that are EXPLICITLY stated in the article text. Do NOT infer, guess, summarize, or use any general / outside knowledge.
        2. If a field is not directly mentioned in the text, set its value to null. "Mentioned" means the article makes a direct factual statement about that exact field.
        3. Return EXACTLY the requested keys -- no extra fields, no renamed fields, no nested objects.
        4. Numeric fields: return raw numbers, not strings. Strip currency symbols and unit suffixes (e.g. "$5 million" -> 5000000, "12 days" -> 12). If the number is approximate or a range, use null.
        5. Date fields: use ISO format YYYY-MM-DD only if the article gives an explicit date. If only a month/year or vague phrasing ("later this year") is given, use null.
        6. Boolean fields: return true for an explicit affirmative statement, false for an explicit negative statement, and null when the field is unmentioned or uncertain. Do not infer booleans from context.
        7. List fields: return a JSON array of strings, each lifted directly from the article. If nothing is stated, use null (not an empty array).
        8. A field name does not need to appear verbatim. Populate a field when the article directly states the same fact using equivalent wording.
        9. The same article sentence may support more than one field when it directly states both facts. Do not use one fact for another field unless the article directly supports that field too.
        10. Output VALID JSON only -- no markdown fences, no commentary, no trailing text.

        FIELD-SPECIFIC GUIDANCE (sector fields, applied to ALL subsectors):
        - "exec_summary": a 1-2 sentence factual summary of the disruption, naming the entity and the impact. Lift facts only from the article. Empty string allowed if the article is too vague to summarize.
        - "geography_scope": The full name of the US state, city, or county. "US" if no specific state is specified, "US Territory" for US territories, "Outside US" for non-US events, or null if not explicit.
        - "start_date" / "end_date": ISO YYYY-MM-DD; null if not explicit.
        - "resilience_or_mitigation_observed": Concise article-grounded statement of stated actions or measures that reduced impact, maintained continuity, supported recovery, or improved resilience; null if none is stated.

        FIELD-SPECIFIC GUIDANCE ({subsector} fields):
        {subsector_guidance}

        ARTICLE TITLE: {title}
        ARTICLE BODY: {body}

        EXTRACTION TEMPLATE (replace the type placeholders with the extracted value, or null for any field not explicitly stated in the article text):
        {template_json}

        JSON RESPONSE:
    """


def request_extraction_completion(prompt, port):
    """Request an extraction completion from the local Ollama server.

    Parameters:
        prompt: Extraction prompt to send to the LLM.
        port: Port number for the local Ollama ``/api/generate`` endpoint.

    Returns:
        The raw JSON response string returned by the model, or ``"{}"`` when
        Ollama returns no ``response`` field.
    """
    url = f"http://localhost:{port}/api/generate"
    resp = requests.post(
        url,
        json={
            "model": AI_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_ctx": 4096},
        },
        timeout=30,
    )
    return resp.json().get("response", "{}")


def parse_extraction_response(
    raw_response, subsector, title, body
) -> tuple[dict, dict]:
    """Parse raw LLM extraction JSON into sector and subsector dictionaries.

    Parameters:
        raw_response: Raw JSON string returned by the LLM.
        subsector: Subsector returned by ``ai_check_validation``.
        title: Title of the current article.
        body: Full body text or excerpt used for extraction.

    Returns:
        A tuple containing universal sector fields first and subsector-specific
        fields second.

    Raises:
        MissingSubsectorFieldsError: If the subsector is unknown or has no
            configured extraction fields.
        json.JSONDecodeError: If ``raw_response`` is not valid JSON.
    """
    subsector_fields = _get_subsector_fields_or_raise(subsector)

    LOGGER.debug(
        "Extraction LLM raw response subsector=%s title=%s raw_response=%s",
        subsector,
        title,
        raw_response,
    )
    raw = json.loads(raw_response)
    LOGGER.debug(
        "extract_fields parsed keys=%s sector_data keys=%s",
        list(raw.keys()),
        LLM_SECTOR_FIELDS,
    )

    sector_data = {k: raw.get(k) for k in LLM_SECTOR_FIELDS}
    _enforce_mitigation_article_support(sector_data, title, body)
    subsector_data = {k: raw.get(k) for k in subsector_fields}
    LOGGER.debug(
        "extract_fields sector_data=%s subsector_data=%s",
        sector_data,
        subsector_data,
    )

    expected_keys = set(LLM_SECTOR_FIELDS) | set(subsector_fields)
    unexpected = set(raw.keys()) - expected_keys
    if unexpected:
        LOGGER.warning(
            "extract_fields unexpected keys from LLM not in template subsector=%s keys=%s",
            subsector,
            unexpected,
        )
    return sector_data, subsector_data


def _enforce_mitigation_article_support(sector_data: dict, title, body) -> None:
    """Null unsupported mitigation text that is not grounded in the article.

    This guard keeps the LLM from saving plausible but unsupported mitigation
    claims. Exact article text is accepted, and close article-grounded
    paraphrases are allowed when enough meaningful terms overlap.

    Parameters:
        sector_data: Mutable dictionary containing universal extraction fields.
        title: Title of the current article.
        body: Full body text or excerpt used for extraction.
    """
    mitigation = sector_data.get("resilience_or_mitigation_observed")
    if not mitigation:
        sector_data["resilience_or_mitigation_observed"] = None
        return

    if not isinstance(mitigation, str):
        sector_data["resilience_or_mitigation_observed"] = None
        return

    article_text = f"{title}\n{body}"
    if mitigation in article_text:
        return

    mitigation_terms = set(re.findall(r"[A-Za-z][A-Za-z-]{3,}", mitigation.lower()))
    article_terms = set(re.findall(r"[A-Za-z][A-Za-z-]{3,}", article_text.lower()))
    shared_terms = mitigation_terms & article_terms
    if len(shared_terms) >= 3 and len(shared_terms) >= max(
        1, len(mitigation_terms) // 2
    ):
        return

    LOGGER.debug(
        "Unsupported mitigation text removed title=%s mitigation=%s",
        title,
        mitigation,
    )
    sector_data["resilience_or_mitigation_observed"] = None


def extract_fields(subsector, title, body, port=11434) -> tuple[dict, dict]:
    """Extract universal and subsector fields for a validated article.

    This function is called after an article classifies as a true vulnerability.
    It sends the article title and body to Ollama to populate the universal
    sector fields and the fields specific to the selected subsector.

    Parameters:
        subsector: Subsector returned by ``ai_check_validation``.
        title: Title of the current article.
        body: Full body text of the current article.
        port: Port number for the local Ollama ``/api/generate`` endpoint.

    Returns:
        A tuple with the universal ``LLM_SECTOR_FIELDS`` values first and the
        matching ``SUBSECTOR_FIELDS`` values second.

    Raises:
        MissingSubsectorFieldsError: If the subsector is unknown or has no
            configured extraction fields.

    Note:
        The AI currently decides which values can be extracted from the article.
        That keeps extraction flexible, but it is not ideal as a long-term
        structured-data contract.
    """
    subsector_fields = _get_subsector_fields_or_raise(subsector)

    try:
        prompt = build_extraction_prompt(subsector, title, body)
        raw_response = request_extraction_completion(prompt, port)
        return parse_extraction_response(raw_response, subsector, title, body)

    except Exception as e:
        LOGGER.warning("Error extracting fields for title %s: %s", title, e)
        return (
            {k: None for k in LLM_SECTOR_FIELDS},
            {k: None for k in subsector_fields},
        )


class model_unavailable_error(RuntimeError):
    """
    Raised when configured Ollama model is unavailable
    """


class LLMUnavailableError(RuntimeError):
    """
    Raised when an LLM HTTP call fails (timeout/connection/HTTP error).

    Distinct from a negative classification: signals the call never produced a
    usable answer, so callers should skip and retry rather than treat the article
    as noise.
    """


checked_ollama_models: set[str] = set()


def ensure_model_available(model: str = AI_MODEL) -> None:
    if model in checked_ollama_models:
        return

    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise model_unavailable_error(
            "[ERROR] Ollama CLI not found.\nInstall and make sure 'ollama' is on PATH"
        ) from exc
    except subprocess.SubprocessError as exc:
        raise model_unavailable_error(
            "[ERROR] Could not query Ollama models.\n"
            "Make sure Ollama is running, then try again"
        ) from exc

    if result.returncode != 0:
        raise model_unavailable_error(
            "[ERROR] Could not query Ollama models.\n"
            "Make sure Ollama is running, then try again"
        )

    installed_models = {
        line.split()[0]
        for line in result.stdout.splitlines()[1:]
        if line.strip() and line.split()
    }
    if model not in installed_models:
        raise model_unavailable_error(
            f"[ERROR] Model '{model}' not found in Ollama. Make sure Ollama is running.\n"
            f"Run: ollama pull {model}"
        )

    checked_ollama_models.add(model)


def run_clean():
    """Clean all GDELT-generated data so the pipeline starts fresh.

    Delegates to :func:`scripts.clean_gdelt.run_clean` which owns the
    canonical implementation.  This wrapper exists for backward compatibility.
    """
    # Inline import to avoid circular dependency and keep the scripts package
    # out of the default import path for shared_utils.
    import importlib

    mod = importlib.import_module("scripts.clean_gdelt")
    mod.run_clean()


def clear_directory(directory: Path) -> None:
    """
    Delete all files and subdirectories inside a directory.

    Parameters:
        directory: The path to the directory to clear.
    """
    if not directory.exists():
        LOGGER.debug("Directory does not exist, skipping clear: %s", directory)
        return
    # Iterate over all items in the directory and remove them
    for item in directory.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except Exception as exc:
            LOGGER.warning("Failed to remove %s: %s", item, exc)


def df_dup(
    dfs: list[pd.DataFrame], verbose: bool = False
) -> tuple[pd.DataFrame, list[str]]:
    """
    Deduplicates a lits of dataframes, used to make one single csv write by scooper

    Args:
        dfs: list of dataframes with the same schema
        verbose: prints messages when True
    Returns:
        One unique DataFrame a list of duplicate titles
    """

    valid_dfs = [df for df in dfs if df is not None and not df.empty]
    if not valid_dfs:
        if verbose:
            print("[WARNING]: All frames were None")
        return pd.DataFrame(), []

    combined = pd.concat(valid_dfs, ignore_index=True)

    key = [c for c in ("source_name", "title") if c in combined.columns]
    if not key:
        return combined.drop_duplicates(ignore_index=True), []

    dup_titles: list[str] = []
    if "title" in combined.columns:
        dup_mask = combined.duplicated(subset=key, keep="first")
        dup_titles = combined.loc[dup_mask, "title"].dropna().unique().tolist()

    deduped = combined.drop_duplicates(subset=key, keep="first", ignore_index=True)
    return deduped, dup_titles


def update_csv(df: pd.DataFrame, path: Path, verbose: bool = False) -> None:
    """
    Appends a csv with a given DataFrame. This is used in scooper after calling 'df_dup'

    Args:
        df: DataFrame to append
        path: CSV path
        verbose: used to print messages if true
    """
    if df is None or df.empty:
        if verbose:
            print("[WARNING]df is not usable (it is empty or None)")
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not path.exists() or path.stat().st_size == 0
    if write_header and verbose:
        print("Writing headers, file was empty or did not exist")

    df.to_csv(
        path,
        mode="a",
        header=write_header,
        index=False,
        date_format="%Y-%m-%d",
    )


def update_json(vulns: list[Vulnerability], path: str) -> None:
    """
    Used to write a list of Vulnerabilities into a given path

    Args:
        vulns: vulnerabilities to add to the file.
        path: destination *.json file (created if it does not exist)
    """
    clean_vulns: list[Vulnerability] = []
    seen: set[tuple[str, str]] = set()
    for vuln in vulns:
        inst = (vuln.title, vuln.source_name)
        if inst in seen:
            continue
        seen.add(inst)
        clean_vulns.append(vuln)

    file_path = Path(path)

    if file_path.exists():
        sources = json.loads(file_path.read_text(encoding="utf-8"))["sources"]
    else:
        sources = []
    sources.extend(vuln.to_dict() for vuln in clean_vulns)

    with open(file_path, "w", encoding="utf-8") as json_file:
        json.dump({"sources": sources}, json_file, indent=2, ensure_ascii=False)


DEBUG_DIR = _PROJECT_ROOT / "data" / "noise"


class NoiseCollector:
    """Accumulate rejected-article records and flush them to a JSON file.

    Used when the ``--debug`` / ``-d`` flag is passed to capture every article
    the pipeline skips or rejects so operators can evaluate false-negative rates.

    Parameters:
        output_path: Destination JSON file (e.g. ``data/noise/debug_noise_gdelt.json``).
    """

    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.records: list[dict] = []

    def add(
        self,
        *,
        url: str,
        title: str,
        source: str,
        reason: str,
        body_preview: str = "",
        stage: str = "",
    ) -> None:
        """Append one noise record.

        Parameters:
            url: The article URL that was rejected.
            title: The article title.
            source: Pipeline source name (e.g. ``"GDELT"``, ``"CyberScoop"``).
            reason: Human-readable rejection reason.
            body_preview: First 250 characters of the article body.
            stage: Pipeline stage that rejected the article (e.g.
                ``"already_seen"``, ``"validation"``, ``"extraction"``).
        """
        self.records.append(
            {
                "url": url,
                "title": title,
                "source": source,
                "reason": reason,
                "body_preview": body_preview[:250],
                "stage": stage,
                "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            }
        )

    def flush(self) -> Path | None:
        """Write accumulated records to the JSON file and return the path.

        Creates the parent directory if it does not exist.  Returns ``None``
        when there are no records to write.
        """
        if not self.records:
            return None
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(
                {"noise_records": self.records, "total": len(self.records)},
                f,
                ensure_ascii=False,
                indent=2,
            )
        LOGGER.info(
            "Wrote %d debug noise records to %s",
            len(self.records),
            self.output_path,
        )
        return self.output_path
