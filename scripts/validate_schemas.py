"""Schema-population validator for Healthcare-GPT.

This is an **opt-in, on-demand** proof-of-concept check. It is intentionally
*not* part of the pytest suite, so it never runs in CI and does not clog the
automated test cases. It exercises the real extraction path against a live
Ollama model.

What it does
------------
For each of the five subsector schemas (``drug_shortage``,
``medical_device_shortage``, ``cyber_attack``, ``natural_disaster``,
``other``) it feeds a hand-crafted "perfect" article -- one whose body
explicitly states a value for **every** field of that schema -- through the
real ``extract_fields()`` LLM extractor and asserts that every field came back
populated. This proves the extraction prompt plus the schema wiring genuinely
populate every field end-to-end, one subsector at a time.

Each article (see ``PERFECT_ARTICLES``) carries a ``description`` documenting
exactly which fields it is designed to exercise -- that is the "what is this
test doing" doc for each subsector.

Why a field can come back empty
-------------------------------
``extract_fields()`` enforces strict rules (see ``src/shared_utils.py``): it
returns ``null`` for any field not *explicitly* stated in a rule-satisfying
form -- ISO ``YYYY-MM-DD`` dates, raw numbers (no ranges/approximations),
explicitly stated booleans, and list items lifted from the text. The sample
articles are written to satisfy those rules for every field. A missing field
is therefore a *diagnostic signal* -- either the article wording drifted from
the rules, or the prompt/schema has a real gap -- not necessarily a code bug.

The "populated" check is ``value is not None`` (with empty string also treated
as unpopulated for the summary field), so a legitimate ``False`` or ``0``
counts as populated rather than a miss.

How to run
----------
Run from the repository root with the virtualenv activated and Ollama running
(the configured model must appear in ``ollama list``)::

    python -m scripts.validate_schemas               # validate all 5 schemas
    python -m scripts.validate_schemas drug_shortage # validate one schema

Exit code is non-zero if any field came back unpopulated, so the script can
double as a manual gate.
"""

from __future__ import annotations

import argparse
import sys

from src.classes.vulnerability import SUBSECTOR_DATA_CLASSES
from src.shared_utils import (
    AI_MODEL,
    LLM_SECTOR_FIELDS,
    MissingSubsectorFieldsError,
    SUBSECTOR_FIELDS,
    ensure_model_available,
    extract_fields,
    model_unavailable_error,
)

# ---------------------------------------------------------------------------
# Hand-crafted "perfect" articles.
#
# Each body is written so that EVERY field of the schema is explicitly stated
# in a form that satisfies the extractor's strict rules (ISO dates, raw
# numbers, explicit booleans, named list items). The ``description`` documents
# what the article is designed to exercise -- i.e. what that subsector's check
# is doing.
# ---------------------------------------------------------------------------
PERFECT_ARTICLES: dict[str, dict[str, str]] = {
    "drug_shortage": {
        "description": (
            "Exercises every drug_shortage field: brand drug_name (Vancocin) vs "
            "generic_name (Vancomycin Hydrochloride), manufacturer, dosage_form, "
            "shortage_reason, an ISO estimated_resolution_date, a multi-region "
            "affected_regions list, and foreign domestic_vs_foreign_dependency, "
            "plus the shared sector fields (summary, geography, start/end dates, "
            "mitigation)."
        ),
        "title": (
            "Hospira halts Vancocin injection production, triggering nationwide "
            "hospital shortage"
        ),
        "body": (
            "On 2026-01-12, Hospira halted production of Vancomycin Hydrochloride, "
            "sold under the brand name Vancocin, in its 500 mg injectable powder "
            "for solution dosage form. The shortage was caused by a sterility "
            "failure at the company's fill-finish line. Hospira told the FDA it "
            "expects supply to recover by 2026-06-30. The shortage affects "
            "hospitals in Texas, California, and New York. The active "
            "pharmaceutical ingredient is manufactured entirely overseas in "
            "India, making the supply foreign dependent. The shortage ended on "
            "2026-05-20 after the FDA approved a temporary import. To mitigate "
            "the gap, pharmacies rationed remaining stock to intensive care "
            "units and substituted Linezolid where clinically appropriate."
        ),
    },
    "medical_device_shortage": {
        "description": (
            "Exercises every medical_device_shortage field: device_name, "
            "device_category, manufacturer, manufacturer_country, "
            "shortage_reason, fda_recall_number, recall_class, an "
            "affected_specialties list, the alternatives_available boolean, an "
            "ISO estimated_resolution_date, and domestic_vs_foreign_dependency, "
            "plus the shared sector fields."
        ),
        "title": (
            "Medtronic recalls HeartWare HVAD controllers over battery fault, "
            "cardiac units face shortage"
        ),
        "body": (
            "On 2026-02-03, Medtronic issued a recall of its HeartWare HVAD "
            "System controller, a cardiac implantable circulatory support "
            "device. The recall, FDA recall number Z-1234-2026, was designated "
            "Class I. The shortage was caused by a battery connector defect that "
            "could interrupt pump power. Medtronic manufactures the controller "
            "in the United States. The recall affects the specialties of "
            "cardiology and cardiac surgery. Alternative devices are available, "
            "as hospitals can substitute the Abbott HeartMate 3. Medtronic "
            "expects corrected controllers to ship by 2026-08-15. Because the "
            "device is built domestically, the supply is domestically dependent. "
            "The event began on 2026-02-03 and the shortage was resolved on "
            "2026-07-10 once replacement units shipped. Affected centers "
            "mitigated risk by issuing backup controllers to every implanted "
            "patient."
        ),
    },
    "cyber_attack": {
        "description": (
            "Exercises every cyber_attack field: attack_type, threat_actor, the "
            "individuals_affected integer, data_types_exposed and "
            "systems_affected lists, the ransom_demanded_usd number, the "
            "ransom_paid boolean (true), the downtime_days integer, a "
            "services_disrupted list, and the law_enforcement_involved and "
            "hhs_breach_portal_listed booleans (true), plus shared sector fields."
        ),
        "title": (
            "Ransomware gang BlackCat hits Mercy Regional Hospital, encrypting "
            "EHR systems"
        ),
        "body": (
            "On 2026-03-09, Mercy Regional Hospital suffered a ransomware attack "
            "carried out by the threat actor group BlackCat. The attack exposed "
            "the protected data of 250000 patients. The data types exposed "
            "included Social Security numbers, medical records, and insurance "
            "information. The attack affected the electronic health record "
            "system, the radiology imaging system, and the billing system. "
            "BlackCat demanded a ransom of 5000000 dollars. The hospital paid the "
            "ransom in full to BlackCat. Systems were offline for 14 days. The "
            "attack "
            "disrupted emergency room intake, surgery scheduling, and laboratory "
            "services. The FBI was notified and is involved in the "
            "investigation. The breach was listed on the HHS breach portal. The "
            "incident started on 2026-03-09 and ended on 2026-03-23. The "
            "hospital diverted ambulances to neighboring facilities and reverted "
            "to paper charting during the outage."
        ),
    },
    "natural_disaster": {
        "description": (
            "Exercises every natural_disaster field: disaster_type, "
            "disaster_name, fema_declaration_id, category_magnitude, the "
            "affected_facilities_count integer, the evacuation_ordered boolean, "
            "the field_hospitals and beds_offline integers, facility_status, the "
            "estimated_damage_usd number, and infrastructure_damage / "
            "services_disrupted lists, plus shared sector fields."
        ),
        "title": "Hurricane Delia floods Gulf Coast hospitals, forcing evacuations",
        "body": (
            "On 2026-09-15, Hurricane Delia, a Category 4 storm, made landfall "
            "on the Gulf Coast of Florida. FEMA issued major disaster "
            "declaration DR-4789-FL. The hurricane affected 12 hospitals in the "
            "region. An evacuation was ordered for all coastal facilities. "
            "Responders set up 3 field hospitals to absorb displaced patients. "
            "The storm took 450 beds offline. Several facilities reported a "
            "status of partially operational. The estimated damage to healthcare "
            "facilities was 75000000 dollars. Infrastructure damage included "
            "flooded basements, roof collapses, and failed backup generators. "
            "The disaster disrupted dialysis services, emergency care, and "
            "pharmacy operations. The event began on 2026-09-15 and recovery was "
            "declared complete on 2026-10-05. Hospitals mitigated the impact by "
            "transferring critical patients to inland facilities by helicopter."
        ),
    },
    "other": {
        "description": (
            "Exercises every 'other' field with a labor-strike scenario: "
            "event_type, event_description, severity, a departments_affected "
            "list, staff_type_affected, the beds_offline integer, a "
            "services_disrupted list, and regulatory_response, plus shared "
            "sector fields."
        ),
        "title": "Nurses strike at Lakeside Medical Center halts elective surgeries",
        "body": (
            "On 2026-04-01, a labor strike began at Lakeside Medical Center. The "
            "event was a multi-day work stoppage by unionized staff over "
            "staffing ratios. The severity was described as major. The strike "
            "affected the surgery department, the emergency department, and the "
            "intensive care unit. The staff type affected was registered nurses. "
            "The hospital took 120 beds offline during the strike. The strike "
            "disrupted elective surgery, outpatient infusion, and patient "
            "transport services. The state health department issued an order "
            "requiring the hospital to maintain minimum emergency staffing. The "
            "strike started on 2026-04-01 and ended on 2026-04-08. The hospital "
            "mitigated the disruption by hiring temporary travel nurses to cover "
            "critical units."
        ),
    },
}


def is_populated(value: object) -> bool:
    """Return True if the extractor produced a real value for a field.

    A field counts as populated when it is not ``None`` and not an empty
    string. Booleans (including ``False``) and the integer ``0`` count as
    populated -- only genuinely missing values fail.
    """
    return value is not None and value != ""


def _format_value(value: object, width: int = 60) -> str:
    """Render a field value for the report, truncated to ``width`` chars."""
    text = repr(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def check_roundtrip(subsector: str, subsector_data: dict) -> bool:
    """Verify the subsector dataclass round-trips the extracted dict.

    Builds the subsector dataclass via ``from_dict`` and confirms that
    ``to_dict`` exposes exactly the fields declared in ``SUBSECTOR_FIELDS``.
    This is a fast, LLM-independent structural check that catches drift
    between ``schema.json``, the dataclasses, and ``SUBSECTOR_FIELDS``.
    """
    cls = SUBSECTOR_DATA_CLASSES[subsector]
    produced = set(cls.from_dict(subsector_data).to_dict().keys())
    expected = set(SUBSECTOR_FIELDS[subsector])
    return produced == expected


def validate(subsector: str) -> bool:
    """Run the perfect-article check for one subsector and print a report.

    Feeds the hand-crafted article through ``extract_fields`` and prints a
    per-field PASS/FAIL table covering the shared sector fields plus every
    subsector-specific field. Returns True only if every field is populated.
    """
    article = PERFECT_ARTICLES[subsector]
    expected_sector = list(LLM_SECTOR_FIELDS)

    try:
        sector_data, subsector_data = extract_fields(
            subsector, article["title"], article["body"]
        )
    except MissingSubsectorFieldsError as exc:
        print(f"\n=== {subsector} ===")
        print(f"  [ERROR] {exc}")
        return False

    expected_subsector = list(SUBSECTOR_FIELDS[subsector])

    print(f"\n=== {subsector} ===")
    print(f"{article['description']}\n")

    all_ok = True
    populated_count = 0
    total = len(expected_sector) + len(expected_subsector)

    for label, fields, data in (
        ("sector", expected_sector, sector_data),
        ("subsector", expected_subsector, subsector_data),
    ):
        for fieldname in fields:
            value = data.get(fieldname)
            ok = is_populated(value)
            populated_count += int(ok)
            all_ok = all_ok and ok
            mark = "✓" if ok else "✗"
            print(f"  [{mark}] {label:<9} {fieldname:<34} = {_format_value(value)}")

    roundtrip_ok = check_roundtrip(subsector, subsector_data)
    all_ok = all_ok and roundtrip_ok
    print(
        f"  [{'✓' if roundtrip_ok else '✗'}] round-trip  dataclass keys match SUBSECTOR_FIELDS"
    )

    print(f"  Result: {populated_count}/{total} fields populated")
    return all_ok


def main(argv: list[str] | None = None) -> int:
    """Parse args, ensure Ollama is ready, and validate the chosen schemas."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate that each schema's fields populate from a perfect article "
            "(requires Ollama running). Optional, on-demand; not part of pytest."
        )
    )
    parser.add_argument(
        "subsector",
        nargs="?",
        choices=sorted(PERFECT_ARTICLES),
        help="Validate a single subsector. Omit to validate all five.",
    )
    args = parser.parse_args(argv)

    try:
        ensure_model_available(AI_MODEL)
    except model_unavailable_error as exc:
        print(str(exc))
        print("\nStart Ollama and pull the configured model, then re-run.")
        return 2

    subsectors = [args.subsector] if args.subsector else sorted(PERFECT_ARTICLES)

    results: dict[str, bool] = {}
    for subsector in subsectors:
        results[subsector] = validate(subsector)

    print("\n=== summary ===")
    for subsector, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {subsector}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
