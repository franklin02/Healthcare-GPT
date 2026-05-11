"""
GDELT end-to-end runner.

Pipeline:
  cyber_security.backfill_cyber_seeds  -- collect candidate seeds from GDELT GKG
  helpers.get_body                     -- scrape page body
  helpers.ai_check_validation          -- LLM validates as active disruption
  helpers.find_subsector_fields        -- LLM extracts schema-specific fields

"""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import os

from gdelt_seeds import backfill_cyber_seeds, SUBSECTOR_THEMES
from helpers import ai_check_validation, find_subsector_fields, get_body

BODY_CHAR_LIMIT = 4000


def stable_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def fmt_dt(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
    except Exception:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return value


def load_seen(seen_file: Path | None = None) -> set:
    """Load seen URLs from file. Returns empty set if file doesn't exist."""
    if seen_file is None:
        seen_file = Path(__file__).parent.parent / "data" / "seen_urls.json"
    try:
        with open(seen_file, "r", encoding="utf-8") as sf:
            return set(json.load(sf) or [])
    except Exception:
        return set()


def save_seen(seen: set, seen_file: Path | None = None) -> None:
    """Save seen URLs to file."""
    if seen_file is None:
        seen_file = Path(__file__).parent.parent / "data" / "seen_urls.json"
    try:
        with open(seen_file, "w", encoding="utf-8") as sf:
            json.dump(sorted(list(seen)), sf, ensure_ascii=False, indent=2)
    except Exception:
        pass


def process_seed(seed: dict, seen: set) -> dict | None:
    """
    Run a single seed through validation + extraction.
    Returns a record dict if validated as a disruption, else None.
    """
    url = seed["url"]

    if url in seen:
        print(f"  -> [skip] already seen by LLM {url[:90]}")
        return None

    print(f"  -> fetching {url[:90]}")
    body = get_body(url)
    if not body:
        print("     [skip] empty body")
        return None

    title = url  
    excerpt = body[:BODY_CHAR_LIMIT]

    is_disruption, detail = ai_check_validation(title, excerpt)

    seen.add(url)

    if not is_disruption:
        print(f"     [skip] not a disruption: {detail}")
        return None

    subsector = detail
    
    # Skip if subsector is invalid or "none"
    valid_subsectors = {"drug_shortage", "medical_device_shortage", "cyber_attack", "natural_disaster", "other"}
    if subsector not in valid_subsectors:
        print(f"     [skip] invalid subsector: {subsector}")
        return None
    
    print(f"     OK  disruption confirmed: {subsector}")

    fields = find_subsector_fields(subsector, title, excerpt)

    return {
        "id": stable_id(url),
        "title": title,
        "source_name": seed.get("source", ""),
        "direct_link": url,
        "subsector": subsector,
        "date_published": seed.get("date", ""),
        "content": body,
        "subsector_data": fields,
    }


def run(num_files: int, limit: int | None, subsectors: str, output_path: str | None = None, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    subsector_list = ["all"] if subsectors == "all" else [s.strip() for s in subsectors.split(",") if s.strip()]
    
    # Validate subsectors early
    valid_subsectors = set(SUBSECTOR_THEMES.keys()) | {"all"}
    invalid = [s for s in subsector_list if s not in valid_subsectors]
    if invalid:
        print(f"Error: Invalid subsector(s): {', '.join(invalid)}")
        print(f"Valid subsectors are: cyber_attack, drug_shortage, medical_device_shortage, natural_disaster, or all")
        return []
    
    # Load seen URLs once at the start
    seen = load_seen()
    
    seeds = [seed for subsector in subsector_list for seed in backfill_cyber_seeds(num_files=num_files, subsector=subsector, start_date=start_date, end_date=end_date)]
    if limit and not (start_date or end_date):
        seeds = seeds[:limit]

    print(f"\nProcessing {len(seeds)} seeds...\n")
    records = []
    for i, seed in enumerate(seeds, start=1):
        print(f"[{i}/{len(seeds)}]")
        rec = process_seed(seed, seen)
        if rec:
            records.append(rec)

    # Save seen URLs once at the end
    save_seen(seen)

    print("\n" + "=" * 60)
    print(f"Seeds in:  {len(seeds)}")
    print(f"Validated: {len(records)}")
    print(f"Skipped:   {len(seeds) - len(records)}")
    print("=" * 60)

    for rec in records:
        print(f"\n--- {rec['id']} ({rec['subsector']}) ---")
        print(f"URL: {rec['direct_link']}")
        print(f"Source: {rec['source_name']}")
        print(f"Fields: {rec['subsector_data']}")

    default_out_dir = Path(__file__).parent.parent / "data" / "Ready_for_RAG"
    if output_path:
        out_path = Path(output_path)
        out_file = out_path if out_path.suffix.lower() == ".json" else out_path / "GDELT.json"
    else:
        out_file = default_out_dir / "GDELT.json"

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_recs = []
    for r in records:
        out_recs.append(
            {
                "id": r.get("id", ""),
                "title": r.get("title", ""),
                "source_name": r.get("source_name", ""),
                "direct_link": r.get("direct_link", ""),
                "subsector": r.get("subsector", ""),
                "date_accessed": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "date_published": fmt_dt(r.get("date_published", "")),
                "content": r.get("content", ""),
                "exec_summary": "",
                "confidence_level": "",
                "risk level": "",
                "geography_scope": "",
                "start_date": "",
                "end_date": "",
                "resilience_or_mitigation_observed": "",
                "subsector_data": r.get("subsector_data", {}),
            }
        )

    try:
        if out_file.exists():
            with open(out_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, dict) and "sources" in existing and isinstance(existing["sources"], list):
                combined = existing["sources"] + out_recs
            elif isinstance(existing, list):
                combined = existing + out_recs
            else:
                combined = out_recs
        else:
            combined = out_recs
    except Exception:
        combined = out_recs

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"sources": combined}, f, ensure_ascii=False, indent=2)
    print(f"Appended {len(out_recs)} records to {out_file} (total: {len(combined)})")

    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GDELT end-to-end runner")
    parser.add_argument(
        "--num-files", "-n",
        type=int,
        default=2,
        help="GDELT GKG files to scan (default: 2 ~= 30 min of data)",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=3,
        help="Cap on seeds to process; useful for smoke-testing (default: 3)",
    )
    parser.add_argument(
        "--output-path", "-o",
        default=None,
        help="Output JSON file or directory. If a directory is provided, GDELT.json is written inside it.",
    )
    parser.add_argument(
        "--start-date", 
        default=None, 
        help="Earliest GDELT file date to include (Format: YYYYMMDD, YYYYMMDDHHMMSS, YYYY-MM-DD, YYYY-MM-DD HH:MM:SS)")
    parser.add_argument(
        "--end-date", 
        default=None, 
        help="Latest GDELT file date to include (Format: YYYYMMDD, YYYYMMDDHHMMSS, YYYY-MM-DD, YYYY-MM-DD HH:MM:SS)")
    parser.add_argument(
        "--subsectors", "-s", 
        default="all", 
        help="Comma-separated subsectors to scan, or all")
    args = parser.parse_args()
    run(num_files=args.num_files, limit=args.limit, subsectors=args.subsectors, output_path=args.output_path, start_date=args.start_date, end_date=args.end_date)
