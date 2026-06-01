"""Semantic fingerprint embedding for a validated Vulnerability.

Given a ``Vulnerability`` object that has already passed
``ai_check_validation``, ``embed_vulnerability`` produces a 384-dim MiniLM
vector tuned for "same incident, worded differently" — the score consumed by
``find_nearest_vulnerability`` in ``src/supabase_function.py``.

The fingerprint concatenates three high-signal components:
  - title (5-15 words; MiniLM weighs short text heavily)
  - first 700 chars of content (news inverted pyramid; trailing body is
    boilerplate that pulls similarity toward noise)
  - subsector_data entity values (drug names, vendors, threat actors,
    facility names — the actual nouns of the story)
"""

from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from src.classes import Vulnerability
from src.logging_utils import get_file_logger

if TYPE_CHECKING:
    from src.cli_reporter import CliReporter, PipelineStats

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = _PROJECT_ROOT / "data" / "logs" / "dedup.log"
LOGGER = get_file_logger(__name__, LOG_FILE)

_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
_LEAD_CHARS = 700
_DEDUP_THRESHOLD = 0.44
_model = None  # lazy-loaded SentenceTransformer instance


def embed_vulnerability(vuln: Vulnerability) -> list[float]:
    """
    Function called to make a unique embedding for a Vulnerability object

    Args: one Vulnerability object, we extract the following
      - Title
      - Content (first 700 chars)
      - subsector_data

    Returns:
        embedding variable (list[float])
    """
    parts: list[str] = []

    title = (vuln.title or "").strip()
    if title:
        parts.append(title)

    content = (vuln.content or "").strip()
    if content:
        parts.append(content[:_LEAD_CHARS])

    if vuln.subsector_data is not None:
        for value in vuln.subsector_data.to_dict().values():
            if value in (None, "", []):
                continue
            if isinstance(value, list):
                parts.append(", ".join(str(item) for item in value))
            else:
                parts.append(str(value))

    text = "\n".join(parts)
    return _embed(text)


def _embed(text: str) -> list[float]:
    """Embed text with all-MiniLM-L6-v2, normalized. Loads the model lazily."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(_EMBED_MODEL_NAME, device="cpu")
    vec = _model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def handle_vuln(
    vuln: Vulnerability,
    reporter: "CliReporter | None" = None,
    stats: "PipelineStats | None" = None,
) -> None:
    """
    This function handles in what table a Vulnerability object should go in

    The only case in which a Vulnerability is put into "duplicates":
    - Nearest row is close (cosine distance ≤ _DEDUP_THRESHOLD) and subsectors match

    How a Vulnerability is put into "vulnerabilities" table:
    - Nearest row is far (distance > _DEDUP_THRESHOLD)
    - Empty table / no embedded rows
    - Nearest row is close but subsectors differ

    """
    from src.supabase_function import (
        insert_vuln,
        insert_duplicate,
        find_nearest_vulnerability,
    )

    embedding = embed_vulnerability(vuln)
    result = find_nearest_vulnerability(embedding)

    if result is not None:
        nearest_id, nearest_subsector, distance = result
        if distance <= _DEDUP_THRESHOLD:
            if vuln.subsector == nearest_subsector:
                LOGGER.info("Duplicate found, UUID: %s", nearest_id)
                insert_duplicate(vuln, embedding, nearest_id)
                if stats is not None:
                    stats.duplicates += 1
                if reporter is not None:
                    reporter.detail(f"[DUPLICATE] {vuln.title}")
                return
            LOGGER.info(
                "Near-duplicate but subsectors differ "
                "(new=%s nearest=%s id=%s); inserting as new",
                vuln.subsector,
                nearest_subsector,
                nearest_id,
            )

    insert_vuln(vuln, embedding)
