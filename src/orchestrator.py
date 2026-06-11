"""Unified runner for GDELT and configured HTML/Scooper scrapers.

The orchestrator owns cross-pipeline CLI concerns so operators can run small
smoke tests or larger backfills from one command while each pipeline keeps its
source-specific defaults and implementation details.
"""

from __future__ import annotations

import argparse
import datetime
import sys
import math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.cli_reporter import CliReporter, PipelineStats
from src.logging_utils import get_file_logger
from src.GDELT.gdelt_seeds import backfill_cyber_seeds
from src.GDELT.runner import load_seen
from src.shared_utils import (
    ensure_model_available,
    get_config_bool,
    get_config_int,
    get_config_value,
    model_unavailable_error,
    run_clean,
)

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
    chunk_size = math.ceil(len(items) / num_chunks)
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def main(argv: list[str] | None = None) -> int:
    """Parse CLI options, run selected pipeline stages, and report summaries.

    Args:
        argv: Optional argument list for tests and programmatic callers. When
            omitted, argparse reads from the process command line.

    Returns:
        Process exit code. A successful orchestrated run returns ``0``.
    """
    raw_args = sys.argv[1:] if argv is None else argv
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
        "--vram",
        type=int,
        default=get_config_int("VRAM_TO_USE", 5),
        help=(
            "Approximate total VRAM (in GB) to use. Determines how many model instances to run concurrently."
        ),
    )
    parser.add_argument(
        "--vram-per-model",
        type=int,
        default=get_config_int("VRAM_PER_MODEL", 5),
        help=(
            "Approximate VRAM (in GB) used by each concurrent model instance. Used with --vram to determine how many model instances to run."
        ),
    )

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

        n_provided = (
            any(opt in sys.argv[1:] for opt in ("-n", "--num-files"))
            or args.num_files is not None
        )
        l_provided = args.limit is not None
        effective_limit = args.limit
        if not l_provided:
            effective_limit = None if n_provided else 3

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

        seen = load_seen()
        models_to_run = max(1, args.vram // args.vram_per_model)
        chunks = chunk_list(raw_seeds, models_to_run)

        with ThreadPoolExecutor(max_workers=models_to_run) as executor:
            futures = []
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
                    )
                )
            for future in as_completed(futures):
                result = future.result()

        summaries.append(gdelt_stats)
        if gdelt_stats.paused:
            reporter.info("GDELT pipeline paused; skipping remaining pipelines.")
            reporter.summary(summaries)
            LOGGER.info("GDELT pipeline paused; skipping remaining pipelines")
            return 0

    if not args.skip_html:
        import src.scrapers.scooper as scooper

        html_stats = PipelineStats("HTML")
        reporter.phase("Running HTML/Scooper pipeline")
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
            )
            html_stats.merge(site_stats)
            if site_stats.paused:
                summaries.append(html_stats)
                reporter.info("HTML scraper paused; skipping remaining pipelines.")
                reporter.summary(summaries)
                LOGGER.info("HTML scraper paused; skipping remaining pipelines")
                return 0
        summaries.append(html_stats)

    if summaries:
        reporter.summary(summaries)
    LOGGER.info("Orchestrator run complete with summaries: %s", summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
