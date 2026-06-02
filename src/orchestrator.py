"""Unified runner for GDELT and configured HTML/Scooper scrapers.

The orchestrator owns cross-pipeline CLI concerns so operators can run small
smoke tests or larger backfills from one command while each pipeline keeps its
source-specific defaults and implementation details.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from src.cli_reporter import CliReporter, PipelineStats
from src.shared_utils import ensure_model_available, model_unavailable_error
from src.logging_utils import get_file_logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_GDELT_DIR = _PROJECT_ROOT / "src" / "GDELT"
if str(_GDELT_DIR) not in sys.path:
    sys.path.insert(0, str(_GDELT_DIR))

LOG_FILE = _PROJECT_ROOT / "data" / "logs" / "orchestrator.log"
LOGGER = get_file_logger(__name__, LOG_FILE)


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
        default=False,
        help="Run BERT pre-filter before LLM field extraction in both pipelines",
    )
    parser.add_argument(
        "--skip-gdelt",
        action="store_true",
        default=False,
        help="Skip the GDELT pipeline",
    )
    parser.add_argument(
        "--skip-html",
        action="store_true",
        default=False,
        help="Skip the HTML scraper pipeline",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Show detailed per-article pipeline output",
    )

    # GDELT-specific
    parser.add_argument(
        "--num-files",
        "-n",
        type=int,
        default=2,
        help="GDELT GKG files to scan (default: 2)",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Cap on seeds to process; defaults to 3 unless --num-files is provided",
    )
    parser.add_argument(
        "--output-path",
        "-o",
        default=None,
        help="Output JSON file or directory for GDELT results",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Earliest GDELT file date to include (YYYYMMDD or YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Latest GDELT file date to include (YYYYMMDD or YYYY-MM-DD)",
    )
    parser.add_argument(
        "--seen-urls-file",
        default=None,
        help="Path to store/load seen URLs JSON file",
    )
    parser.add_argument(
        "--subsectors",
        "-s",
        default="all",
        help="Comma-separated subsectors to scan, or 'all'",
    )
    parser.add_argument(
        "--html-start-page",
        type=int,
        default=None,
        help="Override configured starting page for every HTML scraper site",
    )
    parser.add_argument(
        "--html-page-cap",
        type=int,
        default=None,
        help=(
            "Override configured max page number for every HTML scraper site "
            "(-1 for unlimited)"
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
        from src.GDELT import runner

        n_provided = _option_provided(raw_args, ("-n", "--num-files"))
        l_provided = _option_provided(raw_args, ("-l", "--limit"))
        effective_limit = args.limit
        if not l_provided:
            effective_limit = None if n_provided else 3

        gdelt_stats = PipelineStats("GDELT")
        reporter.phase("Running GDELT pipeline")
        LOGGER.info("Running GDELT pipeline with args: %s", args)
        runner.run(
            num_files=args.num_files,
            limit=effective_limit,
            subsectors=args.subsectors,
            output_path=args.output_path,
            start_date=args.start_date,
            end_date=args.end_date,
            seen_urls_file=args.seen_urls_file,
            use_bert=args.use_bert,
            verbose=args.verbose,
            reporter=reporter,
            stats=gdelt_stats,
        )
        summaries.append(gdelt_stats)

    if not args.skip_html:
        from src.scrapers import html_engine

        html_stats = PipelineStats("HTML")
        reporter.phase("Running HTML/Scooper pipeline")
        LOGGER.info("Running HTML/Scooper pipeline with args %s", args)
        for site in html_engine.HTML_SITES:
            site_stats = html_engine.run_html_scraper(
                site,
                use_bert=args.use_bert,
                verbose=args.verbose,
                starting_page=args.html_start_page,
                page_cap=args.html_page_cap,
                reporter=reporter,
                stats=PipelineStats(site["name"]),
            )
            html_stats.merge(site_stats)
        summaries.append(html_stats)

    if summaries:
        reporter.summary(summaries)
    LOGGER.info("Orchestrator run complete with summaries: %s", summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
