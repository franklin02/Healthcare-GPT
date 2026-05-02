import argparse
import csv
import datetime
import json
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# This script retrieves filings from the SEC EDGAR database for a predefined list of SIC codes.
# It generates two outputs:
# 1. A CSV file containing metadata about the filings.
# 2. A JSON file formatted for schema ingestion, containing detailed information and content from the filings.
# Usage: python edgar_scraper.py -i input.csv -n 100 -c -j --output-csv out.csv --output-json out.json

ALLOWED_FORMS = {"8-K", "10-Q", "10-K", "10-K/A", "20-F", "DEF 14A"}
BASE_URL = "https://www.sec.gov/Archives/edgar/data"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_HEADERS = {
	"User-Agent": "ResearchBot/1.0 boisestate.edu",
	"Accept-Encoding": "gzip, deflate",
	"Accept": "application/json,text/html",
}

SICS = [
	2834,
	2835,
	8000,
	8050,
	8060,
	8062,
	8071,
	8082,
	8090,
	8093,
	3841,
	3842,
	3843,
	3845,
	3851,
	6324,
	5122,
]

OUTPUT_CSV = Path("sec_edgar_filings.csv")
OUTPUT_JSON = Path("sec_edgar_filings.json")
REQUEST_DELAY_SECONDS = 0.2
PROGRESS_EVERY_CIK = 50
PROGRESS_EVERY_FILINGS = 25


def _normalize_form(form_value: str) -> str:
	if form_value is None:
		return ""
	return str(form_value).strip().upper()


def _normalize_cik(cik_value: str) -> str:
	digits = "".join(ch for ch in str(cik_value) if ch.isdigit())
	return str(int(digits)) if digits else ""


def _format_cik_for_submissions(cik_value: str) -> str:
	digits = "".join(ch for ch in str(cik_value) if ch.isdigit())
	return digits.zfill(10) if digits else ""


def _normalize_accession(accession_value: str) -> str:
	digits = "".join(ch for ch in str(accession_value) if ch.isdigit())
	return digits.zfill(18)

# Builds the URL for a filing based on the CIK, accession number, and primary document name.
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

# Adds EDGAR URLs to rows when possible, skipping rows that cannot form a valid URL.
def add_edgar_urls(rows: list[dict]) -> list[dict]:
	updated = []

	for row in rows:
		row_copy = dict(row)
		if row_copy.get("edgarUrl"):
			updated.append(row_copy)
			continue

		url = build_edgar_url(row_copy)
		if not url:
			continue

		row_copy["edgarUrl"] = url
		updated.append(row_copy)

	return updated

# Scrapes a single page of SIC results and extracts entries matching the target columns.
def fetch_sic_page(sic: int, start: int) -> BeautifulSoup:
	url = (
		"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
		f"&SIC={sic}&owner=include&match=starts-with&start={start}&count=100"
		"&hidefilings=0"
	)
	response = requests.get(url, headers=SEC_HEADERS, timeout=30)
	response.raise_for_status()
	return BeautifulSoup(response.content, "html.parser")

# Finds the target table in the SIC page and returns it along with the column indices for the target columns.
def find_target_table(soup: BeautifulSoup, target_columns: list[str]) -> tuple[BeautifulSoup | None, dict]:
	for table in soup.find_all("table"):
		headers = table.find_all("th")
		header_texts = [h.get_text(strip=True) for h in headers]
		if all(col in header_texts for col in target_columns):
			column_indices = {col: header_texts.index(col) for col in target_columns}
			return table, column_indices
	return None, {}

# Scrapes the SIC page for the given SIC code and start index, extracting entries for the target columns.
def scrape_sic_page(sic: int, start: int) -> list[dict]:
	soup = fetch_sic_page(sic, start)
	table, column_indices = find_target_table(soup, ["CIK", "Company", "State/Country"])
	if table is None:
		return []

	entries = []
	# Iterate through each row in the table and extract the target columns based on the identified indices.
	for row in table.find_all("tr"):
		cols = row.find_all("td")
		if not cols:
			continue

		if all(idx < len(cols) for idx in column_indices.values()):
			entry = {
				"cik": cols[column_indices["CIK"]].get_text(strip=True),
				"company": cols[column_indices["Company"]].get_text(strip=True),
				"stateCountry": cols[column_indices["State/Country"]].get_text(strip=True),
				"sic": str(sic),
			}
			entries.append(entry)

	return entries

# This function retrieves all CIK entries for a given SIC code by paginating through the results until no more entries are found.
def get_cik_entries(sic: int) -> list[dict]:
	start = 0
	entries = []
	page = 1
	seen_ciks = set()

	# Loop through pages of SIC results until no more entries are found, accumulating the CIK entries in a list.
	while True:
		print(f"SIC {sic}: fetching page {page} (start={start})")
		page_entries = scrape_sic_page(sic, start)
		if not page_entries:
			print(f"SIC {sic}: no entries on page {page}")
			break

		new_entries = []
		for entry in page_entries:
			cik_value = entry.get("cik", "")
			if cik_value and cik_value not in seen_ciks:
				seen_ciks.add(cik_value)
				new_entries.append(entry)

		if not new_entries:
			print(f"SIC {sic}: no new CIKs on page {page}, stopping pagination")
			break

		entries.extend(new_entries)
		start += 100
		page += 1
		print(
			f"SIC {sic}: page {page - 1} -> {len(page_entries)} rows, "
			f"{len(new_entries)} new, total {len(entries)}"
		)
		time.sleep(REQUEST_DELAY_SECONDS)

	return entries

# Fetches the submissions JSON for a given CIK, returning an empty dict if the CIK is invalid or the request fails.
def fetch_submissions(cik: str) -> dict:
	cik_padded = _format_cik_for_submissions(cik)
	if not cik_padded:
		return {}

	url = SUBMISSIONS_URL.format(cik=cik_padded)
	response = requests.get(url, headers=SEC_HEADERS, timeout=30)
	response.raise_for_status()
	return response.json()

# Builds a list of filing rows for a given CIK entry and its submissions data, extracting relevant fields
def build_filing_rows(cik_entry: dict, submissions: dict) -> list[dict]:
	recent = submissions.get("filings", {}).get("recent", {})
	accession_numbers = recent.get("accessionNumber", [])
	forms = recent.get("form", [])
	primary_docs = recent.get("primaryDocument", [])
	filing_dates = recent.get("filingDate", [])

	name = (
		submissions.get("name")
		or submissions.get("entityName")
		or submissions.get("companyName")
		or cik_entry.get("company", "")
	)

	rows = []
	# Iterate through the recent filings and build a row for each one, ensuring that the indices are within bounds for all fields.
	for idx, accession_number in enumerate(accession_numbers):
		if idx >= len(forms) or idx >= len(primary_docs) or idx >= len(filing_dates):
			continue

		row = {
			"cik": cik_entry.get("cik", ""),
			"name": name,
			"company": cik_entry.get("company", ""),
			"stateCountry": cik_entry.get("stateCountry", ""),
			"sic": cik_entry.get("sic", ""),
			"form": forms[idx],
			"accessionNumber": accession_number,
			"primaryDocument": primary_docs[idx],
			"filingDate": filing_dates[idx],
		}

		url = build_edgar_url(row)
		if not url:
			continue

		row["edgarUrl"] = url
		rows.append(row)

	return rows

# Given a URL, fetch the page content
def scrape_filing_content(url: str, timeout: int = 30) -> tuple[str, str]:
	response = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
	response.raise_for_status()
	soup = BeautifulSoup(response.content, "html.parser")

	# Remove common boilerplate tags to reduce noise in the extracted content
	for tag in soup(["script", "style", "noscript"]):
		tag.decompose()

	title = soup.title.get_text(strip=True) if soup.title else ""
	content = soup.get_text(separator=" ", strip=True)
	return title, content

# Extracts only the portion of the content starting from the first occurrence of "Item", which is a common section header in SEC filings.
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
	scanned = 0

	for row in rows_with_urls:
		if max_entries is not None and len(sources) >= max_entries:
			break

		url = row.get("edgarUrl", "")
		if not url:
			continue

		scanned += 1
		if scanned % PROGRESS_EVERY_FILINGS == 0:
			print(f"Scraping filings: scanned {scanned}, saved {len(sources)}")

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

		time.sleep(REQUEST_DELAY_SECONDS)

	return {"sources": sources}

# Writes a list of records to a CSV file, using the keys from the first record as headers. If the list is empty, it creates an empty file.
def write_csv_records(output_path: Path, rows: list[dict]) -> None:
	fieldnames = [
		"cik",
		"name",
		"company",
		"stateCountry",
		"sic",
		"form",
		"accessionNumber",
		"primaryDocument",
		"filingDate",
		"edgarUrl",
	]

	with open(output_path, "w", encoding="utf-8", newline="") as f:
		if not rows:
			f.write("")
			return

		writer = csv.DictWriter(f, fieldnames=fieldnames)
		writer.writeheader()
		# Write each row to the CSV file, ensuring that the fieldnames are consistent with the expected columns.
		for row in rows:
			writer.writerow(row)


def write_json_records(output_path: Path, payload: dict) -> None:
	with open(output_path, "w", encoding="utf-8") as f:
		json.dump(payload, f, indent=4)


def read_csv_records(input_path: Path) -> list[dict]:
	with open(input_path, "r", encoding="utf-8", newline="") as f:
		reader = csv.DictReader(f)
		return list(reader)



def main() -> None:
	parser = argparse.ArgumentParser(
		description="Build SEC EDGAR CSV and schema JSON outputs (no input file required)."
	)
	parser.add_argument(
		"-i",
		"--input-csv",
		default=None,
		help="Optional path to an input CSV of filings to use instead of SIC scraping.",
	)
	parser.add_argument(
		"--output-csv",
		default=str(OUTPUT_CSV),
		help="Path to write the CSV output.",
	)
	parser.add_argument(
		"--output-json",
		default=str(OUTPUT_JSON),
		help="Path to write the JSON output.",
	)
	parser.add_argument(
		"-n",
		"--max-entries",
		type=int,
		default=None,
		help="Maximum number of filings to scrape into the JSON output.",
	)

	mode_group = parser.add_mutually_exclusive_group()
	mode_group.add_argument(
		"-c",
		"--csv-only",
		action="store_true",
		help="Only create the CSV output (skip JSON scraping).",
	)
	mode_group.add_argument(
		"-j",
		"--json-only",
		action="store_true",
		help="Only create the JSON output (skip writing CSV).",
	)

	args = parser.parse_args()

	all_rows = []
	processed_ciks = 0

	if args.input_csv:
		input_path = Path(args.input_csv)
		print(f"Reading input CSV from {input_path}")
		base_rows = read_csv_records(input_path)
		all_rows = add_edgar_urls(base_rows)
		print(f"Loaded {len(base_rows)} rows, {len(all_rows)} with EDGAR URLs")
	else:
		print(f"Starting EDGAR run for {len(SICS)} SIC codes")

		# Loop through each SIC code, retrieve the CIK entries, fetch their submissions, build filing rows, and accumulate them in a list. Then write the outputs based on the specified arguments.
		for sic_index, sic in enumerate(SICS, start=1):
			print(f"Processing SIC {sic} ({sic_index}/{len(SICS)})")
			cik_entries = get_cik_entries(sic)
			if not cik_entries:
				print(f"No CIKs found for SIC {sic}")
				continue

			print(f"SIC {sic}: {len(cik_entries)} CIKs found")

			# For each CIK entry, attempt to fetch the submissions data. If successful, build the filing rows and add them to the overall list of rows. If any request fails, skip that CIK and continue with the next one.
			for cik_entry in cik_entries:
				try:
					submissions = fetch_submissions(cik_entry.get("cik", ""))
				except requests.RequestException as exc:
					print(f"Skipping CIK {cik_entry.get('cik', '')}: submissions failed ({exc})")
					continue

				rows = build_filing_rows(cik_entry, submissions)
				all_rows.extend(rows)
				processed_ciks += 1
				if processed_ciks % PROGRESS_EVERY_CIK == 0:
					print(
						f"Processed {processed_ciks} CIKs; total filings rows {len(all_rows)}"
					)
				time.sleep(REQUEST_DELAY_SECONDS)

	output_csv = Path(args.output_csv)
	output_json = Path(args.output_json)

	if not args.json_only:
		print("Writing CSV output...")
		write_csv_records(output_csv, all_rows)
		print(f"Wrote {len(all_rows)} records to {output_csv}")

	if not args.csv_only:
		if args.max_entries is None:
			print("Scraping filings for JSON output...")
		else:
			print(f"Scraping filings for JSON output (max {args.max_entries})...")
		payload = build_schema_output(all_rows, max_entries=args.max_entries)
		print("Writing JSON output...")
		write_json_records(output_json, payload)
		print(f"Wrote {len(payload['sources'])} records to {output_json}")


if __name__ == "__main__":
	main()
