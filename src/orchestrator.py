"""Unified runner for GDELT and configured HTML/Scooper scrapers.

The orchestrator owns cross-pipeline CLI concerns so operators can run small
smoke tests or larger backfills from one command while each pipeline keeps its
source-specific defaults and implementation details.
"""

from __future__ import annotations

import argparse
import datetime
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from pathlib import Path
from concurrent.futures import as_completed

from .cli_reporter import CliReporter, PipelineStats
from .logging_utils import get_file_logger
from .GDELT.gdelt_seeds import backfill_cyber_seeds
from .GDELT.runner import load_seen
from .shared_utils import (
    DEBUG_DIR,
    NoiseCollector,
    ensure_model_available,
    get_config_bool,
    get_config_int,
    get_config_value,
    model_unavailable_error,
    run_clean,
)
# from scripts.clean_gdelt import run_clean

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
GDELT_CACHE_DIR = _PROJECT_ROOT / "data" / "gdelt_cache"

LOG_FILE = _PROJECT_ROOT / "data" / "logs" / "orchestrator.log"
LOGGER = get_file_logger(__name__, LOG_FILE)


def _split_date(
    start: datetime.date, end: datetime.date
) -> list[tuple[datetime.date, datetime.date]]:
    """
    Dummy function to split date into K parts. This needs to be rewritten later.
    Team hasnt discussed the best/desired way to split dates. Not documenting on purpose
    """
    if end > start:
        raise ValueError("dates are backwards")

    half = (end - start) // 2
    mid = start + half

    return [(start, mid), (mid - datetime.timedelta(days=1), end)]


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


def _option_provided(raw_args: list[str], options: tuple[str, ...]) -> bool:
    """Return whether any CLI option was supplied, including --option=value."""
    LOGGER.debug(
        "Checking if any of options %s were provided in args: %s", options, raw_args
    )
    return any(
        arg == option or arg.startswith(f"{option}=")
        for arg in raw_args
        for option in options
    )


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
        default=get_config_value("GDELT_START_DATE", None),
        help=(
            "Ceiling date (YYYYMMDD or YYYY-MM-DD): articles newer than this are "
            "skipped. Applied to both GDELT files and HTML article dates."
        ),
    )
    parser.add_argument(
        "--end-date",
        default=get_config_value("GDELT_END_DATE", None),
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
        "--html-start-page",
        type=int,
        default=get_config_int("HTML_START_PAGE", None),
        help="Override configured starting page for every HTML scraper site",
    )
    parser.add_argument(
        "--html-page-cap",
        type=int,
        default=get_config_int("HTML_PAGE_CAP", None),
        help=(
            "Override configured max page number for every HTML scraper site "
            "(-1 for unlimited)"
        ),
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        default=get_config_bool("CLEAN", False),
        help="Clear all modified directories and files before running",
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
    start = time.time()

    args = parser.parse_args(argv)
    reporter = CliReporter(verbose=args.verbose)
    summaries: list[PipelineStats] = []

    if not (args.skip_gdelt and args.skip_html):
        try:
            ensure_model_available()
        except model_unavailable_error as exc:
            LOGGER.error("Model availability check failed: %s", exc)
            print(exc, file=sys.stderr)
            return 1

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
        reporter.phase("Running GDELT pipeline")
        LOGGER.info("Running GDELT pipeline with args: %s", args)
        if args.clean:
            run_clean()
        raw_seeds = [
            seed
            for seed in backfill_cyber_seeds(
                num_files=args.num_files,
                start_date=args.start_date,
                end_date=args.end_date,
                cache_dir=GDELT_CACHE_DIR,
                reporter=reporter,
                stats=gdelt_stats,
            )
        ]
        LOGGER.info(
            f"Seed collection complete in {(time.time() - gdelt_start) / 60:.2f} minutes"
        )
        gdelt_start = time.time()
        if args.seeds_only:
            LOGGER.info("Seeds-only mode enabled; skipping full GDELT processing")
            exit(0)

        seen = load_seen(args.seen_urls_file)
        threads = max(1, args.models) * max(1, args.threads_per_model)
        chunks = chunk_list(raw_seeds, threads)
        port = args.starting_port
        if not chunks:
            chunks = [[]]

        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = []
            n = 0
            for chunk in chunks:
                futures.append(
                    executor.submit(
                        runner.run,
                        num_files=args.num_files,
                        limit=effective_limit,
                        output_path=args.output_path,
                        start_date=args.start_date,
                        end_date=args.end_date,
                        seen=seen,
                        use_bert=args.use_bert,
                        verbose=args.verbose,
                        reporter=None,
                        stats=gdelt_stats,
                        raw_seeds=chunk,
                        debug_noise=gdelt_noise,
                        port=port,
                    )
                )
                n += 1
                if n == args.threads_per_model:
                    port += 1
                    n = 0
            for future in as_completed(futures):
                future.result()
        if gdelt_noise:
            out = gdelt_noise.flush()
            if out:
                reporter.info(f"Debug noise (GDELT): {out}")

        LOGGER.info(
            f"GDELT processing complete in {(time.time() - gdelt_start) / 60:.2f} minutes"
        )
        summaries.append(gdelt_stats)
        if gdelt_stats.paused:
            reporter.info("GDELT pipeline paused; skipping remaining pipelines.")
            reporter.summary(summaries)
            LOGGER.info("GDELT pipeline paused; skipping remaining pipelines")
            return 0

    if not args.skip_html:
        import src.scrapers.scooper as scooper

        html_start = time.time()
        html_stats = PipelineStats("HTML")
        reporter.phase("Running HTML/Scooper pipeline")
        LOGGER.info("Running HTML/Scooper pipeline with args %s", args)

        scooper.setup_scooper(sb_only=args.sb_only)
        vuln_dfs: list[pd.DataFrame] = []
        noise_dfs: list[pd.DataFrame] = []

        # K split — one scooper instance per date window, run in parallel.
        if args.start_date is not None and args.end_date is not None:
            dates: list[tuple[datetime.date, datetime.date]] = _split_date(
                _parse_date(args.start_date), _parse_date(args.end_date)
            )

            def _run_window(
                window: tuple[datetime.date, datetime.date],
            ) -> tuple[PipelineStats, list, pd.DataFrame, pd.DataFrame]:
                start, end = window
                return scooper.run_scooper(
                    use_bert=args.use_bert,
                    verbose=args.verbose,
                    start_date=start,
                    end_date=end,
                    reporter=reporter,
                    stats=PipelineStats(
                        "HTML"
                    ),  # each thread gets its own instance (for now?)
                    sb_only=args.sb_only,
                )

            with ThreadPoolExecutor(max_workers=len(dates)) as executor:
                results = list(executor.map(_run_window, dates))

            vuln_lists: list = []
            for window_stats, w_vuln_list, v_df, n_df in results:
                html_stats.merge(window_stats)
                vuln_dfs.append(v_df)
                noise_dfs.append(n_df)
                vuln_lists.extend(w_vuln_list)

            scooper.save_results(vuln_lists, vuln_dfs, noise_dfs, sb_only=args.sb_only)
        # Default: one thread per site. run_scooper fans out internally and
        # returns frames merged across sites (disjoint); we persist them here.
        else:
            html_stats, vuln_list, v_df, n_df = scooper.run_scooper(
                use_bert=args.use_bert,
                verbose=args.verbose,
                start_date=_parse_date(args.start_date),
                end_date=_parse_date(args.end_date),
                reporter=reporter,
                stats=html_stats,
                sb_only=args.sb_only,
                site_split=True,
            )
            scooper.save_results(vuln_list, [v_df], [n_df], sb_only=args.sb_only)

        # TODO: thread per cite implemented here

        summaries.append(html_stats)
        if html_stats.paused:
            reporter.info("HTML scraper paused; skipping remaining pipelines.")
            reporter.summary(summaries)
            LOGGER.info("HTML scraper paused; skipping remaining pipelines")
            return 0
        
        LOGGER.info(
            f"HTML/Scooper processing complete in {(time.time() - html_start) / 60:.2f} minutes"
        )

    if summaries:
        reporter.summary(summaries)
    LOGGER.info("Orchestrator run complete with summaries: %s", summaries)
    LOGGER.info(f"Total execution time: {(time.time() - start) / 60:.2f} minutes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
