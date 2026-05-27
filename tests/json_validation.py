"""Validate JSON files

This module provides a small command-line validator for the processed source
files produced by the pipeline. It checks the top-level container shape and a
handful of required fields on each source entry so schema regressions are
detected early in tests or local workflows.

Use by adding the path of the JSON file to validate as an argument.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from geography_constants import (
    STATE_ABBREVIATION_TO_NAME,
    US_TERRITORIES,
    US_DOMESTIC_HINTS,
    CITY_NAMES,
    INTERNATIONAL_HINTS,
    COUNTRIES_AND_CODE,
)


ALLOWED_SUBSECTORS = {
    "drug_shortage",
    "medical_device_shortage",
    "cyber_attack",
    "natural_disaster",
    "other",
    "none",
}


DATE_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}$"),
    re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$"),
)


def _has_letter(value: Any) -> bool:
    """Return True when value is a string containing at least one letter."""
    return isinstance(value, str) and any(character.isalpha() for character in value)


def _is_valid_date(value: Any, allow_empty: bool = False) -> bool:
    """Return True when value matches one of the accepted date formats.

    The validator accepts YYYY-MM-DD, YYYY-MM-DD HH-MM, and
    YYYY-MM-DD HH:MM. date_published may also be empty.
    """
    if allow_empty and value == "":
        return True
    return isinstance(value, str) and any(
        pattern.fullmatch(value) for pattern in DATE_PATTERNS
    )


def _normalize_geography_scope(value: Any) -> str | None:
    """Normalize a geography scope value to a canonical US label when possible.

    Parameters:
        value: The original geography_scope value to analyze and normalize.

    Returns:
        A normalized geography scope string
    """
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    lowered = text.lower()

    def _contains_hint(hint: str) -> bool:
        return re.search(rf"\b{re.escape(hint)}\b", lowered) is not None

    # Check for state abbreviations and names first, as they are more specific than general US hints
    for abbreviation, state_name in STATE_ABBREVIATION_TO_NAME.items():
        if _contains_hint(abbreviation.lower()):
            return state_name

    # Check for state names next, as they may be present without abbreviations
    for state_name in STATE_ABBREVIATION_TO_NAME.values():
        if _contains_hint(state_name.lower()):
            return state_name

    # Recognize major US city names as an indicator that the scope is within the US.
    for city_name in CITY_NAMES:
        if _contains_hint(city_name.lower()):
            return "US"

    # If a known country name (from COUNTRIES_AND_CODE) appears, treat as outside US
    for country in COUNTRIES_AND_CODE.keys():
        if _contains_hint(country.lower()):
            return "Outside US"

    # Check for US territories before general US hints, as they are also more specific
    for territory in US_TERRITORIES:
        if _contains_hint(territory):
            return "US Territory"

    if any(_contains_hint(hint) for hint in INTERNATIONAL_HINTS):
        return "Outside US"

    if any(_contains_hint(hint) for hint in US_DOMESTIC_HINTS):
        return "US"

    return None


def validate_source(source: dict[str, Any], index: int) -> list[str]:
    """Validate one source record and return any schema errors found.

    Each record needs a non-empty identifier, descriptive text fields, a URL
    that begins with http, an allowed subsector, normalized geography_scope
    when present, and date fields in one of the accepted formats.
    """
    errors: list[str] = []
    source_id = source.get("id", f"index {index}")
    prefix = f"Source {index} (id={source_id})"

    source_id_value = source.get("id")
    if source_id_value is None:
        errors.append(f"{prefix}: missing id")
    elif any(character.isspace() for character in str(source_id_value)):
        errors.append(f"{prefix}: id should not contain spaces")

    if not _has_letter(source.get("title")):
        errors.append(f"{prefix}: title must contain at least one letter")

    if not _has_letter(source.get("source_name")):
        errors.append(f"{prefix}: source_name must contain at least one letter")

    direct_link = source.get("direct_link")
    if not isinstance(direct_link, str) or not direct_link.startswith("http"):
        errors.append(f"{prefix}: direct_link must start with http")

    # Validate date fields
    for field_name in ("date_accessed", "date_published"):
        allow_empty = field_name == "date_published"
        if not _is_valid_date(source.get(field_name), allow_empty=allow_empty):
            errors.append(
                f"{prefix}: {field_name} must be YYYY-MM-DD, YYYY-MM-DD HH-MM, or YYYY-MM-DD HH:MM"
            )

    geography_scope = source.get("geography_scope")
    # Normalize explicit 'null' or empty values to Python None (JSON null)
    if geography_scope is None or (
        isinstance(geography_scope, str)
        and geography_scope.strip().lower() in ("", "null")
    ):
        source["geography_scope"] = None
    else:
        normalized_scope = _normalize_geography_scope(geography_scope)
        source["geography_scope"] = normalized_scope
        if normalized_scope == "Outside US":
            errors.append(f"{prefix}: geography_scope is Outside US")

    subsector = source.get("subsector")
    if subsector not in ALLOWED_SUBSECTORS:
        allowed_values = ", ".join(sorted(ALLOWED_SUBSECTORS))
        errors.append(f"{prefix}: subsector must be one of {allowed_values}")

    return errors


def validate_json_file(file_path: Path, remove_outside_us: bool = False) -> list[str]:
    """Validate a JSON file that follows the defined schema.

    The file must contain an object at the top level with a sources array.
    Each item in that array is validated with validate_source.

    Returns a list of human-readable error messages. An empty list means the
    file passed all current checks.
    """
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return [f"File not found: {file_path}"]
    except json.JSONDecodeError as exc:
        return [f"Invalid JSON: {exc}"]

    if not isinstance(data, dict):
        return ["Top-level JSON value must be an object containing a sources array"]

    sources = data.get("sources")
    if not isinstance(sources, list):
        return ["Top-level JSON must contain a sources array"]

    errors: list[str] = []
    # Validate each source in the sources array
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            errors.append(f"Source {index}: each item in sources must be an object")
            continue
        errors.extend(validate_source(source, index))

    # Optionally remove records normalized to Outside US and write back the file
    if remove_outside_us:
        cleaned = [
            s
            for s in sources
            if not (isinstance(s, dict) and s.get("geography_scope") == "Outside US")
        ]
        if len(cleaned) != len(sources):
            try:
                with file_path.open("w", encoding="utf-8") as handle:
                    json.dump(
                        {"sources": cleaned}, handle, indent=2, ensure_ascii=False
                    )
                errors.append("Removed records normalized to Outside US from file")
            except Exception as exc:
                errors.append(f"Failed to write cleaned file: {exc}")

    return errors


def main(argv: list[str] | None = None) -> int:
    """Run the validator as a command-line program.

    The CLI accepts a single path argument, prints each validation error on its
    own line, and exits with 1 when validation fails or 0 when the file
    is valid.
    """
    parser = argparse.ArgumentParser(
        description="Validate a Healthcare-GPT JSON file against the expected schema checks."
    )
    parser.add_argument("json_file", help="Path to the JSON file to validate")
    parser.add_argument(
        "--normalize",
        "-n",
        action="store_true",
        help=(
            "Normalize geography_scope in place. When present, also remove records "
            "normalized to 'Outside US' and write the cleaned file back. Without "
            "this flag the validator only reports issues."
        ),
    )
    args = parser.parse_args(argv)

    errors = validate_json_file(Path(args.json_file), remove_outside_us=args.normalize)
    if errors:
        for error in errors:
            print(error)
        return 1

    print("Validation successful")
    return 0


if __name__ == "__main__":
    sys.exit(main())
