import argparse
import csv
import datetime
import json
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Takes in data from a csv with SEC EDGAR document information, buillds URLs to the filings, scrapes the content, and outputs a JSON file with schema-aligned records.
# Use -c to create a CSV with the URLs before scraping. Reccomended to run with -n <number> to limit how many documents are scraped, as there are hundreds of thousands of filings even in the filtered CSV. 
# Usage: python edgar_scraper.py [-c Creates CSV with URLs] [--input <input_csv>] [--output-csv <output_csv>] [--output-json <output_json>] [-n <max_entries>]

ALLOWED_FORMS = {"8-K", "10-Q", "10-K", "10-K/A", "20-F", "DEF 14A"}
# ALLOWED_FORMS = {"8-K"}
BASE_URL = "https://www.sec.gov/Archives/edgar/data"
HEADERS = {
	"User-Agent": (
		"ResearchBot/1.0 boisestate.edu"
	)
}

# Normalizes form values from the CSV
def _normalize_form(form_value: str) -> str:
	if form_value is None:
		return ""
	return str(form_value).strip().upper()

# CIK in the SEC path should not contain punctuation and should not be zero-padded.
def _normalize_cik(cik_value: str) -> str:
	digits = "".join(ch for ch in str(cik_value) if ch.isdigit())
	return str(int(digits)) if digits else ""

# Accession number in the SEC path should be 18 digits, zero-padded, and without dashes.
def _normalize_accession(accession_value: str) -> str:
	digits = "".join(ch for ch in str(accession_value) if ch.isdigit())
	return digits.zfill(18)

# Primary document should be a non-empty string, stripped of whitespace.
def build_edgar_url(record: dict) -> str | None:
	form = _normalize_form(record.get("form"))
	if form not in ALLOWED_FORMS:
		return None

	cik = _normalize_cik(record.get("cik", ""))
	accession = _normalize_accession(record.get("accessionNumber", ""))
	primary_document = str(record.get("primaryDocument", "")).strip()

	if not cik or not accession or not primary_document:
		return None

	return f"{BASE_URL}/{cik}/{accession}/{primary_document}"

# Given a URL, fetch the page content
def add_edgar_urls(sec_extracted_data: list[dict]) -> list[dict]:
	updated = []

	# For each record, build the EDGAR URL and add it to the record if valid
	for row in sec_extracted_data:
		row_copy = dict(row)
		url = build_edgar_url(row_copy)
		if not url:
			continue
		row_copy["edgarUrl"] = url
		updated.append(row_copy)

	return updated

# Given a URL, fetch the page content
def scrape_filing_content(url: str, timeout: int = 30) -> tuple[str, str]:
	response = requests.get(url, headers=HEADERS, timeout=timeout)
	response.raise_for_status()
	soup = BeautifulSoup(response.content, "html.parser")

	# Remove common boilerplate tags to reduce noise in the extracted content
	for tag in soup(["script", "style", "noscript"]):
		tag.decompose()

	title = soup.title.get_text(strip=True) if soup.title else ""
	content = soup.get_text(separator=" ", strip=True)
	return title, content


def _extract_from_item(content: str) -> str:
	if not content:
		return ""

	item_start = content.lower().find("item")
	if item_start == -1:
		return ""

	return content[item_start:].strip()


def _today_date() -> str:
	return datetime.date.today().strftime("%m/%d/%Y")

# Normalize various date formats to MM/DD/YYYY, defaulting to today's date if parsing fails or value is empty.
def _normalize_date(raw_date: str) -> str:
	value = str(raw_date or "").strip()
	if not value:
		return _today_date()

	# Try common date formats
	for date_format in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
		try:
			parsed = datetime.datetime.strptime(value, date_format)
			return parsed.strftime("%m/%d/%Y")
		except ValueError:
			continue

	return _today_date()

# Build a source record in the schema-aligned format, using the provided index, original record, title, URL, and content.
def _build_source_record(index: int, record: dict, title: str, url: str, content: str) -> dict:
	published_date = _normalize_date(record.get("filingDate", ""))
	accessed_date = _today_date()
	name = str(record.get("name", "")).strip()
	form = str(record.get("form", "")).strip()
	title_from_csv = " - ".join(part for part in (name, form) if part)
	accession_number = str(record.get("accessionNumber", "")).strip()

	return {
		"id": accession_number or str(index),
		"title": title_from_csv or title or str(record.get("primaryDocument", "")).strip(),
		"source_name": "SEC EDGAR",
		"direct_link": url,
		"subsector": "",
		"date_accessed": accessed_date,
		"date_published": published_date,
		"content": content,
		"exec_summary": "",
		"confidence_level": "",
		"risk level": "",
		"geography_scope": "US",
		"start_date": "",
		"end_date": "",
		"resilience_or_mitigation_observed": "",
		"subsector_data": {},
	}

# Given a list of records with EDGAR URLs, scrape the content and build a schema-aligned output dictionary, optionally limiting to a maximum number of entries.
def build_schema_output(rows_with_urls: list[dict], max_entries: int | None = None) -> dict:
	sources = []

	# Iterate through the records with URLs, scrape the content, and build source records until reaching the maximum number of entries if specified.
	for row in rows_with_urls:
		if max_entries is not None and len(sources) >= max_entries:
			break

		url = row.get("edgarUrl", "")
		if not url:
			continue

		try:
			title, content = scrape_filing_content(url)
		except requests.RequestException as exc:
			print(f"Skipping {url}: request failed ({exc})")
			continue

		if not content:
			print(f"Skipping {url}: empty content")
			continue

		trimmed_content = _extract_from_item(content)
		if not trimmed_content:
			print(f"Skipping {url}: could not find 'Item'")
			continue

		source_record = _build_source_record(len(sources) + 1, row, title, url, trimmed_content)
		sources.append(source_record)
		print(json.dumps(source_record, indent=4))

	return {"sources": sources}


# Utility function for reading CSV record
def read_csv_records(input_path: Path) -> list[dict]:
	with open(input_path, "r", encoding="utf-8", newline="") as f:
		reader = csv.DictReader(f)
		return list(reader)

# Utility function for writing CSV records
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

# Utility function for writing JSON records
def write_json_records(output_path: Path, payload: dict) -> None:
	with open(output_path, "w", encoding="utf-8") as f:
		json.dump(payload, f, indent=4)


def main() -> None:
	parser = argparse.ArgumentParser(
		description=(
			"Create SEC EDGAR URL CSVs (-c) or scrape filings to schema-aligned JSON (default)."
		)
	)
	parser.add_argument(
		"--input",
		default="sec_extracted_data.csv",
		help="Path to CSV file containing filing records.",
	)
	parser.add_argument(
		"-c",
		"--create-url-csv",
		action="store_true",
		help="Create CSV with EDGAR URLs and exit (no scraping).",
	)
	parser.add_argument(
		"--output-csv",
		default="sec_extracted_data_with_urls.csv",
		help="Path to write URL-enriched CSV when using -c.",
	)
	parser.add_argument(
		"--output-json",
		default="sec_extracted_data_scraped.json",
		help="Path to write schema-aligned JSON records (default mode).",
	)
	parser.add_argument(
		"-n",
		"--max-entries",
		type=int,
		default=None,
		help="Maximum number of scraped JSON entries to create in default mode.",
	)
	args = parser.parse_args()

	input_path = Path(args.input)
	output_csv_path = Path(args.output_csv)
	output_json_path = Path(args.output_json)

	sec_extracted_data = read_csv_records(input_path)

	rows_with_urls = add_edgar_urls(sec_extracted_data)
	if args.create_url_csv:
		write_csv_records(output_csv_path, rows_with_urls)
		print(f"Wrote {len(rows_with_urls)} records to {output_csv_path}")
		return

	payload = build_schema_output(rows_with_urls, max_entries=args.max_entries)
	write_json_records(output_json_path, payload)

	print(f"Wrote {len(payload['sources'])} records to {output_json_path}")


if __name__ == "__main__":
	main()
