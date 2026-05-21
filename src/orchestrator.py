"""Unified runner for GDELT and configured HTML/Scooper scrapers."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_GDELT_DIR = _PROJECT_ROOT / "src" / "GDELT"
if str(_GDELT_DIR) not in sys.path:
    sys.path.insert(0, str(_GDELT_DIR))


if __name__ == "__main__":
    runpy.run_module("src.GDELT.runner", run_name="__main__")

    from src.scrapers import html_engine

    print("\n=== Running HTML/Scooper pipeline ===")
    html_results = []
    for site in html_engine.HTML_SITES:
        html_results.append(html_engine.run_html_scraper(site))
