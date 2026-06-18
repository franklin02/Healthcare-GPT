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
from pathlib import Path
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed

from .cli_reporter import CliReporter, InstanceSpec, PipelineStats
from .logging_utils import get_file_logger
from .GDELT.gdelt_seeds import backfill_cyber_seeds
from .GDELT.runner import load_seen
from .shared_utils import (
    AI_MODEL,
    AI_URL,
    DEBUG_DIR,
    NoiseCollector,
    ensure_model_available,
    get_config_bool,
    get_config_int,
    get_config_value,
    model_unavailable_error,
)
from scripts.clean_gdelt import run_clean

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
GDELT_CACHE_DIR = _PROJECT_ROOT / "data" / "gdelt_cache"

LOG_FILE = _PROJECT_ROOT / "data" / "logs" / "orchestrator.log"
LOGGER = get_file_logger(__name__, LOG_FILE)


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

    run_gdelt = not args.skip_gdelt
    run_html = not args.skip_html
    # GDELT fans out across args.models model instances, each with
    # args.threads_per_model worker threads; HTML still runs a single flow, so it
    # only needs one instance bar. One instance bar per GDELT worker thread.
    models_to_run = max(1, args.models) * max(1, args.threads_per_model)
    instance_count = models_to_run if run_gdelt else 1
    # One overall-bar unit per pipeline phase that will actually run.
    phases = (["GDELT"] if run_gdelt else []) + (["HTML"] if run_html else [])

    if phases:
        try:
            ensure_model_available()
        except model_unavailable_error as exc:
            LOGGER.error("Model availability check failed: %s", exc)
            print(exc, file=sys.stderr)
            return 1
        reporter.build_instances(
            [
                InstanceSpec(f"Instance {i + 1}", model=AI_MODEL, endpoint=AI_URL)
                for i in range(instance_count)
            ],
            model_label=AI_MODEL,
        )
        reporter.set_overall_total(len(phases))
        reporter.set_overall_step("Initializing")

    # Multi-instance mode (>1 instance) routes per-thread output via bound bars;
    # single-instance mode shares one bar, so no binding is needed there.
    multi = instance_count > 1

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
        reporter.set_overall_step("GDELT")
        # main's #202 execution model: args.models instances, each with
        # args.threads_per_model worker threads, ports assigned per model group.
        threads = max(1, args.models) * max(1, args.threads_per_model)
        chunks = chunk_list(raw_seeds, threads)
        if not chunks:
            chunks = [[]]

        def _run_gdelt_chunk(
            instance_name: str, seed_chunk: list[dict], instance_port: int
        ) -> None:
            # Bind the worker thread to its instance bar (multi mode) so the
            # reporter calls inside runner.run land on that instance's row.
            binding = reporter.bind_instance(instance_name) if multi else nullcontext()
            with binding:
                runner.run(
                    num_files=args.num_files,
                    limit=effective_limit,
                    output_path=args.output_path,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    seen=seen,
                    use_bert=args.use_bert,
                    verbose=args.verbose,
                    reporter=reporter,
                    stats=gdelt_stats,
                    raw_seeds=seed_chunk,
                    debug_noise=gdelt_noise,
                    port=instance_port,
                )

        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = []
            port = args.starting_port
            n = 0
            for i, chunk in enumerate(chunks):
                futures.append(
                    executor.submit(_run_gdelt_chunk, f"Instance {i + 1}", chunk, port)
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
        reporter.advance_overall(1)
        if gdelt_stats.paused:
            reporter.info("GDELT pipeline paused; skipping remaining pipelines.")
            reporter.summary(summaries)
            LOGGER.info("GDELT pipeline paused; skipping remaining pipelines")
            return 0

    if not args.skip_html:
        import src.scrapers.scooper as scooper

        html_start = time.time()
        html_noise = (
            NoiseCollector(DEBUG_DIR / "debug_noise_html.json") if args.debug else None
        )
        html_stats = PipelineStats("HTML")
        reporter.phase("Running HTML/Scooper pipeline")
        reporter.set_overall_step("HTML")
        LOGGER.info("Running HTML/Scooper pipeline with args %s", args)
        for site in scooper.HTML_SITES:
            site_stats = scooper.run_html_scraper(
                site,
                use_bert=args.use_bert,
                verbose=args.verbose,
                start_date=_parse_date(args.start_date),
                end_date=_parse_date(args.end_date),
                sb_only=args.sb_only,
                reporter=reporter,
                stats=PipelineStats(site["name"]),
                debug_noise=html_noise,
            )
            html_stats.merge(site_stats)
            if site_stats.paused:
                summaries.append(html_stats)
                if html_noise:
                    out = html_noise.flush()
                    if out:
                        reporter.info(f"Debug noise (HTML): {out}")
                reporter.info("HTML scraper paused; skipping remaining pipelines.")
                reporter.summary(summaries)
                LOGGER.info("HTML scraper paused; skipping remaining pipelines")
                return 0
        if html_noise:
            out = html_noise.flush()
            if out:
                reporter.info(f"Debug noise (HTML): {out}")
        summaries.append(html_stats)
        reporter.advance_overall(1)
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
