"""
GDELT end-to-end runner.

Pipeline:
  gdelt_seeds.backfill_cyber_seeds     -- collect candidate seeds from GDELT GKG
  src.shared_utils.get_body_and_title  -- scrape page body + title in one request
  src.shared_utils.ai_check_validation -- LLM validates as active disruption
  src.shared_utils.extract_fields      -- LLM extracts schema-specific fields

Constants:
    BODY_CHAR_LIMIT: Number of characters to include from the article body when passing to the LLM for validation and extraction.

Functions:
    stable_id(url): Generate a stable ID for a given URL using SHA-256 hashing.
    fmt_dt(value): Format a date string into YYYY-MM-DD HH:MM format. Tries multiple input formats and returns the original value if parsing fails.
    ensure_raw_dirs(): Ensure that the raw directories for seeds, validated, and enriched data exist. Creates them if they don't.
    ensure_cache_dir(): Ensure that the GDELT zip cache directory exists. Creates it if it doesn't.
    save_json(path, data): Save a dictionary as JSON to the specified path, creating parent directories if needed.
    persist_raw_seeds(raw_seeds): Persist raw seeds to the seeds directory, using stable IDs for filenames.
    persist_stage(directory, article_id, stage, url, data): Persist data for a specific stage (validated, enriched) using a stable ID for the filename.
    load_staged_payloads(stage, reporter, stats): Load staged payloads for the requested GDELT stitch stage.
    stitch_staged_records(output_path, stage, seen_urls_file, use_bert, reporter, stats, verbose): Recover final output from a staged GDELT pipeline stage.
    load_seen(seen_file): Load seen URLs from file. Returns a set of URLs that have been seen and processed. If the file does not exist or cannot be read, returns an empty set.
    save_seen(seen, seen_file): Save seen URLs to file.
    process_seed(seed, seen, use_bert, reporter, stats): Run a single seed through validation + extraction. Returns a Vulnerability if validated as a disruption, else None.
    run(num_files, limit, subsectors, output_path, start_date, end_date, seen_urls_file, use_bert, verbose, reporter, stats): Main function to run the GDELT pipeline end-to-end.

"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from .gdelt_seeds import backfill_cyber_seeds
from ..classes import SUBSECTOR_DATA_CLASSES, Vulnerability
from ..cli_reporter import CliReporter, PipelineStats
from ..logging_utils import get_file_logger
from ..shared_utils import (
    AI_MODEL,
    ai_check_validation,
    BODY_CHAR_LIMIT,
    DEBUG_DIR,
    NoiseCollector,
    ensure_model_available,
    extract_fields,
    get_body_and_title,
    get_config_bool,
    get_config_int,
    get_config_value,
    model_unavailable_error,
    clear_directory,
    MissingSubsectorFieldsError,
)
from scripts.clean_gdelt import run_clean

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# intermediate stages directory constants + helper functions
RAW_GDELT_DIR = PROJECT_ROOT / "data" / "raw" / "gdelt"
SEEDS_DIR = RAW_GDELT_DIR / "seeds"
VALIDATED_DIR = RAW_GDELT_DIR / "validated"
ENRICHED_DIR = RAW_GDELT_DIR / "enriched"
STITCH_STAGES = {"seeds", "validated", "enriched"}

LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "gdelt_runner.log"
LOGGER = get_file_logger(__name__, LOG_FILE)

# cache for downloaded GDELT GKG zip files to avoid redownloading
GDELT_CACHE_DIR = PROJECT_ROOT / "data" / "gdelt_cache"

try:
    from ..supabase_function import has_supabase_creds
    from ..dedup import handle_vuln

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


def _resolve_config_path(raw_value: str | None, fallback: Path) -> Path:
    if not raw_value:
        return fallback
    path_value = Path(raw_value)
    if path_value.is_absolute():
        return path_value
    return PROJECT_ROOT / path_value


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


def ensure_cache_dir() -> None:
    """
    Ensure that the GDELT zip cache directory exists. Creates it if it doesn't.

    The cache directory `GDELT_CACHE_DIR` is never cleared by the pipeline so
    that zip files downloaded in one run are reused in subsequent runs covering
    the same date range. Only remove files from this directory manually when you
    want to force a fresh download.
    """
    GDELT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    LOGGER.debug("Ensured GDELT cache directory: %s", GDELT_CACHE_DIR)


def save_json(path: Path, data: dict) -> None:
    """
    Save a dictionary as JSON to the specified path, creating parent directories if needed.

    Parameters:
        path: The path to the JSON file to save.
        data: The dictionary to save as JSON.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def dedupe_raw_seeds(raw_seeds: list[dict]) -> list[dict]:
    """
    Deduplicate raw GDELT seeds by exact URL while preserving subsector evidence.

    The first seed for a URL stays canonical so existing single-subsector seed
    metadata remains backward compatible. Later duplicates only contribute
    unique subsector labels to the canonical seed's detected_subsectors list.

    Parameters:
        raw_seeds: The raw seed dictionaries to deduplicate by URL.

    Returns:
        A list of unique seed dictionaries with all distinct subsector labels
        preserved in detected_subsectors.
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


def _record_identities(record: dict) -> list[tuple[str, str]]:
    """
    Return stable identities used to deduplicate a final output record.

    Parameters:
        record: The output record containing an optional ID and direct link.

    Returns:
        A list of identity type and value pairs for the record.
    """
    identities = []
    record_id = record.get("id")
    if record_id:
        identities.append(("id", str(record_id)))
    direct_link = record.get("direct_link")
    if direct_link:
        identities.append(("direct_link", str(direct_link)))
    return identities


def _dedupe_output_records(records: list[dict]) -> list[dict]:
    """
    Deduplicate final output records by ID or direct link while preserving
    their original order.

    Parameters:
        records: The output records to deduplicate.

    Returns:
        A list containing only the first record for each ID or direct link.
    """
    seen = set()
    unique = []
    for record in records:
        identities = _record_identities(record)
        if any(identity in seen for identity in identities):
            continue
        seen.update(identities)
        unique.append(record)
    return unique


def write_output_records(
    records: list[Vulnerability | dict],
    output_path: str | None,
    reporter: CliReporter,
    stats: PipelineStats,
) -> Path:
    """
    Write completed GDELT records to the configured processed JSON output.

    This helper centralizes the final output write so normal completion and
    graceful interrupt handling use the same merge behavior. Records are
    converted to dictionaries, publication dates are normalized through
    ``fmt_dt``, and existing output files are merged when they already contain a
    ``sources`` list or legacy list-shaped output.

    Parameters:
        records: Completed vulnerability records ready for processed output.
        output_path: Optional JSON file or directory path. Directory paths write
            ``GDELT.json`` inside the directory; ``None`` writes to the default
            processed GDELT output.
        reporter: Reporter used to print the write summary.
        stats: Pipeline statistics updated with the number of newly written
            output records.

    Returns:
        The resolved output file path that was written.
    """
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
    for record in records:
        data = record.to_dict() if isinstance(record, Vulnerability) else dict(record)
        data["date_published"] = fmt_dt(data.get("date_published", ""))
        out_recs.append(data)

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

    combined = _dedupe_output_records(combined)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"sources": combined}, f, ensure_ascii=False, indent=2)
    LOGGER.info("Wrote %s records to %s", len(combined), out_file)
    stats.output_records = len(out_recs)
    reporter.info(f"Wrote {len(out_recs)} GDELT records to {out_file}")
    return out_file


def process_staged_seeds(
    seeds: list[dict],
    seen: set,
    use_bert: bool = False,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    debug_noise: NoiseCollector | None = None,
) -> list[Vulnerability]:
    """
    Process staged GDELT seeds through validation and extraction while
    preserving progress if processing is interrupted.

    Parameters:
        seeds: The staged seed dictionaries to process.
        seen: A set of URLs that have already been processed.
        use_bert: Whether to run a BERT pre-filter before LLM validation.
        reporter: Optional CliReporter for logging progress and details.
        stats: Optional PipelineStats for tracking processing statistics.
        debug_noise: Optional NoiseCollector for recording rejected articles.

    Returns:
        A list of vulnerabilities completed from the staged seeds.
    """
    reporter = reporter or CliReporter()
    stats = stats or PipelineStats("GDELT seed stitch")
    records = []
    stats.discovered = len(seeds)
    reporter.info(f"Processing {len(seeds)} staged GDELT seeds")

    for i, seed in enumerate(seeds, start=1):
        stats.processed += 1
        url = seed["url"]
        was_seen = url in seen
        completed_current = False
        try:
            if reporter.verbose:
                reporter.detail(f"[{i}/{len(seeds)}]")
            article_id = stable_id(url)
            rec = process_seed(
                seed,
                seen,
                use_bert=use_bert,
                reporter=reporter,
                stats=stats,
                debug_noise=debug_noise,
            )
            if rec:
                persist_stage(
                    VALIDATED_DIR, article_id, "validated", url, rec.to_dict()
                )
                persist_stage(ENRICHED_DIR, article_id, "enriched", url, rec.to_dict())
                records.append(rec)
                completed_current = True
            if not reporter.verbose:
                reporter.progress(i, len(seeds), "staged GDELT seeds")
        except KeyboardInterrupt:
            if not was_seen and not completed_current:
                seen.discard(url)
            stats.paused = True
            reporter.finish_line()
            reporter.info(
                "GDELT seed stitch paused by operator; saving completed records "
                "and preserving staged seeds."
            )
            LOGGER.info(
                "GDELT seed stitch paused by operator at seed %s/%s", i, len(seeds)
            )
            break

    return records


def load_staged_payloads(
    stage: str = "enriched",
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    directory: Path | None = None,
) -> list[dict]:
    """
    Load valid staged GDELT payloads for the requested recovery stage,
    skipping files that are malformed or missing the expected payload.

    Parameters:
        stage: The recovery stage to load: seeds, validated, or enriched.
        reporter: Optional CliReporter for reporting malformed staged files.
        stats: Optional PipelineStats updated when staged files are skipped.
        directory: Optional staging directory override for the requested stage.

    Returns:
        A list of payload dictionaries loaded from the staging directory.
    """
    if stage not in STITCH_STAGES:
        raise ValueError(
            "Invalid stitch stage. Choose one of: seeds, validated, enriched."
        )

    reporter = reporter or CliReporter()
    directory = (
        directory
        or {
            "seeds": SEEDS_DIR,
            "validated": VALIDATED_DIR,
            "enriched": ENRICHED_DIR,
        }[stage]
    )
    payload_key = "seed" if stage == "seeds" else "record"
    payload_label = "seed" if stage == "seeds" else "record"

    if not directory.exists():
        LOGGER.debug(
            "%s staging directory does not exist: %s", payload_label, directory
        )
        return []

    payloads = []
    for path in sorted(directory.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            staged_payload = (
                payload.get(payload_key) if isinstance(payload, dict) else None
            )
            if not isinstance(staged_payload, dict):
                raise ValueError(f"missing {payload_key} object")
            payloads.append(staged_payload)
        except Exception as exc:
            reporter.warn(f"Skipping staged {payload_label} {path.name}: {exc}", stats)
            LOGGER.warning("Skipping staged record %s: %s", path, exc)
    return payloads


def stitch_staged_records(
    output_path: str | None = None,
    stage: str = "enriched",
    seen: set | None = None,
    use_bert: bool = False,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    verbose: bool = False,
) -> list[dict]:
    """
    Recover records from a staged GDELT pipeline stage and write the
    deduplicated records to the final output file. Seed recovery resumes
    validation and extraction for seeds without completed staged records.

    Parameters:
        output_path: Optional JSON file or directory path for the final output.
        stage: The recovery stage to stitch: seeds, validated, or enriched.
        seen: Optional set of URLs that have already been processed.
        use_bert: Whether to run a BERT pre-filter during seed recovery.
        reporter: Optional CliReporter for logging progress and details.
        stats: Optional PipelineStats for tracking recovery statistics.
        verbose: Whether to show detailed per-article output.

    Returns:
        A deduplicated list of recovered records with formatted publication dates.
    """
    if stage not in STITCH_STAGES:
        raise ValueError(
            "Invalid stitch stage. Choose one of: seeds, validated, enriched."
        )

    local_reporter = reporter is None
    reporter = reporter or CliReporter(verbose=verbose)
    stats = stats or PipelineStats(f"GDELT {stage} stitch")
    if local_reporter:
        reporter.phase("GDELT staged recovery")
    reporter.status(f"Stitch stage: {stage}")

    if stage == "seeds":
        try:
            ensure_model_available()
        except model_unavailable_error as exc:
            LOGGER.error("Model availability check failed: %s", exc)
            print(exc, file=sys.stderr)
            sys.exit(1)
        if use_bert:
            reporter.status(_bert_status())
        ensure_raw_dirs()
        if seen is None:
            seen = load_seen()
        staged_records = _dedupe_output_records(
            load_staged_payloads("enriched", reporter=reporter, stats=stats)
            + load_staged_payloads("validated", reporter=reporter, stats=stats)
        )
        completed_urls = {
            str(record["direct_link"])
            for record in staged_records
            if record.get("direct_link")
        }
        seeds = load_staged_payloads("seeds", reporter=reporter, stats=stats)
        remaining_seeds = [
            seed for seed in seeds if str(seed.get("url", "")) not in completed_urls
        ]
        records = process_staged_seeds(
            remaining_seeds,
            seen=seen,
            use_bert=use_bert,
            reporter=reporter,
            stats=stats,
        )
        records = staged_records + records
    else:
        records = load_staged_payloads(stage, reporter=reporter, stats=stats)

    write_output_records(records, output_path, reporter, stats)

    if local_reporter:
        reporter.summary(stats)
    formatted_records = []
    for record in records:
        data = record.to_dict() if isinstance(record, Vulnerability) else dict(record)
        data["date_published"] = fmt_dt(data.get("date_published", ""))
        formatted_records.append(data)
    return _dedupe_output_records(formatted_records)


def process_seed(
    seed: dict,
    seen: set,
    use_bert: bool = False,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    debug_noise: NoiseCollector | None = None,
    port=None,
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
    - debug_noise: Optional NoiseCollector for recording rejected articles.

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
        if debug_noise:
            debug_noise.add(
                url=url,
                title=seed.get("title", ""),
                source="GDELT",
                reason="Already seen by LLM",
                stage="dedup",
            )
        return None

    reporter.detail(f"  -> fetching {url[:90]}")
    body, title = get_body_and_title(url)
    if not body:
        if stats is not None:
            stats.skipped += 1
        reporter.detail("     [skip] empty body")
        LOGGER.debug("Empty body for url=%s", url)
        if debug_noise:
            debug_noise.add(
                url=url,
                title=title,
                source="GDELT",
                reason="Empty body",
                stage="fetch",
            )
        return None
    excerpt = body[:BODY_CHAR_LIMIT]

    is_disruption, detail = ai_check_validation(
        title, excerpt, use_bert=use_bert, verbose=reporter.verbose, port=port
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
        if debug_noise:
            debug_noise.add(
                url=url,
                title=title,
                source="GDELT",
                reason=f"Not a disruption: {detail}",
                body_preview=body[:250],
                stage="validation",
            )
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
        if debug_noise:
            debug_noise.add(
                url=url,
                title=title,
                source="GDELT",
                reason=f"Invalid subsector: {subsector}",
                body_preview=body[:250],
                stage="validation",
            )
        return None

    reporter.detail(f"     OK  disruption confirmed: {subsector}")
    LOGGER.info("Disruption confirmed url=%s subsector=%s", url, subsector)

    try:
        sector_data, subsector_data_dict = extract_fields(
            subsector, title, excerpt, port=port
        )
    except MissingSubsectorFieldsError as exc:
        seen.discard(url)
        if stats is not None:
            stats.skipped += 1
        reporter.warn(f"Skipping extraction for {url[:90]}: {exc}", stats)
        LOGGER.warning("Skipping extraction url=%s: %s", url, exc)
        if debug_noise:
            debug_noise.add(
                url=url,
                title=title,
                source="GDELT",
                reason=f"Missing subsector fields: {exc}",
                body_preview=body[:250],
                stage="extraction",
            )
        return None

    if stats is not None:
        stats.validated += 1

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
    output_path: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    seen: set | None = None,
    use_bert: bool = False,
    verbose: bool = False,
    reporter: CliReporter | None = None,
    stats: PipelineStats | None = None,
    raw_seeds: list[dict] | None = None,
    debug_noise: NoiseCollector | None = None,
    port: int | None = None,
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
        seen: Optional set of URLs that have already been processed.
        use_bert: Whether to run a BERT pre-filter before LLM validation.
        verbose: Whether to show detailed per-article output.
        reporter: Optional CliReporter for logging progress and details.
        stats: Optional PipelineStats for tracking statistics.
        clean: Whether to clear modified directories and files before running.
        raw_seeds: Raw seed dictionaries to process
        debug_noise: Optional NoiseCollector for recording rejected articles.
        port: Where to run the ollama server

     Returns:
        A list of validated and enriched vulnerability records as dictionaries.

     Interrupt behavior:
        Pressing ``Ctrl-C`` while a seed is being processed marks the run as
        paused, saves seen URLs, writes any completed records through
        ``write_output_records``, preserves seed staging, and returns the
        completed records collected before the interrupt.
    """
    LOGGER.debug(
        "Run started num_files=%s limit=%s start_date=%s end_date=%s output_path=%s",
        num_files,
        limit,
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

    try:
        ensure_model_available()
    except model_unavailable_error as exc:
        LOGGER.error("Model availability check failed: %s", exc)
        print(exc, file=sys.stderr)
        sys.exit(1)

    ensure_raw_dirs()
    ensure_cache_dir()

    if seen is None:
        seen = load_seen()

    if raw_seeds is None:
        raw_seeds = [
            seed
            for seed in backfill_cyber_seeds(
                num_files=num_files,
                start_date=start_date,
                end_date=end_date,
                cache_dir=GDELT_CACHE_DIR,
                reporter=reporter,
            )
        ]

    raw_seeds = dedupe_raw_seeds(raw_seeds or [])
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
        url = seed["url"]
        was_seen = url in seen
        completed_current = False
        try:
            if reporter.verbose:
                reporter.detail(f"[{i}/{len(seeds)}]")
            LOGGER.debug("Processing seed %s/%s url=%s", i, len(seeds), seed["url"])
            article_id = stable_id(url)
            rec = process_seed(
                seed,
                seen,
                use_bert=use_bert,
                reporter=reporter,
                stats=stats,
                debug_noise=debug_noise,
                port=port,
            )
            if rec:
                persist_stage(
                    VALIDATED_DIR, article_id, "validated", url, rec.to_dict()
                )
                persist_stage(ENRICHED_DIR, article_id, "enriched", url, rec.to_dict())
                records.append(rec)
                completed_current = True
                if SUPABASE_AVAILABLE:
                    try:
                        handle_vuln(rec, reporter=reporter, stats=stats)
                    except Exception as e:
                        LOGGER.warning("dedup/insert failed for %r: %s", rec.title, e)
            else:
                LOGGER.debug("Seed skipped url=%s", url)
            if not reporter.verbose:
                reporter.progress(i, len(seeds), "GDELT articles")
        except KeyboardInterrupt:
            if not was_seen and not completed_current:
                seen.discard(url)
            stats.paused = True
            reporter.finish_line()
            reporter.info(
                "GDELT pipeline paused by operator; saving completed records "
                "and preserving seed staging."
            )
            LOGGER.info(
                "GDELT pipeline paused by operator at seed %s/%s", i, len(seeds)
            )
            break

    # Save seen URLs once at the end
    save_seen(seen)

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

    write_output_records(records, output_path, reporter, stats)

    if stats.paused:
        reporter.detail(f"Preserved seed staging directory: {SEEDS_DIR}")
        LOGGER.debug("Preserved seeds directory after pause: %s", SEEDS_DIR)
    else:
        # Clear the seed files after a successful pipeline run.
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
        default=get_config_int("GDELT_NUM_FILES", 2),
        help="GDELT GKG files to scan (default: 2 ~= 30 min of data)",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=get_config_int("GDELT_LIMIT", None),
        help="Cap on seeds to process; useful for smoke-testing (default: 3 unless --num-files is explicitly provided)",
    )
    parser.add_argument(
        "--output-path",
        "-o",
        default=get_config_value("OUTPUT_PATH", None),
        help="Output JSON file or directory. If a directory is provided, GDELT.json is written inside it. (default: data/processed/GDELT.json)",
    )
    parser.add_argument(
        "--start-date",
        default=get_config_value("GDELT_START_DATE", None),
        help="Earliest GDELT file date to include (Format: YYYYMMDD, YYYYMMDDHHMMSS, YYYY-MM-DD, YYYY-MM-DD HH:MM:SS)",
    )
    parser.add_argument(
        "--end-date",
        default=get_config_value("GDELT_END_DATE", None),
        help="Latest GDELT file date to include (Format: YYYYMMDD, YYYYMMDDHHMMSS, YYYY-MM-DD, YYYY-MM-DD HH:MM:SS)",
    )
    parser.add_argument(
        "--seen-urls-file",
        default=get_config_value("SEEN_URLS_FILE", None),
        help="Path to store/load seen URLs JSON file (default: data/seen_urls.json)",
    )
    parser.add_argument(
        "--use-bert",
        action="store_true",
        default=get_config_bool("USE_BERT", False),
        help="Run BERT pre-filter before LLM validation to skip unrelated articles early",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=get_config_bool("VERBOSE", False),
        help="Show detailed per-article pipeline output",
    )
    parser.add_argument(
        "--stitch-staged",
        action="store_true",
        default=False,
        help=(
            "Recover final output from staged GDELT data using the default "
            "enriched stage. Deprecated; prefer --stitch-stage enriched."
        ),
    )
    parser.add_argument(
        "--stitch-stage",
        choices=["seeds", "validated", "enriched"],
        default=None,
        help=(
            "Recover final output from this staged GDELT stage: seeds, "
            "validated, or enriched."
        ),
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        default=get_config_bool("CLEAN", False),
        help="Clear all modified directories and files before running",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        default=get_config_bool("DEBUG", False),
        help="Log all rejected/skipped articles (noise) to JSON in data/noise/",
    )
    args = parser.parse_args()

    if args.stitch_staged or args.stitch_stage:
        output_path_provided = any(
            arg in ("-o", "--output-path") or arg.startswith("--output-path=")
            for arg in sys.argv[1:]
        )
        seen = load_seen()
        stitch_staged_records(
            output_path=args.output_path if output_path_provided else None,
            stage=args.stitch_stage or "enriched",
            seen=seen,
            use_bert=args.use_bert,
            verbose=args.verbose,
        )
        sys.exit(0)

    # If --num-files/-n is explicitly provided without --limit/-l, process all
    # discovered seeds for that fetch window instead of using the smoke-test cap.
    n_provided = (
        any(opt in sys.argv[1:] for opt in ("-n", "--num-files"))
        or args.num_files is not None
    )
    l_provided = args.limit is not None
    effective_limit = args.limit
    if not l_provided:
        effective_limit = None if n_provided else 3

    if args.clean:
        run_clean()

    seen = load_seen()
    noise = NoiseCollector(DEBUG_DIR / "debug_noise_gdelt.json") if args.debug else None
    run(
        num_files=args.num_files,
        limit=effective_limit,
        output_path=args.output_path,
        start_date=args.start_date,
        end_date=args.end_date,
        seen=seen,
        use_bert=args.use_bert,
        verbose=args.verbose,
        reporter=CliReporter(verbose=args.verbose),
        debug_noise=noise,
    )
    save_seen(seen)
    if noise:
        noise.flush()
