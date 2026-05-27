import requests
import pandas as pd
import zipfile
import io
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.cli_reporter import CliReporter, PipelineStats  # noqa: E402
from src.logging_utils import get_file_logger  # noqa: E402

LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "gdelt_seeds.log"
LOGGER = get_file_logger(__name__, LOG_FILE)

GKG_COLS = {
    0: "GkgRecordId",
    1: "V21Date",
    3: "V2SourceCommonName",
    4: "V2DocumentIdentifier",
    7: "V1Themes",
    9: "V1Locations",
    11: "V1Organizations",
    15: "V2Tone",
}

CYBER_THEMES = {
    "CYBER_ATTACK",
    "TAX_FNCACT_HACKER",
    "RANSOMWARE",
    "DATA_BREACH",
    "EPU_SECURITY_CYB",
    "CYBERSECURITY",
    "TAX_FNCACT_CYBERCRIMINAL",
    "WB_IMFWEO_DIGITAL",
}

HEALTH_THEMES = {
    "HOSPITAL",
    "WB_HEALTH_SYSTEMS",
    "SOC_GENERALHEALTH",
    "HEALTH_INSTITUTION",
    "MEDICAL",
    "HEALTH_PANDEMIC",
    "TAX_FNCACT_NURSE",
    "TAX_FNCACT_DOCTOR",
    "TAX_FNCACT_PHYSICIAN",
    "HEALTHCARE",
}

DRUG_SHORTAGE_THEMES = {
    "SHORTAGE",
    "PHARMACEUTICAL_SUPPLY_CHAIN",
    "ESSENTIAL_MEDICINES",
    "MANUFACTURING_OF_DRUGS",
    "PHARMACEUTICALS",
    "GENERIC_DRUGS",
    "PHARMACEUTICAL_PRICING",
    "PHARMACEUTICAL_POLICY",
    "PHARMACEUTICAL_REGULATION",
    "QUALITY_ASSURANCE_FOR_PHARMACEUTICALS",
    "FINANCING_OF_DRUGS",
    "RATIONAL_SELECTION_AND_USE_OF_DRUGS",
}

DEVICE_SHORTAGE_THEMES = {
    "MEDICAL_EQUIPMENT",
    "HEALTH_TECHNOLOGIES",
    "PROCUREMENT_OF_HEALTH_TECHNOLOGIES",
    "MEDICAL_SUPPLIES_FINANCE",
}

NATURAL_DISASTER_THEMES = {"NATURAL_DISASTER"}

NOISE_THEMES = {"SPORTS", "GAMES_ESPORTS", "ENV_", "TOURISM", "EDUCATION_UNIVERSITY"}

# Mapping of subsectors to their required theme sets
SUBSECTOR_THEMES = {
    "drug_shortage": DRUG_SHORTAGE_THEMES,
    "medical_device_shortage": DEVICE_SHORTAGE_THEMES,
    "cyber_attack": CYBER_THEMES,
    "natural_disaster": NATURAL_DISASTER_THEMES,
}

US_TLDS = {
    ".com",
    ".org",
    ".net",
    ".gov",
    ".us",
    ".edu",
}  # Note: may need to add more depending on other US territories.

BLOCKED_TLDS = {
    ".ru",
    ".cn",
    ".pk",
    ".in",
    ".au",
    ".co.uk",
    ".ca",
    ".de",
    ".fr",
    ".br",
    ".mx",
    ".za",
    ".ng",
    ".ph",
    ".id",
}

URL_DENY_PATTERNS = re.compile(
    r"sport|footbal|soccer|nba|nfl|entertain|celebrit|gossip|"
    r"weather|horoscope|recipe|fashion|travel|realestate|crypto(?!.*hospital)",
    re.IGNORECASE,
)
URL_REQUIRE_PATTERNS = re.compile(
    r"cyber|hack|ransomware|breach|attack|security|hospital|health|"
    r"medical|clinic|patient|ehr|emr|phishing|malware|infosec",
    re.IGNORECASE,
)


# Note:
# This checks to see if the loc is in the US, this will need to be changed to include US territories aswell.
def is_us_located(location_str):
    if not isinstance(location_str, str) or not location_str.strip():
        LOGGER.debug("Location string is not valid: %s", location_str)
        return True
    for entry in location_str.split(";"):
        parts = entry.split("#")
        if len(parts) >= 3 and parts[2].strip().upper() == "US":
            LOGGER.debug("Location string indicates US location: %s", location_str)
            return True
    LOGGER.debug("Location string does not indicate US location: %s", location_str)
    return False


def _matches_any_theme(theme_str, theme_set):
    if not isinstance(theme_str, str):
        LOGGER.debug("Theme string is not valid: %s", theme_str)
        return False
    tokens = [token.strip().upper() for token in theme_str.split(";") if token.strip()]
    LOGGER.debug("Checking themes %s against set %s", tokens, theme_set)
    return any(any(expected in token for token in tokens) for expected in theme_set)


def themes_match(theme_str, subsector="all"):
    """
    Check if themes match a requested subsector.

    Supported subsector values:
    - a specific subsector name in SUBSECTOR_THEMES
    - "all" to match any supported subsector
    """
    LOGGER.debug("Checking themes %s against subsector %s", theme_str, subsector)
    if subsector == "all":
        return any(
            _matches_any_theme(theme_str, HEALTH_THEMES)
            and _matches_any_theme(theme_str, theme_set)
            for theme_set in SUBSECTOR_THEMES.values()
        )

    return (
        subsector in SUBSECTOR_THEMES
        and _matches_any_theme(theme_str, HEALTH_THEMES)
        and _matches_any_theme(theme_str, SUBSECTOR_THEMES[subsector])
    )


def detect_subsector(theme_str):
    """Return the first matching subsector for a theme string, or None."""
    if not _matches_any_theme(theme_str, HEALTH_THEMES):
        LOGGER.debug("Theme string does not match health themes: %s", theme_str)
        return None

    for subsector, theme_set in SUBSECTOR_THEMES.items():
        if _matches_any_theme(theme_str, theme_set):
            LOGGER.debug(
                "Detected subsector %s for theme string: %s", subsector, theme_str
            )
            return subsector
    LOGGER.debug("No specific subsector detected for theme string: %s", theme_str)
    return None


def themes_match_noise(theme_str):
    if not isinstance(theme_str, str):
        LOGGER.debug("Theme string is not valid: %s", theme_str)
        return False
    u = theme_str.upper()
    LOGGER.debug("Checking if themes %s match noise patterns", theme_str)
    return any(n in u for n in NOISE_THEMES)


def url_passes_quality(url):
    if not isinstance(url, str) or not url.startswith("http"):
        LOGGER.debug("URL is not valid: %s", url)
        return False
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.lower()
    except Exception:
        LOGGER.warning("Failed to parse URL %s: %s", url, exc_info=True)
        return False
    for btld in BLOCKED_TLDS:
        if domain.endswith(btld):
            LOGGER.debug(
                "URL domain %s ends with blocked TLD %s: %s", domain, btld, url
            )
            return False
    if not any(domain.endswith(tld) for tld in US_TLDS):
        LOGGER.debug("URL domain %s is not in US TLDs: %s", domain, url)
        return False
    if URL_DENY_PATTERNS.search(path):
        LOGGER.debug("URL path %s matches deny patterns: %s", path, url)
        return False
    if not URL_REQUIRE_PATTERNS.search(url.lower()):
        LOGGER.debug("URL %s does not match any require patterns", url)
        return False
    LOGGER.debug("URL passed quality checks: %s", url)
    return True


def _normalize_date_bound(value, end=False):
    if not value:
        LOGGER.debug("No date bound provided, returning None")
        return None
    if value.isdigit() and len(value) in (8, 14):
        LOGGER.debug("Normalizing date bound from numeric value: %s", value)
        return (
            (value + ("235959" if end else "000000"))[:14] if len(value) == 8 else value
        )
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            LOGGER.debug("Trying to parse date bound %s with format %s", value, fmt)
            return datetime.strptime(value, fmt).strftime("%Y%m%d%H%M%S")
        except ValueError:
            LOGGER.debug("Date bound %s does not match format %s", value, fmt)
            pass
    try:
        LOGGER.debug("Trying to parse date bound %s as ISO format", value)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
            "%Y%m%d%H%M%S"
        )
    except ValueError:
        LOGGER.warning("Failed to parse date bound %s with any format", value)
        return value


def process_gkg_file(
    link,
    subsector="all",
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
):
    """Download and filter one GDELT GKG file into candidate seed records."""
    reporter = reporter or CliReporter(verbose=True)
    LOGGER.debug("Processing GKG file link=%s subsector=%s", link, subsector)
    try:
        r = requests.get(link, timeout=20)
        r.raise_for_status()
    except Exception as e:
        reporter.warn(f"Download failed {link.split('/')[-1]}: {e}", stats)
        LOGGER.warning("Download failed link=%s error=%s", link, e)
        return [], 0
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw = pd.read_csv(
                z.open(z.namelist()[0]),
                sep="\t",
                encoding="latin-1",
                header=None,
                on_bad_lines="skip",
                low_memory=False,
                dtype=str,
            )
        if raw.shape[1] < 16:
            reporter.warn(f"Only {raw.shape[1]} cols in {link.split('/')[-1]}", stats)
            LOGGER.warning("Too few columns link=%s cols=%s", link, raw.shape[1])
            return [], 0

        date_col = raw.iloc[:, 1]
        source_col = raw.iloc[:, 3]
        url_col = raw.iloc[:, 4]
        themes_col = raw.iloc[:, 7]
        locs_col = raw.iloc[:, 9]

        df = pd.DataFrame(
            {
                "date": date_col.values,
                "source": source_col.values,
                "url": url_col.values,
                "themes": themes_col.values,
                "locs": locs_col.values,
            }
        )

        total = len(df)
        LOGGER.debug("Loaded %s rows for link=%s", total, link)
        # Filter for the requested subsector, or all supported subsectors, excluding noise
        subsector_match = df["themes"].apply(lambda t: themes_match(t, subsector))
        noise = df["themes"].apply(lambda t: themes_match_noise(t))
        df = df[subsector_match & ~noise].copy()
        if df.empty:
            reporter.detail("    [FILTERED OUT] No results after theme filter")
            LOGGER.debug("Filtered out by theme link=%s", link)
            return [], total

        df = df[df["locs"].apply(is_us_located)].copy()
        if df.empty:
            reporter.detail("    [FILTERED OUT] No results after US location filter")
            LOGGER.debug("Filtered out by US location link=%s", link)
            return [], total

        df = df[df["url"].apply(url_passes_quality)].copy()
        if df.empty:
            reporter.detail("    [FILTERED OUT] No results after URL quality filter")
            LOGGER.debug("Filtered out by URL quality link=%s", link)
            return [], total

        fname = link.split("/")[-1]
        reporter.detail(f"  OK {fname}: {len(df)} leads from {total} rows")
        LOGGER.info("File %s produced %s leads from %s rows", fname, len(df), total)
        seeds = [
            {
                "url": row["url"],
                "source": row["source"],
                "themes": row["themes"],
                "subsector": subsector
                if subsector != "all"
                else (detect_subsector(row["themes"]) or "other"),
                "date": row["date"],
                "file": fname,
            }
            for _, row in df.iterrows()
        ]
        return seeds, total
    except Exception as e:
        reporter.warn(f"Parse error {link.split('/')[-1]}: {e}", stats)
        LOGGER.warning("Parse error link=%s error=%s", link, e)
        return [], 0


def backfill_cyber_seeds(
    num_files=20,
    subsector="all",
    start_date=None,
    end_date=None,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
):
    """Collect recent or date-bounded GDELT seeds for the requested subsector."""
    reporter = reporter or CliReporter(verbose=True)
    reporter.detail("Fetching GDELT master file list...")
    LOGGER.debug(
        "Backfill start num_files=%s subsector=%s start_date=%s end_date=%s",
        num_files,
        subsector,
        start_date,
        end_date,
    )
    resp = requests.get(
        "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt", timeout=15
    )
    links = [
        line.split(" ")[2]
        for line in resp.text.strip().split("\n")
        if ".gkg.csv.zip" in line
    ]
    start_date = _normalize_date_bound(start_date)
    end_date = _normalize_date_bound(end_date, end=True)
    LOGGER.debug("Normalized date bounds start=%s end=%s", start_date, end_date)
    if start_date:
        links = [link for link in links if link.split("/")[-1][:14] >= start_date]
    if end_date:
        links = [link for link in links if link.split("/")[-1][:14] <= end_date]
    recent = links if (start_date or end_date) else links[-num_files:]
    scope_label = "all subsectors" if subsector == "all" else subsector
    reporter.info(
        f"Scanning {len(recent)} GDELT files for {scope_label} "
        f"(~{len(recent) * 15 / 60:.1f} hours)"
    )
    LOGGER.debug("Scanning %s files for %s", len(recent), scope_label)

    all_seeds = []
    total_rows = 0
    for index, link in enumerate(recent, start=1):
        seeds, rows = process_gkg_file(
            link,
            subsector=subsector,
            reporter=reporter,
            stats=stats,
        )
        all_seeds.extend(seeds)
        total_rows += rows
        if recent and not reporter.verbose:
            reporter.progress(index, len(recent), "GDELT files")

    seen, unique = set(), []
    for s in all_seeds:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique.append(s)
    LOGGER.debug("Unique seeds=%s from total_rows=%s", len(unique), total_rows)

    reporter.info(f"Found {len(unique)} unique seeds from {total_rows} rows checked")
    for s in unique:
        reporter.detail(f"[{s['date']}]  {s['source']}")
        reporter.detail(f"  URL: {s['url']}")
        relevant = [
            t
            for t in (s["themes"] or "").split(";")
            if any(c in t.upper() for c in CYBER_THEMES | HEALTH_THEMES)
        ]
        reporter.detail(f"  Themes: {' | '.join(relevant[:8])}\n")

    return unique
