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

from src.classes import Vulnerability

_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
_LEAD_CHARS = 700
_model = None  # lazy-loaded SentenceTransformer instance


def embed_vulnerability(vuln: Vulnerability) -> list[float]:
    """384-dim MiniLM embedding tuned for 'same incident, different wording'.

    Concatenates three high-signal components and embeds the result:
      - Title
      - Content (first 700 chars) 
      - subsector_data
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
