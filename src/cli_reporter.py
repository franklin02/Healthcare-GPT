"""tqdm-backed CLI reporter for the pipeline.

The sticky area at the bottom of the screen holds two bars: a *phase bar* that is
re-pointed at whatever phase is currently running (GDELT, then HTML) and an
overall "Pipeline progress" bar that climbs smoothly across the whole run. (Per-
instance bars — one persistent bar per concurrent worker — are a later chunk; see
issue #229.)

This module is a thin layer over tqdm: :class:`InstanceBar` is a small adapter
that forwards to a native ``tqdm`` (``update``/``reset``/``set_postfix_str``/…),
and :class:`CliReporter` owns the two bars plus the log-routing and run-summary
concerns that tqdm has no opinion about. All human output
(``info``/``detail``/``warn``/``error`` and records routed from :mod:`logging`) is
written through :func:`tqdm.write`, so it scrolls in the area *above* the bars
instead of smashing them.
"""

from __future__ import annotations

import logging
import random
import sys
import threading
from dataclasses import dataclass
from typing import TextIO

from tqdm import tqdm

# Both bars share one shape so the sticky area reads consistently: name, percent,
# bar, then the free-form current step (tqdm's native ``{postfix}``, set via
# ``set_postfix_str``). The overall bar adds elapsed time before the step. The bar
# auto-sizes to the terminal via ``dynamic_ncols`` — no manual width math.
PHASE_BAR_FORMAT = "{desc} {percentage:3.0f}%|{bar}| {postfix}"
OVERALL_BAR_FORMAT = "{desc} {percentage:3.0f}%|{bar}| [{elapsed}] {postfix}"
_MIN_INTERVAL = 0.1

# The sticky area is a *contiguous* stack of tqdm bars starting at position 0.
# We deliberately do NOT insert blank rows for breathing room: empty position
# rows OR blank "spacer" bars both corrupt tqdm's cursor/position accounting and
# leave a ghost copy of the bottom (overall) bar one row down. Contiguous is the
# only layout tqdm renders cleanly under log scrolling and concurrent threads.
_BARS_TOP = 0

# A dash of whimsy (issue #191): occasional stand-in step labels shown while a
# slow call is in flight. Bar step labels only — never logged.
WHIMS: tuple[str, ...] = (
    "rejecting everything",
    "making no mistakes",
    "spinning GPU fans",
    "burning tokens",
    "hallucinating results",
    "consulting the oracle",
    "reticulating splines",
    "asking the model nicely",
    "double-checking its homework",
    "separating wheat from chaff",
    "herding electrons",
    "warming up the thinking rocks",
    "negotiating with the LLM",
    "doomscrolling the news",
    "sifting through the junk drawer",
)
WHIM_CHANCE = 0.15
_whim_rng = random.Random()

# Module-level handle on the reporter that owns the terminal right now. Low-level
# code (e.g. logging handlers) has no reporter reference, so it looks here to draw
# above the bars instead of smashing them.
_active_reporter: "CliReporter | None" = None


def set_active_reporter(reporter: "CliReporter | None") -> None:
    """Register the reporter that currently owns the bars."""
    global _active_reporter
    _active_reporter = reporter


def get_active_reporter() -> "CliReporter | None":
    """Return the reporter currently owning the bars, if any."""
    return _active_reporter


def whim(default: str) -> str:
    """Return ``default``, or occasionally a whimsical stand-in label.

    Use only for bar step labels while a slow call is in flight, and restore the
    factual label afterwards — never feed the result to a logger.
    Patch ``WHIM_CHANCE`` to 0.0 or 1.0 for deterministic tests.
    """
    if _whim_rng.random() < WHIM_CHANCE:
        return _whim_rng.choice(WHIMS)
    return default


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
    elapsed_seconds: float = 0.0

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
        self.elapsed_seconds += other.elapsed_seconds

    @property
    def rejection_rate(self) -> float:
        rejected_or_skipped = self.rejected + self.skipped
        outcomes = self.validated + rejected_or_skipped
        if outcomes == 0:
            return 0.0
        return rejected_or_skipped / outcomes


def _is_tty(file: TextIO) -> bool:
    isatty = getattr(file, "isatty", None)
    try:
        return bool(isatty and isatty())
    except (ValueError, OSError):
        return False


class InstanceBar:
    """Thin adapter over a single native ``tqdm`` bar.

    Every method forwards directly to tqdm; the class exists only to give the
    rest of the codebase a small, stable vocabulary (``advance``/``set_progress``/
    ``set_step``/…) over tqdm's native calls.
    """

    def __init__(self, bar: tqdm) -> None:
        self._bar = bar

    @property
    def total(self) -> int | float | None:
        return self._bar.total

    @property
    def n(self) -> int | float:
        return self._bar.n

    def set_description(self, desc: str) -> None:
        self._bar.set_description_str(str(desc), refresh=False)

    def advance(self, n: int | float = 1) -> None:
        self._bar.update(n)

    def set_progress(self, current: int, total: int | None = None) -> None:
        """Set the bar to an absolute position."""
        if total is not None:
            self._bar.total = total
        self._bar.n = current
        self._bar.refresh()

    def set_total(self, total: int | None) -> None:
        self._bar.total = total
        self._bar.refresh()

    def reset(self, total: int | None = None) -> None:
        """Re-zero the bar for a new phase (e.g. file scan -> seed processing).

        Assign ``total`` directly first: tqdm's ``reset(total=None)`` *keeps* the
        old total, but a re-pointed bar must start indeterminate unless told a new
        one. Setting it before ``reset()`` makes ``None`` mean "indeterminate".
        """
        self._bar.total = total
        self._bar.reset()

    def set_step(self, step: str) -> None:
        """Set the free-form "current step" shown after the bar (tqdm postfix)."""
        self._bar.set_postfix_str(str(step))

    def close(self) -> None:
        self._bar.close()


class CliReporter:
    """tqdm-backed reporter owning the two sticky bars at the bottom of the screen.

    A re-pointable *phase bar* (``instance``/``start_phase`` re-task it to the
    current phase) and an overall "Pipeline progress" bar below it. Per-item
    processing calls :meth:`advance`, which moves the phase bar and nudges the
    overall bar by ``1 / phase_units`` so the overall bar climbs smoothly instead
    of jumping a whole phase at a time. (Per-instance bars for concurrent workers
    are a later chunk — see #229.)
    """

    def __init__(
        self,
        verbose: bool = False,
        stream: TextIO | None = None,
        *,
        file: TextIO | None = None,
        disable: bool | None = None,
    ) -> None:
        self.verbose = verbose
        # ``stream`` is the historical kwarg (tests pass ``stream=StringIO()``);
        # ``file`` is the tqdm-native name. Either resolves to the output target.
        self._file = file if file is not None else (stream or sys.stdout)
        # Bars only animate on a real terminal; pipes/CI/StringIO get clean text
        # output (logs + summary still print via tqdm.write).
        self._disable = (not _is_tty(self._file)) if disable is None else disable
        self._task: InstanceBar | None = None
        self._task_name: str | None = None
        self._overall: InstanceBar | None = None
        # Units in the current phase, so per-item advances can nudge the overall
        # bar by the right fraction. None until a phase total is established.
        self._phase_units: float | None = None
        # One reentrant lock guards the bar registry and serializes terminal
        # draws across threads (tqdm.write log scrolling vs. bar refreshes).
        # Reentrant because reporter methods hold it while calling bar ops that
        # re-acquire it through tqdm.
        self._lock = threading.RLock()
        tqdm.set_lock(self._lock)
        set_active_reporter(self)

    # ---- phase + overall bar management -------------------------------

    def _new_bar(
        self, name: str, position: int, bar_format: str, total=None
    ) -> InstanceBar:
        return InstanceBar(
            tqdm(
                total=total,
                position=position,
                leave=True,
                desc=name,
                bar_format=bar_format,
                file=self._file,
                disable=self._disable,
                dynamic_ncols=True,
                mininterval=_MIN_INTERVAL,
            )
        )

    def _ensure_phase_bar(self, name: str, total: int | None = None) -> InstanceBar:
        """Return the shared phase bar, re-pointed at ``name`` when it changes."""
        with self._lock:
            if self._task is None:
                self._task = self._new_bar(
                    name, _BARS_TOP, PHASE_BAR_FORMAT, total=total
                )
                self._task_name = name
            elif name != self._task_name:
                self._task_name = name
                self._task.set_description(name)
                self._task.reset(total=total)
            elif total is not None:
                self._task.set_total(total)
            return self._task

    def register_instance(self, name: str, *, total: int | None = None) -> InstanceBar:
        """Return the phase bar, re-pointed at the phase ``name`` (sized to ``total``)."""
        return self._ensure_phase_bar(name, total=total)

    def instance(self, name: str) -> InstanceBar:
        """Shorthand for :meth:`register_instance` without metadata updates."""
        return self._ensure_phase_bar(name)

    def start_phase(self, name: str, total: int | None = None) -> InstanceBar:
        """Begin a phase: re-zero the phase bar to ``total`` and record its unit
        count for the smooth overall-bar coupling.
        """
        with self._lock:
            bar = self._ensure_phase_bar(name)
            bar.reset(total=total)
            self._phase_units = float(total) if total and total > 0 else None
            if self._overall is not None:
                self._overall.set_step(name)
            return bar

    def advance(self, n: int = 1) -> None:
        """Advance the phase bar by ``n`` items and nudge the overall bar by the
        matching fraction of one phase unit. Thread-safe: tqdm ``update`` is
        guarded by the shared lock, so concurrent worker advances sum correctly.
        """
        with self._lock:
            if self._task is not None:
                self._task.advance(n)
            if self._overall is not None and self._phase_units:
                bar = self._overall._bar
                delta = n / self._phase_units
                # Clamp so accumulated float fractions never overshoot the total
                # (tqdm warns and clamps when frac > 1); cap at the last bit.
                if bar.total is not None and bar.n + delta > bar.total:
                    delta = bar.total - bar.n
                if delta > 0:
                    bar.update(delta)

    def _ensure_overall(self, total: int | None = None) -> InstanceBar:
        with self._lock:
            if self._overall is None:
                # One row below the phase bar, contiguous with it.
                self._overall = self._new_bar(
                    "Pipeline progress", _BARS_TOP + 1, OVERALL_BAR_FORMAT, total=total
                )
            elif total is not None:
                self._overall.set_total(total)
            return self._overall

    def overall(self) -> InstanceBar:
        return self._ensure_overall()

    def set_overall_total(self, total: int) -> None:
        self._ensure_overall(total=total)

    def advance_overall(self, n: int = 1) -> None:
        self._ensure_overall().advance(n)

    def set_overall_step(self, step: str) -> None:
        self._ensure_overall().set_step(step)

    # ---- log routing (above the bars) ---------------------------------

    def _write(self, message: str) -> None:
        tqdm.write(str(message), file=self._file)

    def log(self, message: str) -> None:
        """Single coordinated write path for logging handlers."""
        self._write(message)

    def phase(self, message: str) -> None:
        """Print a section header above the bars."""
        self._write(f"\n=== {message} ===")

    def status(self, message: str) -> None:
        """Always-visible message printed above the bars."""
        self._write(message)

    def info(self, message: str) -> None:
        """High-level pipeline message — visible in every mode."""
        self._write(message)

    def detail(self, message: str) -> None:
        """Per-item detail — visible only with --verbose."""
        if self.verbose:
            self._write(message)

    def warn(self, message: str, stats: PipelineStats | None = None) -> None:
        """Always count warnings; only print them in verbose mode."""
        if stats is not None:
            stats.warnings += 1
        if self.verbose:
            self._write(f"[WARN] {message}")

    def error(self, message: str, stats: PipelineStats | None = None) -> None:
        """Always print errors and count them."""
        if stats is not None:
            stats.errors += 1
        self._write(f"[ERROR] {message}")

    # ---- summary + teardown -------------------------------------------

    @staticmethod
    def _safe_close(bar: "InstanceBar | tqdm | None") -> None:
        if bar is None:
            return
        try:
            bar.close()
        except Exception:
            pass

    def finish_bars(self) -> None:
        """Close every bar (top to bottom) so they freeze as plain lines.

        Idempotent. After this, subsequent output prints below the frozen
        bars; new bars would start a fresh sticky area.
        """
        with self._lock:
            self._safe_close(self._task)
            # Close the overall bar last so the cursor ends below the whole stack
            # and the next write (the summary) lands on a clean line.
            self._safe_close(self._overall)
            self._task = None
            self._task_name = None
            self._overall = None

    def summary(self, stats: PipelineStats | list[PipelineStats]) -> None:
        """Print the run summary as one table (metrics as rows, runs as columns).

        Summaries mark the end of a run: any live bars are finished first so
        the summary is the last thing on screen.
        """
        self.finish_bars()
        stats_list = stats if isinstance(stats, list) else [stats]

        rows: list[tuple[str, list[str]]] = []
        if any(item.paused for item in stats_list):
            rows.append(("Paused", ["yes" if s.paused else "-" for s in stats_list]))
        if any(item.sites_scanned for item in stats_list):
            rows.append(
                (
                    "Sites scanned",
                    [
                        str(s.sites_scanned) if s.sites_scanned else "-"
                        for s in stats_list
                    ],
                )
            )
        rows.extend(
            [
                ("Discovered", [str(s.discovered) for s in stats_list]),
                ("Processed", [str(s.processed) for s in stats_list]),
                ("Validated", [str(s.validated) for s in stats_list]),
                ("Rejected", [str(s.rejected) for s in stats_list]),
                ("Skipped", [str(s.skipped) for s in stats_list]),
                ("Duplicates", [str(s.duplicates) for s in stats_list]),
                ("Rejection rate", [f"{s.rejection_rate:.0%}" for s in stats_list]),
                ("Warnings", [str(s.warnings) for s in stats_list]),
                ("Errors", [str(s.errors) for s in stats_list]),
                ("Output records", [str(s.output_records) for s in stats_list]),
                (
                    "Time elapsed",
                    [tqdm.format_interval(int(s.elapsed_seconds)) for s in stats_list],
                ),
            ]
        )

        # Size the table: the label column fits the widest metric name, and each
        # run's column fits the wider of its header (run name) and its values.
        label_width = max(len(label) for label, _ in rows)
        col_widths = [
            max(len(item.name), max(len(row[1][i]) for row in rows))
            for i, item in enumerate(stats_list)
        ]
        header = " " * label_width + "".join(
            f"  {item.name:>{col_widths[i]}}" for i, item in enumerate(stats_list)
        )
        lines = ["\n=== Run Summary ===", header]
        for label, values in rows:
            lines.append(
                f"{label:<{label_width}}"
                + "".join(f"  {values[i]:>{col_widths[i]}}" for i in range(len(values)))
            )
        self._write("\n".join(lines))

    def close(self) -> None:
        """Close every bar and release the active-reporter slot."""
        self.finish_bars()
        if get_active_reporter() is self:
            set_active_reporter(None)

    def __enter__(self) -> "CliReporter":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


class CliReporterLoggingHandler(logging.Handler):
    """Route log records through the active reporter so they don't break a bar.

    When a reporter owns the bars, records are drawn above them via
    ``reporter.log`` (``tqdm.write``). With no active reporter (scripts, tests)
    the record falls back to ``stderr`` so nothing is silently dropped.
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
