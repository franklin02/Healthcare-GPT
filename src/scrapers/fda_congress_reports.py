"""
This file is going to scrape Reports to Congress from the FDA website (2024-2020)
Some pdf included have images, these have been skipped (we dont know how the digestions 
works rn). It also gets outputed as text and json files for RAG ingestion (same reason)
"""

import os
import re
import json
import time
import requests
import pdfplumber
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent / "fda_congress_reports"

OUTPUT_DIR.mkdir(exist_ok=True)

PDF_DIR    = OUTPUT_DIR / "pdfs"
TEXT_DIR   = OUTPUT_DIR / "texts"
JSON_DIR   = OUTPUT_DIR / "json"

OUTPUT_DIR.mkdir(exist_ok=True)
PDF_DIR.mkdir(exist_ok=True)
TEXT_DIR.mkdir(exist_ok=True)
JSON_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ── Reports to Congress — CY2020 through CY2024 ───────────────────────────────
REPORTS = [
    {
        "year": 2024,
        "title": "Twelfth Annual Report on Drug Shortages For Calendar Year 2024",
        "url": "https://www.fda.gov/media/189325/download?attachment",
        "filename": "cy2024_drug_shortage_report.pdf",
    },
    {
        "year": 2023,
        "title": "Eleventh Annual Report on Drug Shortages For Calendar Year 2023",
        "url": "https://www.fda.gov/media/179156/download?attachment",
        "filename": "cy2023_drug_shortage_report.pdf",
    },
    {
        "year": 2022,
        "title": "Tenth Annual Report on Drug Shortages for Calendar Year 2022",
        "url": "https://www.fda.gov/media/169302/download?attachment",
        "filename": "cy2022_drug_shortage_report.pdf",
    },
    {
        "year": 2021,
        "title": "Ninth Annual Report on Drug Shortages for Calendar Year 2021",
        "url": "https://www.fda.gov/media/159302/download?attachment",
        "filename": "cy2021_drug_shortage_report.pdf",
    },
    {
        "year": 2020,
        "title": "Eighth Annual Report on Drug Shortages for Calendar Year 2020",
        "url": "https://www.fda.gov/media/150409/download?attachment",
        "filename": "cy2020_drug_shortage_report.pdf",
    },
]


def clean_text(text: str) -> str:
    # Collapse multiple blank lines → single blank line
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove stray form-feed characters
    text = text.replace('\x0c', '\n')
    # Strip leading/trailing whitespace per line
    lines = [line.rstrip() for line in text.splitlines()]
    text = '\n'.join(lines)
    return text.strip()

#  Download a PDF and return its local path, or None on failure.
def download_pdf(report: dict) -> Path | None:
    pdf_path = PDF_DIR / report["filename"]

    if pdf_path.exists():
        print(f"  [SKIP] Already downloaded: {report['filename']}")
        return pdf_path

    print(f"  [GET]  {report['url']}")
    try:
        resp = requests.get(report["url"], headers=HEADERS, timeout=60, allow_redirects=True)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type.lower() and len(resp.content) < 1000:
            print(f"  [WARN] Unexpected content-type: {content_type} — skipping")
            return None

        pdf_path.write_bytes(resp.content)
        size_kb = len(resp.content) // 1024
        print(f"  [OK]   Saved {size_kb} KB → {pdf_path.name}")
        return pdf_path

    except Exception as e:
        print(f"  [ERR]  Failed to download {report['filename']}: {e}")
        return None


def extract_text(pdf_path: Path, report: dict) -> str | None:
    """Extract and clean text from a PDF using pdfplumber."""
    try:
        pages_text = []
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                if page_text.strip():
                    pages_text.append(f"[Page {i+1}/{total}]\n{page_text}")

        if not pages_text:
            print(f"  [WARN] No text extracted from {pdf_path.name} (may be scanned image PDF)")
            return None

        raw = "\n\n".join(pages_text)
        return clean_text(raw)

    except Exception as e:
        print(f"  [ERR]  Text extraction failed for {pdf_path.name}: {e}")
        return None


def save_text(text: str, report: dict) -> Path:
    """Save extracted text with a metadata header for RAG context."""
    txt_name = report["filename"].replace(".pdf", ".txt")
    txt_path = TEXT_DIR / txt_name

    header = (
        f"SOURCE: FDA Drug Shortages – Report to Congress\n"
        f"TITLE:  {report['title']}\n"
        f"YEAR:   {report['year']}\n"
        f"URL:    {report['url']}\n"
        f"{'=' * 72}\n\n"
    )

    txt_path.write_text(header + text, encoding="utf-8")
    print(f"  [TXT]  Saved text → {txt_path.name}")
    return txt_path


def save_json(text: str, report: dict, pages: int, char_count: int) -> Path:
    """Save extracted content as a structured JSON document for RAG ingestion."""
    json_name = report["filename"].replace(".pdf", ".json")
    json_path = JSON_DIR / json_name

    # Split into page chunks for finer-grained RAG retrieval
    page_chunks = []
    for block in text.split("\n\n"):
        match = re.match(r'^\[Page (\d+)/(\d+)\]\n(.*)', block, re.DOTALL)
        if match:
            page_chunks.append({
                "page": int(match.group(1)),
                "total_pages": int(match.group(2)),
                "content": match.group(3).strip(),
            })

    doc = {
        "source": "FDA Drug Shortages – Report to Congress",
        "title": report["title"],
        "year": report["year"],
        "url": report["url"],
        "pdf_filename": report["filename"],
        "pages": pages,
        "char_count": char_count,
        "page_chunks": page_chunks,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)

    print(f"  [JSON] Saved JSON → {json_path.name}")
    return json_path

def run():
    manifest = []
    print(f"\n{'=' * 60}")
    print("FDA Drug Shortage Reports — Scraper")
    print(f"Output: {OUTPUT_DIR.resolve()}")
    print(f"{'=' * 60}\n")

    for report in REPORTS:
        print(f"\n── CY{report['year']}: {report['title'][:60]}…")

        pdf_path = download_pdf(report)
        time.sleep(1)  # so we dont get blocked

        entry = {
            "year": report["year"],
            "title": report["title"],
            "url": report["url"],
            "pdf_file": str(pdf_path) if pdf_path else None,
            "text_file": None,
            "json_file": None,
            "pages": None,
            "char_count": None,
            "status": "failed",
        }

        if pdf_path:
            text = extract_text(pdf_path, report)
            if text:
                txt_path = save_text(text, report)
                # Count pages from PDF
                with pdfplumber.open(pdf_path) as pdf:
                    entry["pages"] = len(pdf.pages)
                entry["char_count"] = len(text)
                json_path = save_json(text, report, entry["pages"], entry["char_count"])
                entry["text_file"] = str(txt_path)
                entry["json_file"] = str(json_path)
                entry["status"] = "success"
            else:
                entry["status"] = "extracted_no_text"

        manifest.append(entry)

    # Write manifest
    manifest_path = OUTPUT_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Summary
    success = sum(1 for e in manifest if e["status"] == "success")
    print(f"\n{'=' * 60}")
    print(f"Done. {success}/{len(REPORTS)} reports extracted successfully.")
    print(f"Manifest: {manifest_path}")
    print(f"Text files: {TEXT_DIR}")
    print(f"JSON files: {JSON_DIR}")
    print(f"{'=' * 60}\n")

    return manifest


if __name__ == "__main__":
    run()