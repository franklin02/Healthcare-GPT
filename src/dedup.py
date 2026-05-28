"""Dedup logic for vulnerability records.

Pure logic: no DB, no network. The orchestrator wires the embedding-based
nearest-neighbor lookup (Supabase pgvector) and cheap pre-filter lookups
(canonical URL / normalized title) in via the ``find_neighbor`` /
``find_by_url`` / ``find_by_title`` callables, so this module stays
independently testable with stubbed embedding and lookups.

Semantics mirror the deprecated Chroma flow in src/ingest.py:
  - cosine distance <= threshold AND subsector matches      -> merge
  - cosine distance <= threshold AND subsector differs      -> log + insert
  - no neighbor OR distance > threshold                     -> insert
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from typing import Callable, Literal
from urllib.parse import urlparse

_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
_model = None  # lazy-loaded SentenceTransformer instance


@dataclass(frozen=True)
class Action:
    """Result of a dedup decision for a single record."""

    kind: Literal["insert", "merge_into", "log_conflict_and_insert"]
    existing_id: str | None = None
    reason: str | None = None
    distance: float | None = None
    embedding: list[float] | None = None


def canonicalize_url(url: str) -> str:
    """Lowercase host, drop query/fragment, strip trailing slash."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if parsed.scheme:
        return f"{parsed.scheme.lower()}://{netloc}{path}"
    return f"{netloc}{path}"


_PUNCT_TABLE = str.maketrans("", "", string.punctuation)
_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not title:
        return ""
    return _WS_RE.sub(" ", title.lower().translate(_PUNCT_TABLE)).strip()


def record_to_fingerprint_text(record: dict) -> str:
    """Build the embedding input for a record.

    Trimmed vs src/ingest.py:record_to_text: title + subsector + the first
    ~1000 chars of content (lead paragraph carries the signal; trailing body
    is mostly shared hospital boilerplate that inflates similarity between
    unrelated stories) + the subsector_data values (drug names, vendor names,
    facility names — the actual entities that distinguish incidents).
    """
    parts = []
    title = (record.get("title") or "").strip()
    if title:
        parts.append(title)
    subsector = (record.get("subsector") or "").strip()
    if subsector:
        parts.append(subsector)
    content = (record.get("content") or "").strip()
    if content:
        parts.append(content[:1000])
    sd = record.get("subsector_data")
    if isinstance(sd, dict):
        for v in sd.values():
            if v in (None, "", []):
                continue
            parts.append(str(v))
    return "\n".join(parts)


def embed_fingerprint(text: str) -> list[float]:
    """Embed text with all-MiniLM-L6-v2, normalized. Loads the model lazily."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(_EMBED_MODEL_NAME, device="cpu")
    vec = _model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def decide_action(
    record: dict,
    neighbor: tuple[str, str, float] | None,
    threshold: float,
) -> Action:
    """Apply the merge/conflict/insert rules to a (record, neighbor) pair.

    ``neighbor`` is ``(existing_id, existing_subsector, cosine_distance)`` or
    ``None`` when no neighbor was found. Distance <= threshold counts as a hit
    (matches the Chroma flow at src/ingest.py:350 which also used <=).
    """
    if neighbor is None:
        return Action("insert")
    existing_id, existing_subsector, distance = neighbor
    if distance > threshold:
        return Action("insert")
    new_subsector = (record.get("subsector") or "").strip().lower()
    existing_norm = (existing_subsector or "").strip().lower()
    if new_subsector == existing_norm:
        return Action(
            "merge_into",
            existing_id=existing_id,
            distance=distance,
        )
    return Action(
        "log_conflict_and_insert",
        existing_id=existing_id,
        reason="subsector_mismatch",
        distance=distance,
    )


def merge_records(existing: dict, new: dict) -> dict:
    """Deep-merge two records with the same subsector.

    Ported verbatim from src/ingest.py:merge_records (deprecated Chroma path)
    so dedup.py stays self-contained. Merge strategy:
    - id, subsector: kept from existing
    - title/content/exec_summary: concat with "\\n\\n---\\n\\n" separator
    - source_name/direct_link: joined with " | " when distinct
    - date_accessed/date_published: latest (ISO strings sort lexically)
    - subsector_data: deep-merged (lists extended + deduped, scalars favor existing)
    - Unknown fields: scalars favor existing-then-new; lists extended + deduped
    """

    def _concat(a, b, sep="\n\n---\n\n"):
        a = (a or "").strip()
        b = (b or "").strip()
        if not a:
            return b
        if not b:
            return a
        if a == b:
            return a
        return f"{a}{sep}{b}"

    def _join(a, b, sep=" | "):
        a = (a or "").strip() if isinstance(a, str) else a
        b = (b or "").strip() if isinstance(b, str) else b
        if not a:
            return b
        if not b:
            return a
        if a == b:
            return a
        return f"{a}{sep}{b}"

    def _latest_date(a, b):
        if not a:
            return b
        if not b:
            return a
        return a if a >= b else b

    def _merge_subsector_data(a: dict, b: dict) -> dict:
        out = dict(a or {})
        for k, v_new in (b or {}).items():
            if k not in out or out[k] in (None, "", []):
                out[k] = v_new
                continue
            v_old = out[k]
            if isinstance(v_old, list) and isinstance(v_new, list):
                combined = list(v_old)
                for item in v_new:
                    if item not in combined:
                        combined.append(item)
                out[k] = combined
            elif isinstance(v_old, dict) and isinstance(v_new, dict):
                out[k] = _merge_subsector_data(v_old, v_new)
        return out

    merged: dict = {}
    merged["id"] = existing.get("id")
    merged["title"] = _concat(existing.get("title", ""), new.get("title", ""))
    merged["source_name"] = _join(
        existing.get("source_name", ""), new.get("source_name", "")
    )
    merged["direct_link"] = _join(
        existing.get("direct_link", ""), new.get("direct_link", "")
    )
    merged["subsector"] = existing.get("subsector", new.get("subsector", ""))
    merged["date_accessed"] = _latest_date(
        existing.get("date_accessed", ""), new.get("date_accessed", "")
    )
    merged["date_published"] = _latest_date(
        existing.get("date_published", ""), new.get("date_published", "")
    )
    merged["content"] = _concat(existing.get("content", ""), new.get("content", ""))
    merged["exec_summary"] = _concat(
        existing.get("exec_summary", ""), new.get("exec_summary", "")
    )
    merged["subsector_data"] = _merge_subsector_data(
        existing.get("subsector_data", {}) or {},
        new.get("subsector_data", {}) or {},
    )

    known = set(merged.keys())
    for source in (existing, new):
        for k, v in source.items():
            if k in known:
                continue
            if k not in merged or merged[k] in (None, "", []):
                merged[k] = v
            elif isinstance(merged[k], list) and isinstance(v, list):
                merged[k] = merged[k] + [x for x in v if x not in merged[k]]

    return merged


def dedupe_records(
    records: list[dict],
    *,
    find_neighbor: Callable[[list[float]], tuple[str, str, float] | None],
    find_by_url: Callable[[str], tuple[str, str] | None] | None = None,
    find_by_title: Callable[[str], tuple[str, str] | None] | None = None,
    threshold: float = 0.44,
) -> list[tuple[dict, Action]]:
    """Decide insert/merge/conflict for each record.

    Pre-filter (cheap, exact-match): canonicalized direct_link, then
    normalized title. Either hit short-circuits to merge_into without
    embedding.

    Embedding path: build fingerprint, embed, call ``find_neighbor``, hand to
    ``decide_action``. The computed embedding rides on the returned Action so
    the caller can write it into the new row without re-embedding.
    """
    out: list[tuple[dict, Action]] = []
    for record in records:
        if find_by_url is not None:
            canon = canonicalize_url(record.get("direct_link") or "")
            if canon:
                hit = find_by_url(canon)
                if hit is not None:
                    existing_id, _existing_subsector = hit
                    out.append(
                        (
                            record,
                            Action(
                                "merge_into",
                                existing_id=existing_id,
                                reason="url_exact",
                            ),
                        )
                    )
                    continue
        if find_by_title is not None:
            norm = normalize_title(record.get("title") or "")
            if norm:
                hit = find_by_title(norm)
                if hit is not None:
                    existing_id, _existing_subsector = hit
                    out.append(
                        (
                            record,
                            Action(
                                "merge_into",
                                existing_id=existing_id,
                                reason="title_exact",
                            ),
                        )
                    )
                    continue
        embedding = embed_fingerprint(record_to_fingerprint_text(record))
        neighbor = find_neighbor(embedding)
        action = decide_action(record, neighbor, threshold)
        # carry the embedding through so the caller can write it on insert
        out.append(
            (
                record,
                Action(
                    kind=action.kind,
                    existing_id=action.existing_id,
                    reason=action.reason,
                    distance=action.distance,
                    embedding=embedding,
                ),
            )
        )
    return out
