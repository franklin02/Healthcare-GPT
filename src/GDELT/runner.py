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
import shutil

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gdelt_seeds import backfill_cyber_seeds, SUBSECTOR_THEMES
from src.shared_utils import ai_check_validation, extract_fields, get_body
from src.classes import Vulnerability, SUBSECTOR_DATA_CLASSES
from src.logging_utils import get_file_logger

# intermediate stages directory constants + helper functions
PROJECT_ROOT = Path(__file__).parents[2]
RAW_GDELT_DIR = PROJECT_ROOT / "data" / "raw" / "gdelt"
SEEDS_DIR = RAW_GDELT_DIR / "seeds"
VALIDATED_DIR = RAW_GDELT_DIR / "validated"
ENRICHED_DIR = RAW_GDELT_DIR / "enriched"

LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "gdelt_runner.log"

BODY_CHAR_LIMIT = 4000
LOGGER = get_file_logger(__name__, LOG_FILE)


def stable_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def fmt_dt(value: str) -> str:
    try:
        LOGGER.debug("Formatting date value: %s", value)
        return datetime.strptime(value, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
    except Exception:
        LOGGER.warning("Failed to parse date value %s", value)
        try:
            LOGGER.debug("Trying alternative date format for value: %s", value)
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
                "%Y-%m-%d %H:%M"
            )
        except Exception:
            LOGGER.warning("Failed to parse date value %s", value)
            return value


def ensure_raw_dirs() -> None:
    for directory in (SEEDS_DIR, VALIDATED_DIR, ENRICHED_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    LOGGER.debug("Ensured raw directories: %s", RAW_GDELT_DIR)


def save_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    LOGGER.debug("Wrote JSON: %s", path)


def clear_directory(directory: Path) -> None:
    """Delete all files and subdirectories inside a directory."""
    if not directory.exists():
        LOGGER.debug("Directory does not exist, skipping clear: %s", directory)
        return
    for item in directory.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except Exception as exc:
            print(f"Warning: failed to remove {item}: {exc}")
            LOGGER.warning("Failed to remove %s: %s", item, exc)


def persist_raw_seeds(raw_seeds: list[dict]) -> None:
    LOGGER.debug("Persisting %s raw seeds", len(raw_seeds))
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
    LOGGER.debug("Persisting stage=%s id=%s url=%s", stage, article_id, url)
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
            LOGGER.debug("Loaded seen URLs from %s", seen_file)
            return set(json.load(sf) or [])
    except Exception:
        LOGGER.debug("No seen URLs file found at %s", seen_file)
        return set()


def save_seen(seen: set, seen_file: Path | None = None) -> None:
    """Save seen URLs to file."""
    if seen_file is None:
        seen_file = PROJECT_ROOT / "data" / "seen_urls.json"
    try:
        with open(seen_file, "w", encoding="utf-8") as sf:
            json.dump(sorted(list(seen)), sf, ensure_ascii=False, indent=2)
        LOGGER.debug("Saved %s seen URLs to %s", len(seen), seen_file)
    except Exception:
        LOGGER.warning("Failed to save seen URLs to %s", seen_file)
        pass


def process_seed(seed: dict, seen: set) -> Vulnerability | None:
    """
    Run a single seed through validation + extraction.
    Returns a Vulnerability if validated as a disruption, else None.
    """
    url = seed["url"]
    LOGGER.debug("Processing seed url=%s", url)

    if url in seen:
        print(f"  -> [skip] already seen by LLM {url[:90]}")
        LOGGER.debug("Skipping seen url=%s", url)
        return None

    print(f"  -> fetching {url[:90]}")
    body = get_body(url)
    if not body:
        print("     [skip] empty body")
        LOGGER.debug("Empty body for url=%s", url)
        return None

    title = url
    excerpt = body[:BODY_CHAR_LIMIT]

    is_disruption, detail = ai_check_validation(title, excerpt)
    LOGGER.debug(
        "LLM validation url=%s disruption=%s detail=%s", url, is_disruption, detail
    )

    seen.add(url)

    if not is_disruption:
        print(f"     [skip] not a disruption: {detail}")
        LOGGER.debug("Not a disruption url=%s detail=%s", url, detail)
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
        LOGGER.debug("Invalid subsector url=%s subsector=%s", url, subsector)
        return None

    print(f"     OK  disruption confirmed: {subsector}")
    LOGGER.debug("Disruption confirmed url=%s subsector=%s", url, subsector)

    sector_data, subsector_data_dict = extract_fields(subsector, title, excerpt)
    LOGGER.debug(
        "Extracted fields url=%s sector_keys=%s subsector_keys=%s",
        url,
        list(sector_data.keys()),
        list(subsector_data_dict.keys()),
    )
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
    LOGGER.debug(
        "Run started num_files=%s limit=%s subsectors=%s start_date=%s end_date=%s output_path=%s",
        num_files,
        limit,
        subsectors,
        start_date,
        end_date,
        output_path,
    )
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
            "Valid subsectors are: cyber_attack, drug_shortage, medical_device_shortage, natural_disaster, or all"
        )
        LOGGER.warning("Invalid subsectors requested: %s", invalid)
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
    LOGGER.debug("Collected %s raw seeds", len(raw_seeds))
    persist_raw_seeds(raw_seeds)

    # Date-bounded runs should always process the full matched seed set.
    if start_date or end_date:
        limit = None

    seeds = raw_seeds
    if limit:
        seeds = seeds[:limit]
    LOGGER.debug("Processing %s seeds after limit", len(seeds))

    print(f"\nProcessing {len(seeds)} seeds...\n")
    records = []
    for i, seed in enumerate(seeds, start=1):
        print(f"[{i}/{len(seeds)}]")
        LOGGER.debug("Processing seed %s/%s url=%s", i, len(seeds), seed["url"])
        url = seed["url"]
        article_id = stable_id(url)
        rec = process_seed(seed, seen)
        if rec:
            persist_stage(VALIDATED_DIR, article_id, "validated", url, rec.to_dict())
            persist_stage(ENRICHED_DIR, article_id, "enriched", url, rec.to_dict())
            records.append(rec)
        else:
            LOGGER.debug("Seed skipped url=%s", url)

    # Save seen URLs once at the end
    save_seen(seen, seen_urls_path)

    print("\n" + "=" * 60)
    print(f"Seeds in:  {len(seeds)}")
    print(f"Validated: {len(records)}")
    print(f"Skipped:   {len(seeds) - len(records)}")
    print("=" * 60)
    LOGGER.debug(
        "Summary seeds_in=%s validated=%s skipped=%s",
        len(seeds),
        len(records),
        len(seeds) - len(records),
    )

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
    LOGGER.debug("Output file resolved to %s", out_file)

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
        LOGGER.warning(
            "Failed to read existing output file %s: %s", out_file, exc_info=True
        )
        combined = out_recs

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"sources": combined}, f, ensure_ascii=False, indent=2)
    print(f"Appended {len(out_recs)} records to {out_file} (total: {len(combined)})")
    LOGGER.debug("Wrote %s records to %s", len(combined), out_file)

    # Clear the seed files after a successful pipeline run
    clear_directory(SEEDS_DIR)
    print(f"Cleared seed staging directory: {SEEDS_DIR}")
    LOGGER.debug("Cleared seeds directory: %s", SEEDS_DIR)

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
        default=None,
        help="Cap on seeds to process; useful for smoke-testing (default: 3 unless --num-files is explicitly provided)",
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

    # If --num-files/-n is explicitly provided without --limit/-l, process all
    # discovered seeds for that fetch window instead of using the smoke-test cap.
    n_provided = any(opt in sys.argv[1:] for opt in ("-n", "--num-files"))
    l_provided = any(opt in sys.argv[1:] for opt in ("-l", "--limit"))
    effective_limit = args.limit
    if not l_provided:
        effective_limit = None if n_provided else 3

    run(
        num_files=args.num_files,
        limit=effective_limit,
        subsectors=args.subsectors,
        output_path=args.output_path,
        start_date=args.start_date,
        end_date=args.end_date,
        seen_urls_file=args.seen_urls_file,
    )
