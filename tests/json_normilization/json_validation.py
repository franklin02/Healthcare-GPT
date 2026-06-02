"""Validate JSON files

This module provides a small command-line validator for the processed source
files produced by the pipeline. It checks the top-level container shape and a
handful of required fields on each source entry so schema regressions are
detected early in tests or local workflows.

Use by adding the path of the JSON file to validate as an argument.

Usage: python json_validation.py [-n --normalize] path/to/file.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.json_normilization.geography_constants import (  # noqa: E402
    STATE_ABBREVIATION_TO_NAME,
    US_TERRITORIES,
    US_DOMESTIC_HINTS,
    INTERNATIONAL_HINTS,
    COUNTRIES_AND_CODE,
)

from src.logging_utils import get_file_logger  # noqa: E402

LOG_FILE = PROJECT_ROOT / "data" / "logs" / "json_validation.log"
LOGGER = get_file_logger(__name__, LOG_FILE)

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


US_CITIES_PATH = Path(__file__).with_name("uscities.csv")
LOCALITY_SUFFIXES = (
    " city",
    " county",
    " parish",
    " borough",
    " municipality",
    " township",
    " town",
    " village",
    " census area",
)


def _normalize_place_key(value: str) -> str:
    """Normalize a place name for exact matching."""
    return " ".join(value.lower().split())


def _strip_locality_words(value: str) -> str:
    """Remove common locality prefixes and suffixes from a place name.

    Parameters:
        value: The normalized place name to strip of common locality words.

    Returns:
        The place name with common locality prefixes and suffixes removed, if present.
    """
    text = _normalize_place_key(value)

    # Remove common locality prefixes like "city of" or "county of"
    for prefix in ("city of ", "county of "):
        if text.startswith(prefix):
            text = text[len(prefix) :]

    # Remove common locality suffixes like " city" or " county"
    for suffix in LOCALITY_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()

    return text


@lru_cache(maxsize=1)
def _load_us_place_mappings() -> tuple[
    dict[str, dict[str, int]], dict[str, dict[str, int]]
]:
    """
    Load city and county lookups from the bundled uscities.csv file.

    Returns:
        A tuple of two dictionaries: (city_to_states, county_to_states). Each maps
        normalized place names to tuples of state names where that place exists.
    """
    # Map normalized place -> {state_name: max_population}
    city_states: dict[str, dict[str, int]] = {}
    county_states: dict[str, dict[str, int]] = {}

    def _add(
        mapping: dict[str, dict[str, int]], key: str, state_name: str, population: int
    ) -> None:
        if not key:
            LOGGER.warning(
                f"Attempted to add empty key for state {state_name} with population {population}, skipping"
            )
            return
        entry = mapping.setdefault(key, {})
        # keep the largest observed population for this state+place key
        entry[state_name] = max(entry.get(state_name, 0), population)

    try:
        with US_CITIES_PATH.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            # The CSV contains city and county names along with their corresponding state names.
            for row in reader:
                state_name = (row.get("state_name") or "").strip()
                if not state_name:
                    continue

                # Normalize and index city names
                # Parse population if available; fall back to 0 on error
                pop_val = 0
                try:
                    pop_raw = (row.get("population") or "0").strip()
                    pop_val = int(float(pop_raw)) if pop_raw else 0
                except Exception:
                    LOGGER.warning(
                        f"Failed to parse population value {row.get('population')!r} for city {row.get('city_ascii')!r}, defaulting to 0"
                    )
                    pop_val = 0

                for field_name in ("city_ascii", "city"):
                    city_name = row.get(field_name)
                    if city_name:
                        city_key = _normalize_place_key(city_name)
                        _add(city_states, city_key, state_name, pop_val)
                        stripped_city = _strip_locality_words(city_key)
                        if stripped_city != city_key:
                            _add(city_states, stripped_city, state_name, pop_val)

                county_name = row.get("county_name")
                if county_name:
                    county_key = _normalize_place_key(county_name)
                    _add(county_states, county_key, state_name, pop_val)
                    stripped_county = _strip_locality_words(county_key)
                    if stripped_county != county_key:
                        _add(county_states, stripped_county, state_name, pop_val)
    except FileNotFoundError:
        LOGGER.error(
            f"US cities file not found at {US_CITIES_PATH}, city/county scope normalization will be unavailable"
        )
        return {}, {}
    LOGGER.info(
        f"Loaded US place mappings: {len(city_states)} cities and {len(county_states)} counties"
    )
    return city_states, county_states


def _has_letter(value: Any) -> bool:
    """Return True when value is a string containing at least one letter."""
    return isinstance(value, str) and any(character.isalpha() for character in value)


def _is_valid_date(value: Any, allow_empty: bool = False) -> bool:
    """Return True when value matches one of the accepted date formats.

    The validator accepts YYYY-MM-DD, YYYY-MM-DD HH-MM, and
    YYYY-MM-DD HH:MM. date_published may also be empty.
    """
    if allow_empty and value == "":
        LOGGER.debug("Allowing empty string for date field")
        return True
    LOGGER.debug(f"Validating date value: {value!r}")
    return isinstance(value, str) and any(
        pattern.fullmatch(value) for pattern in DATE_PATTERNS
    )


def _iter_place_candidates(value: str) -> list[str]:
    """Yield normalized place-name candidates extracted from a scope string.

    Inputs:
        value: The original geography_scope string to analyze for place-name candidates.

    Returns:
        A list of normalized place-name candidates derived from the input string.
    """
    text = _normalize_place_key(value)
    if not text:
        LOGGER.debug("No text to extract place candidates from after normalization")
        return []

    candidates: list[str] = [text]
    candidates.extend(
        part.strip() for part in re.split(r"[,;/|]+", text) if part.strip()
    )

    normalized_candidates: list[str] = []
    # Iterate through candidates and add both the normalized and stripped versions to the list of candidates to check against our city/county mappings.
    for candidate in candidates:
        normalized_candidate = _normalize_place_key(candidate)
        stripped_candidate = _strip_locality_words(normalized_candidate)

        # Add the normalized candidate if it's not empty and not already in the list
        for item in (normalized_candidate, stripped_candidate):
            if item and item not in normalized_candidates:
                normalized_candidates.append(item)
    LOGGER.debug(
        f"Extracted place candidates from {value!r}: {normalized_candidates!r}"
    )
    return normalized_candidates


def _lookup_us_place_scope(value: Any) -> str | None:
    """Resolve a city or county scope to a state when the match is unambiguous.

    Parameters:
        value: The original geography_scope value to analyze for city or county matches.

    Returns:
        The name of the US state when a unique city or county match is found, or None otherwise.
    """
    if not isinstance(value, str):
        LOGGER.debug(
            f"Value {value!r} is not a string, cannot lookup as US place scope"
        )
        return None

    city_states, county_states = _load_us_place_mappings()
    # Iterate through candidates and resolve to the best matching state.
    for candidate in _iter_place_candidates(value):
        city_matches = city_states.get(candidate)
        if city_matches:
            # If there's only one state, return it. If multiple, choose the state
            # where that place has the highest population in our DB.
            if len(city_matches) == 1:
                LOGGER.debug(
                    f"Unique city match found for candidate {candidate!r}: {city_matches}, selecting state {next(iter(city_matches))}"
                )
                return next(iter(city_matches))
            LOGGER.debug(
                f"Multiple city matches found for candidate {candidate!r}: {city_matches}, selecting state with highest population"
            )
            return max(city_matches.items(), key=lambda kv: kv[1])[0]

        county_matches = county_states.get(candidate)
        if county_matches:
            if len(county_matches) == 1:
                LOGGER.debug(
                    f"Unique county match found for candidate {candidate!r}: {county_matches}, selecting state {next(iter(county_matches))}"
                )
                return next(iter(county_matches))
            LOGGER.debug(
                f"Multiple county matches found for candidate {candidate!r}: {county_matches}, selecting state with highest population"
            )
            return max(county_matches.items(), key=lambda kv: kv[1])[0]
    LOGGER.debug(f"No city or county matches found for value {value!r}")
    return None


def _normalize_geography_scope(value: Any) -> str | None:
    """Normalize a geography scope value to a canonical US label when possible.

    Parameters:
        value: The original geography_scope value to analyze and normalize.

    Returns:
        A normalized geography scope string
    """
    if not isinstance(value, str):
        LOGGER.debug(
            f"Value {value!r} is not a string, cannot normalize geography scope"
        )
        return None

    text = value.strip()
    if not text:
        LOGGER.debug(f"Value {value!r} is empty after stripping, cannot normalize")
        return None

    lowered = text.lower()

    # If the value is already one of our canonical labels or a full state name,
    # preserve it exactly as provided instead of attempting further normalization.
    if lowered in ("us", "us territory", "outside us") or any(
        lowered == state.lower() for state in STATE_ABBREVIATION_TO_NAME.values()
    ):
        LOGGER.debug(f"Value {value!r} is already a canonical geography scope")
        return text

    def _contains_hint(hint: str) -> bool:
        return re.search(rf"\b{re.escape(hint)}\b", lowered) is not None

    has_explicit_separator = bool(re.search(r"[,;/|]", text))

    if has_explicit_separator:
        # State hint first
        for abbreviation, state_name in STATE_ABBREVIATION_TO_NAME.items():
            if _contains_hint(abbreviation.lower()):
                LOGGER.debug(
                    f"Found state abbreviation hint {abbreviation} in value {value!r}, normalizing to {state_name}"
                )
                return state_name

        for state_name in STATE_ABBREVIATION_TO_NAME.values():
            if _contains_hint(state_name.lower()):
                LOGGER.debug(
                    f"Found state name hint {state_name} in value {value!r}, normalizing to {state_name}"
                )
                return state_name

        # Fallback to city/county lookup
        city_or_county_state = _lookup_us_place_scope(text)
        if city_or_county_state:
            LOGGER.debug(
                f"Found unique geography scope for {value!r}: {city_or_county_state}"
            )
            return city_or_county_state
    else:
        # No explicit separator: try city/county lookup first, then state hints.
        city_or_county_state = _lookup_us_place_scope(text)
        if city_or_county_state:
            LOGGER.debug(
                f"Found unique geography scope for {value!r}: {city_or_county_state}"
            )
            return city_or_county_state

        for abbreviation, state_name in STATE_ABBREVIATION_TO_NAME.items():
            if _contains_hint(abbreviation.lower()):
                LOGGER.debug(
                    f"Found state abbreviation hint {abbreviation} in value {value!r}, normalizing to {state_name}"
                )
                return state_name

        for state_name in STATE_ABBREVIATION_TO_NAME.values():
            if _contains_hint(state_name.lower()):
                LOGGER.debug(
                    f"Found state name hint {state_name} in value {value!r}, normalizing to {state_name}"
                )
                return state_name

    # If a known country name (from COUNTRIES_AND_CODE) appears, treat as outside US
    for country in COUNTRIES_AND_CODE.keys():
        if _contains_hint(country.lower()):
            LOGGER.debug(
                f"Found country hint {country} in value {value!r}, treating as outside US"
            )
            return "Outside US"

    # Check for US territories before general US hints, as they are also more specific
    for territory in US_TERRITORIES:
        if _contains_hint(territory):
            LOGGER.debug(
                f"Found US territory hint {territory} in value {value!r}, normalizing to US Territory"
            )
            return "US Territory"

    if any(_contains_hint(hint) for hint in INTERNATIONAL_HINTS):
        LOGGER.debug(
            f"Found international hint in value {value!r}, treating as outside US"
        )
        return "Outside US"

    if any(_contains_hint(hint) for hint in US_DOMESTIC_HINTS):
        LOGGER.debug(f"Found US domestic hint in value {value!r}, normalizing to US")
        return "US"
    LOGGER.debug(f"No hints found to normalize value {value!r}, leaving as is")
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
    LOGGER.debug(f"{prefix}: validating source with id value {source_id_value!r}")
    if source_id_value is None:
        LOGGER.info(f"{prefix}: missing id field")
        errors.append(f"{prefix}: missing id")
    elif any(character.isspace() for character in str(source_id_value)):
        LOGGER.info(f"{prefix}: id contains whitespace, which is not allowed")
        errors.append(f"{prefix}: id should not contain spaces")

    LOGGER.debug(
        f"{prefix}: validating title {source.get('title')!r} and source_name {source.get('source_name')!r}"
    )
    if not _has_letter(source.get("title")):
        LOGGER.info(f"{prefix}: title does not contain any letters")
        errors.append(f"{prefix}: title must contain at least one letter")

    if not _has_letter(source.get("source_name")):
        LOGGER.info(f"{prefix}: source_name does not contain any letters")
        errors.append(f"{prefix}: source_name must contain at least one letter")

    LOGGER.debug(f"{prefix}: validating direct_link {source.get('direct_link')!r}")
    direct_link = source.get("direct_link")
    if not isinstance(direct_link, str) or not direct_link.startswith("http"):
        LOGGER.info(
            f"{prefix}: direct_link is not a string starting with http, which is required"
        )
        errors.append(f"{prefix}: direct_link must start with http")

    # Validate date fields
    for field_name in ("date_accessed", "date_published"):
        allow_empty = field_name == "date_published"
        if not _is_valid_date(source.get(field_name), allow_empty=allow_empty):
            LOGGER.info(
                f"{prefix}: {field_name} value {source.get(field_name)!r} is not in a valid format"
            )
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
        if normalized_scope != geography_scope:
            LOGGER.info(
                f"{prefix}: geography_scope normalized from {geography_scope!r} to {normalized_scope!r}"
            )
        source["geography_scope"] = normalized_scope

    LOGGER.debug(f"{prefix}: validating subsector {source.get('subsector')!r}")
    subsector = source.get("subsector")
    if subsector not in ALLOWED_SUBSECTORS:
        allowed_values = ", ".join(sorted(ALLOWED_SUBSECTORS))
        LOGGER.info(
            f"{prefix}: subsector {subsector!r} is not one of the allowed values: {allowed_values}"
        )
        errors.append(f"{prefix}: subsector must be one of {allowed_values}")

    LOGGER.debug(f"{prefix}: validation completed with {len(errors)} error(s)")
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
        LOGGER.error(f"File not found: {file_path}")
        return [f"File not found: {file_path}"]
    except json.JSONDecodeError as exc:
        LOGGER.error(f"Invalid JSON in file {file_path}: {exc}")
        return [f"Invalid JSON: {exc}"]

    if not isinstance(data, dict):
        LOGGER.error(
            f"Top-level JSON value must be an object containing a sources array, got {type(data).__name__}"
        )
        return ["Top-level JSON value must be an object containing a sources array"]

    sources = data.get("sources")
    if not isinstance(sources, list):
        LOGGER.error(
            f"Top-level JSON must contain a sources array, got {type(sources).__name__}"
        )
        return ["Top-level JSON must contain a sources array"]

    errors: list[str] = []
    # Validate each source in the sources array
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            LOGGER.info(
                f"Source {index}: each item in sources must be an object, got {type(source).__name__}"
            )
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
                LOGGER.info(
                    f"Removed records normalized to Outside US from file {file_path}, updated file with {len(cleaned)} sources remaining"
                )
                errors.append("Removed records normalized to Outside US from file")
            except Exception as exc:
                LOGGER.error(f"Failed to write cleaned file: {exc}")
                errors.append(f"Failed to write cleaned file: {exc}")

    LOGGER.info(
        f"Validation completed for file {file_path} with {len(errors)} error(s)"
    )
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
            LOGGER.error(error)
        return 1

    print("Validation successful")
    LOGGER.info(f"Validation successful for file: {args.json_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
