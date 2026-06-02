"""
GDELT end-to-end runner.

Pipeline:
  gdelt_seeds.backfill_cyber_seeds     -- collect candidate seeds from GDELT GKG
  src.shared_utils.get_body            -- scrape page body
  src.shared_utils.get_title           -- scrape page title (skipped on empty body)
  src.shared_utils.ai_check_validation -- LLM validates as active disruption
  src.shared_utils.extract_fields      -- LLM extracts schema-specific fields

Constants:
    BODY_CHAR_LIMIT: Number of characters to include from the article body when passing to the LLM for validation and extraction.

Functions:
    stable_id(url): Generate a stable ID for a given URL using SHA-256 hashing.
    fmt_dt(value): Format a date string into YYYY-MM-DD HH:MM format. Tries multiple input formats and returns the original value if parsing fails.
    ensure_raw_dirs(): Ensure that the raw directories for seeds, validated, and enriched data exist. Creates them if they don't.
    save_json(path, data): Save a dictionary as JSON to the specified path, creating parent directories if needed.
    clear_directory(directory): Delete all files and subdirectories inside a directory.
    persist_raw_seeds(raw_seeds): Persist raw seeds to the seeds directory, using stable IDs for filenames.
    persist_stage(directory, article_id, stage, url, data): Persist data for a specific stage (validated, enriched) using a stable ID for the filename.
    load_seen(seen_file): Load seen URLs from file. Returns a set of URLs that have been seen and processed. If the file does not exist or cannot be read, returns an empty set.
    save_seen(seen, seen_file): Save seen URLs to file.
    process_seed(seed, seen, use_bert, reporter, stats): Run a single seed through validation + extraction. Returns a Vulnerability if validated as a disruption, else None.
    run(num_files, limit, subsectors, output_path, start_date, end_date, seen_urls_file, use_bert, verbose, reporter, stats): Main function to run the GDELT pipeline end-to-end.

"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from src.GDELT.gdelt_seeds import SUBSECTOR_THEMES, backfill_cyber_seeds
from src.classes import SUBSECTOR_DATA_CLASSES, Vulnerability
from src.cli_reporter import CliReporter, PipelineStats
from src.logging_utils import get_file_logger
from src.shared_utils import (
    AI_MODEL,
    ai_check_validation,
    ensure_model_available,
    extract_fields,
    get_body,
    get_title,
    model_unavailable_error,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# intermediate stages directory constants + helper functions
RAW_GDELT_DIR = PROJECT_ROOT / "data" / "raw" / "gdelt"
SEEDS_DIR = RAW_GDELT_DIR / "seeds"
VALIDATED_DIR = RAW_GDELT_DIR / "validated"
ENRICHED_DIR = RAW_GDELT_DIR / "enriched"

LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "gdelt_runner.log"

BODY_CHAR_LIMIT = 4000
LOGGER = get_file_logger(__name__, LOG_FILE)

try:
    from src.supabase_function import has_supabase_creds
    from src.dedup import handle_vuln

    SUPABASE_AVAILABLE = has_supabase_creds()
    if not SUPABASE_AVAILABLE:
        LOGGER.warning("SUPABASE_URL or SUPABASE_KEY missing; DB writes disabled")
except Exception as e:
    LOGGER.warning("Supabase unavailable, DB writes disabled: %s", e)
    SUPABASE_AVAILABLE = False


def _bert_status() -> str:
    """
    Return a string describing the status of the BERT pre-filter, including model details if available. This is used for logging and reporting.

    Returns:
        A string describing the BERT pre-filter status.
    """
    try:
        from src.GDELT.BERT_filter import describe_model

        model_id, device_label = describe_model()
        return f"BERT pre-filter: {model_id} using {device_label}"
    except Exception as exc:
        LOGGER.warning("Failed to describe BERT model: %s", exc)
        return "BERT pre-filter: enabled"


def stable_id(url: str) -> str:
    """Generate a stable ID for a given URL using SHA-256 hashing."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def fmt_dt(value: str) -> str:
    """
    Format a date string into YYYY-MM-DD HH:MM format. Tries multiple input formats and returns the original value if parsing fails.

    Parameters:
        value: The input date string to format.

    Returns:
        A formatted date string in YYYY-MM-DD HH:MM format, or the original value if parsing fails.
    """
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
    """Ensure that the raw directories for seeds, validated, and enriched data exist. Creates them if they don't."""
    for directory in (SEEDS_DIR, VALIDATED_DIR, ENRICHED_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    LOGGER.debug("Ensured raw directories: %s", RAW_GDELT_DIR)


def save_json(path: Path, data: dict) -> None:
    """
    Save a dictionary as JSON to the specified path, creating parent directories if needed.

    Parameters:
        path: The path to the JSON file to save.
        data: The dictionary to save as JSON.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def clear_directory(directory: Path) -> None:
    """
    Delete all files and subdirectories inside a directory.

    Parameters:
        directory: The path to the directory to clear.
    """
    if not directory.exists():
        LOGGER.debug("Directory does not exist, skipping clear: %s", directory)
        return
    # Iterate over all items in the directory and remove them
    for item in directory.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except Exception as exc:
            LOGGER.warning("Failed to remove %s: %s", item, exc)


def dedupe_raw_seeds(raw_seeds: list[dict]) -> list[dict]:
    """
    Deduplicate raw GDELT seeds by exact URL while preserving subsector evidence.

    The first seed for a URL stays canonical so existing single-subsector seed
    metadata remains backward compatible. Later duplicates only contribute
    unique subsector labels to the canonical seed's detected_subsectors list.
    """
    seeds_by_url: dict[str, dict] = {}
    detected_by_url: dict[str, list[str]] = {}

    for seed in raw_seeds:
        url = seed["url"]
        if url not in seeds_by_url:
            seeds_by_url[url] = dict(seed)
            detected_by_url[url] = []

        labels = []
        detected = seed.get("detected_subsectors")
        if isinstance(detected, list):
            labels.extend(detected)
        subsector = seed.get("subsector")
        if subsector:
            labels.append(subsector)

        for label in labels:
            if label not in detected_by_url[url]:
                detected_by_url[url].append(label)

    for url, seed in seeds_by_url.items():
        if detected_by_url[url]:
            seed["detected_subsectors"] = detected_by_url[url]

    return list(seeds_by_url.values())


def persist_raw_seeds(raw_seeds: list[dict]) -> None:
    """
    Persist raw seeds to the seeds directory, using stable IDs for filenames.

    Parameters:
        raw_seeds: A list of seed dictionaries to persist.
    """
    LOGGER.debug("Persisting %s raw seeds", len(raw_seeds))
    # Use stable_id of the URL for the filename to ensure consistent naming and avoid issues with special characters in URLs. This also allows for easy deduplication if the same URL appears multiple times.
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
    """
    Persist data for a specific stage (validated, enriched) using a stable ID for the filename.

    Parameters:
    - directory: The directory to save the file in (e.g., validated, enriched).
    - article_id: The stable ID for the article, used as the filename.
    - stage: The processing stage (e.g., "validated", "enriched") to include in the saved data.
    - url: The URL of the article, included in the saved data for reference.
    - data: The dictionary of data to save for this stage.
    """
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
    """
    Load seen URLs from file. Returns a set of URLs that have been seen and processed. If the file does not exist or cannot be read, returns an empty set.

    Parameters:
        seen_file: Optional path to the JSON file containing seen URLs. If None, defaults to data/seen_urls.json in the project root.

    Returns:
        A set of URLs that have been seen and processed. Returns an empty set if the file does not exist or cannot be read.
    """
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
    """
    Save seen URLs to file.

    Parameters:
        seen: A set of URLs that have been seen and processed.
        seen_file: Optional path to the JSON file to save seen URLs. If None, defaults to data/seen_urls.json in the project root.
    """
    if seen_file is None:
        seen_file = PROJECT_ROOT / "data" / "seen_urls.json"
    try:
        with open(seen_file, "w", encoding="utf-8") as sf:
            json.dump(sorted(list(seen)), sf, ensure_ascii=False, indent=2)
        LOGGER.debug("Saved %s seen URLs to %s", len(seen), seen_file)
    except Exception:
        LOGGER.warning("Failed to save seen URLs to %s", seen_file)
        pass


def process_seed(
    seed: dict,
    seen: set,
    use_bert: bool = False,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
) -> Vulnerability | None:
    """
    Run a single seed through validation + extraction.
    Returns a Vulnerability if validated as a disruption, else None.

    Parameters:
    - seed: The seed dictionary containing at least a "url" key.
    - seen: A set of URLs that have already been processed, used to skip duplicates.
    - use_bert: Whether to run a BERT pre-filter before LLM validation.
    - reporter: Optional CliReporter for logging progress and details.
    - stats: Optional PipelineStats for tracking statistics.

    Returns:
    - A Vulnerability object if the seed is validated as a disruption, or None if it is skipped or rejected.
    """
    reporter = reporter or CliReporter()
    url = seed["url"]
    LOGGER.debug("Processing seed url=%s", url)

    if url in seen:
        if stats is not None:
            stats.skipped += 1
        reporter.detail(f"  -> [skip] already seen by LLM {url[:90]}")
        LOGGER.debug("Skipping seen url=%s", url)
        return None

    reporter.detail(f"  -> fetching {url[:90]}")
    body = get_body(url)
    if not body:
        if stats is not None:
            stats.skipped += 1
        reporter.detail("     [skip] empty body")
        LOGGER.debug("Empty body for url=%s", url)
        return None

    title = get_title(url)
    excerpt = body[:BODY_CHAR_LIMIT]

    is_disruption, detail = ai_check_validation(
        title, excerpt, use_bert=use_bert, verbose=reporter.verbose
    )
    LOGGER.debug(
        "LLM validation url=%s disruption=%s detail=%s", url, is_disruption, detail
    )

    seen.add(url)

    if not is_disruption:
        if stats is not None:
            stats.rejected += 1
        reporter.detail(f"     [skip] not a disruption: {detail}")
        LOGGER.info("Not a disruption url=%s detail=%s", url, detail)
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
        if stats is not None:
            stats.skipped += 1
        reporter.warn(f"Invalid subsector '{subsector}' for {url[:90]}", stats)
        LOGGER.debug("Invalid subsector url=%s subsector=%s", url, subsector)
        return None

    if stats is not None:
        stats.validated += 1
    reporter.detail(f"     OK  disruption confirmed: {subsector}")
    LOGGER.info("Disruption confirmed url=%s subsector=%s", url, subsector)

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
    use_bert: bool = False,
    verbose: bool = False,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
) -> list[dict]:
    """
    Main function to run the GDELT pipeline end-to-end.

     Parameters:
        num_files: Number of GDELT GKG files to scan for seeds.
        limit: Optional cap on the number of seeds to process, useful for testing.
        subsectors: Comma-separated list of subsectors to include, or "all".
        output_path: Optional path to output JSON file or directory.
        start_date: Optional earliest date for GDELT files to include (YYYYMMDD or ISO format).
        end_date: Optional latest date for GDELT files to include (YYYYMMDD or ISO format).
        seen_urls_file: Optional path to JSON file for storing/loading seen URLs.
        use_bert: Whether to run a BERT pre-filter before LLM validation.
        verbose: Whether to show detailed per-article output.
        reporter: Optional CliReporter for logging progress and details.
        stats: Optional PipelineStats for tracking statistics.

     Returns:
        A list of validated and enriched vulnerability records as dictionaries.
    """
    LOGGER.debug(
        "Run started num_files=%s limit=%s subsectors=%s start_date=%s end_date=%s output_path=%s",
        num_files,
        limit,
        subsectors,
        start_date,
        end_date,
        output_path,
    )
    local_reporter = reporter is None
    reporter = reporter or CliReporter(verbose=verbose)
    stats = stats or PipelineStats("GDELT")
    if local_reporter:
        reporter.phase("GDELT pipeline")
    reporter.status(f"LLM model: {AI_MODEL}")
    if use_bert:
        reporter.status(_bert_status())

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
        reporter.error(f"Invalid subsector(s): {', '.join(invalid)}", stats)
        reporter.info(
            "Valid subsectors are: cyber_attack, drug_shortage, "
            "medical_device_shortage, natural_disaster, or all"
        )
        LOGGER.warning("Invalid subsectors requested: %s", invalid)
        if local_reporter:
            reporter.summary(stats)
        return []

    try:
        ensure_model_available()
    except model_unavailable_error as exc:
        LOGGER.error("Model availability check failed: %s", exc)
        print(exc, file=sys.stderr)
        sys.exit(1)

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
            reporter=reporter,
            stats=stats,
        )
    ]
    raw_seeds = dedupe_raw_seeds(raw_seeds)
    LOGGER.debug("Collected %s raw seeds", len(raw_seeds))
    stats.discovered = len(raw_seeds)
    persist_raw_seeds(raw_seeds)

    # Date-bounded runs should always process the full matched seed set.
    if start_date or end_date:
        limit = None

    seeds = raw_seeds
    if limit:
        seeds = seeds[:limit]
    LOGGER.debug("Processing %s seeds after limit", len(seeds))

    reporter.info(f"Processing {len(seeds)} GDELT seeds")
    records = []
    for i, seed in enumerate(seeds, start=1):
        stats.processed += 1
        if reporter.verbose:
            reporter.detail(f"[{i}/{len(seeds)}]")
        LOGGER.debug("Processing seed %s/%s url=%s", i, len(seeds), seed["url"])
        url = seed["url"]
        article_id = stable_id(url)
        rec = process_seed(
            seed,
            seen,
            use_bert=use_bert,
            reporter=reporter,
            stats=stats,
        )
        if rec:
            persist_stage(VALIDATED_DIR, article_id, "validated", url, rec.to_dict())
            persist_stage(ENRICHED_DIR, article_id, "enriched", url, rec.to_dict())
            records.append(rec)
            if SUPABASE_AVAILABLE:
                try:
                    handle_vuln(rec, reporter=reporter, stats=stats)
                except Exception as e:
                    LOGGER.warning("dedup/insert failed for %r: %s", rec.title, e)
        else:
            LOGGER.debug("Seed skipped url=%s", url)
        if not reporter.verbose:
            reporter.progress(i, len(seeds), "GDELT articles")

    # Save seen URLs once at the end
    save_seen(seen, seen_urls_path)

    LOGGER.debug(
        "Summary seeds_in=%s validated=%s skipped=%s",
        len(seeds),
        len(records),
        len(seeds) - len(records),
    )

    for rec in records:
        reporter.detail(f"\n--- {rec.id} ({rec.subsector}) ---")
        reporter.detail(f"URL: {rec.direct_link}")
        reporter.detail(f"Source: {rec.source_name}")
        reporter.detail(f"Fields: {rec.subsector_data}")

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
    # Convert records to dicts and format date_published before saving, to ensure consistent output formatting regardless of how dates are represented in the Vulnerability objects.
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
            "Failed to read existing output file %s", out_file, exc_info=True
        )
        combined = out_recs

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"sources": combined}, f, ensure_ascii=False, indent=2)
    LOGGER.info("Wrote %s records to %s", len(combined), out_file)
    stats.output_records = len(out_recs)
    reporter.info(f"Wrote {len(out_recs)} GDELT records to {out_file}")

    # Clear the seed files after a successful pipeline run
    clear_directory(SEEDS_DIR)
    reporter.detail(f"Cleared seed staging directory: {SEEDS_DIR}")
    LOGGER.debug("Cleared seeds directory: %s", SEEDS_DIR)

    if local_reporter:
        reporter.summary(stats)

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
    parser.add_argument(
        "--use-bert",
        action="store_true",
        default=False,
        help="Run BERT pre-filter before LLM validation to skip unrelated articles early",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Show detailed per-article pipeline output",
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
        use_bert=args.use_bert,
        verbose=args.verbose,
    )
