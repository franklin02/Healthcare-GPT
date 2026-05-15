import json
import csv
import os
import tempfile
import requests
from pathlib import Path
import sys as _sys

# Anchor to the project root so this works both as `scrapers.shared_utils`
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))
from src.classes import Vulnerability


READY_FOR_RAG_DIR = _PROJECT_ROOT / "data" / "processed"
NOISE_DIR = _PROJECT_ROOT / "data" / "noise"
VULNERABILITIES_DIR = _PROJECT_ROOT / "data" / "vulnerabilities"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


VULN_CSV_HEADER = [
    "date_accessed",
    "date_published",
    "source_name",
    "subsector",
    "title",
    "direct_link",
    "exec_summary",
    "content_preview",
]

NOISE_CSV_HEADER = [
    "date_accessed",
    "source_name",
    "title",
    "url",
    "reason",
    "body_preview",
]


def get_page(url):
    resp = requests.get(url, timeout=15, headers=HEADERS)
    resp.raise_for_status()
    return resp


def _site_filename(site_name: str) -> str:
    return site_name.strip()


def check_valid_file(site_name):
    READY_FOR_RAG_DIR.mkdir(parents=True, exist_ok=True)
    NOISE_DIR.mkdir(parents=True, exist_ok=True)
    VULNERABILITIES_DIR.mkdir(parents=True, exist_ok=True)

    stem = _site_filename(site_name)

    json_path = READY_FOR_RAG_DIR / f"{stem}.json"
    if not json_path.exists():
        json_path.write_text(json.dumps({"sources": []}, indent=4), encoding="utf-8")
        print(f"Created {json_path}")

    noise_path = NOISE_DIR / f"{stem}.csv"
    if not noise_path.exists():
        with open(noise_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(NOISE_CSV_HEADER)
        print(f"Created {noise_path}")

    vulnerabilities_path = VULNERABILITIES_DIR / f"{stem}.csv"
    if not vulnerabilities_path.exists():
        with open(vulnerabilities_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(VULN_CSV_HEADER)
        print(f"Created {vulnerabilities_path}")


def _content_preview(body: str | None) -> str:
    return (body or "")[:250].replace("\n", " ")


def _top_row_matches(
    csv_path: Path,
    title: str,
    body_snippet: str,
    preview_column: str,
) -> bool:
    if not csv_path.exists():
        return False

    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        try:
            first_row = next(reader)
        except StopIteration:
            return False

    if first_row.get("title", "") != title:
        return False

    incoming_preview = _content_preview(body_snippet)
    if first_row.get(preview_column, "") != incoming_preview:
        print(
            f"[WARN] Title matched but body preview differs for {title!r} "
            f"— stopping anyway"
        )
    return True



def is_known_article(site_name: str, title: str, body_snippet: str) -> bool:
    site = _site_filename(site_name)
    if _top_row_matches(
        VULNERABILITIES_DIR / f"{site}.csv", title, body_snippet, "content_preview"
    ):
        return True
    if _top_row_matches(
        NOISE_DIR / f"{site}.csv", title, body_snippet, "body_preview"
    ):
        return True
    return False



def prepend_vuln_csv(site_name: str, new_rows: list[list[str]]) -> None:
    if not new_rows:
        return

    csv_path = VULNERABILITIES_DIR / f"{_site_filename(site_name)}.csv"
    existing_data_rows: list[list[str]] = []
    if csv_path.exists():
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                next(reader)  # drop the existing header
            except StopIteration:
                pass
            existing_data_rows = list(reader)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{_site_filename(site_name)}.",
        suffix=".csv.tmp",
        dir=str(VULNERABILITIES_DIR),
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(VULN_CSV_HEADER)
            writer.writerows(new_rows)
            writer.writerows(existing_data_rows)
        os.replace(tmp_path, csv_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise



def prepend_noise_csv(site_name: str, new_rows: list[list[str]]) -> None:
    if not new_rows:
        return

    csv_path = NOISE_DIR / f"{_site_filename(site_name)}.csv"
    existing_data_rows: list[list[str]] = []
    if csv_path.exists():
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                next(reader)  # drop the existing header
            except StopIteration:
                pass
            existing_data_rows = list(reader)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{_site_filename(site_name)}.",
        suffix=".csv.tmp",
        dir=str(NOISE_DIR),
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(NOISE_CSV_HEADER)
            writer.writerows(new_rows)
            writer.writerows(existing_data_rows)
        os.replace(tmp_path, csv_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise



def prepend_json_sources(site_name: str, new_vulns: list[Vulnerability]) -> None:
    if not new_vulns:
        return

    json_path = READY_FOR_RAG_DIR / f"{_site_filename(site_name)}.json"
    if json_path.exists():
        data = json.loads(json_path.read_text(encoding="utf-8"))
    else:
        data = {"sources": []}

    new_dicts = [v.to_dict() for v in new_vulns]
    data["sources"] = new_dicts + data.get("sources", [])

    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{_site_filename(site_name)}.",
        suffix=".json.tmp",
        dir=str(READY_FOR_RAG_DIR),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, json_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


