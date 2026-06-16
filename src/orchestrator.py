"""Unified runner for GDELT and configured HTML/Scooper scrapers.

The orchestrator owns cross-pipeline CLI concerns so operators can run small
smoke tests or larger backfills from one command while each pipeline keeps its
source-specific defaults and implementation details.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from pathlib import Path

from .cli_reporter import CliReporter, PipelineStats
from .logging_utils import get_file_logger
from .GDELT.gdelt_seeds import backfill_cyber_seeds
from .shared_utils import (
    DEBUG_DIR,
    NoiseCollector,
    df_dup,
    ensure_model_available,
    get_config_bool,
    get_config_int,
    get_config_value,
    model_unavailable_error,
    run_clean,
    update_csv,
)

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
        raise ValueError(f"dates are backwards")

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

        n_provided = _option_provided(raw_args, ("-n", "--num-files"))
        l_provided = _option_provided(raw_args, ("-l", "--limit"))
        effective_limit = args.limit
        if not l_provided:
            config_limit = get_config_int("GDELT_LIMIT", None)
            effective_limit = (
                config_limit
                if config_limit is not None
                else (None if n_provided else 3)
            )

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
        runner.run(
            num_files=args.num_files,
            limit=effective_limit,
            output_path=args.output_path,
            start_date=args.start_date,
            end_date=args.end_date,
            seen_urls_file=args.seen_urls_file,
            use_bert=args.use_bert,
            verbose=args.verbose,
            reporter=reporter,
            stats=gdelt_stats,
            raw_seeds=raw_seeds,
            debug_noise=gdelt_noise,
        )
        if gdelt_noise:
            out = gdelt_noise.flush()
            if out:
                reporter.info(f"Debug noise (GDELT): {out}")
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

            for window_stats, _vuln_list, v_df, n_df in results:
                html_stats.merge(window_stats)
                vuln_dfs.append(v_df)
                noise_dfs.append(n_df)

            clean_vuls, _dup_titles = df_dup(vuln_dfs)
            clean_noise, _ = df_dup(noise_dfs)
            if not args.sb_only:
                update_csv(clean_vuls, scooper.VULN_CSV_PATH)
                update_csv(clean_noise, scooper.NOISE_CSV_PATH)
            # TODO: write as JSON here

            else:
                print("SB stuff goes here? ")  # TODO implement sb here
            # ^
        # Default, 1 thread per site — run_scooper fans out internally and
        # returns frames already merged across sites (disjoint, no df_dup).
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
            if not args.sb_only:
                update_csv(v_df, scooper.VULN_CSV_PATH)
                update_csv(n_df, scooper.NOISE_CSV_PATH)
            else:
                print("SB stuff goes here? ")  # TODO implement sb here

        # TODO: thread per cite implemented here

        summaries.append(html_stats)
        if html_stats.paused:
            reporter.info("HTML scraper paused; skipping remaining pipelines.")
            reporter.summary(summaries)
            LOGGER.info("HTML scraper paused; skipping remaining pipelines")
            return 0

    if summaries:
        reporter.summary(summaries)
    LOGGER.info("Orchestrator run complete with summaries: %s", summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
