"""
Standalone deduplication for processed JSON source files.

Loads one or more files from data/processed/, computes MiniLM embeddings using
the same fingerprint construction as src/dedup.py, and writes one output file per
input to data/output/ named <stem>_d.json containing only unique records
(first occurrence wins). Duplicates are dropped silently.

Does NOT require Supabase. Deduplication is purely within each supplied file.
For this reason this standalone script should only be used for prototyping and
testing how deduplication works. In order to properly deduplicate either load
the entire raw datasets and run this script or use the prefered method of using
Supabase.

Usage:
    python scripts/dedup_file.py data/processed/StateScoop.json
    python scripts/dedup_file.py data/processed/*.json
    python scripts/dedup_file.py data/processed/StateScoop.json --threshold 0.5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.logging_utils import get_file_logger  # noqa: E402

LOG_FILE = PROJECT_ROOT / "data" / "logs" / "dedup_file.log"
LOGGER = get_file_logger(__name__, LOG_FILE)

_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
_DEFAULT_THRESHOLD = 0.44
_LEAD_CHARS = 700
_model = None


def _embed_model():
    """
    Return the singleton SentenceTransformer instance, loading it on first call.
    Given that emebedings models are small CPU device selection is ideal.

    Returns:
        SentenceTransformer: The loaded all-MiniLM-L6-v2 model.
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
    """
    Compute a semantic fingerprint embedding for a raw source record dict.

    Uses the same fingerprint construction as src/dedup.embed_vulnerability:
    title, first 700 chars of content, and subsector_data entity values are
    concatenated and embedded together. The comparison step differs — this
    script compares in-memory rather than querying Supabase.

    Args:
        source: A source record dict as found in a processed JSON file.

    Returns:
        A list of floats representing the L2-normalised embedding vector.
    """
    parts: list[str] = []

    title = (source.get("title") or "").strip()
    if title:
        parts.append(title)

    content = (source.get("content") or "").strip()
    if content:
        parts.append(content[:_LEAD_CHARS])

    subsector_data = source.get("subsector_data")
    if isinstance(subsector_data, dict):
        for value in subsector_data.values():
            if value in (None, "", []):
                continue
            if isinstance(value, list):
                parts.append(", ".join(str(v) for v in value))
            else:
                parts.append(str(value))

    return _embed("\n".join(parts))


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """
    Compute cosine distance between two L2-normalised vectors.

    Args:
        a: First normalised embedding vector.
        b: Second normalised embedding vector.

    Returns:
        Cosine distance in [0, 2]; 0 means identical, 2 means opposite.
    """
    # Vectors are already L2-normalised by sentence-transformers, so
    # dot product == cosine similarity; distance = 1 - similarity.
    dot = sum(x * y for x, y in zip(a, b))
    return 1.0 - dot


def dedup_sources(
    sources: list[dict],
    threshold: float = _DEFAULT_THRESHOLD,
) -> tuple[list[dict], list[dict]]:
    """
    Split a list of source records into unique and duplicate groups.

    Runs in two passes. First, records whose title or content exactly matches
    an already-seen record are dropped — this is cheap and certain, so it
    happens before any embedding is computed. Second, the survivors go through
    embedding-based near-duplicate detection: a record is a duplicate when its
    cosine distance to any already-accepted record is at or below the threshold
    AND both records share the same subsector value. The first occurrence of
    any (exact or near) duplicate cluster is always kept.

    Args:
        sources: List of source record dicts to deduplicate.
        threshold: Cosine-distance cutoff below which two records with matching
            subsectors are considered duplicates. Defaults to 0.44.

    Returns:
        A tuple of (unique, duplicates) where each element is a list of source
        record dicts with no fields added or removed.
    """
    accepted: list[dict] = []
    accepted_embeddings: list[list[float]] = []
    duplicates: list[dict] = []
    seen_titles: set[str] = set()
    seen_contents: set[str] = set()
    candidates: list[dict] = []
    for i, source in enumerate(sources, 1):
        source_id = source.get("id", f"index-{i}")
        title = (source.get("title") or "").strip().lower()
        content = (source.get("content") or "").strip().lower()
        if (title and title in seen_titles) or (content and content in seen_contents):
            duplicates.append(source)
            LOGGER.info("Exact duplicate: %s", source_id)
            continue
        if title:
            seen_titles.add(title)
        if content:
            seen_contents.add(content)
        candidates.append(source)

    total = len(candidates)
    for i, source in enumerate(candidates, 1):
        source_id = source.get("id", f"index-{i}")
        LOGGER.debug("Embedding %s (%d/%d)", source_id, i, total)
        print(f"  [{i}/{total}] embedding {source_id[:8]}…", end="\r", flush=True)

        embedding = _fingerprint(source)

        matched_id: str | None = None
        best_distance = float("inf")
        for j, (prev, prev_emb) in enumerate(zip(accepted, accepted_embeddings)):
            dist = _cosine_distance(embedding, prev_emb)
            if dist < best_distance:
                best_distance = dist
                if dist <= threshold and source.get("subsector") == prev.get(
                    "subsector"
                ):
                    matched_id = prev.get("id", f"index-{j}")

        if matched_id is not None:
            duplicates.append(source)
            LOGGER.info(
                "Duplicate: %s → %s (distance=%.4f)",
                source_id,
                matched_id,
                best_distance,
            )
        else:
            accepted.append(source)
            accepted_embeddings.append(embedding)

    print()
    return accepted, duplicates


def main(argv: list[str] | None = None) -> int:
    """
    Parse CLI arguments, deduplicate each input file, and write output.

    Reads each supplied JSON file, runs dedup_sources on its sources array,
    and writes the unique records to data/output/<stem>_d.json.

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
    args = parser.parse_args(argv)

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

        print(f"\n{path.name}: {len(sources)} records deduplicating…")
        unique, dupes = dedup_sources(sources, threshold=args.threshold)

        out_path = output_dir / f"{path.stem}_d.json"
        out_path.write_text(
            json.dumps({"sources": unique}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        total_unique += len(unique)
        total_dupes += len(dupes)
        print(
            f"  {len(unique)} kept, {len(dupes)} dropped {out_path.relative_to(PROJECT_ROOT)}"
        )

    print(f"\nDone. Total kept: {total_unique}, total dropped: {total_dupes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
