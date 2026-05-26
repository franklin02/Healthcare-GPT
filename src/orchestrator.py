"""Unified runner for GDELT and configured HTML/Scooper scrapers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_GDELT_DIR = _PROJECT_ROOT / "src" / "GDELT"
if str(_GDELT_DIR) not in sys.path:
    sys.path.insert(0, str(_GDELT_DIR))


if __name__ == "__main__":
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

    args = parser.parse_args()

    if not args.skip_gdelt:
        from src.GDELT import runner

        n_provided = any(opt in sys.argv[1:] for opt in ("-n", "--num-files"))
        l_provided = any(opt in sys.argv[1:] for opt in ("-l", "--limit"))
        effective_limit = args.limit
        if not l_provided:
            effective_limit = None if n_provided else 3

        print("=== Running GDELT pipeline ===")
        runner.run(
            num_files=args.num_files,
            limit=effective_limit,
            subsectors=args.subsectors,
            output_path=args.output_path,
            start_date=args.start_date,
            end_date=args.end_date,
            seen_urls_file=args.seen_urls_file,
            use_bert=args.use_bert,
        )

    if not args.skip_html:
        from src.scrapers import html_engine

        print("\n=== Running HTML/Scooper pipeline ===")
        for site in html_engine.HTML_SITES:
            html_engine.run_html_scraper(site, use_bert=args.use_bert)
