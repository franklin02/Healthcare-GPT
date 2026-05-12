import json
import csv
import datetime
import requests
from pathlib import Path
import sys as _sys

# Anchor to the project root so this works both as `scrapers.shared_utils`
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))
from src.classes import Vulnerability


READY_FOR_RAG_DIR = _PROJECT_ROOT / "data" / "Ready_for_RAG"
NOISE_DIR = _PROJECT_ROOT / "data" / "Noise"
VULNERABILITIES_DIR = _PROJECT_ROOT / "data" / "Vulnerabilities"

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


"""
This function builds the page url for the site. Each scraper module
passes in its own page_param default since RSS and HTML paginate differently.
"""
def build_page_url(site_config, current_page, starting_page, default_page_param):
    if current_page == starting_page:
        return site_config["url"]

    page_param = site_config["map"].get("page_param", default_page_param)
    return f"{site_config['url']}?{page_param}={current_page}"
