"""Validate JSON files that follow the Healthcare-GPT source schema."""

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
	re.compile(r"^\d{2}/\d{2}/\d{4}$"),
)


def _has_letter(value: Any) -> bool:
	"""Check if the value is a string that contains at least one letter.
	This ensures that fields like title and source_name are not just numbers or empty."""
	return isinstance(value, str) and any(character.isalpha() for character in value)


def _is_valid_date(value: Any) -> bool:
	"""Check if the value is a string that matches one of the allowed date formats."""
	return isinstance(value, str) and any(pattern.fullmatch(value) for pattern in DATE_PATTERNS)


def validate_source(source: dict[str, Any], index: int) -> list[str]:
	"""Validate a single source object and return a list of error messages."""
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
	if not isinstance(direct_link, str) or not direct_link.startswith("https://"):
		errors.append(f"{prefix}: direct_link must start with https://")

	# Validate date fields
	for field_name in ("date_accessed", "date_published"):
		if not _is_valid_date(source.get(field_name)):
			errors.append(
				f"{prefix}: {field_name} must be YYYY-MM-DD, YYYY-MM-DD HH-MM, or MM/DD/YYYY"
			)

	subsector = source.get("subsector")
	if subsector not in ALLOWED_SUBSECTORS:
		allowed_values = ", ".join(sorted(ALLOWED_SUBSECTORS))
		errors.append(f"{prefix}: subsector must be one of {allowed_values}")

	return errors


def validate_json_file(file_path: Path) -> list[str]:
	"""Validate the JSON file at the given path and return a list of error messages."""
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
	"""Main function to parse arguments and validate the JSON file."""
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
