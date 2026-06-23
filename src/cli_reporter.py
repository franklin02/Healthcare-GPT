"""tqdm-backed CLI reporter for the pipeline.

The sticky area at the bottom of the screen holds two bars: a shared *task bar*
that is re-pointed at whatever unit of work is currently running (the GDELT
pipeline, then each HTML scraper site) and an overall "Pipeline progress" bar
with elapsed time. (Per-instance bars — one persistent bar per concurrent worker
— are a later chunk; see issue #181.)

All human output (``info``/``detail``/``warn``/``error`` and records routed
from :mod:`logging`) is written through :func:`tqdm.write`, so it scrolls in
the area *above* the bars instead of smashing them.

tqdm is imported and customized in this one module; the rest of the codebase
talks only to :class:`CliReporter` / :class:`InstanceBar`.
"""

from __future__ import annotations

import logging
import random
import shutil
import sys
import threading
from dataclasses import dataclass
from typing import TextIO

from tqdm import tqdm

# Both bars share one shape so the sticky area reads consistently: name, percent,
# bar, then the free-form current step. The overall bar adds elapsed time before
# the step. ``{step}`` is OUR placeholder (substituted per update in InstanceBar),
# not tqdm's ``{postfix}`` — tqdm forces a ", " before postfix, which reads as
# jank. ``{bar}`` is replaced with a sized ``{bar:N}`` per reporter (CliReporter).
INSTANCE_BAR_FORMAT = "{desc} {percentage:3.0f}% |{bar}| {step}"
OVERALL_BAR_FORMAT = "{desc} {percentage:3.0f}% |{bar}| [{elapsed}] {step}"
_STEP_FIELD = "{step}"
_MIN_INTERVAL = 0.1

# Both bars use one glyph width, clamped to a floor/ceiling so a wide terminal
# can't make them swallow the line (and the whole line still fits). Tunable.
_BAR_WIDTH_MIN, _BAR_WIDTH_MAX = 10, 28

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


def _format_elapsed(seconds: float) -> str:
    """Render a duration as ``2h 3m 4s`` / ``3m 4s`` / ``4s``."""
    secs = int(seconds)
    hours, rem = divmod(secs, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


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


def _supports_unicode(file: TextIO) -> bool:
    encoding = getattr(file, "encoding", None)
    if encoding is None:
        return True
    return "utf" in encoding.lower()


class InstanceBar:
    """One tqdm progress bar owned by an instance (or shared task slot).

    A *shared* bar (single-instance mode) shows the current task name as its
    description and is re-pointed at each unit of work via :meth:`start_task`.
    A non-shared bar keeps the instance name as its description and shows the
    current task as a ``task: step`` prefix on the step text instead.
    """

    def __init__(
        self,
        name: str,
        position: int,
        *,
        total: int | None = None,
        model: str | None = None,
        endpoint: str | None = None,
        file: TextIO | None = None,
        disable: bool = False,
        verbose: bool = False,
        bar_format: str = INSTANCE_BAR_FORMAT,
        use_ascii: bool = False,
        shared: bool = False,
    ) -> None:
        self.name = name
        self.model = model
        self.endpoint = endpoint
        self.task: str | None = name if shared else None
        self._shared = shared
        self._verbose = verbose
        self._base_format = bar_format
        self._step = ""
        self._bar = tqdm(
            total=total,
            position=position,
            leave=True,
            desc=self._compose_desc(),
            bar_format=self._render_bar_format(),
            file=file,
            disable=disable,
            dynamic_ncols=True,
            mininterval=_MIN_INTERVAL,
            ascii=use_ascii,
        )

    def _render_bar_format(self) -> str:
        # Substitute our literal ``{step}`` ourselves so tqdm never sees it (and
        # never prepends the ", " it forces before ``{postfix}``). Escape any
        # braces in the step text so tqdm's later ``.format`` leaves it alone.
        safe = self._step.replace("{", "{{").replace("}", "}}")
        return self._base_format.replace(_STEP_FIELD, safe)

    def _compose_desc(self) -> str:
        base = (self.task or self.name) if self._shared else self.name
        # Verbose mode annotates which instance is tied to which model endpoint.
        if self._verbose and self.model:
            endpoint_label = f" @ {self.endpoint}" if self.endpoint else ""
            return f"{base} [{self.model}{endpoint_label}]"
        return base

    def start_task(
        self,
        task: str,
        *,
        total: int | None = None,
        model: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        """Re-point the bar at a new unit of work (re-zeroed, fresh labels)."""
        self.task = task
        if model is not None:
            self.model = model
        if endpoint is not None:
            self.endpoint = endpoint
        self._bar.set_description_str(self._compose_desc(), refresh=False)
        self._step = ""
        self._bar.bar_format = self._render_bar_format()
        # Assign total directly: tqdm's reset(total=None) keeps the old total,
        # but a fresh task must start indeterminate unless told otherwise.
        self._bar.total = total
        self._bar.reset()

    def set_total(self, total: int | None) -> None:
        self._bar.total = total
        self._bar.refresh()

    def reset(self, total: int | None = None) -> None:
        """Re-zero the bar for a new phase (e.g. file scan -> seed processing)."""
        self._bar.reset(total=total)

    def advance(self, n: int = 1) -> None:
        self._bar.update(n)

    def set_progress(self, current: int, total: int | None = None) -> None:
        """Set the bar to an absolute position."""
        if total is not None:
            self._bar.total = total
        self._bar.n = current
        self._bar.refresh()

    def set_step(self, step: str) -> None:
        """Set the free-form "current step" shown after the bar."""
        label = str(step)
        if not self._shared and self.task:
            label = f"{self.task}: {label}"
        self._step = label
        self._bar.bar_format = self._render_bar_format()
        self._bar.refresh()

    def close(self) -> None:
        self._bar.close()


class CliReporter:
    """tqdm-backed reporter owning the sticky bars at the bottom of the screen.

    Two stacked bars: one shared task bar that every unit of work reuses in turn
    (``register_instance``/``instance`` re-task it to the current unit), and the
    overall pipeline-progress bar below it. (Per-instance bars for concurrent
    workers are a later chunk — see #181.)
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
        self._use_ascii = not _supports_unicode(self._file)
        # One capped bar width for both bars so they line up and the whole line
        # fits (fixed fallback off-terminal keeps output stable).
        cols = shutil.get_terminal_size((80, 20)).columns if _is_tty(self._file) else 80
        bar_width = min(_BAR_WIDTH_MAX, max(_BAR_WIDTH_MIN, cols // 4))
        self._instance_bar_format = INSTANCE_BAR_FORMAT.replace(
            "{bar}", f"{{bar:{bar_width}}}"
        )
        self._overall_bar_format = OVERALL_BAR_FORMAT.replace(
            "{bar}", f"{{bar:{bar_width}}}"
        )
        self._task: InstanceBar | None = None
        self._overall: InstanceBar | None = None
        self._cursor_hidden = False
        # One reentrant lock guards the bar registry and serializes terminal
        # draws across threads (tqdm.write log scrolling vs. bar refreshes).
        # Reentrant because reporter methods hold it while calling bar ops that
        # re-acquire it through tqdm.
        self._lock = threading.RLock()
        tqdm.set_lock(self._lock)
        set_active_reporter(self)

    # ---- instance + overall bar management ----------------------------

    def _hide_cursor(self) -> None:
        """Hide the terminal cursor so it doesn't park as a block on a bar row."""
        if self._disable or self._cursor_hidden:
            return
        try:
            self._file.write("\x1b[?25l")
            self._file.flush()
        except Exception:
            pass
        self._cursor_hidden = True

    def _show_cursor(self) -> None:
        if not self._cursor_hidden:
            return
        try:
            self._file.write("\x1b[?25h")
            self._file.flush()
        except Exception:
            pass
        self._cursor_hidden = False

    def register_instance(
        self,
        name: str,
        *,
        total: int | None = None,
        model: str | None = None,
        endpoint: str | None = None,
    ) -> InstanceBar:
        """Return the shared task bar, re-pointed at the unit of work ``name``
        and updated with any metadata. Any name is accepted — the one bar is
        reused for each unit in turn.
        """
        return self._resolve_bar(name, total=total, model=model, endpoint=endpoint)

    def instance(self, name: str) -> InstanceBar:
        """Shorthand for :meth:`register_instance` without metadata updates."""
        return self._resolve_bar(name)

    def _resolve_bar(
        self,
        name: str,
        *,
        total: int | None = None,
        model: str | None = None,
        endpoint: str | None = None,
    ) -> InstanceBar:
        """Return the one shared task bar, re-tasked to ``name``.

        Every unit of work reuses this single bar in turn. The first caller
        lazily creates it; later callers re-task it to their name (or just
        update its metadata if it's already on that unit).
        """
        with self._lock:
            bar = self._task
            if bar is None:
                # Lazily create the shared bar on first use.
                self._hide_cursor()
                bar = InstanceBar(
                    name,
                    position=_BARS_TOP,
                    total=total,
                    model=model,
                    endpoint=endpoint,
                    file=self._file,
                    disable=self._disable,
                    verbose=self.verbose,
                    bar_format=self._instance_bar_format,
                    use_ascii=self._use_ascii,
                    shared=True,
                )
                self._task = bar
                return bar
            # Re-task the shared bar when the unit changes.
            if bar.task != name:
                bar.start_task(name, total=total, model=model, endpoint=endpoint)
            else:
                self._update_bar(bar, total=total, model=model, endpoint=endpoint)
            return bar

    @staticmethod
    def _update_bar(
        bar: InstanceBar,
        *,
        total: int | None,
        model: str | None,
        endpoint: str | None,
    ) -> None:
        if total is not None:
            bar.set_total(total)
        if model is not None:
            bar.model = model
        if endpoint is not None:
            bar.endpoint = endpoint

    def _ensure_overall(self, total: int | None = None) -> InstanceBar:
        with self._lock:
            if self._overall is None:
                self._hide_cursor()
                # One row below the shared task bar, contiguous with it.
                self._overall = InstanceBar(
                    "Pipeline progress",
                    position=_BARS_TOP + 1,
                    total=total,
                    file=self._file,
                    disable=self._disable,
                    verbose=False,
                    bar_format=self._overall_bar_format,
                    use_ascii=self._use_ascii,
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
            self._overall = None
            self._show_cursor()

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
                    [_format_elapsed(s.elapsed_seconds) for s in stats_list],
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
