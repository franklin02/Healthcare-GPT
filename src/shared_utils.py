"""Provide shared utility functions for file validation, data processing, and URL handling in the application.

This module provides utility functions and constants that assist in processing and managing data for the application.
It includes functions for fetching news articles, extracting key information, and classifying them into different categories.
Including page fetching, validation of files, JSON and CSV outputs, and URL construction.
The shared utilities aim to simplify and streamline repetitive tasks or operations across the project.

Attributes:
    - `AI_URL`: The base URL for the AI service.
    - `AI_MODEL`: The specific model that the AI will use for processing.
    - `_sys`: System-related functionality or constant.
    - `_PROJECT_ROOT`: Specifies the project's root directory.
    - `READY_FOR_RAG_DIR`: Directory designated for resources ready for retrieval-augmented generation (RAG).
    - `NOISE_DIR`: Directory for storing noise data.
    - `VULNERABILITIES_DIR`: Directory for storing vulnerabilities data.
    - `HEADERS`: Headers for HTTP-related tasks.
    - `VULN_CSV_HEADER`: Header for the vulnerabilities CSV file.
    - `NOISE_CSV_HEADER`: Header for the noise CSV file.
    - `SUBSECTOR_FIELDS`: A dictionary that maps subsectors to their specific fields.

Functions:
    - `get_page`: Retrieves web page content for a given URL, handling HTTP requests.
    - `_site_filename`: Generates or retrieves specific filename associated with a site.
    - `check_valid_file`: Validates files against specific criteria.
    - `json_output`: Outputs data in JSON format.
    - `vuln_output`: Processes and generates output related to vulnerabilities.
    - `noise_output`: Processes and generates output related to noise.
    - `build_page_url`: Constructs URLs for web pages based on given parameters.
    - `ai_check_validation`: Parses and verifies whether a healthcare-related article describes an ongoing operational disruption or confirmed breach
       at a named healthcare entity based on strict, predefined criteria.
    - `find_subsector_fields`: Extracts specific fields for a given healthcare subsector by utilizing an AI-based metadata extraction process from the provided article title and body.


Possible subsectors:
        - "drug_shortage": A confirmed shortage of a named drug patients need now.
        - "medical_device_shortage": A confirmed inability to supply a specific named medical device.
        - "cyber_attack": A confirmed breach or attack involving a named healthcare entity.
        - "natural_disaster": Operational shutdowns due to fire, flood, storm, or other physical events.
        - "other": Other confirmed operational disruptions that do not fit the previous categories.
        - "none": Used when no operational disruption or breach is confirmed.

"""

import json
import csv
import os
import tempfile
import requests
import sys
from pathlib import Path
import re
from bs4 import BeautifulSoup

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.logging_utils import get_file_logger
from src.classes import Vulnerability

AI_URL = "http://localhost:11434/api/generate"
AI_MODEL = "llama3.2"

# Anchor to the project root so this works both as `scrapers.shared_utils`
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
        print(f"Created {json_path}")
        LOGGER.debug("Created JSON file for site %s at %s", site_name, json_path)

    noise_path = NOISE_DIR / f"{stem}.csv"
    if not noise_path.exists():
        with open(noise_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(NOISE_CSV_HEADER)
        print(f"Created {noise_path}")
        LOGGER.debug("Created noise CSV file for site %s at %s", site_name, noise_path)

    vulnerabilities_path = VULNERABILITIES_DIR / f"{stem}.csv"
    if not vulnerabilities_path.exists():
        with open(vulnerabilities_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(VULN_CSV_HEADER)
        print(f"Created {vulnerabilities_path}")
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
        print(
            f"[WARN] Title matched but body preview differs for {title!r} "
            f"— stopping anyway"
        )
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
        print(f"[ERROR] Failed to fetch {url[:80]}: {e}")
        return url

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("title")
    if title_tag:
        raw = title_tag.get_text(strip=True)
        if raw:
            cleaned = _TITLE_SITE_SUFFIX_RE.sub("", raw).strip()
            return cleaned if cleaned else raw
    return url


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
        print(f"[ERROR] Failed to fetch {url[:80]}: {e}")
        LOGGER.error("Failed to fetch URL %s: %s", url, e)
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

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
        print("[WARN] no body found")
        LOGGER.warning("No body found for URL %s", url)
        return ""

    # Prefer paragraph text (gives cleaner article body across sites.)
    # Fall back to all text if no <p> tags found.
    paragraphs = [p.get_text(" ", strip=True) for p in main.find_all("p")]
    paragraphs = [p for p in paragraphs if p]

    if paragraphs:
        LOGGER.debug("Extracted %d paragraphs from URL %s", len(paragraphs), url)
        return "\n\n".join(paragraphs)
    LOGGER.debug("No paragraphs found, falling back to all text for URL %s", url)
    return main.get_text(" ", strip=True)


_classifier = None


def _run_bert(title: str, body: str) -> str:
    """
    Run BERT inference on an article. Returns the predicted subsector string or "none".
    Loads the classifier once on first call and reuses it for subsequent articles.
    "potential_hit" from the base model fallback is normalized to "other".
    """
    global _classifier
    try:
        from src.GDELT.BERT_filter import run_bert_inference, load_model
    except ImportError as exc:
        LOGGER.error("Failed to import BERT_filter module: %s", exc)
        raise RuntimeError("BERT_filter.py not found at src/GDELT/") from exc

    if _classifier is None:
        _classifier = load_model()
        if _classifier is None:
            print("[WARN] BERT classifier failed to load, skipping.")
            return "none"

    else:
        LOGGER.debug("Reusing cached BERT classifier for title %s", title)
        pass

    try:
        result = run_bert_inference({"title": title, "body": body}, _classifier)
    except Exception as e:
        print(f"[ERROR] BERT inference failed: {e}")
        return "none"

    if result == "potential_hit":
        return "other"
    return result


def ai_check_validation(title, body, use_bert=False) -> tuple[bool, str]:
    """
    Parses and verifies whether a healthcare-related article describes an ongoing operational disruption or confirmed breach at a named healthcare entity based on strict, predefined criteria.

    Parameters:
        title (str): The title of the article being analyzed.
        body (str): The main content or excerpt of the article.
        use_bert (bool): False by default, calls bert before calling the llm to save time

    Returns: A tuple:
        - A boolean indicating whether the article is flagged as a threat (True if operational disruption or confirmed breach).
        - A string providing further details: the subsector if flagged as a disruption or the reason for rejection if not flagged.

    This function sends the article's title and body to an AI system for evaluation. The AI follows explicit rules to assess disruptions or breaches in healthcare. If an operational disruption is identified, the response will specify the subsector such as 'cyber_attack', 'drug_shortage', etc. If not, the output will explain why the article was rejected.

    Exceptions:
    If an error occurs during the request or response parsing, the function catches the error, logs it, and returns False with "Parsing Error".
    """

    if use_bert:
        bert_subsector = _run_bert(title, body)
        if bert_subsector == "none":
            print("[BERT] rejected skipping LLM")
            LOGGER.info("BERT rejected article with title %s", title)
            return False, "BERT: unrelated news"
        print(f"[BERT] flagged as '{bert_subsector}' sending to LLM for confirmation")
        LOGGER.info("BERT flagged article with title %s as %s", title, bert_subsector)

    prompt = f"""
        [INST] <<SYS>>
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
        - Interviews, executive profiles, conferences, op-eds, opinion pieces
        - Anything hedged with "potential", "could", "may affect", "future risk", "expected to"

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
        <</SYS>>

        TITLE: {title}
        EXCERPT: {body}

        [/INST]
    """

    promptG = f"""
    You are a strict Healthcare Operations Auditor. Your ONLY job is to flag articles that describe a REAL, ALREADY-OCCURRING healthcare disruption or a CONFIRMED breach at a named healthcare entity.

    DEFAULT TO NO. Reject the article unless the evidence is explicit, named, and concrete. The vast majority of healthcare news is NOT a disruption.

    ===== ACCEPT (mark YES) ONLY IF (A) OR (B) IS TRUE =====

    (A) ACTIVE CARE DISRUPTION — the article states that a NAMED facility (hospital, clinic, pharmacy, lab, healthcare network) is CURRENTLY or RECENTLY:
        - Diverting ambulances, cancelling surgeries, or turning patients away
        - Operating on downtime / paper procedures because EHR is offline
        - Suspending services or evacuating due to fire, flood, storm, or other physical event
        - Physically out of a specific drug or medical device that patients need now (real supply outage, not pricing or formulary debate)
        - Cut off from operations by a workforce strike, power outage, or other concrete event

    (B) CONFIRMED HEALTHCARE BREACH / CYBERATTACK — both must be true:
        Part 1: Named healthcare entity (hospitals, clinics, pharmacies, insurers, device manufacturers, EHR vendors, labs)
        Part 2: Incident already confirmed (ransomware, PHI exposed, breach disclosed, systems impacted)

    ===== REJECT (mark NO) =====
    - Earnings, funding, IPOs, partnerships, product launches
    - Policy, legislation, regulation, research, clinical trials
    - Drug pricing without actual supply outage
    - Cyber threats/advisories not yet exploited
    - Op-eds, interviews, wellness articles, anything hedged with "could" or "may"

    ===== OUTPUT =====
    Respond with EXACTLY this JSON and nothing else:
    {{
    "analysis": "One factual sentence: name the entity and impact, OR reason for rejection.",
    "is_operational_disruption": true or false,
    "subsector": "drug_shortage" | "medical_device_shortage" | "cyber_attack" | "natural_disaster" | "other" | "none"
    }}

    TITLE: {title}
    EXCERPT: {body}
    """

    try:
        resp = requests.post(
            AI_URL,
            json={
                "model": AI_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
            },
            timeout=60,
        )
        LOGGER.debug("HTTP status %d", resp.status_code)
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

    except Exception as e:
        print(f"Error parsing AI response: {e}")
        LOGGER.error("Error parsing AI response for title %s: %s", title, e)
        return False, "Parsing Error"


def extract_fields(subsector, title, body) -> tuple[dict, dict]:
    """
    This function is called once we know an article classifies as a true vulnerability. We pass in the artile information
    to AI (currently Ollama) to get all of the sector and subsector fields to build the 'Vulnerability' shape, this will
    later be used to make a JSON structure to be ingested.

    Args:
        subsector (string): this is obtained by ai_check_validation
        title (string): title of the current article
        body (string): full body of the current article

    Returns: A tuple with 2 dicts
        - First dict: contains all the universal LLM_SECTOR_FIELDS applicable (decided by AI)
        - Second dict: contains all the SUBSECTOR_FIELDS applicable (also decided by AI)

    Note:
        - We should find an alternative to this function, currently the AI decided which fields can be grabbed given the current article,
        this is not ideal for the long term.

    """
    subsector_fields = SUBSECTOR_FIELDS.get(subsector)
    if not subsector_fields:
        print(f"No fields found for subsector: {subsector}")
        LOGGER.error("No fields found for subsector %s", subsector)
        exit(1)

    all_fields = LLM_SECTOR_FIELDS + subsector_fields
    fields_string = ", ".join([f'"{f}"' for f in all_fields])

    prompt = f"""
        [INST] <<SYS>>
        You are a Healthcare Data Extractor. Extract specific metadata from a confirmed healthcare disruption article. Be conservative — when in doubt, return null.

        STRICT RULES:
        1. Only extract values that are EXPLICITLY stated in the article text. Do NOT infer, guess, summarize, or use any general / outside knowledge.
        2. If a field is not directly mentioned in the text, set its value to null. "Mentioned" means the article makes a direct factual statement about that exact field.
        3. Return EXACTLY the requested keys — no extra fields, no renamed fields, no nested objects.
        4. Numeric fields: return raw numbers, not strings. Strip currency symbols and unit suffixes (e.g. "$5 million" -> 5000000, "12 days" -> 12). If the number is approximate or a range, use null.
        5. Date fields: use ISO format YYYY-MM-DD only if the article gives an explicit date. If only a month/year or vague phrasing ("later this year") is given, use null.
        6. Boolean fields: true or false ONLY if explicitly stated; otherwise null. Do not infer booleans from context.
        7. List fields: return a JSON array of strings, each lifted directly from the article. If nothing is stated, use null (not an empty array).
        8. Output VALID JSON only — no markdown fences, no commentary, no trailing text.

        FIELD-SPECIFIC GUIDANCE (sector fields, applied to ALL subsectors):
        - "exec_summary": a 1-2 sentence factual summary of the disruption, naming the entity and the impact. Lift facts only from the article. Empty string allowed if the article is too vague to summarize.
        - "geography_scope": the U.S. state, region, or "US Territory" the disruption affects, only if stated. Otherwise null.
        - "start_date" / "end_date": ISO YYYY-MM-DD; null if not explicit.
        - "resilience_or_mitigation_observed": any specific mitigation, workaround, or response action stated in the article (e.g. "diverted ambulances to nearby hospital", "restored systems within 48 hours"). Null if none stated.
        <</SYS>>

        ARTICLE TITLE: {title}
        ARTICLE BODY: {body}

        EXTRACT THESE FIELDS (and ONLY these): {fields_string}

        JSON RESPONSE:
        [/INST]
    """

    try:
        resp = requests.post(
            AI_URL,
            json={
                "model": AI_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            },
            timeout=30,
        )

        raw_response = resp.json().get("response", "{}")
        LOGGER.debug(
            "Extraction LLM raw response subsector=%s title=%s raw_response=%s",
            subsector,
            title,
            raw_response,
        )
        raw = json.loads(raw_response)

        sector_data = {k: raw.get(k) for k in LLM_SECTOR_FIELDS}
        subsector_data = {k: raw.get(k) for k in subsector_fields}
        return sector_data, subsector_data

    except Exception as e:
        print(f"Error extracting fields: {e}")
        LOGGER.error("Error extracting fields for title %s: %s", title, e)
        return (
            {k: None for k in LLM_SECTOR_FIELDS},
            {k: None for k in subsector_fields},
        )
