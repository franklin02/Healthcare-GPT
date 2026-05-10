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


def process_seed(seed: dict) -> dict | None:
    """
    Run a single seed through validation + extraction.
    Returns a record dict if validated as a disruption, else None.
    """
    url = seed["url"]
    print(f"  -> fetching {url[:90]}")
    body = get_body(url)
    if not body:
        print("     [skip] empty body")
        return None

    title = url  
    excerpt = body[:BODY_CHAR_LIMIT]

    is_disruption, detail = ai_check_validation(title, excerpt)
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


def run(num_files: int, limit: int | None, subsectors: str) -> list[dict]:
    subsector_list = ["all"] if subsectors == "all" else [s.strip() for s in subsectors.split(",") if s.strip()]
    
    # Validate subsectors early
    valid_subsectors = set(SUBSECTOR_THEMES.keys()) | {"all"}
    invalid = [s for s in subsector_list if s not in valid_subsectors]
    if invalid:
        print(f"Error: Invalid subsector(s): {', '.join(invalid)}")
        print(f"Valid subsectors are: cyber_attack, drug_shortage, medical_device_shortage, natural_disaster, or all")
        return []
    
    seeds = [seed for subsector in subsector_list for seed in backfill_cyber_seeds(num_files=num_files, subsector=subsector)]
    if limit:
        seeds = seeds[:limit]

    print(f"\nProcessing {len(seeds)} seeds...\n")
    records = []
    for i, seed in enumerate(seeds, start=1):
        print(f"[{i}/{len(seeds)}]")
        rec = process_seed(seed)
        if rec:
            records.append(rec)

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

    # write records out grouped by source to src/data/Ready_for_RAG/<source>.json
    def _norm_source(name: str) -> str:
        if not name:
            return "unknown"
        return "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in name).lower()

    out_dir = Path(__file__).parent.parent / "data" / "Ready_for_RAG"
    out_dir.mkdir(parents=True, exist_ok=True)

    # write single GDELT.json with top-level "sources" array (schema-like)
    out_file = out_dir / "GDELT.json"
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

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"sources": out_recs}, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(out_recs)} records to {out_file}")

    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GDELT end-to-end runner")
    parser.add_argument(
        "--num-files",
        type=int,
        default=2,
        help="GDELT GKG files to scan (default: 2 ~= 30 min of data)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Cap on seeds to process; useful for smoke-testing (default: 3)",
    )
    parser.add_argument("--subsectors", default="all", help="Comma-separated subsectors to scan, or all")
    args = parser.parse_args()
    run(num_files=args.num_files, limit=args.limit, subsectors=args.subsectors)
