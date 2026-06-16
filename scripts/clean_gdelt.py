"""Standalone script to clean GDELT pipeline artifacts.

Clears the GDELT cache, raw seed staging directories, runner and seed logs,
processed output, and the seen-URLs deduplication file.  Can be run directly
from the command line without invoking the full GDELT runner CLI::

    python -m scripts.clean_gdelt          # from the project root
    python scripts/clean_gdelt.py          # also works

The ``run_clean()`` function is the single entry-point used by both this
script and the ``--clean`` flag in ``src.GDELT.runner`` and
``src.orchestrator``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.logging_utils import get_file_logger  # noqa: E402
from src.shared_utils import clear_directory  # noqa: E402

LOG_FILE = PROJECT_ROOT / "data" / "logs" / "clean_gdelt.log"
LOGGER = get_file_logger(__name__, LOG_FILE)


def run_clean() -> None:
    """Remove all GDELT-generated data so the pipeline starts fresh.

    Targets:
        - ``data/gdelt_cache/``   — downloaded GKG zip cache
        - ``data/raw/gdelt/``     — staged seed / validated / enriched JSON
        - ``data/logs/gdelt_runner.log`` and ``data/logs/gdelt_seeds.log``
        - ``data/processed/GDELT.json`` — final merged output
        - ``data/seen_urls.json`` — URL deduplication state
    """
    clear_directory(PROJECT_ROOT / "data" / "gdelt_cache")  # gkg cache
    clear_directory(PROJECT_ROOT / "data" / "raw" / "gdelt")  # seeds

    open(
        PROJECT_ROOT / "data" / "logs" / "gdelt_runner.log", "w"
    ).close()  # clear runner log
    open(
        PROJECT_ROOT / "data" / "logs" / "gdelt_seeds.log", "w"
    ).close()  # clear seeds log

    processed = PROJECT_ROOT / "data" / "processed" / "GDELT.json"
    if processed.exists():
        os.remove(processed)  # final output

    seen = PROJECT_ROOT / "data" / "seen_urls.json"
    if seen.exists():
        os.remove(seen)  # deduplication state

    LOGGER.info("Cleaned GDELT modified directories and files")


def main() -> int:
    """CLI entry-point: run the clean and print confirmation."""
    run_clean()
    print("GDELT pipeline data cleaned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
