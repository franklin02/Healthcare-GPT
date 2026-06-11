"""tqdm-backed CLI reporter for the (future multi-instance) pipeline.

An *instance* is one pipeline worker (issue #181). Today the pipeline runs a
single instance, so the sticky area at the bottom of the screen holds just two
bars: a shared *task bar* that is re-pointed at whatever unit of work is
currently running (the GDELT pipeline, then each HTML scraper site) and an
overall "Pipeline progress" bar with elapsed time. When more than one instance
is declared via :meth:`CliReporter.build_instances` (demo script, #181), each
instance instead gets its own persistent bar stacked above the overall bar.

All human output (``info``/``detail``/``warn``/``error`` and records routed
from :mod:`logging`) is written through :func:`tqdm.write`, so it scrolls in
the area *above* the bars instead of smashing them.

tqdm is imported and customized in this one module; the rest of the codebase
talks only to :class:`CliReporter` / :class:`InstanceBar`.
"""

from __future__ import annotations

import logging
import random
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TextIO

from tqdm import tqdm

# The configured look. ``{desc}`` carries the instance/task name, ``{postfix}``
# the current step (tqdm prefixes it with ", " when set), and tqdm renders
# ``{bar}`` itself (unicode blocks or ascii). Keeping the step in the postfix —
# after the bar — keeps every stacked bar left-aligned at the same width.
INSTANCE_BAR_FORMAT = "{desc} {percentage:3.0f}% |{bar}|{postfix}"
OVERALL_BAR_FORMAT = "{desc} |{bar}| {n_fmt}/{total_fmt} [{elapsed}]{postfix}"
_MIN_INTERVAL = 0.1

# A dash of whimsy (issue #191): occasional stand-in step labels shown while a
# slow call is in flight. Bar-postfix only — never logged.
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

    Use only for bar step labels (postfix) while a slow call is in flight, and
    restore the factual label afterwards — never feed the result to a logger.
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


@dataclass
class InstanceSpec:
    """Declaration of one instance bar to build at startup."""

    name: str
    total: int | None = None
    model: str | None = None
    endpoint: str | None = None


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
    current task as a ``task: step`` postfix instead.
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
        self._bar = tqdm(
            total=total,
            position=position,
            leave=True,
            desc=self._compose_desc(),
            bar_format=bar_format,
            file=file,
            disable=disable,
            dynamic_ncols=True,
            mininterval=_MIN_INTERVAL,
            ascii=use_ascii,
        )

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
        self._bar.set_postfix_str("", refresh=False)
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
        self._bar.set_postfix_str(label, refresh=True)

    def close(self) -> None:
        self._bar.close()


class CliReporter:
    """tqdm-backed reporter owning the sticky bars at the bottom of the screen.

    Single-instance mode (the default, and the only mode the real pipeline
    uses until #181): one shared task bar plus the overall bar. Multi-instance
    mode (``build_instances`` with two or more specs): one persistent bar per
    instance, overall bar below them. ``register_instance``/``instance`` route
    to the right bar in both modes, so runner/scooper call sites are identical.
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
        self._multi = False
        self._task: InstanceBar | None = None
        self._instances: dict[str, InstanceBar] = {}
        self._order: list[str] = []
        self._overall: InstanceBar | None = None
        # Guards the bar registry; tqdm's own class lock serializes drawing.
        self._lock = threading.RLock()
        self._local = threading.local()
        set_active_reporter(self)

    # ---- instance + overall bar management ----------------------------

    def build_instances(
        self, specs: list[InstanceSpec], *, model_label: str | None = None
    ) -> None:
        """Declare the instances for this run and create the overall bar.

        Renders the startup sequence the issue describes: a "Building
        instances" line, the loaded model, an "Instances built" line, then the
        stickied overall bar. One spec keeps single-instance mode (shared task
        bar); two or more switch to multi-instance mode with one persistent
        bar per spec. The overall bar's total is set separately via
        ``set_overall_total`` (units of work, not instances).
        """
        self.info(f"Building instances ({len(specs)})...")
        with self._lock:
            if len(specs) > 1:
                self._multi = True
                for spec in specs:
                    bar = InstanceBar(
                        spec.name,
                        position=len(self._order),
                        total=spec.total,
                        model=spec.model,
                        endpoint=spec.endpoint,
                        file=self._file,
                        disable=self._disable,
                        verbose=self.verbose,
                        use_ascii=self._use_ascii,
                    )
                    self._instances[spec.name] = bar
                    self._order.append(spec.name)
            elif specs:
                spec = specs[0]
                self._task = InstanceBar(
                    spec.name,
                    position=0,
                    total=spec.total,
                    model=spec.model,
                    endpoint=spec.endpoint,
                    file=self._file,
                    disable=self._disable,
                    verbose=self.verbose,
                    use_ascii=self._use_ascii,
                    shared=True,
                )
        if model_label:
            self.info(f"LLM Model loaded: {model_label}")
        self.info(f"Instances built ({len(specs)})")
        self._ensure_overall()

    @contextmanager
    def bind_instance(self, name: str) -> Iterator[InstanceBar]:
        """Route this thread's ``register_instance``/``instance`` calls to ``name``.

        Multi-instance mode only: a worker thread binds itself to its declared
        instance so unit code (runner/scooper) lands on that instance's bar
        without knowing which instance it runs in.
        """
        with self._lock:
            if not self._multi or name not in self._instances:
                raise KeyError(
                    f"unknown instance {name!r}; declare it via build_instances() first"
                )
            bar = self._instances[name]
        previous = getattr(self._local, "instance", None)
        self._local.instance = name
        try:
            yield bar
        finally:
            self._local.instance = previous

    def register_instance(
        self,
        name: str,
        *,
        total: int | None = None,
        model: str | None = None,
        endpoint: str | None = None,
    ) -> InstanceBar:
        """Return the bar that should track the unit of work called ``name``.

        Single mode: the shared task bar, re-pointed at ``name`` if it was on a
        different task (note: an unknown name therefore re-tasks the shared bar
        rather than failing). Multi mode: the bar of the thread's bound
        instance, or the instance named ``name`` itself; any other name raises
        ``KeyError``.
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
        with self._lock:
            if not self._multi:
                bar = self._task
                if bar is None:
                    bar = InstanceBar(
                        name,
                        position=0,
                        total=total,
                        model=model,
                        endpoint=endpoint,
                        file=self._file,
                        disable=self._disable,
                        verbose=self.verbose,
                        use_ascii=self._use_ascii,
                        shared=True,
                    )
                    self._task = bar
                    return bar
            else:
                bound = getattr(self._local, "instance", None)
                if bound is not None:
                    bar = self._instances[bound]
                elif name in self._instances:
                    # Addressed by instance name directly: no task re-pointing.
                    bar = self._instances[name]
                    self._update_bar(bar, total=total, model=model, endpoint=endpoint)
                    return bar
                else:
                    raise KeyError(
                        f"unknown instance {name!r}; declare it via "
                        "build_instances() or bind the thread with bind_instance()"
                    )
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
                # Always the bottom-most line: below the shared task bar in
                # single mode, below every instance bar in multi mode.
                position = len(self._order) if self._multi else 1
                self._overall = InstanceBar(
                    "Pipeline progress",
                    position=position,
                    total=total,
                    file=self._file,
                    disable=self._disable,
                    verbose=False,
                    bar_format=OVERALL_BAR_FORMAT,
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

    def finish_bars(self) -> None:
        """Close every bar (top to bottom) so they freeze as plain lines.

        Idempotent. After this, subsequent output prints below the frozen
        bars; new bars would start a fresh sticky area.
        """
        with self._lock:
            for name in self._order:
                try:
                    self._instances[name].close()
                except Exception:
                    pass
            if self._task is not None:
                try:
                    self._task.close()
                except Exception:
                    pass
            if self._overall is not None:
                try:
                    self._overall.close()
                except Exception:
                    pass
            self._instances.clear()
            self._order.clear()
            self._task = None
            self._overall = None

    def summary(self, stats: PipelineStats | list[PipelineStats]) -> None:
        """Print one or more pipeline run summaries.

        Summaries mark the end of a run: any live bars are finished first so
        the summary is the last thing on screen.
        """
        self.finish_bars()
        stats_list = stats if isinstance(stats, list) else [stats]
        self._write("\n=== Run Summary ===")
        for item in stats_list:
            lines = [f"{item.name}:"]
            if item.paused:
                lines.append("  Paused:         yes")
            if item.sites_scanned:
                lines.append(f"  Sites scanned:  {item.sites_scanned}")
            lines.append(f"  Discovered:     {item.discovered}")
            lines.append(f"  Processed:      {item.processed}")
            lines.append(f"  Validated:      {item.validated}")
            lines.append(f"  Rejected:       {item.rejected}")
            lines.append(f"  Skipped:        {item.skipped}")
            lines.append(f"  Duplicates:     {item.duplicates}")
            lines.append(f"  Rejection rate: {item.rejection_rate:.0%}")
            lines.append(f"  Warnings:       {item.warnings}")
            lines.append(f"  Errors:         {item.errors}")
            lines.append(f"  Output records: {item.output_records}")
            lines.append(f"  Time elapsed:   {_format_elapsed(item.elapsed_seconds)}")
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
