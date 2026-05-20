"""
GDELT end-to-end runner.

Pipeline:
  gdelt_seeds.backfill_cyber_seeds     -- collect candidate seeds from GDELT GKG
  src.shared_utils.get_body            -- scrape page body
  src.shared_utils.ai_check_validation -- LLM validates as active disruption
  src.shared_utils.extract_fields      -- LLM extracts schema-specific fields

"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
import os
import shutil

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from gdelt_seeds import backfill_cyber_seeds, SUBSECTOR_THEMES
from src.shared_utils import ai_check_validation, extract_fields, get_body
from src.classes import Vulnerability, SUBSECTOR_DATA_CLASSES

BODY_CHAR_LIMIT = 4000


def stable_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def fmt_dt(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
    except Exception:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
                "%Y-%m-%d %H:%M"
            )
        except Exception:
            return value


# intermediate stages directory constants + helper functions
PROJECT_ROOT = Path(__file__).parents[2]
RAW_GDELT_DIR = PROJECT_ROOT / "data" / "raw" / "gdelt"
SEEDS_DIR = RAW_GDELT_DIR / "seeds"
VALIDATED_DIR = RAW_GDELT_DIR / "validated"
ENRICHED_DIR = RAW_GDELT_DIR / "enriched"


def ensure_raw_dirs() -> None:
    for directory in (SEEDS_DIR, VALIDATED_DIR, ENRICHED_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def clear_directory(directory: Path) -> None:
    """Delete all files and subdirectories inside a directory."""
    if not directory.exists():
        return
    for item in directory.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except Exception as exc:
            print(f"Warning: failed to remove {item}: {exc}")


def persist_raw_seeds(raw_seeds: list[dict]) -> None:
    for seed in raw_seeds:
        article_id = stable_id(seed["url"])

        save_json(
            SEEDS_DIR / f"{article_id}.json",
            {
                "id": article_id,
                "stage": "seed",
                "url": seed["url"],
                "seed": seed,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            },
        )


def persist_stage(
    directory: Path,
    article_id: str,
    stage: str,
    url: str,
    data: dict,
) -> None:
    save_json(
        directory / f"{article_id}.json",
        {
            "id": article_id,
            "stage": stage,
            "url": url,
            "record": data,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


def load_seen(seen_file: Path | None = None) -> set:
    """Load seen URLs from file. Returns empty set if file doesn't exist."""
    if seen_file is None:
        seen_file = PROJECT_ROOT / "data" / "seen_urls.json"
    try:
        with open(seen_file, "r", encoding="utf-8") as sf:
            return set(json.load(sf) or [])
    except Exception:
        return set()


def save_seen(seen: set, seen_file: Path | None = None) -> None:
    """Save seen URLs to file."""
    if seen_file is None:
        seen_file = PROJECT_ROOT / "data" / "seen_urls.json"
    try:
        with open(seen_file, "w", encoding="utf-8") as sf:
            json.dump(sorted(list(seen)), sf, ensure_ascii=False, indent=2)
    except Exception:
        pass


def process_seed(seed: dict, seen: set) -> Vulnerability | None:
    """
    Run a single seed through validation + extraction.
    Returns a Vulnerability if validated as a disruption, else None.
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
    valid_subsectors = {
        "drug_shortage",
        "medical_device_shortage",
        "cyber_attack",
        "natural_disaster",
        "other",
    }
    if subsector not in valid_subsectors:
        print(f"     [skip] invalid subsector: {subsector}")
        return None

    print(f"     OK  disruption confirmed: {subsector}")

    sector_data, subsector_data_dict = extract_fields(subsector, title, excerpt)
    subsector_cls = SUBSECTOR_DATA_CLASSES[subsector]

    return Vulnerability(
        id=stable_id(url),
        title=title,
        source_name=seed.get("source", ""),
        direct_link=url,
        subsector=subsector,
        date_accessed=datetime.now().strftime("%Y-%m-%d %H:%M"),
        date_published=seed.get("date", ""),
        content=body,
        exec_summary=sector_data.get("exec_summary") or "",
        geography_scope=sector_data.get("geography_scope"),
        start_date=sector_data.get("start_date"),
        end_date=sector_data.get("end_date"),
        resilience_or_mitigation_observed=sector_data.get(
            "resilience_or_mitigation_observed"
        ),
        subsector_data=subsector_cls.from_dict(subsector_data_dict),
    )


def run(
    num_files: int,
    limit: int | None,
    subsectors: str,
    output_path: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    seen_urls_file: str | None = None,
) -> list[dict]:
    subsector_list = (
        ["all"]
        if subsectors == "all"
        else [s.strip() for s in subsectors.split(",") if s.strip()]
    )

    if seen_urls_file:
        seen_urls_path = Path(seen_urls_file)
        if seen_urls_path.suffix.lower() != ".json":
            seen_urls_path = seen_urls_path / "seen_urls.json"
    else:
        seen_urls_path = None

    # Validate subsectors early
    valid_subsectors = set(SUBSECTOR_THEMES.keys()) | {"all"}
    invalid = [s for s in subsector_list if s not in valid_subsectors]
    if invalid:
        print(f"Error: Invalid subsector(s): {', '.join(invalid)}")
        print(
            f"Valid subsectors are: cyber_attack, drug_shortage, medical_device_shortage, natural_disaster, or all"
        )
        return []

    ensure_raw_dirs()

    # Load seen URLs once at the start
    seen = load_seen(seen_urls_path)

    raw_seeds = [
        seed
        for subsector in subsector_list
        for seed in backfill_cyber_seeds(
            num_files=num_files,
            subsector=subsector,
            start_date=start_date,
            end_date=end_date,
        )
    ]
    persist_raw_seeds(raw_seeds)
    seeds = raw_seeds
    if limit and not (start_date or end_date):
        seeds = seeds[:limit]

    print(f"\nProcessing {len(seeds)} seeds...\n")
    records = []
    for i, seed in enumerate(seeds, start=1):
        print(f"[{i}/{len(seeds)}]")
        url = seed["url"]
        article_id = stable_id(url)
        rec = process_seed(seed, seen)
        if rec:
            persist_stage(VALIDATED_DIR, article_id, "validated", url, rec.to_dict())
            persist_stage(ENRICHED_DIR, article_id, "enriched", url, rec.to_dict())
            records.append(rec)

    # Save seen URLs once at the end
    save_seen(seen, seen_urls_path)

    print("\n" + "=" * 60)
    print(f"Seeds in:  {len(seeds)}")
    print(f"Validated: {len(records)}")
    print(f"Skipped:   {len(seeds) - len(records)}")
    print("=" * 60)

    for rec in records:
        print(f"\n--- {rec.id} ({rec.subsector}) ---")
        print(f"URL: {rec.direct_link}")
        print(f"Source: {rec.source_name}")
        print(f"Fields: {rec.subsector_data}")

    default_out_dir = PROJECT_ROOT / "data" / "processed"
    if output_path:
        out_path = Path(output_path)
        out_file = (
            out_path if out_path.suffix.lower() == ".json" else out_path / "GDELT.json"
        )
    else:
        out_file = default_out_dir / "GDELT.json"

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_recs = []
    for r in records:
        d = r.to_dict()
        d["date_published"] = fmt_dt(d.get("date_published", ""))
        out_recs.append(d)

    try:
        if out_file.exists():
            with open(out_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if (
                isinstance(existing, dict)
                and "sources" in existing
                and isinstance(existing["sources"], list)
            ):
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

    # Clear the seed files after a successful pipeline run
    clear_directory(SEEDS_DIR)
    print(f"Cleared seed staging directory: {SEEDS_DIR}")

    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GDELT end-to-end runner")
    parser.add_argument(
        "--num-files",
        "-n",
        type=int,
        default=2,
        help="GDELT GKG files to scan (default: 2 ~= 30 min of data)",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=3,
        help="Cap on seeds to process; useful for smoke-testing (default: 3)",
    )
    parser.add_argument(
        "--output-path",
        "-o",
        default=None,
        help="Output JSON file or directory. If a directory is provided, GDELT.json is written inside it. (default: data/processed/GDELT.json)",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Earliest GDELT file date to include (Format: YYYYMMDD, YYYYMMDDHHMMSS, YYYY-MM-DD, YYYY-MM-DD HH:MM:SS)",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Latest GDELT file date to include (Format: YYYYMMDD, YYYYMMDDHHMMSS, YYYY-MM-DD, YYYY-MM-DD HH:MM:SS)",
    )
    parser.add_argument(
        "--seen-urls-file",
        default=None,
        help="Path to store/load seen URLs JSON file (default: data/seen_urls.json)",
    )
    parser.add_argument(
        "--subsectors",
        "-s",
        default="all",
        help="Comma-separated subsectors to scan, or all",
    )
    args = parser.parse_args()
    run(
        num_files=args.num_files,
        limit=args.limit,
        subsectors=args.subsectors,
        output_path=args.output_path,
        start_date=args.start_date,
        end_date=args.end_date,
        seen_urls_file=args.seen_urls_file,
    )
