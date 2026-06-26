"""
GDELT seed discovery and backfill logic.
This module provides functions to fetch and process GDELT GKG files, filter them for relevant themes and US locations,
and extract candidate seed records for the healthcare subsectors of interest.
It also includes a backfill function to collect seeds from recent GDELT files based on date bounds or a specified number of recent files.

Constants:
GKG_COLS: Mapping of column indices to their corresponding field names in the GDELT GKG files.
CYBER_THEMES: Set of themes related to cyber attacks.
HEALTH_THEMES: Set of themes related to healthcare.
DRUG_SHORTAGE_THEMES: Set of themes related to drug shortages.
DEVICE_SHORTAGE_THEMES: Set of themes related to medical device shortages.
NATURAL_DISASTER_THEMES: Set of themes related to natural disasters.
NOISE_THEMES: Set of themes considered noise.
SUBSECTOR_THEMES: Mapping of subsectors to their required theme sets.
US_TLDS: Set of top-level domains associated with US-based websites.
BLOCKED_TLDS: Set of top-level domains associated with non-US-based websites that should be blocked.
URL_DENY_PATTERNS: Regular expression pattern to identify URLs that should be denied based on their path.
URL_REQUIRE_PATTERNS: Regular expression pattern to identify URLs that should be required based on their content.

Functions:
is_us_located(location_str): Check if a location string from GDELT indicates a US location.
themes_match(theme_str, subsector="all"): Check if themes in a theme string match the required themes for a given subsector or any supported subsector.
detect_subsector(theme_str): Detect the specific subsector for a theme string based on the presence of required themes.
themes_match_noise(theme_str): Check if themes in a theme string match any of the noise themes.
url_passes_quality(url): Check if a URL passes quality checks based on its domain and path.
process_gkg_file(link, subsector="all", reporter=None, stats=None): Download and process a GDELT GKG file, filtering for relevant themes, US locations, and quality URLs, and return candidate seed records.
backfill_cyber_seeds(num_files=20, subsector="all", start_date=None, end_date=None, reporter=None, stats=None): Collect recent or date-bounded GDELT seeds for the requested subsector by scanning the master file list and processing relevant GKG files.
"""

import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import json

import pandas as pd
import requests

from ..cli_reporter import CliReporter, PipelineStats
from ..logging_utils import get_file_logger

PROJECT_ROOT = Path(__file__).resolve().parents[2]

LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "gdelt_seeds.log"
LOGGER = get_file_logger(__name__, LOG_FILE)

SEEDS_DIR = PROJECT_ROOT / "data" / "raw" / "gdelt" / "seeds"

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


def is_us_located(location_str):
    """
    Check if a GDELT location string indicates a US location.

    Parameters:
        location_str: The location string from GDELT's V1Locations field.

    Returns:
        True if the location string indicates a US location, False otherwise.
    """
    if not isinstance(location_str, str) or not location_str.strip():
        return True
    # GDELT location strings have entries separated by semicolons, with fields separated by hashes. The country code is typically the third field. We check if any entry has "US" as the country code.
    for entry in location_str.split(";"):
        parts = entry.split("#")
        if len(parts) >= 3 and parts[2].strip().upper() == "US":
            return True
    return False


def _matches_any_theme(theme_str, theme_set):
    """
    Check if any of the expected themes in theme_set are present in theme_str.

    Parameters:
        theme_str: The theme string from GDELT's V1Themes field, which may contain multiple themes separated by semicolons.
        theme_set: A set of themes to check against.

    Returns:
        True if any theme in theme_set is present in theme_str, False otherwise.
    """
    if not isinstance(theme_str, str):
        return False
    tokens = [token.strip().upper() for token in theme_str.split(";") if token.strip()]
    return any(any(expected in token for token in tokens) for expected in theme_set)


def themes_match(theme_str):
    """
    Check if themes match a requested subsector.

    Parameters:
        theme_str: The theme string from GDELT's V1Themes field.

    Returns:
        True if the themes match the requested subsector, False otherwise.
    """
    if not _matches_any_theme(theme_str, HEALTH_THEMES):
        return False

    return any(
        _matches_any_theme(theme_str, theme_set)
        for theme_set in SUBSECTOR_THEMES.values()
    )


def detect_subsectors(theme_str):
    """
    Return all matching subsectors for a theme string.

    Parameters:
        theme_str: The theme string from GDELT's V1Themes field.

    Returns:
        A list of detected subsector names, or an empty list if no specific
        subsectors can be detected.
    """
    if not _matches_any_theme(theme_str, HEALTH_THEMES):
        return []

    subsectors = []
    for subsector, theme_set in SUBSECTOR_THEMES.items():
        if _matches_any_theme(theme_str, theme_set):
            subsectors.append(subsector)
    if subsectors:
        return subsectors
    return []


def detect_subsector(theme_str):
    """
    Return the first matching subsector for a theme string, or None.

    Parameters:
        theme_str: The theme string from GDELT's V1Themes field.

    Returns:
        The detected subsector name if a match is found, or None if no specific subsector can be detected.
    """
    subsectors = detect_subsectors(theme_str)
    return subsectors[0] if subsectors else None


def themes_match_noise(theme_str):
    """
    Check if themes match any noise patterns.

    Parameters:
        theme_str: The theme string from GDELT's V1Themes field.

    Returns:
        True if any noise theme is present in the theme string, False otherwise.
    """
    if not isinstance(theme_str, str):
        return False
    u = theme_str.upper()
    return any(n in u for n in NOISE_THEMES)


def url_passes_quality(url):
    """
    Check if a URL passes quality checks.
    The URL must start with http, have a domain in US_TLDS and not in BLOCKED_TLDS,
    and its path must not match URL_DENY_PATTERNS while matching URL_REQUIRE_PATTERNS.

    Parameters:
        url: The URL to check.

    Returns:
        True if the URL passes quality checks, False otherwise.
    """
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
    # Check for blocked TLDs first to quickly filter out non-US domains before checking for allowed TLDs.
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
    """
    Normalize a date bound value to YYYYMMDDHHMMSS format for comparison with GDELT file timestamps.

    Parameters:
        value: The input date bound, which can be in various formats (e.g., YYYY-MM-DD, YYYY-MM-DD HH:MM, numeric YYYYMMDD or YYYYMMDDHHMMSS).
        end: If True, treat date-only inputs as the end of the day (23:59:59) rather than the start of the day (00:00:00).

    Returns:
        A string representing the normalized date bound in YYYYMMDDHHMMSS format, or None if the input value is empty or None. If the input cannot be parsed, returns the original value.
    """
    if not value:
        LOGGER.debug("No date bound provided, returning None")
        return None
    if value.isdigit() and len(value) in (8, 14):
        LOGGER.debug("Normalizing date bound from numeric value: %s", value)
        return (
            (value + ("235959" if end else "000000"))[:14] if len(value) == 8 else value
        )
    # Try parsing with known date formats. GDELT files use UTC, so we can treat naive datetimes as UTC for normalization purposes.
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
    cache_dir: Path | None = None,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
):
    """
    Download and filter one GDELT GKG file into candidate seed records.

    Parameters:
        link: The URL to the GDELT GKG file (a .zip containing a .csv).
        subsector: The subsector to filter for, or "all" for any supported subsector.
        cache_dir: Optional directory for caching downloaded zip files.
        reporter: Optional CliReporter for logging progress and warnings.
        stats: Optional PipelineStats for recording statistics.

    Returns:
        A tuple containing a list of candidate seed records (dicts) and the total number of rows processed from the GKG file. Each seed record includes the URL, source, themes, subsector, date, and file name.
    """
    reporter = reporter or CliReporter(verbose=True)
    LOGGER.debug("Processing GKG file link=%s", link)

    fname = link.split("/")[-1]
    cached_path = (cache_dir / fname) if cache_dir else None

    if cached_path and cached_path.exists():
        LOGGER.debug("Cache hit, reading from disk: %s", cached_path)
        zip_bytes = cached_path.read_bytes()
    else:
        try:
            r = requests.get(link, timeout=20)
            r.raise_for_status()
        except Exception as e:
            reporter.warn(f"Download failed {link.split('/')[-1]}: {e}", stats)
            LOGGER.warning("Download failed link=%s error=%s", link, e)
            return [], 0
        zip_bytes = r.content
        if cached_path:
            try:
                cached_path.write_bytes(zip_bytes)
                LOGGER.debug("Cached zip to %s", cached_path)
            except Exception as exc:
                LOGGER.warning("Failed to write cache file %s: %s", cached_path, exc)

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
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
        subsector_match = df["themes"].apply(lambda t: themes_match(t))
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

        fname = link.split("/")[-1]  # already defined above; kept for clarity
        reporter.detail(f"  OK {fname}: {len(df)} leads from {total} rows")
        LOGGER.info("File %s produced %s leads from %s rows", fname, len(df), total)
        seeds = []
        for _, row in df.iterrows():
            detected_subsectors = detect_subsectors(row["themes"])
            seed = {
                "url": row["url"],
                "source": row["source"],
                "themes": row["themes"],
                "subsector": detected_subsectors[0] if detected_subsectors else "other",
                "date": row["date"],
                "file": fname,
            }
            if detected_subsectors:
                seed["detected_subsectors"] = detected_subsectors
            seeds.append(seed)
        return seeds, total
    except Exception as e:
        reporter.warn(f"Parse error {link.split('/')[-1]}: {e}", stats)
        LOGGER.warning("Parse error link=%s error=%s", link, e)
        return [], 0


def fetch_gkg_links(num_files=20, start_date=None, end_date=None):
    """
    Fetch and filter GDELT GKG zip URLs from the master file list.

    Parameters:
        num_files: When no date bounds are set, return this many most-recent files.
        start_date: Optional inclusive lower bound on file timestamps.
        end_date: Optional inclusive upper bound on file timestamps.

    Returns:
        A list of GKG zip URLs matching the requested bounds.
    """
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
    if start_date:
        links = [link for link in links if link.split("/")[-1][:14] >= start_date]
    if end_date:
        links = [link for link in links if link.split("/")[-1][:14] <= end_date]
    return links if (start_date or end_date) else links[-num_files:]


def backfill_cyber_seeds(
    num_files=20,
    start_date=None,
    end_date=None,
    links=None,
    cache_dir: Path | None = None,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    instance_name: str | None = None,
):
    """
    Collect recent or date-bounded GDELT seeds for the requested subsector.

    Parameters:
        num_files: The number of most recent GDELT files to scan if no date bounds are provided. Ignored if start_date or end_date is specified.
        subsector: The subsector to filter for, or "all" for any supported subsector.
        start_date: Optional start date bound (inclusive) in formats like YYYY-MM-DD or YYYYMMDD. If provided, only files with timestamps on or after this date will be processed.
        end_date: Optional end date bound (inclusive) in formats like YYYY-MM-DD or YYYYMMDD. If provided, only files with timestamps on or before this date will be processed.
        links: Optional pre-filtered GKG zip URLs to process instead of fetching the master file list.
        reporter: Optional CliReporter for logging progress and warnings.
        stats: Optional PipelineStats for recording statistics.

    Returns:
        A list of unique seed records (dicts) that match the specified subsector and date bounds, extracted from the relevant GDELT GKG files. Each seed record includes the URL, source, themes, subsector, date, and file name.
    """
    reporter = reporter or CliReporter(verbose=True)
    if links is None:
        reporter.detail("Fetching GDELT master file list...")
        recent = fetch_gkg_links(
            num_files=num_files, start_date=start_date, end_date=end_date
        )
    else:
        recent = list(links)
    LOGGER.debug(
        "Backfill start num_files=%s start_date=%s end_date=%s files=%s",
        num_files,
        start_date,
        end_date,
        len(recent),
    )
    LOGGER.debug("Scanning %s files", len(recent))

    all_seeds = []
    total_rows = 0
    for index, link in enumerate(recent, start=1):
        fname = link.split("/")[-1]
        cached_seeds = []
        try:
            if SEEDS_DIR.exists():
                for p in SEEDS_DIR.glob("*.json"):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            j = json.load(f)
                        s = j.get("seed") or j
                        if isinstance(s, dict) and s.get("file") == fname:
                            cached_seeds.append(s)
                    except Exception:
                        LOGGER.warning("Failed to read cache file %s: %s", p)
                        continue
        except Exception:
            LOGGER.warning("Failed to access cache directory %s: %s", SEEDS_DIR)
            cached_seeds = []

        if cached_seeds:
            reporter.detail(f"  Reusing {len(cached_seeds)} cached seeds from {fname}")
            LOGGER.info("Reusing %s cached seeds from %s", len(cached_seeds), fname)
            all_seeds.extend(cached_seeds)
            if recent and not reporter.verbose:
                reporter.instance(instance_name or "GDELT").set_progress(
                    index, len(recent)
                )
            continue
        else:
            seeds, rows = process_gkg_file(
                link,
                cache_dir=cache_dir,
                reporter=reporter,
                stats=stats,
            )
        all_seeds.extend(seeds)
        total_rows += rows
        if recent and not reporter.verbose:
            reporter.instance(instance_name or "GDELT").set_progress(index, len(recent))

    seen, unique = set(), []
    for s in all_seeds:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique.append(s)
    LOGGER.debug("Unique seeds=%s from total_rows=%s", len(unique), total_rows)

    reporter.info(f"Found {len(unique)} unique seeds from {total_rows} rows checked")
    # Sort unique seeds by date descending for display purposes
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
