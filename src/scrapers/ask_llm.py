"""Facilitates interactions with a large language model (LLM).

Attributes
    - `AI_URL`: The base URL for the AI service.
    - `AI_MODEL`: The specific model that the AI will use for processing.
    - `SUBSECTOR_FIELDS`: A dictionary that maps subsectors to their specific fields.

Functions:
    - `ai_check_validation`: Parses and verifies whether a healthcare-related article describes an ongoing operational disruption or confirmed breach
       at a named healthcare entity based on strict, predefined criteria.
    - `extract_fields`: Extracts sector and subsector fields for a confirmed healthcare disruption using the provided article title and body.

Possible subsectors:
        - "drug_shortage": A confirmed shortage of a named drug patients need now.
        - "medical_device_shortage": A confirmed inability to supply a specific named medical device.
        - "cyber_attack": A confirmed breach or attack involving a named healthcare entity.
        - "natural_disaster": Operational shutdowns due to fire, flood, storm, or other physical events.
        - "other": Other confirmed operational disruptions that do not fit the previous categories.
        - "none": Used when no operational disruption or breach is confirmed.
"""

import json
import requests

AI_URL = "http://localhost:11434/api/generate"
AI_MODEL = "llama3.2"


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
    """
    Parses and verifies whether a healthcare-related article describes an ongoing operational disruption or confirmed breach at a named healthcare entity based on strict, predefined criteria.

    Parameters:
        title (str): The title of the article being analyzed.
        body (str): The main content or excerpt of the article.
        use_bert (bool): If true, run BERT first as a lightweight rejection
            filter before sending the article to the LLM.

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
            return False, "BERT: unrelated news"
        print(f"[BERT] flagged as '{bert_subsector}' sending to LLM for confirmation")

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


def extract_fields(subsector, title, body) -> tuple[dict, dict]:
    subsector_fields = SUBSECTOR_FIELDS.get(subsector)
    if not subsector_fields:
        print(f"No fields found for subsector: {subsector}")
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
        raw = json.loads(raw_response)

        sector_data = {k: raw.get(k) for k in LLM_SECTOR_FIELDS}
        subsector_data = {k: raw.get(k) for k in subsector_fields}
        return sector_data, subsector_data

    except Exception as e:
        print(f"Error extracting fields: {e}")
        return (
            {k: None for k in LLM_SECTOR_FIELDS},
            {k: None for k in subsector_fields},
        )
