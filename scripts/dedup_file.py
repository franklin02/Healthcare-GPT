"""Standalone deduplication for processed JSON source files.

Loads one or more files from ``data/processed/``, computes MiniLM embeddings
from each record's title and content, and writes one output file per input to
``data/output/`` named ``<stem>_d.json`` containing only unique records. Exact
title/content duplicates keep the first occurrence; within a near-duplicate
cluster the record with the fewest null ``subsector_data`` fields is kept (ties
keep the first occurrence). Duplicates are dropped silently.

Does NOT require Supabase: deduplication is purely within each supplied file, so
this script is meant only for prototyping and testing how dedup behaves. Proper
deduplication runs against the full datasets via Supabase.

Usage:
    python scripts/dedup_file.py data/processed/scooper.json
    python scripts/dedup_file.py data/processed/*.json
    python scripts/dedup_file.py data/processed/scooper.json --threshold 0.5
    # Sample 10% of records and write the kept/dropped diff for inspection:
    python scripts/dedup_file.py data/processed/scooper.json --sample 0.1 --write-dropped
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.logging_utils import get_file_logger  # noqa: E402

LOG_FILE = PROJECT_ROOT / "data" / "logs" / "dedup_file.log"
LOGGER = get_file_logger(__name__, LOG_FILE)

_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
_DEFAULT_THRESHOLD = 0.15
_DEFAULT_SEED = 45
_LEAD_CHARS = 700
_model = None


def _embed_model():
    """Return the singleton SentenceTransformer, loading it on first call.

    Returns:
        The loaded all-MiniLM-L6-v2 model, pinned to the CPU device.
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(_EMBED_MODEL_NAME, device="cpu")
    return _model


def _embed(text: str) -> list[float]:
    """
    Embed a string with all-MiniLM-L6-v2 and return a normalised vector.

    Args:
        text: The text to embed.

    Returns:
        A list of floats representing the L2-normalised embedding vector.
    """
    vec = _embed_model().encode(text, normalize_embeddings=True)
    return vec.tolist()


def _fingerprint(source: dict) -> list[float]:
    """Compute a semantic fingerprint embedding for a raw source record.

    Built from the title and the first 700 chars of content only. Subsector
    data is intentionally excluded so similarity reflects title and content
    alone, regardless of subsector or geography scope.

    Args:
        source: A source record dict as found in a processed JSON file.

    Returns:
        The L2-normalised embedding vector as a list of floats.
    """
    parts: list[str] = []

    title = (source.get("title") or "").strip()
    if title:
        parts.append(title)

    content = (source.get("content") or "").strip()
    if content:
        parts.append(content[:_LEAD_CHARS])

    return _embed("\n".join(parts))


def _subsector_null_count(source: dict) -> float:
    """Count the null or empty fields in a record's ``subsector_data``.

    Used to break duplicate ties, the record with more populated subsector
    fields is the more useful one to keep.

    Args:
        source: A source record dict.

    Returns:
        The number of null/empty values in ``subsector_data``, or ``inf`` when
        the record has no usable ``subsector_data`` dict.
    """
    data = source.get("subsector_data")
    if not isinstance(data, dict) or not data:
        return float("inf")
    return sum(
        1
        for value in data.values()
        if value is None or (isinstance(value, (str, list, dict)) and len(value) == 0)
    )


def _prefer_new(new: dict, kept: dict) -> bool:
    """Decide whether a near-duplicate should replace the record kept so far.

    Only used for near matches; exact matches always keep the first occurrence.
    The record with fewer null ``subsector_data`` fields wins; ties keep the
    existing record, preserving "first occurrence wins".

    Args:
        new: The newly encountered duplicate record.
        kept: The record currently kept for this duplicate cluster.

    Returns:
        True if ``new`` is strictly more complete and should replace ``kept``.
    """
    return _subsector_null_count(new) < _subsector_null_count(kept)


def _near_reason(dropped: dict, kept: dict, replaced: bool) -> str:
    """Explains a near-duplicate drop in a single standardized sentence.

    Args:
        dropped: The record being dropped.
        kept: The record kept in its place.
        replaced: True when ``dropped`` was the earlier original displaced by a
            more complete later record (survivor is the later record); False
            when ``dropped`` is the later record and the earlier one was kept.

    Returns:
        The standardized explanation sentence.
    """
    dropped_nulls = _subsector_null_count(dropped)
    kept_nulls = _subsector_null_count(kept)
    survivor = "later" if replaced else "earlier"
    if kept_nulls < dropped_nulls:
        why = (
            f"it has fewer null subsector_data fields ({kept_nulls} vs {dropped_nulls})"
        )
    else:
        why = (
            f"it is the first occurrence "
            f"(tie on null subsector_data fields: {kept_nulls})"
        )
    return f"dropped in favor of the {survivor} record because {why}"


def _dropped_record(
    dropped: dict,
    kept_id: str,
    match_type: str,
    distance: float | None = None,
    reason: str | None = None,
) -> dict:
    """Build a diagnostic copy of a dropped duplicate naming its match.

    Args:
        dropped: The record being dropped.
        kept_id: Id (or index fallback) of the kept record it matched.
        match_type: "exact" or "near".
        distance: Cosine distance to the kept record, for near matches only.
        reason: Explanation of why the record was dropped and the kept record
            preferred. Supplied for near matches only.

    Returns:
        A shallow copy of ``dropped`` with ``_duplicate_of`` (kept record id),
        ``_match_type``, and, for near matches, ``_match_distance`` and
        ``_match_reason``.
    """
    record = {
        **dropped,
        "_duplicate_of": kept_id,
        "_match_type": match_type,
    }
    if distance is not None:
        record["_match_distance"] = round(distance, 4)
    if reason is not None:
        record["_match_reason"] = reason
    return record


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """
    Compute cosine distance between two L2-normalised vectors.

    Args:
        a: First normalised embedding vector.
        b: Second normalised embedding vector.

    Returns:
        Cosine distance in [0, 2]; 0 means identical, 2 means opposite.
    """
    dot = sum(x * y for x, y in zip(a, b))
    return 1.0 - dot


def dedup_sources(
    sources: list[dict],
    threshold: float = _DEFAULT_THRESHOLD,
) -> tuple[list[dict], list[dict]]:
    """Split a list of source records into unique and duplicate groups.

    Runs in two passes. First, records whose title or content exactly matches an
    already-seen record are dropped before any embedding is computed; the first
    occurrence wins. Second, the survivors go through embedding-based
    near-duplicate detection: a record is a duplicate when its cosine distance
    to any already-accepted record is at or below the threshold. Subsector is
    ignored when matching — only title/content similarity decides a duplicate.
    Within a near-duplicate cluster the kept record is the one with the fewest
    null ``subsector_data`` fields (ties keep the first occurrence), so a more
    complete record seen later can displace the earlier one it matches.

    Args:
        sources: List of source record dicts to deduplicate.
        threshold: Cosine-distance cutoff at or below which two records are
            considered duplicates.

    Returns:
        A tuple of ``(unique, duplicates)``. Unique records are returned
        unchanged. Each duplicate is a shallow copy of the original with
        diagnostic fields added: ``_duplicate_of`` (id of the matched kept
        record), ``_match_type`` ("exact" or "near"), and, for near matches,
        ``_match_distance`` and ``_match_reason``.
    """
    accepted: list[dict] = []
    accepted_embeddings: list[list[float]] = []
    duplicates: list[dict] = []
    seen_titles: dict[str, int] = {}
    seen_contents: dict[str, int] = {}
    candidates: list[dict] = []
    for i, source in enumerate(sources, 1):
        source_id = source.get("id", f"index-{i}")
        title = (source.get("title") or "").strip().lower()
        content = (source.get("content") or "").strip().lower()
        matched_idx = seen_titles.get(title) if title else None
        if matched_idx is None and content:
            matched_idx = seen_contents.get(content)
        if matched_idx is not None:
            kept = candidates[matched_idx]
            kept_id = kept.get("id", f"index-{matched_idx + 1}")
            duplicates.append(_dropped_record(source, kept_id, "exact"))
            LOGGER.info("Exact duplicate: %s → %s", source_id, kept_id)
            continue
        idx = len(candidates)
        if title:
            seen_titles[title] = idx
        if content:
            seen_contents[content] = idx
        candidates.append(source)

    total = len(candidates)
    for i, source in enumerate(candidates, 1):
        source_id = source.get("id", f"index-{i}")
        LOGGER.debug("Embedding %s (%d/%d)", source_id, i, total)
        print(f"  [{i}/{total}] embedding {source_id[:8]}…", end="\r", flush=True)

        embedding = _fingerprint(source)

        matched_j: int | None = None
        best_distance = 0.0
        for j, prev_emb in enumerate(accepted_embeddings):
            dist = _cosine_distance(embedding, prev_emb)
            if dist <= threshold:
                matched_j = j
                best_distance = dist
                break

        if matched_j is not None:
            kept = accepted[matched_j]
            kept_id = kept.get("id", f"index-{matched_j}")
            if _prefer_new(source, kept):
                # The new record is more complete: keep it and drop the old one.
                reason = _near_reason(kept, source, replaced=True)
                duplicates.append(
                    _dropped_record(kept, source_id, "near", best_distance, reason)
                )
                LOGGER.info(
                    "Duplicate: %s → %s (distance=%.4f)",
                    kept_id,
                    source_id,
                    best_distance,
                )
                accepted[matched_j] = source
                accepted_embeddings[matched_j] = embedding
            else:
                reason = _near_reason(source, kept, replaced=False)
                duplicates.append(
                    _dropped_record(source, kept_id, "near", best_distance, reason)
                )
                LOGGER.info(
                    "Duplicate: %s → %s (distance=%.4f)",
                    source_id,
                    kept_id,
                    best_distance,
                )
        else:
            accepted.append(source)
            accepted_embeddings.append(embedding)

    print()
    return accepted, duplicates


def _sample_sources(sources: list[dict], fraction: float, seed: int) -> list[dict]:
    """Take a random sample of source records based on a float to keep. Can take
    in a random seed as a parameter.

    Args:
        sources: Full list of source record dicts.
        fraction: Fraction of records to keep, in (0, 1]. Values >= 1 return all
            records unchanged.
        seed: Seed for the random number generator so samples are reproducible.

    Returns:
        A list containing the sampled records in their original order.
    """
    if fraction >= 1.0:
        return sources
    sample_size = max(1, round(len(sources) * fraction))
    rng = random.Random(seed)
    chosen = sorted(rng.sample(range(len(sources)), sample_size))
    return [sources[i] for i in chosen]


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments, deduplicate each input file, and write output.

    Reads each supplied JSON file, optionally samples a fraction of its records,
    runs ``dedup_sources`` on them, and writes the unique records to
    ``data/output/<stem>_d.json``. When ``--sample`` is used, the sampled slice
    is written to ``<stem>_sample.json`` so output can be reconciled against its
    input. With ``--write-dropped``, the dropped duplicates are written to
    ``<stem>_dropped.json``.

    Args:
        argv: Argument list to parse. Defaults to sys.argv when None.

    Returns:
        0 on success, 1 if any input file cannot be loaded or is malformed.
    """
    parser = argparse.ArgumentParser(
        description="Deduplicate processed Healthcare-GPT JSON files."
    )
    parser.add_argument(
        "json_files",
        nargs="+",
        help="One or more paths to processed JSON files (each must have a 'sources' array).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=_DEFAULT_THRESHOLD,
        help=f"Cosine-distance threshold for duplicates (default: {_DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "data" / "output"),
        help="Directory to write output files (default: data/output/).",
    )
    parser.add_argument(
        "--sample",
        type=float,
        default=1.0,
        help="Fraction of records to randomly sample before dedup, e.g. 0.1 for "
        "10%% (default: 1.0, use all records).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=_DEFAULT_SEED,
        help=f"Random seed for --sample so samples are reproducible "
        f"(default: {_DEFAULT_SEED}).",
    )
    parser.add_argument(
        "--write-dropped",
        action="store_true",
        help="Also write the dropped duplicates to <stem>_dropped.json for "
        "inspecting what dedup removed.",
    )
    args = parser.parse_args(argv)

    if not 0.0 < args.sample <= 1.0:
        print("ERROR: --sample must be in (0, 1]", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    total_unique = 0
    total_dupes = 0

    for path_str in args.json_files:
        path = Path(path_str)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"ERROR: could not load {path}: {exc}", file=sys.stderr)
            return 1

        sources = data.get("sources")
        if not isinstance(sources, list):
            print(f"ERROR: {path} has no 'sources' array", file=sys.stderr)
            return 1

        if args.sample < 1.0:
            sampled = _sample_sources(sources, args.sample, args.seed)
            print(
                f"\n{path.name}: sampled {len(sampled)} of {len(sources)} records "
                f"({args.sample:.0%}, seed={args.seed})"
            )
            sample_path = output_dir / f"{path.stem}_sample.json"
            sample_path.write_text(
                json.dumps({"sources": sampled}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"  sample set: {sample_path.relative_to(PROJECT_ROOT)}")
        else:
            sampled = sources
            print(f"\n{path.name}: {len(sampled)} records deduplicating…")

        unique, dupes = dedup_sources(sampled, threshold=args.threshold)

        out_path = output_dir / f"{path.stem}_d.json"
        out_path.write_text(
            json.dumps({"sources": unique}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        msg = f"  {len(unique)} kept, {len(dupes)} dropped {out_path.relative_to(PROJECT_ROOT)}"

        if args.write_dropped:
            dropped_path = output_dir / f"{path.stem}_dropped.json"
            dropped_path.write_text(
                json.dumps({"sources": dupes}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            msg += f"\n  dropped diff: {dropped_path.relative_to(PROJECT_ROOT)}"

        total_unique += len(unique)
        total_dupes += len(dupes)
        print(msg)

    print(f"\nDone. Total kept: {total_unique}, total dropped: {total_dupes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
