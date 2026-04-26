import argparse
import csv
from pathlib import Path


ALLOWED_FORMS = {"8-K", "10-Q", "10-K", "10-K/A", "20-F", "DEF 14A"}
BASE_URL = "https://www.sec.gov/Archives/edgar/data"


def _normalize_form(form_value: str) -> str:
	if form_value is None:
		return ""
	return str(form_value).strip().upper()


def _normalize_cik(cik_value: str) -> str:
	# CIK in the SEC path should not contain punctuation and should not be zero-padded.
	digits = "".join(ch for ch in str(cik_value) if ch.isdigit())
	return str(int(digits)) if digits else ""


def _normalize_accession(accession_value: str) -> str:
	# Remove separators and front-pad with 0s to 18 digits as requested.
	digits = "".join(ch for ch in str(accession_value) if ch.isdigit())
	return digits.zfill(18)


def build_edgar_url(record: dict) -> str | None:
	form = _normalize_form(record.get("form"))
	if form not in ALLOWED_FORMS:
		return None

	cik = _normalize_cik(record.get("cik", ""))
	accession = _normalize_accession(record.get("accessionNumber", ""))
	primary_document = str(record.get("primaryDocument", "")).strip()

	if not cik or not accession or not primary_document:
		return None

	print(f"{BASE_URL}/{cik}/{accession}/{primary_document}")

	return f"{BASE_URL}/{cik}/{accession}/{primary_document}"


def add_edgar_urls(sec_extracted_data: list[dict]) -> list[dict]:
	updated = []

	for row in sec_extracted_data:
		row_copy = dict(row)
		url = build_edgar_url(row_copy)
		if url:
			row_copy["edgarUrl"] = url
		updated.append(row_copy)

	return updated


def read_csv_records(input_path: Path) -> list[dict]:
	with open(input_path, "r", encoding="utf-8", newline="") as f:
		reader = csv.DictReader(f)
		return list(reader)


def write_csv_records(output_path: Path, rows: list[dict]) -> None:
	if not rows:
		with open(output_path, "w", encoding="utf-8", newline="") as f:
			f.write("")
		return

	fieldnames = list(rows[0].keys())
	if "edgarUrl" not in fieldnames:
		fieldnames.append("edgarUrl")

	with open(output_path, "w", encoding="utf-8", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames)
		writer.writeheader()
		for row in rows:
			writer.writerow(row)


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Build SEC EDGAR archive URLs for eligible filing forms (CSV only)."
	)
	parser.add_argument(
		"--input",
		default="sec_extracted_data.csv",
		help="Path to CSV file containing filing records.",
	)
	parser.add_argument(
		"--output",
		default="sec_extracted_data_with_urls.csv",
		help="Path to write updated CSV records.",
	)
	args = parser.parse_args()

	input_path = Path(args.input)
	output_path = Path(args.output)

	sec_extracted_data = read_csv_records(input_path)

	updated = add_edgar_urls(sec_extracted_data)
	write_csv_records(output_path, updated)

	print(f"Wrote {len(updated)} records to {output_path}")


if __name__ == "__main__":
	main()
