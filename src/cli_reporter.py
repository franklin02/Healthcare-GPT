"""Sticky single-line CLI reporter (stdlib only).

The reporter keeps at most one "sticky" line at the bottom of the output
stream and re-draws it after any interrupting message — similar in feel to
``tqdm.write``, but without the dependency. Two sticky styles are supported:

- ``progress(current, total, label)`` for determinate work (fill bar)
- ``tick(label, **counters)`` for indeterminate work (rolling counters)
"""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass
from typing import TextIO

# Module-level handle on the reporter that owns the terminal right now. Low-level
# code (e.g. logging handlers) has no reporter reference, so it looks here to draw
# above the sticky line instead of smashing it.
_active_reporter: "CliReporter | None" = None


def set_active_reporter(reporter: "CliReporter | None") -> None:
    """Register the reporter that currently owns the sticky line."""
    global _active_reporter
    _active_reporter = reporter


def get_active_reporter() -> "CliReporter | None":
    """Return the reporter currently owning the sticky line, if any."""
    return _active_reporter


@dataclass
class PipelineStats:
    """Counters reported after pipeline runs."""

    name: str
    discovered: int = 0
    processed: int = 0
    validated: int = 0
    rejected: int = 0
    skipped: int = 0
    duplicates: int = 0
    warnings: int = 0
    errors: int = 0
    output_records: int = 0
    sites_scanned: int = 0
    paused: bool = False

    def merge(self, other: "PipelineStats") -> None:
        self.discovered += other.discovered
        self.processed += other.processed
        self.validated += other.validated
        self.rejected += other.rejected
        self.skipped += other.skipped
        self.duplicates += other.duplicates
        self.warnings += other.warnings
        self.errors += other.errors
        self.output_records += other.output_records
        self.sites_scanned += other.sites_scanned
        self.paused = self.paused or other.paused

    @property
    def rejection_rate(self) -> float:
        rejected_or_skipped = self.rejected + self.skipped
        outcomes = self.validated + rejected_or_skipped
        if outcomes == 0:
            return 0.0
        return rejected_or_skipped / outcomes


class CliReporter:
    """Sticky-line reporter with tqdm-like redraw on interrupting output."""

    def __init__(self, verbose: bool = False, stream: TextIO | None = None) -> None:
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self._sticky: str | None = None
        self._sticky_len = 0
        set_active_reporter(self)

    # ---- public API ----------------------------------------------------

    def log(self, message: str) -> None:
        """Print a pre-formatted log line above the sticky bar.

        Single coordinated write path for logging handlers so records redraw
        cleanly instead of colliding with the progress line.
        """
        self._print_above(message)

    def phase(self, message: str) -> None:
        """Print a section header; finalizes any active sticky line."""
        self.finish_line()
        print(f"\n=== {message} ===", file=self.stream)

    def status(self, message: str) -> None:
        """Always-visible message printed above the sticky line."""
        self._print_above(message)

    def info(self, message: str) -> None:
        """High-level pipeline message — visible in every mode."""
        self._print_above(message)

    def detail(self, message: str) -> None:
        """Per-item detail — visible only with --verbose."""
        if self.verbose:
            self._print_above(message)

    def warn(self, message: str, stats: PipelineStats | None = None) -> None:
        """Always count warnings; only print them in verbose mode."""
        if stats is not None:
            stats.warnings += 1
        if self.verbose:
            self._print_above(f"[WARN] {message}")

    def error(self, message: str, stats: PipelineStats | None = None) -> None:
        """Always print errors and count them."""
        if stats is not None:
            stats.errors += 1
        self._print_above(f"[ERROR] {message}")

    def progress(self, current: int, total: int, label: str = "Progress") -> None:
        """Draw or update the sticky progress bar.

        When ``current >= total`` (and ``total > 0``) the line is finalized
        with a newline so the completion state stays visible above any
        subsequent output.
        """
        bar = self._progress_bar(current, total)
        pct = self._percent(current, total)
        text = f"Progress: {bar} {pct}% {label} ({current}/{total})"
        self._draw_sticky(text)
        if total > 0 and current >= total:
            self.finish_line()

    def tick(self, label: str, **counters: int) -> None:
        """Draw an indeterminate sticky counter line.

        Used when totals aren't known up front but a single sticky line of
        rolling counts is still useful (e.g. paginated scraping).
        """
        parts = [label] if label else []
        for key, value in counters.items():
            parts.append(f"{key}={value}")
        self._draw_sticky(" | ".join(parts))

    def finish_line(self) -> None:
        """Terminate any active sticky line with a newline."""
        if self._sticky is not None:
            print(file=self.stream)
            self._sticky = None
            self._sticky_len = 0

    def summary(self, stats: PipelineStats | list[PipelineStats]) -> None:
        """Print one or more pipeline run summaries."""
        self.finish_line()
        stats_list = stats if isinstance(stats, list) else [stats]
        print("\n=== Run Summary ===", file=self.stream)
        for item in stats_list:
            print(f"{item.name}:", file=self.stream)
            if item.paused:
                print("  Paused:         yes", file=self.stream)
            if item.sites_scanned:
                print(f"  Sites scanned:  {item.sites_scanned}", file=self.stream)
            print(f"  Discovered:     {item.discovered}", file=self.stream)
            print(f"  Processed:      {item.processed}", file=self.stream)
            print(f"  Validated:      {item.validated}", file=self.stream)
            print(f"  Rejected:       {item.rejected}", file=self.stream)
            print(f"  Skipped:        {item.skipped}", file=self.stream)
            print(f"  Duplicates:     {item.duplicates}", file=self.stream)
            print(f"  Rejection rate: {item.rejection_rate:.0%}", file=self.stream)
            print(f"  Warnings:       {item.warnings}", file=self.stream)
            print(f"  Errors:         {item.errors}", file=self.stream)
            print(f"  Output records: {item.output_records}", file=self.stream)

    # ---- internals -----------------------------------------------------

    def _print_above(self, message: str) -> None:
        saved = self._sticky
        self._clear_sticky()
        print(message, file=self.stream)
        if saved is not None:
            self._draw_sticky(saved)

    def _draw_sticky(self, text: str) -> None:
        text = self._truncate_for_width(text)
        pad = " " * max(self._sticky_len - len(text), 0)
        print(f"\r{text}{pad}", end="", file=self.stream, flush=True)
        self._sticky = text
        self._sticky_len = len(text)

    def _clear_sticky(self) -> None:
        if self._sticky is not None:
            print(f"\r{' ' * self._sticky_len}\r", end="", file=self.stream, flush=True)
            self._sticky = None
            self._sticky_len = 0

    def _truncate_for_width(self, text: str) -> str:
        width = self._term_width()
        if width is None or len(text) < width:
            return text
        return text[: max(width - 2, 1)] + "…"

    def _term_width(self) -> int | None:
        # Only clamp when writing to a real terminal; otherwise leave full
        # text so captured output (StringIO, pipes) stays exact.
        isatty = getattr(self.stream, "isatty", None)
        if not (isatty and isatty()):
            return None
        try:
            return shutil.get_terminal_size((80, 20)).columns
        except (OSError, ValueError):
            return None

    def _progress_bar(self, current: int, total: int, width: int = 10) -> str:
        total = max(total, 0)
        current = min(max(current, 0), total) if total else 0
        if total <= 0 or current == total:
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


class CliReporterLoggingHandler(logging.Handler):
    """Route log records through the active reporter so they don't break the bar.

    When a reporter owns the sticky line, records are drawn above it via
    ``reporter.log``. With no active reporter (scripts, tests) the record falls
    back to ``stderr`` so nothing is silently dropped.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            reporter = get_active_reporter()
            if reporter is not None:
                reporter.log(message)
            else:
                print(message, file=sys.stderr, flush=True)
        except Exception:
            self.handleError(record)
