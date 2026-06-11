"""Streaming JSON output for classifier-rejected pipeline articles."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEBUG_DIR = PROJECT_ROOT / "data" / "debug"


class NoiseDebugWriter:
    """Append rejection records while keeping the output valid JSON."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or self._new_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file: BinaryIO = self.path.open("w+b")
        self._file.write(b'{"noise":[')
        self._tail_position = self._file.tell()
        self._file.write(b"]}\n")
        self._file.flush()
        self.count = 0
        self._closed = False

    @staticmethod
    def _new_path() -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = DEFAULT_DEBUG_DIR / f"noise-{timestamp}.json"
        suffix = 1
        while path.exists():
            path = DEFAULT_DEBUG_DIR / f"noise-{timestamp}-{suffix}.json"
            suffix += 1
        return path

    def write_rejection(
        self,
        *,
        pipeline: str,
        source: str,
        title: str,
        url: str,
        publication_date: str,
        classification_stage: str,
        rejection_reason: str,
        classified_text: str,
    ) -> None:
        """Write one BERT or LLM rejection to the debug file."""
        if self._closed:
            raise ValueError("Cannot write to a closed noise debug writer")

        record = {
            "pipeline": pipeline,
            "source": source,
            "title": title,
            "url": url,
            "publication_date": publication_date,
            "classification_stage": classification_stage,
            "rejection_reason": rejection_reason,
            "classified_text": classified_text,
            "rejected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        payload = json.dumps(record, ensure_ascii=False, default=str).encode("utf-8")

        self._file.seek(self._tail_position)
        if self.count:
            self._file.write(b",")
        self._file.write(payload)
        self._tail_position = self._file.tell()
        self._file.write(b"]}\n")
        self._file.truncate()
        self._file.flush()
        self.count += 1

    def close(self) -> None:
        """Close the output file. Safe to call more than once."""
        if self._closed:
            return
        self._file.close()
        self._closed = True

    def __enter__(self) -> "NoiseDebugWriter":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def classification_stage(reason: str | None) -> str | None:
    """Return the rejecting classifier, excluding operational failures/skips."""
    if not isinstance(reason, str):
        return None
    if reason.startswith("BERT:"):
        return "bert"
    if reason in {"Body too short for LLM review", "Parsing Error"}:
        return None
    return "llm"
