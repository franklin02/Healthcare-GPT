"""
This migrates a JSON file from the path data/lablr/*.json into supaabase. This does duplicate 
cleaning by calling dedup_file.py, no other data cleaning is given. This will push everything 
to the lablr_raw table, keep all the schema, and add only 2 cols: reviewer and classified

- Reviewer alternates the REVIEWERS list to make sure we all get assigned an even percentage
    of the given file.
- Classified is a bool set to false. When false, it will show up on the user's feed. Only
    after the user lables this with lablr (either true,lfalse, or re-classified) this will turn
    true and be taken away from their feed

Before pushing, the loaded rows are run through the same semantic dedup as
scripts/dedup_file.py (via dedup_sources function), so only unique incidents 
reach lablr_raw.

----------- How to run -----------
1) Have supabase creds
2) Have an already clean ish JSON file in data/lablr/
3) run the terminal command `python -m scripts.lablr_migration`
    optional: `--threshold 0.5` to tune the dedup cosine-distance cutoff

"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.dedup_file import _DEFAULT_THRESHOLD, dedup_sources  # noqa: E402
from src.logging_utils import get_file_logger  # noqa: E402

try:
    import httpx
    from supabase import ClientOptions, create_client
except ModuleNotFoundError:
    create_client = None

LOG_DIR = _PROJECT_ROOT / "data" / "logs"
LOGGER = get_file_logger(__name__, LOG_DIR / "lablr_migration.log")

LABLR_DIR = _PROJECT_ROOT / "data" / "lablr"

REVIEWERS = [
    "Edgar",
    "Hunter",
    "Evan",
    "Lachlan",
    "Dolan",
    "Briana",
]

_reviewer_cycle = itertools.cycle(REVIEWERS)


def _next_person() -> str:
    """
    Return the next reviewer, cycling through the 6-person REVIEWERS list
    """
    return next(_reviewer_cycle)


ALLOWED_SUBSECTORS = {
    "drug_shortage",
    "medical_device_shortage",
    "cyber_attack",
    "natural_disaster",
    "other",
}

_BATCH_SIZE = 100

load_dotenv()


def _has_supabase_creds() -> bool:
    return bool(os.environ.get("SUPABASE_URL")) and bool(os.environ.get("SUPABASE_KEY"))


def _get_client():
    """
    Gets a supabase client, if no keys this handles it gracefully
    """
    if create_client is None:
        return None
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not (url and key):
        return None
    return create_client(url, key, options=ClientOptions(httpx_client=httpx.Client()))


def _build_row(record: dict) -> dict | None:
    """
    Validate a raw record and shape it into a lablr_raw row (no reviewer yet).
    """
    record_id = (record.get("id") or "").strip()
    title = (record.get("title") or "").strip()
    source_name = (record.get("source_name") or "").strip()
    subsector = (record.get("subsector") or "").strip()

    if not record_id:
        LOGGER.info("[SKIP] missing id: %s", title[:80])
        return None
    if not title:
        LOGGER.info("[SKIP] missing title: id=%s", record_id)
        return None
    if not source_name:
        LOGGER.info("[SKIP] missing source_name: %s", title[:80])
        return None
    if subsector not in ALLOWED_SUBSECTORS:
        LOGGER.info("[SKIP] subsector '%s' not allowed: %s", subsector, title[:80])
        return None

    return {
        "id": record_id,
        "title": title,
        "source_name": source_name,
        "direct_link": record.get("direct_link") or None,
        "subsector": subsector,
        "date_accessed": record.get("date_accessed") or None,
        "date_published": record.get("date_published") or None,
        "content": record.get("content") or None,
        "exec_summary": record.get("exec_summary") or "",
        "confidence_level": record.get("confidence_level") or None,
        "risk_level": record.get("risk_level") or None,
        "geography_scope": record.get("geography_scope") or None,
        "start_date": record.get("start_date") or None,
        "end_date": record.get("end_date") or None,
        "resilience_or_mitigation_observed": record.get(
            "resilience_or_mitigation_observed"
        )
        or None,
        "subsector_data": record.get("subsector_data") or {},
    }


def _load_rows() -> tuple[list[dict], int, int]:
    """
    Read every data/lablr/*.json and return (valid_rows, total_records, skipped),
    pure stats/sanity check
    """
    rows: list[dict] = []
    total = 0
    skipped = 0

    for path in sorted(LABLR_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WARN] Could not read {path.name}: {exc}")
            LOGGER.warning("Could not read %s: %s", path.name, exc)
            continue

        sources = payload.get("sources", []) if isinstance(payload, dict) else []
        if not isinstance(sources, list):
            print(f"[WARN] {path.name}: 'sources' is not a list, skipping file")
            LOGGER.warning("%s: 'sources' is not a list, skipping", path.name)
            continue

        for record in sources:
            total += 1
            if not isinstance(record, dict):
                skipped += 1
                continue
            row = _build_row(record)
            if row is None:
                skipped += 1
            else:
                rows.append(row)

    return rows, total, skipped


def _assign_reviewers(rows: list[dict]) -> None:
    """
    Assign reviewers by cycling `_next_person` over rows sorted by id.
    """
    rows.sort(key=lambda r: r["id"])
    for row in rows:
        row["reviewer"] = _next_person()


def migrate_lablr_raw(threshold: float = _DEFAULT_THRESHOLD) -> None:
    if not _has_supabase_creds():
        print("[WARN] SUPABASE_URL or SUPABASE_KEY not set — nothing to migrate")
        LOGGER.warning("Supabase creds missing; aborting migration")
        return

    client = _get_client()
    if client is None:
        print("[WARN] Could not create Supabase client (supabase-py not installed?)")
        LOGGER.warning("create_client returned None; aborting migration")
        return

    if not sorted(LABLR_DIR.glob("*.json")):
        print(f"[WARN] No *.json files found under {LABLR_DIR}")
        LOGGER.warning("No JSON files found in %s", LABLR_DIR)
        return

    rows, total, skipped = _load_rows()

    if not rows:
        print(f"[WARN] No valid rows to insert (total {total}, skipped {skipped})")
        return

    rows, dupes = dedup_sources(rows, threshold=threshold)
    print(f"[OK] dedup: {len(rows)} unique | {len(dupes)} near-duplicates dropped")
    LOGGER.info("dedup unique=%d dropped=%d", len(rows), len(dupes))

    if not rows:
        print("[WARN] No unique rows left after dedup")
        return

    _assign_reviewers(rows)

    sent = 0
    inserted = 0
    for start in range(0, len(rows), _BATCH_SIZE):
        batch = rows[start : start + _BATCH_SIZE]
        resp = (
            client.table("lablr_raw")
            .upsert(batch, on_conflict="id", ignore_duplicates=True)
            .execute()
        )
        sent += len(batch)

        inserted += (
            len(resp.data) if getattr(resp, "data", None) is not None else len(batch)
        )

    per_reviewer = Counter(row["reviewer"] for row in rows)
    split = ", ".join(f"{name}={per_reviewer.get(name, 0)}" for name in REVIEWERS)

    print(
        f"[OK] total {total} | skipped {skipped} | "
        f"sent {sent} | newly inserted {inserted} (rest already present)"
    )
    print(f"[OK] reviewer split: {split}")
    LOGGER.info(
        "total=%d skipped=%d sent=%d inserted=%d split=%s",
        total,
        skipped,
        sent,
        inserted,
        split,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Dedup data/lablr/*.json and migrate the unique rows to lablr_raw."
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=_DEFAULT_THRESHOLD,
        help=f"Cosine-distance cutoff for near-duplicates (default: {_DEFAULT_THRESHOLD}).",
    )
    args = parser.parse_args()
    migrate_lablr_raw(threshold=args.threshold)
