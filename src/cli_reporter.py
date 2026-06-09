"""tqdm-backed CLI reporter for the multi-instance pipeline.

An *instance* maps onto one unit of pipeline work — the GDELT pipeline or one
HTML scraper site — and owns a progress bar that shows its percent complete plus
a free-form "current step". A stickied overall bar tracks pipeline-wide progress
at the bottom of the screen. All human output (``info``/``detail``/``warn``/
``error`` and records routed from :mod:`logging`) is written through
:func:`tqdm.write`, so it scrolls in the area *above* the bars instead of
smashing them.

tqdm is imported and customized in this one module; the rest of the codebase
talks only to :class:`CliReporter` / :class:`InstanceBar`.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import TextIO

from tqdm import tqdm

# The configured look. ``{desc}`` carries the instance name, ``{postfix}`` the
# current step (tqdm prefixes it with ", " when set), and tqdm renders ``{bar}``
# itself (unicode blocks or ascii). Keeping the step in the postfix — after the
# bar — keeps every stacked bar left-aligned at the same width.
INSTANCE_BAR_FORMAT = "{desc} {percentage:3.0f}% |{bar}|{postfix}"
OVERALL_BAR_FORMAT = "{desc} |{bar}| {n_fmt}/{total_fmt}{postfix}"
_MIN_INTERVAL = 0.1

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
    """One tqdm progress bar for a single pipeline instance."""

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
    ) -> None:
        self.name = name
        self.model = model
        self.endpoint = endpoint
        # Verbose mode annotates which instance is tied to which model endpoint.
        desc = name
        if verbose and model:
            endpoint_label = f" @ {endpoint}" if endpoint else ""
            desc = f"{name} [{model}{endpoint_label}]"
        self._bar = tqdm(
            total=total,
            position=position,
            leave=True,
            desc=desc,
            bar_format=bar_format,
            file=file,
            disable=disable,
            dynamic_ncols=True,
            mininterval=_MIN_INTERVAL,
            ascii=use_ascii,
        )

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
        self._bar.set_postfix_str(str(step), refresh=True)

    def close(self) -> None:
        self._bar.close()


class CliReporter:
    """tqdm-backed reporter that owns per-instance bars and an overall bar."""

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
        self._instances: dict[str, InstanceBar] = {}
        self._order: list[str] = []
        self._overall: InstanceBar | None = None
        # One shared lock across all bars — safe if a thread-per-instance model
        # lands later.
        self._lock = threading.RLock()
        tqdm.set_lock(self._lock)
        set_active_reporter(self)

    # ---- instance + overall bar management ----------------------------

    def register_instance(
        self,
        name: str,
        *,
        total: int | None = None,
        model: str | None = None,
        endpoint: str | None = None,
    ) -> InstanceBar:
        """Create (or look up) the bar for ``name``; idempotent by name."""
        with self._lock:
            bar = self._instances.get(name)
            if bar is None:
                bar = InstanceBar(
                    name,
                    position=len(self._order),
                    total=total,
                    model=model,
                    endpoint=endpoint,
                    file=self._file,
                    disable=self._disable,
                    verbose=self.verbose,
                    use_ascii=self._use_ascii,
                )
                self._instances[name] = bar
                self._order.append(name)
            else:
                if total is not None:
                    bar.set_total(total)
                if model is not None:
                    bar.model = model
                if endpoint is not None:
                    bar.endpoint = endpoint
            return bar

    def instance(self, name: str) -> InstanceBar:
        """Return the bar for ``name``, creating a bare one if needed."""
        bar = self._instances.get(name)
        return bar if bar is not None else self.register_instance(name)

    def build_instances(
        self, specs: list[InstanceSpec], *, model_label: str | None = None
    ) -> None:
        """Register every instance bar up front and create the overall bar.

        Renders the startup sequence the issue describes: a "Building
        instances" line, the loaded model, an "Instances built" line, then the
        stickied overall bar.
        """
        self.info(f"Building instances ({len(specs)})...")
        for spec in specs:
            self.register_instance(
                spec.name, total=spec.total, model=spec.model, endpoint=spec.endpoint
            )
        if model_label:
            self.info(f"LLM Model loaded: {model_label}")
        self.info(f"Instances built ({len(specs)})")
        self._ensure_overall(total=len(specs))

    def _ensure_overall(self, total: int | None = None) -> InstanceBar:
        if self._overall is None:
            self._overall = InstanceBar(
                "Pipeline progress",
                position=len(self._order),
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

    def finish_line(self) -> None:
        """No-op retained for call-site compatibility (bars self-manage)."""
        return None

    # ---- summary + teardown -------------------------------------------

    def summary(self, stats: PipelineStats | list[PipelineStats]) -> None:
        """Print one or more pipeline run summaries above the bars."""
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
            self._write("\n".join(lines))

    def close(self) -> None:
        """Close every bar and release the active-reporter slot."""
        with self._lock:
            for name in self._order:
                try:
                    self._instances[name].close()
                except Exception:
                    pass
            if self._overall is not None:
                try:
                    self._overall.close()
                except Exception:
                    pass
            self._instances.clear()
            self._order.clear()
            self._overall = None
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
