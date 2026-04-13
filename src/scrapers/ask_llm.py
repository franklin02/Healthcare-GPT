import json, uuid, datetime, csv
import requests, time
from pathlib import Path
from bs4 import BeautifulSoup
from enum import Enum

AI_URL = "http://localhost:11434/api/generate"
AI_MODEL = "llama3.2"
SUBSECTOR_FIELDS = {
    "drug_shortage": [
        "drug_name", "generic_name", "manufacturer", "dosage_form", 
        "shortage_reason", "estimated_resolution_date", "affected_regions"
    ],
    "medical_device_shortage": [
        "device_name", "device_category", "manufacturer", "manufacturer_country", 
        "shortage_reason", "fda_recall_number", "recall_class", 
        "affected_specialties", "alternatives_available", "estimated_resolution_date"
    ],
    "cyber_attack": [
        "attack_type", "threat_actor", "individuals_affected", "data_types_exposed", 
        "systems_affected", "ransom_demanded_usd", "ransom_paid", "downtime_days", 
        "services_disrupted", "law_enforcement_involved", "hhs_breach_portal_listed"
    ],
    "natural_disaster": [
        "disaster_type", "disaster_name", "fema_declaration_id", "category_magnitude", 
        "affected_facilities_count", "evacuation_ordered", "field_hospitals", 
        "beds_offline", "facility_status", "estimated_damage_usd", 
        "infrastructure_damage", "services_disrupted"
    ],
    "other": [
        "event_type", "event_description", "severity", "departments_affected", 
        "staff_type_affected", "beds_offline", "services_disrupted", "regulatory_response"
    ]
}

'''
This function is used to call an AI model (current Ollama) to check
if the article we parsed presents a risk to the healthcare industry.
It expects 2 arguments: the title and the body of the article which 
at this point should be already parsed and cleaned.

'''
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
                "options": { "temperature": 0.1 }
            },
            timeout=60,
        )
        
        raw_response = resp.json().get("response", "{}")
        data = json.loads(raw_response)
        
        is_threat = data.get("is_operational_disruption", False)

        # Use subsector if it's a threat, otherwise use the analysis as the "reason"
        detail = data.get("subsector", "none") if is_threat else data.get("analysis", "No impact detected")
        
        return is_threat, detail

    except Exception as e:
        print(f"Error parsing AI response: {e}")
        return False, "Parsing Error"


'''
Once we KNOW a source clasifies as a vulnerability, we need to find all the
subsector specific fields (found in src/data/schema.json) and return them in a dictionary.
'''
def find_subsector_fields(subsector, title, body) -> dict:

    # Get the specific fields for this subsector or exist if none found
    fields_to_extract = SUBSECTOR_FIELDS.get(subsector)
    if not fields_to_extract:
        print(f"No fields found for subsector: {subsector}")
        exit(1)
    
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
                "options": { "temperature": 0.0 }
            },
            timeout=30,
        )
        
        raw_response = resp.json().get("response", "{}")
        return json.loads(raw_response)

    except Exception as e:
        print(f"Error extracting subsector fields: {e}")
        return {key: None for key in fields_to_extract}
    