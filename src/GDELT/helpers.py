import json
import re
import requests
from bs4 import BeautifulSoup

AI_URL = "http://localhost:11434/api/generate"
AI_MODEL = "llama3.2"
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
    "ad", "advert", "promo", "sidebar", "related", "newsletter",
    "subscribe", "comment", "social", "share", "cookie",
)
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)
_NOISE_SELECTOR = ",".join(f'[class*="{p}"]' for p in _NOISE_PATTERNS)

'''
This function takes a url (string) as an argument and returns the ENTIRE
body. This works on most cites. The body may be needed to be truncated depending
on your use case.
'''
def get_body(url: str) -> str:
    if not url:
        return ""

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Fetch
    try:
        resp = requests.get(url, timeout = 30, headers = HEADERS)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] Failed to fetch {url[:80]}: {e}")
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # Strip non-content tags
    for tag in soup(["script", "style", "noscript", "iframe", "form",
                     "header", "footer", "nav", "aside"]):
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

"""
This function is used to call an AI model (currently Ollama) to check
if the article we parsed presents a risk to the healthcare industry.
It expects 2 arguments: the title and the body of the article which 
at this point should be already parsed and cleaned.
Returns a tuple: (is_threat, detail)
    - is_threat (bool): True if an active disruption is identified.
    - detail (str): The subsector name (if True) OR the reason for exclusion/analysis (if False).
NOTE: this will be subbed out for BERT later one
"""
def ai_check_validation(title, body) -> tuple[bool, str]:
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


"""
Once we KNOW a source classifies as a vulnerability, we need to find all the
subsector specific fields (found in src/data/schema.json) and return them in a dictionary.
"""
def find_subsector_fields(subsector, title, body) -> dict:

    # Get the specific fields for this subsector or exist if none found
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
