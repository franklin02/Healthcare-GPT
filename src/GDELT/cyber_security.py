import requests
import pandas as pd
import zipfile
import io
import re
from urllib.parse import urlparse

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


DRUG_SHORAGE_THEMES = {
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
    "RATIONAL_SELECTION_AND_USE_OF_DRUGS"
}

DEVICE_SHORTAGE_THEMES = {
    "MEDICAL_EQUIPMENT",
    "HEALTH_TECHNOLOGIES",
    "PROCUREMENT_OF_HEALTH_TECHNOLOGIES",
    "MEDICAL_SUPPLIES_FINANCE"
}

NATURAL_DISASTER_THEMES = {
    "NATURAL_DISASTER"
}

NOISE_THEMES = {"SPORTS", "GAMES_ESPORTS", "ENV_", "TOURISM", "EDUCATION_UNIVERSITY"}

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
        return True
    for entry in location_str.split(";"):
        parts = entry.split("#")
        if len(parts) >= 3 and parts[2].strip().upper() == "US":
            return True
    return False


def themes_match(theme_str, theme_set):
    if not isinstance(theme_str, str):
        return False
    u = theme_str.upper()
    return any(t in u for t in theme_set)


def themes_match_noise(theme_str):
    if not isinstance(theme_str, str):
        return False
    u = theme_str.upper()
    return any(n in u for n in NOISE_THEMES)


def url_passes_quality(url):
    if not isinstance(url, str) or not url.startswith("http"):
        return False
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.lower()
    except Exception:
        return False
    for btld in BLOCKED_TLDS:
        if domain.endswith(btld):
            return False
    if not any(domain.endswith(tld) for tld in US_TLDS):
        return False
    if URL_DENY_PATTERNS.search(path):
        return False
    if not URL_REQUIRE_PATTERNS.search(url.lower()):
        return False
    return True


def process_gkg_file(link):
    try:
        r = requests.get(link, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  [SKIP] Download failed {link.split('/')[-1]}: {e}")
        return []
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
            print(f"  [SKIP] Only {raw.shape[1]} cols in {link.split('/')[-1]}")
            return []

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
        cyber = df["themes"].apply(lambda t: themes_match(t, CYBER_THEMES))
        health = df["themes"].apply(lambda t: themes_match(t, HEALTH_THEMES))
        noise = df["themes"].apply(lambda t: themes_match(t, NOISE_THEMES))
        df = df[cyber & health & ~noise].copy()
        if df.empty:
            print(f"    [FILTERED OUT] No results after theme filter")
            return []

        df = df[df["locs"].apply(is_us_located)].copy()
        if df.empty:
            print(f"    [FILTERED OUT] No results after US location filter")
            return []

        df = df[df["url"].apply(url_passes_quality)].copy()
        if df.empty:
            print(f"    [FILTERED OUT] No results after URL quality filter")
            return []

        fname = link.split("/")[-1]
        print(f"  ✓ {fname}: {len(df)} leads from {total} rows")
        return [
            {
                "url": row["url"],
                "source": row["source"],
                "themes": row["themes"],
                "date": row["date"],
                "file": fname,
            }
            for _, row in df.iterrows()
        ]
    except Exception as e:
        print(f"  [SKIP] Parse error {link.split('/')[-1]}: {e}")
        return []


def backfill_cyber_seeds(num_files=20):
    print("Fetching GDELT master file list...")
    resp = requests.get(
        "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt", timeout=15
    )
    links = [
        line.split(" ")[2]
        for line in resp.text.strip().split("\n")
        if ".gkg.csv.zip" in line
    ]
    recent = links[-num_files:]
    print(f"Scanning {num_files} files (~{num_files * 15 / 60:.1f} hours)...\n")

    all_seeds = []
    for link in recent:
        all_seeds.extend(process_gkg_file(link))

    seen, unique = set(), []
    for s in all_seeds:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique.append(s)

    print(f"\n{'=' * 60}")
    print(f"Total Unique Seeds Found: {len(unique)}")
    print(f"{'=' * 60}\n")
    for s in unique:
        print(f"[{s['date']}]  {s['source']}")
        print(f"  URL: {s['url']}")
        relevant = [
            t
            for t in (s["themes"] or "").split(";")
            if any(c in t.upper() for c in CYBER_THEMES | HEALTH_THEMES)
        ]
        print(f"  Themes: {' | '.join(relevant[:8])}\n")

    return unique


if __name__ == "__main__":
    seeds = backfill_cyber_seeds(num_files=150)
    urls = [s["url"] for s in seeds]
    from ollama_filter import filter_with_ollama

    confirmed = filter_with_ollama(urls)
    print(f"Confirmed: {len(confirmed)} / {len(urls)} URLs passed Ollama filter")
    for url in confirmed:
        print(url)
