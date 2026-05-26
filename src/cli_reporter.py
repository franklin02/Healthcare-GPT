"""Small stdlib-only helpers for cleaner pipeline CLI output."""

from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import TextIO


@dataclass
class PipelineStats:
    """Core counters reported after pipeline runs."""

    name: str
    discovered: int = 0
    processed: int = 0
    validated: int = 0
    rejected: int = 0
    skipped: int = 0
    warnings: int = 0
    errors: int = 0
    output_records: int = 0
    sites_scanned: int = 0

    def merge(self, other: "PipelineStats") -> None:
        """Add another pipeline's counters into this stats object."""
        self.discovered += other.discovered
        self.processed += other.processed
        self.validated += other.validated
        self.rejected += other.rejected
        self.skipped += other.skipped
        self.warnings += other.warnings
        self.errors += other.errors
        self.output_records += other.output_records
        self.sites_scanned += other.sites_scanned

    @property
    def rejection_rate(self) -> float:
        """Return rejected/processed as a fraction, or zero when nothing ran."""
        if self.processed == 0:
            return 0.0
        return self.rejected / self.processed


class CliReporter:
    """Print compact default output, with details enabled by verbose mode."""

    def __init__(self, verbose: bool = False, stream: TextIO | None = None) -> None:
        """Create a reporter that writes to stdout unless a stream is supplied."""
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self._progress_active = False
        self._last_progress_len = 0
        self._progress_issue_lines = 0

    def phase(self, message: str) -> None:
        """Print a visible phase header."""
        self._print(f"\n=== {message} ===")

    def detail(self, message: str) -> None:
        """Print a message only when verbose output is enabled."""
        if self.verbose:
            self._print(message)

    def info(self, message: str) -> None:
        """Print a normal user-facing message."""
        self._print(message)

    def warn(self, message: str, stats: PipelineStats | None = None) -> None:
        """Print a warning and optionally increment warning stats."""
        if stats is not None:
            stats.warnings += 1
        self._print_issue(f"[WARN] {message}")

    def error(self, message: str, stats: PipelineStats | None = None) -> None:
        """Print an error and optionally increment error stats."""
        if stats is not None:
            stats.errors += 1
        self._print_issue(f"[ERROR] {message}")

    def progress(self, current: int, total: int, label: str = "Progress") -> None:
        """Redraw a fixed-width progress bar for the current item count."""
        message = (
            f"Progress: {self._progress_bar(current, total)} "
            f"{self._percent(current, total)}% {label} ({current}/{total})"
        )
        padding = " " * max(self._last_progress_len - len(message), 0)
        if self._progress_issue_lines:
            print(
                f"\033[{self._progress_issue_lines}A"
                f"\r{message}{padding}"
                f"\033[{self._progress_issue_lines}B\r",
                end="",
                file=self.stream,
                flush=True,
            )
        else:
            print(f"\r{message}{padding}", end="", file=self.stream, flush=True)
        self._progress_active = True
        self._last_progress_len = len(message)
        if total <= 0 or current >= total:
            self._finish_progress_line()

    def summary(self, stats: PipelineStats | list[PipelineStats]) -> None:
        """Print one or more pipeline run summaries."""
        stats_list = stats if isinstance(stats, list) else [stats]
        self._print("\n=== Run Summary ===")
        for item in stats_list:
            self._print(f"{item.name}:")
            if item.sites_scanned:
                self._print(f"  Sites scanned:  {item.sites_scanned}")
            self._print(f"  Discovered:     {item.discovered}")
            self._print(f"  Processed:      {item.processed}")
            self._print(f"  Validated:      {item.validated}")
            self._print(f"  Rejected:       {item.rejected}")
            self._print(f"  Skipped:        {item.skipped}")
            self._print(f"  Rejection rate: {item.rejection_rate:.0%}")
            self._print(f"  Warnings:       {item.warnings}")
            self._print(f"  Errors:         {item.errors}")
            self._print(f"  Output records: {item.output_records}")

    def _progress_bar(self, current: int, total: int, width: int = 10) -> str:
        total = max(total, 0)
        current = min(max(current, 0), total) if total else 0
        if total <= 0:
            filled = width
        elif current == total:
            filled = width
        else:
            filled = int(width * current / total)
        empty = width - filled
        if self._supports_unicode():
            return "[" + ("█" * filled) + ("░" * empty) + "]"
        return "[" + ("#" * filled) + ("-" * empty) + "]"

    def _percent(self, current: int, total: int) -> int:
        if total <= 0:
            return 100
        return round((current / total) * 100)

    def _supports_unicode(self) -> bool:
        encoding = getattr(self.stream, "encoding", None)
        if encoding is None:
            return True
        return "utf" in encoding.lower()

    def _print(self, message: str) -> None:
        self._finish_progress_line()
        print(message, file=self.stream)

    def _print_issue(self, message: str) -> None:
        if self._progress_active:
            print(f"\n\t{message}", end="", file=self.stream, flush=True)
            self._progress_issue_lines += 1
            return
        print(f"\t{message}", file=self.stream)

    def _finish_progress_line(self) -> None:
        if self._progress_active:
            print(file=self.stream)
            self._progress_active = False
            self._last_progress_len = 0
            self._progress_issue_lines = 0
