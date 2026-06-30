"""rich-backed CLI reporter for the pipeline.

The sticky area at the bottom of the screen holds two bars: a *phase bar* that is
re-pointed at whatever phase is currently running (GDELT, then HTML) and an
overall "Pipeline progress" bar that climbs smoothly across the whole run.

This module is a thin layer over rich: :class:`InstanceBar` is a small adapter
that forwards to a native ``rich.progress.Progress``,
and :class:`CliReporter` owns the two bars plus the log-routing and run-summary
concerns. All human output
(``info``/``detail``/``warn``/``error`` and records routed from :mod:`logging`) is
written through ``progress.print``, so it scrolls in the area *above* the bars
instead of smashing them.
"""

from __future__ import annotations

import logging
import random
import sys
import threading
from dataclasses import dataclass
from typing import TextIO

from rich.progress import (
    Progress,
    ProgressColumn,
    TextColumn,
    BarColumn,
    TaskID,
    SpinnerColumn,
)
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich import box

_MIN_INTERVAL = 0.1

OVERALL_BAR_FORMAT = "{elapsed}"  # Kept for backwards compatibility with test_overall_bar_format_shows_elapsed_time

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

_active_reporter: "CliReporter | None" = None


def set_active_reporter(reporter: "CliReporter | None") -> None:
    """Register the reporter that currently owns the bars."""
    global _active_reporter
    _active_reporter = reporter


def get_active_reporter() -> "CliReporter | None":
    """Return the reporter currently owning the bars, if any."""
    return _active_reporter


def whim(default: str) -> str:
    if _whim_rng.random() < WHIM_CHANCE:
        return _whim_rng.choice(WHIMS)
    return default


@dataclass
class PipelineStats:
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


class PostfixColumn(ProgressColumn):
    """Custom column to render the free-form postfix step."""
    def render(self, task):
        postfix = task.fields.get("postfix", "")
        if postfix:
            return Text(str(postfix), style="dim italic")
        return Text("")


class ElapsedColumnIfOverall(ProgressColumn):
    """Custom column to render elapsed time only for the overall bar."""
    def render(self, task):
        if task.fields.get("is_overall", False):
            elapsed = task.finished_time or task.elapsed
            if elapsed is None:
                return Text("[--:--]", style="cyan")
            minutes, seconds = divmod(int(elapsed), 60)
            return Text(f"[{minutes:02d}:{seconds:02d}]", style="cyan")
        return Text("")


def format_interval(seconds: int) -> str:
    """Helper to format MM:SS."""
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes:02d}:{seconds:02d}"


class InstanceBar:
    """Thin adapter over a single native ``rich`` task."""

    def __init__(self, progress: Progress, task_id: TaskID) -> None:
        self._progress = progress
        self._task_id = task_id

    @property
    def _bar(self):
        """For backwards compatibility with tests that access _bar directly."""
        return self

    @property
    def _task(self):
        return self._progress.tasks[self._task_id]

    @property
    def total(self) -> int | float | None:
        return self._task.total

    @property
    def n(self) -> int | float:
        return self._task.completed

    @property
    def postfix(self) -> str:
        return self._task.fields.get("postfix", "")

    @property
    def pos(self) -> int:
        """For backwards compatibility with tests checking bar position."""
        return list(self._progress.tasks).index(self._task)

    @property
    def desc(self) -> str:
        return self._task.description

    @property
    def disable(self) -> bool:
        return getattr(self._progress, "disable", False) or not self._task.visible

    def set_description(self, desc: str) -> None:
        self._progress.update(self._task_id, description=str(desc), refresh=False)

    def advance(self, n: int | float = 1) -> None:
        self._progress.advance(self._task_id, advance=n)

    def set_progress(self, current: int, total: int | None = None) -> None:
        kwargs = {"completed": current}
        if total is not None:
            kwargs["total"] = total
        self._progress.update(self._task_id, **kwargs)

    def set_total(self, total: int | None) -> None:
        self._progress.update(self._task_id, total=total)

    def reset(self, total: int | None = None) -> None:
        self._task.total = total
        self._progress.reset(self._task_id, completed=0)

    def set_step(self, step: str) -> None:
        self._progress.update(self._task_id, postfix=str(step))

    def close(self) -> None:
        self._progress.stop_task(self._task_id)
        self._progress.update(self._task_id, visible=False)


class CliReporter:
    def __init__(
        self,
        verbose: bool = False,
        stream: TextIO | None = None,
        *,
        file: TextIO | None = None,
        disable: bool | None = None,
    ) -> None:
        self.verbose = verbose
        self._file = file if file is not None else (stream or sys.stdout)
        self._disable = (not _is_tty(self._file)) if disable is None else disable

        self._console = Console(file=self._file, force_terminal=False if self._disable else None)
        self._progress: Progress | None = None

        self._task: InstanceBar | None = None
        self._task_name: str | None = None
        self._overall: InstanceBar | None = None
        self._phase_units: float | None = None
        self._lock = threading.RLock()
        set_active_reporter(self)

    def _ensure_progress(self) -> Progress:
        if self._progress is None:
            self._progress = Progress(
                SpinnerColumn(spinner_name="runner"),
                TextColumn("[bold blue]{task.description}"),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                BarColumn(
                    complete_style="green",
                    finished_style="bold green",
                    pulse_style="bold yellow",
                ),
                ElapsedColumnIfOverall(),
                PostfixColumn(),
                console=self._console,
                disable=self._disable,
                transient=False,
            )
            self._progress.start()
        return self._progress

    def _ensure_phase_bar(self, name: str, total: int | None = None) -> InstanceBar:
        with self._lock:
            p = self._ensure_progress()
            if self._task is None:
                tid = p.add_task(name, total=total, postfix="", is_overall=False)
                self._task = InstanceBar(p, tid)
                self._task_name = name
            elif name != self._task_name:
                self._task_name = name
                self._task.set_description(name)
                self._task.reset(total=total)
            elif total is not None:
                self._task.set_total(total)
            return self._task

    def register_instance(self, name: str, *, total: int | None = None) -> InstanceBar:
        return self._ensure_phase_bar(name, total=total)

    def instance(self, name: str) -> InstanceBar:
        return self._ensure_phase_bar(name)

    def start_phase(self, name: str, total: int | None = None) -> InstanceBar:
        with self._lock:
            bar = self._ensure_phase_bar(name)
            bar.reset(total=total)
            self._phase_units = float(total) if total and total > 0 else None
            if self._overall is not None:
                self._overall.set_step(name)
            return bar

    def advance(self, n: int = 1) -> None:
        with self._lock:
            if self._task is not None:
                self._task.advance(n)
            if self._overall is not None and self._phase_units:
                bar = self._overall
                delta = n / self._phase_units
                if bar.total is not None and bar.n + delta > bar.total:
                    delta = bar.total - bar.n
                if delta > 0:
                    bar.advance(delta)

    def _ensure_overall(self, total: int | None = None) -> InstanceBar:
        with self._lock:
            p = self._ensure_progress()
            if self._overall is None:
                tid = p.add_task("Pipeline progress", total=total, postfix="", is_overall=True)
                self._overall = InstanceBar(p, tid)
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

    def _write(self, message: str | Text) -> None:
        with self._lock:
            msg_to_print = Text(message) if isinstance(message, str) else message
            if self._progress is not None:
                self._progress.print(msg_to_print, highlight=False)
            else:
                self._console.print(msg_to_print, highlight=False)

    def log(self, message: str) -> None:
        self._write(message)

    def phase(self, message: str) -> None:
        t = Text()
        t.append(f"\n=== {message} ===", style="bold magenta")
        self._write(t)

    def status(self, message: str) -> None:
        self._write(Text(message, style="bold cyan"))

    def info(self, message: str) -> None:
        self._write(message)

    def detail(self, message: str) -> None:
        if self.verbose:
            self._write(Text(message, style="dim"))

    def warn(self, message: str, stats: PipelineStats | None = None) -> None:
        if stats is not None:
            stats.warnings += 1
        if self.verbose:
            t = Text("⚠️  WARN: ", style="bold yellow")
            t.append(message, style="yellow")
            self._write(t)

    def error(self, message: str, stats: PipelineStats | None = None) -> None:
        if stats is not None:
            stats.errors += 1
        t = Text("❌ ERROR: ", style="bold red")
        t.append(message, style="red")
        self._write(t)

    def finish_bars(self) -> None:
        with self._lock:
            if self._progress is not None:
                if self._task is not None:
                    self._task.close()
                if self._overall is not None:
                    self._overall.close()
                self._progress.stop()
                self._progress = None
            self._task = None
            self._task_name = None
            self._overall = None

    def summary(self, stats: PipelineStats | list[PipelineStats]) -> None:
        self.finish_bars()
        stats_list = stats if isinstance(stats, list) else [stats]

        table = Table(
            title="[bold magenta]=== Run Summary ===[/bold magenta]",
            show_header=True,
            header_style="bold cyan",
            box=box.ROUNDED,
            padding=(0, 2)
        )
        table.add_column("Metric", style="bold white")

        for item in stats_list:
            table.add_column(item.name, justify="right")

        if any(item.paused for item in stats_list):
            table.add_row("Paused", *["yes" if s.paused else "-" for s in stats_list])

        if any(item.sites_scanned for item in stats_list):
            table.add_row("Sites scanned", *[str(s.sites_scanned) if s.sites_scanned else "-" for s in stats_list])

        table.add_row("Discovered", *[str(s.discovered) for s in stats_list], style="dim")
        table.add_row("Processed", *[str(s.processed) for s in stats_list], style="dim")
        table.add_row("Validated", *[str(s.validated) for s in stats_list], style="green")
        table.add_row("Rejected", *[str(s.rejected) if s.rejected == 0 else f"[red]{s.rejected}[/red]" for s in stats_list], style="red")
        table.add_row("Rejection rate", *[f"{s.rejection_rate:.0%}" for s in stats_list], style="dim")
        table.add_row("Skipped", *[str(s.skipped) for s in stats_list], style="dim")
        table.add_row("Duplicates", *[str(s.duplicates) for s in stats_list], style="dim")
        table.add_row("Warnings", *[str(s.warnings) if s.warnings == 0 else f"[bold yellow]{s.warnings}[/yellow]" for s in stats_list], style="yellow")
        table.add_row("Errors", *[str(s.errors) if s.errors == 0 else f"[bold red]{s.errors}[/bold red]" for s in stats_list], style="bold red")
        table.add_row("Output records", *[str(s.output_records) for s in stats_list])
        table.add_row("Time elapsed", *[format_interval(int(s.elapsed_seconds)) for s in stats_list])

        self._console.print("\n")
        self._console.print(table)

    def close(self) -> None:
        self.finish_bars()
        if get_active_reporter() is self:
            set_active_reporter(None)

    def __enter__(self) -> "CliReporter":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


class CliReporterLoggingHandler(logging.Handler):
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
