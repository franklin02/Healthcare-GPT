"""Helpers for scraping, AI validation, and subsector field extraction.

This module provides utilities used by the GDELT pipeline:

- `get_body(url)`: fetch and clean article body text from a URL.
- `ai_check_validation(title, body)`: call a local AI service to check if
    an article describes an active operational disruption.
- `find_subsector_fields(subsector, title, body)`: extract subsector-specific
    metadata fields via the AI service.

Constants:
- `AI_URL`, `AI_MODEL`: local AI endpoint configuration.
- `SUBSECTOR_FIELDS`: mapping of subsector -> required extraction fields.

The AI calls expect a local Ollama-like API at `AI_URL` that returns a JSON
`response` string containing the model output as JSON.
"""

import json
import re
import requests
from bs4 import BeautifulSoup

AI_URL = "http://localhost:11434/api/generate"
AI_MODEL = "gemma4:e4b"
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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

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
        print("[WARNING] no body found")
        return ""

    # Prefer paragraph text (gives cleaner article body across sites.)
    # Fall back to all text if no <p> tags found.
    paragraphs = [p.get_text(" ", strip=True) for p in main.find_all("p")]
    paragraphs = [p for p in paragraphs if p]

    if paragraphs:
        return "\n\n".join(paragraphs)
    return main.get_text(" ", strip=True)


def _run_bert(title: str, body: str) -> str:
    import sys
    from pathlib import Path

    gdelt_dir = Path(__file__).resolve().parent.parent / "GDELT"
    if str(gdelt_dir) not in sys.path:
        sys.path.append(str(gdelt_dir))

    try:
        from BERT_filter import run_bert_inference
    except ImportError as exc:
        raise RuntimeError(f"BERT_filter.py not found at {gdelt_dir}") from exc

    return run_bert_inference({"title": title, "body": body})


def ai_check_validation(title, body, use_bert=False) -> tuple[bool, str]:
    """Call the local AI endpoint to validate whether an article describes
    an active operational disruption.

    The function formats a safety-focused prompt and posts it to the local
    AI service defined by `AI_URL`. The AI is expected to return JSON with
    at least `is_operational_disruption` and `subsector` / `analysis` fields.

    Args:
        title (str): Article title.
        body (str): Article body/excerpt (cleaned text).

    Returns:
        tuple[bool, str]: `(is_threat, detail)` where `is_threat` is True if
            the AI indicates an active operational disruption, and `detail`
            is either the subsector name (for threats) or a short analysis
            string explaining why the article was excluded.

    On error the function returns `(False, "Parsing Error")` and prints the
    exception to stdout.

    When ``use_bert`` is True, BERT runs first as a lightweight rejection
    filter. Articles that pass BERT are still sent to the LLM for confirmation.
    """
    if use_bert:
        bert_subsector = _run_bert(title, body)
        if bert_subsector == "none":
            print("[BERT] rejected skipping LLM")
            return False, "BERT: unrelated news"
        print(f"[BERT] flagged as '{bert_subsector}' sending to LLM for confirmation")

    prompt = f"""
        [INST] <<SYS>>
        You are a Healthcare Crisis Auditor. Your task is to identify ACTIVE OPERATIONAL DISRUPTIONS in healthcare. 
        An operational disruption is defined as an event where the actual delivery of care is currently degraded, stopped, or physically prevented.

        CRITICAL EXCLUSIONS (Mark as NO):
        - "Potential" risks or "discovered vulnerabilities" that haven't been exploited yet.
        - Policy debates, government legislation, or funding news.
        - Corporate business news (mergers, stock prices, quarterly earnings).
        - General research papers or clinical trial results without an active supply failure.
        - Historical data or sentencing for crimes that happened years ago.

        VALID DISRUPTION EXAMPLES (Mark as YES):
        - A specific hospital diverting ambulances or canceling surgeries.
        - A ransomware attack currently locking a facility's EHR (Electronic Health Records).
        - A drug being physically unavailable at pharmacies due to a factory shutdown.
        - Physical damage to a clinic from weather or fire.

        You MUST respond in this JSON format:
        {{
        "analysis": "A one-sentence explanation of the active impact found in the text.",
        "is_operational_disruption": boolean,
        "subsector": "drug_shortage" | "medical_device_shortage" | "cyber_attack" | "natural_disaster" | "other" | "none"
        }}
        <</SYS>>

        TITLE: {title}
        EXCERPT: {body}

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
                "options": {"temperature": 0.1},
            },
            timeout=60,
        )

        raw_response = resp.json().get("response", "{}")
        data = json.loads(raw_response)

        is_threat = data.get("is_operational_disruption", False)
        # Handle both boolean False and string "NO" as not a disruption
        if isinstance(is_threat, str):
            is_threat = is_threat.upper() != "NO"
        else:
            is_threat = bool(is_threat)

        # Use subsector if it's a threat, otherwise use the analysis as the "reason"
        detail = (
            data.get("subsector", "none")
            if is_threat
            else data.get("analysis", "No impact detected")
        )

        return is_threat, detail

    except Exception as e:
        print(f"Error parsing AI response: {e}")
        return False, "Parsing Error"


# Once a source is confirmed as a disruption, extract the subsector-specific
# fields mirrored by src/classes/vulnerability.py and src/config/schema.json.


def find_subsector_fields(subsector, title, body) -> dict:
    """Extract subsector-specific fields from article text via the AI service.

    The function looks up the expected fields for `subsector` in
    `SUBSECTOR_FIELDS`, builds a prompt asking the AI to return a JSON
    object containing those fields (or null for missing values), and
    returns the parsed JSON as a Python dict.

    Args:
        subsector (str): One of the keys in `SUBSECTOR_FIELDS` (e.g.
            "drug_shortage", "cyber_attack").
        title (str): Article title.
        body (str): Article body/excerpt (cleaned text).

    Returns:
        dict: Parsed JSON from the AI containing the requested fields. If the
            subsector is unknown an empty dict is returned. On AI or parsing
            errors the function returns a dict mapping each requested field to
            `None`.
    """

    # Get the specific fields for this subsector or exit if none found
    fields_to_extract = SUBSECTOR_FIELDS.get(subsector)
    if not fields_to_extract:
        print(f"Error: No fields found for subsector: {subsector}")
        return {}

    # Format the list into a string for the prompt
    fields_string = ", ".join([f'"{f}"' for f in fields_to_extract])

    prompt = f"""
        [INST] <<SYS>>
        You are a Healthcare Data Extractor. Your job is to extract specific technical metadata from news articles regarding healthcare disruptions.
        
        RULES:
        1. Only extract information explicitly stated in the text.
        2. If a field is not mentioned, set the value to null.
        3. Return your answer in VALID JSON format.
        4. Match the subsector requirements exactly.
        <</SYS>>

        ARTICLE TITLE: {title}
        ARTICLE BODY: {body}

        INSTRUCTION: 
        Extract the following fields from the article above: {fields_string}. 

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
        return json.loads(raw_response)

    except Exception as e:
        print(f"Error extracting subsector fields: {e}")
        return {key: None for key in fields_to_extract}
