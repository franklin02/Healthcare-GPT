"""Unified runner for GDELT and configured HTML/Scooper scrapers.

The orchestrator owns cross-pipeline CLI concerns so operators can run small
smoke tests or larger backfills from one command while each pipeline keeps its
source-specific defaults and implementation details.
"""

from __future__ import annotations
from supabase import create_client, Client

import argparse
import datetime
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from pathlib import Path
import os

from .cli_reporter import CliReporter, PipelineStats
from .logging_utils import get_file_logger
from .GDELT.gdelt_seeds import backfill_seeds, fetch_gkg_links
from .GDELT.runner import load_seen, write_output_records
from .shared_utils import (
    DEBUG_DIR,
    NoiseCollector,
    collect_as_completed,
    ensure_model_available,
    exit_if_shutdown,
    get_config_bool,
    get_config_int,
    get_config_value,
    install_handler,
    model_unavailable_error,
    run_clean,
    shutdown_executor,
)
# from scripts.clean_gdelt import run_clean

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
GDELT_CACHE_DIR = _PROJECT_ROOT / "data" / "gdelt_cache"

LOG_FILE = _PROJECT_ROOT / "data" / "logs" / "orchestrator.log"
LOGGER = get_file_logger(__name__, LOG_FILE)
AI_MODEL = get_config_value("AI_MODEL", "llama3.2:latest")

supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def _split_date(
    start: datetime.date, end: datetime.date, k: int
) -> list[tuple[datetime.date, datetime.date]]:
    """
    Splits the date into K parts, where K is the number of threads
    NOTE: only used when both dates are passed into scooper

    Args:
        start:
        end:
        k: Number of threads

    Returns:
        Start-End window / Number of threads
    """
    if end > start:
        LOGGER.debug("date range given oldest-first; normalizing %s..%s", start, end)
        start, end = end, start

    num_days = (start - end).days + 1  # inclusive day count

    k = max(1, min(k, num_days))
    base, extra = divmod(num_days, k)

    windows: list[tuple[datetime.date, datetime.date]] = []
    cursor = end
    for i in range(k):
        size = base + (1 if i < extra else 0)
        window_end = cursor
        window_start = cursor + datetime.timedelta(days=size - 1)
        windows.append((window_start, window_end))
        cursor = window_start + datetime.timedelta(days=1)

    windows.reverse()
    return windows


def _parse_date(s: str | None) -> datetime.date | None:
    """Parse a YYYY-MM-DD or YYYYMMDD string into a date, or return None."""
    if s is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    LOGGER.warning("Could not parse date %r; ignoring for HTML engine", s)
    return None


def chunk_list(items, num_chunks):
    """
    Split a list of items into a specified number of chunks, as evenly as possible.

    Args:
        items: The list of items to split.
        num_chunks: The number of chunks to create.

    Returns:
        A list of lists, where each sublist is a chunk of the original items.
    """
    if num_chunks is None or num_chunks <= 0:
        num_chunks = 1
    if not items:
        return []
    chunk_size = math.ceil(len(items) / num_chunks)
    if chunk_size <= 0:
        return []
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def _collect_gdelt_seeds(
    *,
    num_files: int,
    start_date: str | None,
    end_date: str | None,
    cache_dir: Path,
    reporter: CliReporter,
    stats: PipelineStats,
    seed_threads: int,
    sector: str | None = None,
) -> list[dict]:
    """Collect GDELT seeds, optionally splitting work across threads.

    Parameters:
        num_files: Number of GDELT GKG files to scan.
        start_date: Ceiling date (YYYYMMDD or YYYY-MM-DD) for articles; newer articles are skipped.
        end_date: Floor date (YYYYMMDD or YYYY-MM-DD) for articles; crawling stops at older articles.
        cache_dir: Directory to cache downloaded GKG files.
        reporter: CliReporter for logging progress and warnings.
        stats: PipelineStats for recording statistics.
        seed_threads: Number of threads to use for seed collection.
        sector: Sector key into SECTOR_THEMES (default: health).

    Returns:
        A list of candidate seed records (dicts) collected from GDELT.
    """
    if start_date is not None and end_date is not None:
        num_days = (_parse_date(start_date) - _parse_date(end_date)).days + 1
        reporter.info(
            f"Collecting GDELT seeds for date range {_parse_date(start_date)} - {_parse_date(end_date)} ({num_days} days)"
        )
    else:
        reporter.info(
            f"Collecting GDELT seeds for {num_files} files ({num_files / 4} hours)"
        )

    seed_threads = max(1, seed_threads)
    if seed_threads > 1:
        reporter.info(f"Collecting GDELT seeds with {seed_threads} threads")
    if seed_threads == 1:
        LOGGER.debug("Collecting GDELT seeds in single-threaded mode")
        return list(
            backfill_seeds(
                num_files=num_files,
                start_date=start_date,
                end_date=end_date,
                cache_dir=cache_dir,
                reporter=reporter,
                stats=stats,
                sector=sector,
            )
        )

    quiet_reporter = CliReporter(verbose=False)
    raw_seeds: list[dict] = []

    # Resolve the concrete GKG file list for either a date range or a recent
    # file count, then split that list across threads. Splitting by file (not
    # by calendar day) honors seed_threads even when the requested date range
    # spans fewer days than threads.
    links = fetch_gkg_links(
        num_files=num_files, start_date=start_date, end_date=end_date
    )
    chunks = chunk_list(links, seed_threads)
    if not chunks:
        return []

    executor = ThreadPoolExecutor(max_workers=len(chunks))
    try:
        futures = []

        # Submit tasks for each chunk of links
        for link_chunk in chunks:
            futures.append(
                executor.submit(
                    backfill_seeds,
                    links=link_chunk,
                    cache_dir=cache_dir,
                    reporter=quiet_reporter,
                    sector=sector,
                )
            )
        collect_as_completed(futures, raw_seeds.extend)
    finally:
        shutdown_executor(executor)

    LOGGER.debug(
        "Collected %d GDELT seeds across %d link chunks with %d threads",
        len(raw_seeds),
        len(chunks),
        seed_threads,
    )
    return raw_seeds


def main(argv: list[str] | None = None) -> int:
    """Parse CLI options, run selected pipeline stages, and report summaries.

    Args:
        argv: Optional argument list for tests and programmatic callers. When
            omitted, argparse reads from the process command line.

    Returns:
        Process exit code. A successful orchestrated run returns ``0``.
    """
    parser = argparse.ArgumentParser(
        description="Unified runner for GDELT and HTML scrapers"
    )

    # Shared
    parser.add_argument(
        "--use-bert",
        "-b",
        action="store_true",
        default=get_config_bool("USE_BERT", False),
        help="Run BERT pre-filter before LLM field extraction in both pipelines",
    )
    parser.add_argument(
        "--skip-gdelt",
        action="store_true",
        default=get_config_bool("SKIP_GDELT", False),
        help="Skip the GDELT pipeline",
    )
    parser.add_argument(
        "--skip-html",
        action="store_true",
        default=get_config_bool("SKIP_HTML", False),
        help="Skip the HTML scraper pipeline",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=get_config_bool("VERBOSE", False),
        help="Show detailed per-article pipeline output",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        default=get_config_bool("DEBUG", False),
        help="Log all rejected/skipped articles (noise) to JSON files in data/noise/",
    )
    parser.add_argument(
        "--start-date",
        default=get_config_value("START_DATE", None),
        help=(
            "Ceiling date (YYYYMMDD or YYYY-MM-DD): articles newer than this are "
            "skipped. Applied to both GDELT files and HTML article dates."
        ),
    )
    parser.add_argument(
        "--end-date",
        default=get_config_value("END_DATE", None),
        help=(
            "Floor date (YYYYMMDD or YYYY-MM-DD): crawling stops at articles older "
            "than this. Applied to both GDELT files and HTML article dates."
        ),
    )
    parser.add_argument(
        "--sb-only",
        action="store_true",
        default=get_config_bool("HTML_SB_ONLY", False),
        help="HTML pipeline: write to Supabase only, no local reads or writes",
    )

    # GDELT-specific
    parser.add_argument(
        "--num-files",
        "-n",
        type=int,
        default=get_config_int("GDELT_NUM_FILES", 2),
        help="GDELT GKG files to scan (default: 2)",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=get_config_int("GDELT_LIMIT", None),
        help="Cap on seeds to process; defaults to 3 unless --num-files is provided",
    )
    parser.add_argument(
        "--output-path",
        "-o",
        default=get_config_value("OUTPUT_PATH", "data/output/results.json"),
        help="Output JSON file or directory for GDELT results",
    )
    parser.add_argument(
        "--seen-urls-file",
        default=get_config_value("SEEN_URLS_FILE", None),
        help="Path to store/load seen URLs JSON file",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        default=get_config_bool("CLEAN", False),
        help="Clear all modified directories and files before running",
    )
    parser.add_argument(
        "--gdelt-seed-threads",
        type=int,
        default=get_config_int("GDELT_SEED_THREADS", 1),
        help="Number of threads to use for GDELT seed collection (default: 1)",
    )
    parser.add_argument(
        "--models",
        type=int,
        default=get_config_int("MODELS", 1),
        help=("Number of model instances to run concurrently."),
    )
    parser.add_argument(
        "--threads-per-model",
        type=int,
        default=get_config_int("THREADS_PER_MODEL", 1),
        help=("Number of threads to use per model instance."),
    )
    parser.add_argument(
        "--starting-port",
        type=int,
        default=get_config_int("STARTING_PORT", 11434),
        help=(
            "Starting port number for LLM instances. Each instance is expected to "
            "run on a consecutive port (e.g. 11434, 11435, etc.)"
        ),
    )
    parser.add_argument(
        "--seeds_only",
        action="store_true",
        default=get_config_bool("SEEDS_ONLY", False),
        help="Process only seed articles, skipping full scraping and processing. Also skips the scooper pipeline.",
    )
    parser.add_argument(
        "--sector",
        default=get_config_value("SECTOR", "health"),
        help="The sector to process (default: health)",
    )
    start = time.time()

    args = parser.parse_args(argv)
    install_handler()
    reporter = CliReporter(verbose=args.verbose)
    summaries: list[PipelineStats] = []

    # Overall progress bar: one unit of work per pipeline phase that will run.
    # When only one phase runs, the overall bar would just mirror the phase bar,
    # so we suppress it entirely.
    phases = int(not args.skip_gdelt) + int(not args.skip_html)
    show_overall = phases > 1
    if show_overall:
        reporter.set_overall_total(phases)
        reporter.set_overall_step("Initializing")

    threads = max(1, args.models) * max(1, args.threads_per_model)

    if not args.skip_gdelt:
        import src.GDELT.runner as runner

        gdelt_start = time.time()
        n_provided = (
            any(opt in sys.argv[1:] for opt in ("-n", "--num-files"))
            or args.num_files is not None
        )
        l_provided = args.limit is not None
        effective_limit = args.limit
        if not l_provided:
            effective_limit = None if n_provided else 3

        gdelt_noise = (
            NoiseCollector(DEBUG_DIR / "debug_noise_gdelt.json") if args.debug else None
        )
        gdelt_stats = PipelineStats("GDELT")
        reporter.phase(f"Running GDELT pipeline for sector: {args.sector}")
        if show_overall:
            reporter.set_overall_step("GDELT")
        LOGGER.info("Running GDELT pipeline with args: %s", args)
        if args.clean:
            run_clean()

        raw_seeds = _collect_gdelt_seeds(
            num_files=args.num_files,
            start_date=args.start_date,
            end_date=args.end_date,
            cache_dir=GDELT_CACHE_DIR,
            reporter=reporter,
            stats=gdelt_stats,
            seed_threads=args.gdelt_seed_threads,
            sector=args.sector,
        )
        LOGGER.info(
            f"Seed collection complete in {(time.time() - gdelt_start) / 60:.2f} minutes"
        )
        gdelt_processing_start = time.time()
        if args.seeds_only:
            LOGGER.info("Seeds-only mode enabled; skipping full GDELT processing")
            exit(0)

        seen = load_seen(args.seen_urls_file)
        chunks = chunk_list(raw_seeds, threads)
        port = args.starting_port
        if not chunks:
            chunks = [[]]

        try:
            ensure_model_available()
            reporter.info(f"LLM model: {AI_MODEL}")
        except model_unavailable_error as exc:
            LOGGER.error("Model availability check failed: %s", exc)
            print(exc, file=sys.stderr)
            return 1

        # Size the phase bar to the seeds that will actually process (each worker
        # applies effective_limit to its own chunk). Per-seed advances from the
        # workers then drive both the phase bar and, smoothly, the overall bar.
        gdelt_units = sum(
            min(len(c), effective_limit) if effective_limit else len(c) for c in chunks
        )
        reporter.start_phase("GDELT", total=gdelt_units)
        reporter.info(f"\nProcessing {effective_limit} seeds")
        if threads > 1:
            reporter.info(f"Models: {args.models}")
            reporter.info(f"Threads per model: {args.threads_per_model}")
            reporter.info(f"Total threads: {threads}")
        all_records = []
        executor = ThreadPoolExecutor(max_workers=threads)
        try:
            futures = []
            n = 0
            for chunk in chunks:
                futures.append(
                    executor.submit(
                        runner.run,
                        num_files=args.num_files,
                        limit=effective_limit,
                        output_path=None,
                        start_date=args.start_date,
                        end_date=args.end_date,
                        seen=seen,
                        use_bert=args.use_bert,
                        verbose=args.verbose,
                        reporter=reporter,
                        stats=PipelineStats("GDELT"),
                        raw_seeds=chunk,
                        debug_noise=gdelt_noise,
                        port=port,
                    )
                )
                n += 1
                if n == args.threads_per_model:
                    port += 1
                    n = 0

            def _merge_gdelt(result):
                worker_stats, records = result
                gdelt_stats.merge(worker_stats)
                all_records.extend(records)

            collect_as_completed(futures, _merge_gdelt)

            if all_records:
                supabase.table("vulnerability").insert(
                    [r.to_dict() for r in all_records]
                ).execute()
        finally:
            shutdown_executor(executor)
        write_output_records(all_records, args.output_path, reporter, gdelt_stats)
        if gdelt_noise:
            out = gdelt_noise.flush()
            if out:
                reporter.info(f"Debug noise (GDELT): {out}")

        LOGGER.info(
            f"GDELT processing complete in {(time.time() - gdelt_processing_start) / 60:.2f} minutes"
        )
        # Whole-phase wall clock for the run summary. gdelt_start is set
        # at the top of the GDELT branch; gdelt_processing_start above only covers
        # the LLM-processing sub-phase.
        gdelt_stats.elapsed_seconds = time.time() - gdelt_start
        summaries.append(gdelt_stats)
        if gdelt_stats.paused:
            reporter.info("GDELT pipeline paused; skipping remaining pipelines.")
            reporter.summary(summaries)
            LOGGER.info("GDELT pipeline paused; skipping remaining pipelines")
            return exit_if_shutdown(0)

    if not args.skip_html:
        import src.scrapers.scooper as scooper

        html_start = time.time()
        html_stats = PipelineStats("HTML")
        reporter.phase("Running HTML/Scooper pipeline")
        if show_overall:
            reporter.set_overall_step("HTML")
        LOGGER.info("Running HTML/Scooper pipeline with args %s", args)

        scooper.setup_scooper(sb_only=args.sb_only)
        vuln_dfs: list[pd.DataFrame] = []
        noise_dfs: list[pd.DataFrame] = []

        # K split — one scooper instance per date window, run in parallel.
        start_date = _parse_date(args.start_date)
        end_date = _parse_date(args.end_date)
        if start_date is not None and end_date is not None:
            dates: list[tuple[datetime.date, datetime.date]] = _split_date(
                end_date, start_date, threads
            )
            # One phase unit per date window; advanced as each window completes.
            reporter.start_phase("HTML", total=len(dates))

            # One scooper instance per date window
            port = args.starting_port
            results = []

            try:
                ensure_model_available()
                reporter.info(f"LLM model: {AI_MODEL}")
            except model_unavailable_error as exc:
                LOGGER.error("Model availability check failed: %s", exc)
                print(exc, file=sys.stderr)
                return 1

            executor = ThreadPoolExecutor(max_workers=len(dates))
            try:
                futures = []
                n = 0
                for win_start, win_end in dates:
                    futures.append(
                        executor.submit(
                            scooper.run_scooper,
                            use_bert=args.use_bert,
                            verbose=args.verbose,
                            start_date=win_start,
                            end_date=win_end,
                            reporter=reporter,
                            stats=PipelineStats("HTML"),  # per-window; merged below
                            sb_only=args.sb_only,
                            port=port,
                        )
                    )
                    n += 1
                    if n == args.threads_per_model:
                        port += 1
                        n = 0

                def _append_html(result):
                    results.append(result)
                    reporter.advance(1)

                collect_as_completed(futures, _append_html)
            finally:
                shutdown_executor(executor)

            vuln_lists: list = []
            for window_stats, w_vuln_list, v_df, n_df in results:
                html_stats.merge(window_stats)
                vuln_dfs.append(v_df)
                noise_dfs.append(n_df)
                vuln_lists.extend(w_vuln_list)

            scooper.save_results(vuln_lists, vuln_dfs, noise_dfs, sb_only=args.sb_only)
        # Default: one thread per site. run_scooper fans out internally and
        # returns frames merged across sites (disjoint); we persist them here.
        # Per-site HTML progress lives inside scooper (deferred to #229); treat
        # the whole phase as one unit for now.
        else:
            reporter.start_phase("HTML", total=1)
            html_stats, vuln_list, v_df, n_df = scooper.run_scooper(
                use_bert=args.use_bert,
                verbose=args.verbose,
                start_date=_parse_date(args.end_date),  # swapped here
                end_date=_parse_date(args.start_date),  # swapped here
                reporter=reporter,
                stats=html_stats,
                sb_only=args.sb_only,
                site_split=True,
            )
            scooper.save_results(vuln_list, [v_df], [n_df], sb_only=args.sb_only)
            reporter.advance(1)

        # TODO: thread per cite implemented here

        html_stats.elapsed_seconds = time.time() - html_start
        summaries.append(html_stats)
        if html_stats.paused:
            reporter.info("HTML scraper paused; skipping remaining pipelines.")
            reporter.summary(summaries)
            LOGGER.info("HTML scraper paused; skipping remaining pipelines")
            return exit_if_shutdown(0)

        LOGGER.info(
            f"HTML/Scooper processing complete in {(time.time() - html_start) / 60:.2f} minutes"
        )

    if summaries:
        reporter.summary(summaries)
    LOGGER.info("Orchestrator run complete with summaries: %s", summaries)
    LOGGER.info(f"Total execution time: {(time.time() - start) / 60:.2f} minutes")
    return exit_if_shutdown(0)


if __name__ == "__main__":
    raise SystemExit(main())
