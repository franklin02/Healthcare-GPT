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


def validate_source(source: dict[str, Any], index: int) -> list[str]:
    """Validate one source record and return any schema errors found.

    Each record needs a non-empty identifier, descriptive text fields, a URL
    that begins with http, an allowed subsector, and date fields in one of
    the accepted formats.
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

    subsector = source.get("subsector")
    if subsector not in ALLOWED_SUBSECTORS:
        allowed_values = ", ".join(sorted(ALLOWED_SUBSECTORS))
        errors.append(f"{prefix}: subsector must be one of {allowed_values}")

    return errors


def validate_json_file(file_path: Path) -> list[str]:
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
    args = parser.parse_args(argv)

    errors = validate_json_file(Path(args.json_file))
    if errors:
        for error in errors:
            print(error)
        return 1

    print("Validation successful")
    return 0


if __name__ == "__main__":
    sys.exit(main())
