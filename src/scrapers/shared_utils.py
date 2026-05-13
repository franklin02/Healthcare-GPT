import json
import csv
import datetime
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

# CSV column orders. Vulnerabilities = "real" disruptions; Noise = everything
# the AI rejected. Kept intentionally small so we can scan them easier
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


"""
This function is a shared HTTP GET with a User-Agent header to blend 
in with normal traffic and avoid getting blocked by the website.
"""
def get_page(url):
    resp = requests.get(url, timeout=15, headers=HEADERS)
    resp.raise_for_status()
    return resp


def _site_filename(site_name: str) -> str:
    return site_name.strip()


"""
This function checks to see that we have the data subfolders created and
seeds each output file with the right header so the first run never produces
an empty CSV with no column names.
"""
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


"""
Append a confirmed Vulnerability to data/Ready_for_RAG/{source_name}.json.
The caller is responsible for fully populating the Vulnerability dataclass
(including wrapping subsector_data in the right SubsectorData subclass).
"""
def json_output(vuln: Vulnerability) -> None:
    json_path = READY_FOR_RAG_DIR / f"{_site_filename(vuln.source_name)}.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["sources"].append(vuln.to_dict())
    json_path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    print(f"[VALID] ({vuln.subsector}): {vuln.title}")


"""
Append a confirmed Vulnerability to data/Vulnerabilities/{source_name}.csv as
ONE row using the base reviewer-friendly columns. No is_threat branching:
every row in this file is, by definition, a confirmed disruption.
"""
def vuln_output(vuln: Vulnerability) -> None:
    csv_path = VULNERABILITIES_DIR / f"{_site_filename(vuln.source_name)}.csv"
    content_preview = (vuln.content or "")[:250].replace("\n", " ")
    row = [
        vuln.date_accessed,
        vuln.date_published,
        vuln.source_name,
        vuln.subsector,
        vuln.title,
        vuln.direct_link,
        vuln.exec_summary,
        content_preview,
    ]
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


"""
Append a non-disruption ("noise") article to data/Noise/{site_name}.csv.
`reason` is the AI's one-sentence analysis from ai_check_validation, so
reviewers can see WHY the article was filtered out without re-reading the body.
"""
def noise_output(site_name: str, title: str, url: str, body: str, reason: str) -> None:
    csv_path = NOISE_DIR / f"{_site_filename(site_name)}.csv"
    body_preview = (body or "")[:250].replace("\n", " ")
    row = [
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        site_name,
        title,
        url,
        reason,
        body_preview,
    ]
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


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


"""
Cheap "have we already seen this article?" check used by the HTML scraper to
break out of pagination once it walks back into known territory. Inspects ONLY
the first data row of BOTH data/vulnerabilities/{site_name}.csv AND
data/noise/{site_name}.csv (newest-first ordering is enforced by
prepend_vuln_csv / prepend_noise_csv below).

Returns True if the incoming title matches the top row of either file. Body
mismatch on a title hit prints a [WARN] but still returns True.
"""
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


"""
Atomically rewrite data/vulnerabilities/{site_name}.csv as
    header + new_rows + existing_data_rows
so the most recent run's articles end up at the top. new_rows must already be
ordered newest-first. No-op when new_rows is empty.
"""
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


"""
Atomically rewrite data/noise/{site_name}.csv as
    header + new_rows + existing_data_rows
so the most recent run's articles end up at the top. new_rows must already be
ordered newest-first. No-op when new_rows is empty.
"""
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


"""
Insert new vulns at the FRONT of data/processed/{site_name}.json's `sources`
list (newest-first). No-op when new_vulns is empty.
"""
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


"""
This function builds the page url for the site. Each scraper module
passes in its own page_param default since RSS and HTML paginate differently.
"""
def build_page_url(site_config, current_page, starting_page, default_page_param):
    if current_page == starting_page:
        return site_config["url"]

    page_param = site_config["map"].get("page_param", default_page_param)
    return f"{site_config['url']}?{page_param}={current_page}"
