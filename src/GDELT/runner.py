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

from cyber_security import backfill_cyber_seeds
from helpers import ai_check_validation, find_subsector_fields, get_body

BODY_CHAR_LIMIT = 4000


def stable_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


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


def run(num_files: int, limit: int | None) -> list[dict]:
    seeds = backfill_cyber_seeds(num_files=num_files)
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
    args = parser.parse_args()
    run(num_files=args.num_files, limit=args.limit)
