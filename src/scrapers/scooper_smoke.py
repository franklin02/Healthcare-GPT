"""Live smoke test for the Scooper raw-only collection path.

Drives the same entry point the orchestrator's HTML stage will call once it is
rewired onto the two-stage design: collect raw article rows into
``scooper_raw.csv`` now, classify later from the CSV. This is a *live* run, not
a pytest test — point it at one or two sites with a small page cap to confirm
the ``Scooper`` class, listing/article fetch, known-article stop, and CSV
persistence all work end to end against the real sites.

    python src/scrapers/scooper_smoke.py --fast            # MedicalNewsToday, 1 page
    python src/scrapers/scooper_smoke.py -s AHA --cap 2    # AHA, 2 pages
    python src/scrapers/scooper_smoke.py --fresh           # all sites, clean baseline

By default it writes to ``data/raw/scooper_raw_smoke.csv`` so a run never
clobbers the real corpus; pass ``--real-corpus`` to exercise the production
file. Re-running against the same smoke CSV is itself a useful check: the second
run should stop early on known articles per the skip-known semantics.
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import src.scrapers.scooper as scooper  # noqa: E402


def _select_sites(names: list[str] | None, cap: int | None) -> list[dict]:
    """Return a deep copy of the configured sites, trimmed and capped.

    Deep-copying keeps the cap override local to the smoke run so the patched
    ``scooper.HTML_SITES`` never leaks back into the module's real config.
    """
    available = {s["name"].lower(): s for s in scooper.HTML_SITES}
    if names:
        wanted = [n.lower() for n in names]
        missing = [n for n in wanted if n not in available]
        if missing:
            raise SystemExit(
                f"Unknown site(s): {', '.join(missing)}. "
                f"Available: {', '.join(s['name'] for s in scooper.HTML_SITES)}"
            )
        selected = [available[n] for n in wanted]
    else:
        selected = list(scooper.HTML_SITES)

    selected = copy.deepcopy(selected)
    if cap is not None:
        for site in selected:
            site["map"]["cap"] = cap
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live smoke test for the Scooper raw-only collection path."
    )
    parser.add_argument(
        "-s",
        "--site",
        action="append",
        dest="sites",
        metavar="NAME",
        help="Restrict to a configured site (repeatable). Default: all sites.",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=None,
        help="Override each selected site's page cap (e.g. 1 for a fast run).",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=None,
        help="Override the starting page for every site (sets HTML_START_PAGE).",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Shortcut for one quick site: MedicalNewsToday, cap 1 page.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete the smoke CSV first for a clean baseline run.",
    )
    parser.add_argument(
        "--real-corpus",
        action="store_true",
        help="Write to data/raw/scooper_raw.csv instead of the smoke file.",
    )
    args = parser.parse_args(argv)

    if args.fast:
        args.sites = args.sites or ["MedicalNewsToday"]
        args.cap = 1 if args.cap is None else args.cap

    if args.start_page is not None:
        os.environ["HTML_START_PAGE"] = str(args.start_page)

    sites = _select_sites(args.sites, args.cap)

    # Wire the module the way the orchestrator's HTML stage will once it moves
    # onto the two-stage design: trim HTML_SITES, then let setup_scooper() drive
    # one windowless Scooper over them and persist the new raw rows.
    scooper.HTML_SITES = sites
    if not args.real_corpus:
        scooper.RAW_CSV_PATH = _PROJECT_ROOT / "data" / "raw" / "scooper_raw_smoke.csv"

    if args.fresh and scooper.RAW_CSV_PATH.exists():
        scooper.RAW_CSV_PATH.unlink()
        print(f"Removed {scooper.RAW_CSV_PATH} for a fresh run")

    cap_summary = ", ".join(f"{s['name']}={s['map']['cap']}" for s in sites)
    print("=== Scooper raw-only smoke run ===")
    print(f"Sites:  {', '.join(s['name'] for s in sites)}")
    print(f"Caps:   {cap_summary}")
    print(f"Output: {scooper.RAW_CSV_PATH}")
    print("-" * 34)

    raw_df = scooper.setup_scooper()

    print("-" * 34)
    print(f"Raw corpus rows now: {len(raw_df)}")
    if not raw_df.empty:
        print(f"By source: {raw_df['source_name'].value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
