import io
import logging
import threading


import src.cli_reporter as cli_reporter
from src.cli_reporter import (
    OVERALL_BAR_FORMAT,
    CliReporter,
    CliReporterLoggingHandler,
    PipelineStats,
    get_active_reporter,
    set_active_reporter,
    whim,
)


# ---- phase / overall bars ---------------------------------------------


def test_instance_bar_tracks_progress_and_step():
    """A bar records progress and a free-form step label (tqdm postfix)."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        bar = reporter.register_instance("GDELT", total=4)
        bar.advance(1)
        bar.set_step("validating 1/4")

        assert bar._bar.n == 1
        assert bar._bar.total == 4
        assert bar._bar.postfix == "validating 1/4"


def test_instance_lookup_is_idempotent_by_name():
    """Looking up an instance returns the same bar; it is never duplicated."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        first = reporter.register_instance("CyberScoop", total=5)
        again = reporter.instance("CyberScoop")

        assert first is again


def test_reset_rezeros_bar_for_a_new_phase():
    """reset() moves a bar to a new total and clears prior progress."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        bar = reporter.register_instance("GDELT", total=3)
        bar.advance(2)
        bar.reset(total=10)

        assert bar._bar.n == 0
        assert bar._bar.total == 10


def test_single_mode_shares_one_task_bar_across_units():
    """Sequential units re-point the one shared phase bar instead of stacking."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        gdelt = reporter.register_instance("GDELT", total=4)
        gdelt.advance(2)
        gdelt.set_step("processing 2/4")

        site = reporter.register_instance("CyberScoop")

        assert site is gdelt
        assert site._bar.n == 0
        assert site._bar.total is None
        assert site._bar.desc == "CyberScoop"


def test_same_task_lookup_does_not_reset_progress():
    """Mid-unit lookups of the current task must not re-zero the bar."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        bar = reporter.register_instance("GDELT", total=10)
        bar.advance(3)

        again = reporter.instance("GDELT")

        assert again is bar
        assert again._bar.n == 3


def test_overall_bar_sits_below_the_task_bar():
    """The two bars form a contiguous stack: phase bar at 0, overall at 1."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        # tqdm stores position N as pos == -N. Contiguous rows render cleanly.
        assert abs(reporter.register_instance("GDELT")._bar.pos) == 0
        assert abs(reporter.overall()._bar.pos) == 1


def test_bars_auto_disable_on_non_tty():
    """Bars stay inert when the target is not a real terminal (pipes/CI/tests)."""
    reporter = CliReporter(file=io.StringIO())  # disable defaults to auto
    bar = reporter.register_instance("GDELT", total=3)

    assert bar._bar.disable is True
    reporter.close()


# ---- smooth overall coupling ------------------------------------------


def test_advance_drives_phase_bar_and_overall_fraction():
    """Per-item advance moves the phase bar by 1 and the overall bar by 1/units."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        reporter.set_overall_total(2)  # two phases
        reporter.start_phase("GDELT", total=4)

        reporter.advance(1)
        reporter.advance(1)

        assert reporter._task._bar.n == 2
        # Two of four items processed -> half of one phase unit on the overall bar.
        assert reporter.overall()._bar.n == 0.5


def test_concurrent_advances_sum_on_both_bars():
    """N worker threads each advancing sum correctly (tqdm update is lock-guarded)."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        reporter.set_overall_total(1)  # single phase
        reporter.start_phase("GDELT", total=100)

        def work():
            for _ in range(25):
                reporter.advance(1)

        workers = [threading.Thread(target=work) for _ in range(4)]
        for t in workers:
            t.start()
        for t in workers:
            t.join()

        assert reporter._task._bar.n == 100
        # 100 items across one phase unit -> the overall bar reaches exactly 1.0.
        assert round(reporter.overall()._bar.n, 6) == 1.0


def test_start_phase_does_not_double_count_collection_progress():
    """Direct phase-bar progress (seed collection) never touches the overall bar."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        reporter.set_overall_total(2)
        # Collection drives the phase bar directly, not via advance().
        reporter.instance("GDELT").set_progress(3, 3)
        assert reporter.overall()._bar.n == 0

        # Processing then re-zeros the phase bar and couples to the overall bar.
        reporter.start_phase("GDELT", total=2)
        reporter.advance(2)
        assert reporter.overall()._bar.n == 1.0


# ---- log routing (above the bars) -------------------------------------


def test_log_info_and_error_write_above_bars(monkeypatch):
    """log/info/error all emit through tqdm.write so they scroll above bars."""
    writes = []
    monkeypatch.setattr(
        "src.cli_reporter.CliReporter._write", lambda self, msg: writes.append(str(msg))
    )
    reporter = CliReporter(file=io.StringIO())

    reporter.info("hello")
    reporter.log("logged")
    reporter.error("boom")

    assert "hello" in writes
    assert "logged" in writes
    assert any("ERROR" in w and "boom" in w for w in writes)
    reporter.close()


def test_detail_only_writes_when_verbose(monkeypatch):
    """Detail messages should only print when verbose mode is enabled."""
    writes = []
    monkeypatch.setattr(
        "src.cli_reporter.CliReporter._write", lambda self, msg: writes.append(str(msg))
    )

    CliReporter(file=io.StringIO(), verbose=False).detail("hidden")
    assert writes == []

    CliReporter(file=io.StringIO(), verbose=True).detail("visible")
    assert "visible" in writes


def test_warning_is_hidden_by_default_but_counted(monkeypatch):
    """Default-mode warnings should increment stats without writing output."""
    writes = []
    monkeypatch.setattr(
        "src.cli_reporter.CliReporter._write", lambda self, msg: writes.append(str(msg))
    )
    reporter = CliReporter(file=io.StringIO())
    stats = PipelineStats("test")

    reporter.warn("something happened", stats)

    assert writes == []
    assert stats.warnings == 1


def test_verbose_warning_writes_and_counts(monkeypatch):
    """Verbose warnings print above the bars and still count."""
    writes = []
    monkeypatch.setattr(
        "src.cli_reporter.CliReporter._write", lambda self, msg: writes.append(str(msg))
    )
    reporter = CliReporter(file=io.StringIO(), verbose=True)
    stats = PipelineStats("test")

    reporter.warn("something happened", stats)

    assert any("WARN" in w and "something happened" in w for w in writes)
    assert stats.warnings == 1


def test_error_always_writes_and_counts(monkeypatch):
    """Errors are always written and counted regardless of verbosity."""
    writes = []
    monkeypatch.setattr(
        "src.cli_reporter.CliReporter._write", lambda self, msg: writes.append(str(msg))
    )
    reporter = CliReporter(file=io.StringIO())
    stats = PipelineStats("test")

    reporter.error("kaboom", stats)

    assert any("ERROR" in w and "kaboom" in w for w in writes)
    assert stats.errors == 1


# ---- logging handler integration --------------------------------------


def test_logging_handler_routes_through_active_reporter():
    """Records reach the active reporter's log() so they don't break a bar."""
    reporter = CliReporter(file=io.StringIO())
    logged: list[str] = []
    reporter.log = lambda message: logged.append(message)  # spy
    set_active_reporter(reporter)

    handler = CliReporterLoggingHandler()
    record = logging.LogRecord(
        "test", logging.WARNING, __file__, 1, "routed message", None, None
    )
    handler.emit(record)

    assert logged == ["routed message"]
    reporter.close()


def test_logging_handler_falls_back_to_stderr(capsys):
    """With no active reporter the record is printed to stderr, not dropped."""
    set_active_reporter(None)
    handler = CliReporterLoggingHandler()
    record = logging.LogRecord(
        "test", logging.WARNING, __file__, 1, "fallback message", None, None
    )
    handler.emit(record)

    assert "fallback message" in capsys.readouterr().err


# ---- teardown ----------------------------------------------------------


def test_context_manager_closes_bars_and_clears_active():
    """Exiting the context closes bars and releases the active-reporter slot."""
    reporter = CliReporter(file=io.StringIO(), disable=False)
    with reporter:
        reporter.register_instance("GDELT", total=2)
        assert get_active_reporter() is reporter

    assert get_active_reporter() is None


def test_summary_finishes_bars_before_printing():
    """summary() freezes any live bars so the summary is last on screen."""
    stream = io.StringIO()
    reporter = CliReporter(file=stream, disable=False)
    bar = reporter.register_instance("GDELT", total=2)

    reporter.summary(PipelineStats("GDELT"))

    assert bar._bar.disable is True  # tqdm marks closed bars disabled
    assert "=== Run Summary ===" in stream.getvalue()
    reporter.close()


# ---- summary / stats (preserved contract) -----------------------------


def _summary_row(output: str, label: str) -> str:
    """Return the summary table row that contains ``label``."""
    line = next(line for line in output.splitlines() if label in line)
    return line.replace("│", "").strip()


def test_summary_prints_core_counts():
    """Summary output should include core counters and rejection rate."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats(
        "GDELT",
        discovered=4,
        processed=2,
        validated=1,
        rejected=1,
        output_records=1,
    )

    reporter.summary(stats)

    output = stream.getvalue()
    assert "GDELT" in output  # column header
    assert _summary_row(output, "Discovered").endswith("4")
    assert _summary_row(output, "Rejection rate").endswith("50%")


def test_summary_prints_paused_state():
    """Paused stats should render an explicit paused row in the summary."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats("GDELT", paused=True)

    reporter.summary(stats)

    output = stream.getvalue()
    assert "GDELT" in output
    assert _summary_row(output, "Paused").endswith("yes")


def test_stats_merge_preserves_paused_state():
    """Merging paused child stats should mark the aggregate stats as paused."""
    combined = PipelineStats("Combined")
    gdelt = PipelineStats("GDELT", paused=True)

    combined.merge(gdelt)

    assert combined.paused is True


def test_summary_counts_skipped_items_as_negative_outcomes():
    """Skipped items should count as negative outcomes without using processed."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats(
        "GDELT",
        processed=3,
        validated=1,
        rejected=1,
        skipped=1,
    )

    reporter.summary(stats)

    assert _summary_row(stream.getvalue(), "Rejection rate").endswith("67%")


def test_summary_rejection_rate_does_not_exceed_100_with_skipped_items():
    """Skipped counts must not shrink the rejection-rate denominator."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats(
        "FedScoop",
        processed=164,
        validated=12,
        rejected=150,
        skipped=18,
    )

    reporter.summary(stats)

    assert _summary_row(stream.getvalue(), "Rejection rate").endswith("93%")


def test_summary_rejection_rate_handles_no_outcomes():
    """No terminal outcomes should report 0% instead of dividing by zero."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats("Empty", processed=0)

    reporter.summary(stats)

    assert _summary_row(stream.getvalue(), "Rejection rate").endswith("0%")


def test_summary_rejection_rate_handles_all_skipped_items():
    """All-skipped runs should be bounded and avoid the old zero denominator."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats("Skipped", processed=3, skipped=3)

    reporter.summary(stats)

    assert _summary_row(stream.getvalue(), "Rejection rate").endswith("100%")


def test_summary_accepts_a_list_of_stats():
    """A list of stats renders one table with a column per pipeline."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.summary([PipelineStats("GDELT", discovered=2), PipelineStats("HTML")])

    output = stream.getvalue()
    assert "=== Run Summary ===" in output
    header = next(line for line in output.splitlines() if "GDELT" in line)
    assert "HTML" in header  # both pipelines share the header row
    assert _summary_row(output, "Discovered").split() == ["Discovered", "2", "0"]


# ---- elapsed time ------------------------------------------------------


def test_overall_bar_format_shows_elapsed_time():
    """The stickied pipeline bar includes elapsed time per the issue."""
    assert "{elapsed}" in OVERALL_BAR_FORMAT


def test_summary_prints_time_elapsed():
    """Each pipeline's summary section reports its elapsed wall time."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.summary(PipelineStats("GDELT", elapsed_seconds=83))

    # tqdm.format_interval renders 83s as MM:SS.
    assert _summary_row(stream.getvalue(), "Time elapsed").endswith("01:23")


def test_merge_sums_elapsed_seconds():
    """Merged stats accumulate elapsed time across pipelines."""
    combined = PipelineStats("Combined", elapsed_seconds=10)

    combined.merge(PipelineStats("GDELT", elapsed_seconds=5.5))

    assert combined.elapsed_seconds == 15.5


# ---- whims ---------------------------------------------------------------


def test_whim_returns_default_when_chance_is_zero(monkeypatch):
    """With the chance zeroed out, the factual label always comes back."""
    monkeypatch.setattr(cli_reporter, "WHIM_CHANCE", 0.0)

    assert whim("processing 1/5") == "processing 1/5"


def test_whim_returns_whimsical_label_when_chance_is_one(monkeypatch):
    """With the chance maxed, a label from WHIMS stands in."""
    monkeypatch.setattr(cli_reporter, "WHIM_CHANCE", 1.0)

    assert whim("processing 1/5") in cli_reporter.WHIMS
