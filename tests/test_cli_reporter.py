import io
import logging
import os
import threading

import pytest

import src.cli_reporter as cli_reporter
from src.cli_reporter import (
    OVERALL_BAR_FORMAT,
    CliReporter,
    CliReporterLoggingHandler,
    InstanceSpec,
    PipelineStats,
    get_active_reporter,
    set_active_reporter,
    whim,
)


# ---- instance / overall bars ------------------------------------------


def test_instance_bar_tracks_progress_and_step():
    """An instance bar records absolute progress and a free-form step label."""
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
    """Sequential units re-point the one shared task bar instead of stacking."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        gdelt = reporter.register_instance("GDELT", total=4)
        gdelt.advance(2)
        gdelt.set_step("processing 2/4")

        site = reporter.register_instance("CyberScoop")

        assert site is gdelt
        assert site._bar.n == 0
        assert site._bar.total is None
        assert site._bar.desc == "CyberScoop"
        assert site._bar.postfix == ""


def test_same_task_lookup_does_not_reset_progress():
    """Mid-unit lookups of the current task must not re-zero the bar."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        bar = reporter.register_instance("GDELT", total=10)
        bar.advance(3)

        again = reporter.instance("GDELT")

        assert again is bar
        assert again._bar.n == 3


def test_multiple_instances_and_overall_advance_independently():
    """In multi mode each instance bar plus the overall bar count separately."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        reporter.build_instances(
            [InstanceSpec("Instance 1"), InstanceSpec("Instance 2")]
        )
        one = reporter.register_instance("Instance 1", total=10)
        two = reporter.register_instance("Instance 2", total=5)
        reporter.set_overall_total(2)

        one.advance(3)
        two.advance(1)
        reporter.advance_overall(1)

        assert one is not two
        assert one._bar.n == 3
        assert two._bar.n == 1
        assert reporter.overall()._bar.n == 1


def test_multi_mode_overall_bar_sits_below_instance_bars():
    """Bars form a contiguous stack with no gap rows (gaps corrupt tqdm draws)."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        reporter.build_instances(
            [InstanceSpec("Instance 1"), InstanceSpec("Instance 2")]
        )

        # tqdm stores position N as pos == -N. Contiguous: instances 0-1,
        # overall 2 — every row is a real bar so the stack renders cleanly.
        assert abs(reporter.instance("Instance 1")._bar.pos) == 0
        assert abs(reporter.instance("Instance 2")._bar.pos) == 1
        assert abs(reporter.overall()._bar.pos) == 2


def test_bar_widths_are_capped_relative_to_terminal():
    """Instance bars get ~1/5 of the width, the overall bar ~1/2 (80 cols off-tty)."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        task = reporter.register_instance("GDELT", total=4)

        assert "{bar:16}" in task._bar.bar_format
        assert "{bar:40}" in reporter.overall()._bar.bar_format


def test_bar_widths_clamp_to_absolute_caps_on_wide_terminals(monkeypatch):
    """A wide terminal must not make bars swallow the line: caps at 20 / 40."""
    monkeypatch.setattr(cli_reporter, "_is_tty", lambda _file: True)
    monkeypatch.setattr(
        cli_reporter.shutil,
        "get_terminal_size",
        lambda _default=None: os.terminal_size((200, 50)),
    )
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        task = reporter.register_instance("GDELT", total=4)

        assert "{bar:20}" in task._bar.bar_format
        assert "{bar:40}" in reporter.overall()._bar.bar_format


def test_bound_instance_prefixes_task_in_step_label():
    """A bound thread's unit lands on its instance bar with a task: step postfix."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        reporter.build_instances(
            [InstanceSpec("Instance 1"), InstanceSpec("Instance 2")]
        )

        with reporter.bind_instance("Instance 2") as bound:
            bar = reporter.register_instance("GDELT", total=5)
            bar.set_step("processing 1/5")

            assert bar is bound
            assert bar.task == "GDELT"
            assert bar._bar.desc == "Instance 2"
            assert bar._bar.postfix == "GDELT: processing 1/5"


def test_bind_instance_routes_threads_to_their_own_bars():
    """Two bound worker threads advance their own bars, never each other's."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        reporter.build_instances(
            [InstanceSpec("Instance 1"), InstanceSpec("Instance 2")]
        )

        def work(instance_name: str, count: int) -> None:
            with reporter.bind_instance(instance_name):
                bar = reporter.register_instance("GDELT", total=count)
                for _ in range(count):
                    bar.advance(1)

        threads = [
            threading.Thread(target=work, args=("Instance 1", 3)),
            threading.Thread(target=work, args=("Instance 2", 5)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert reporter.instance("Instance 1")._bar.n == 3
        assert reporter.instance("Instance 2")._bar.n == 5


def test_multi_mode_unknown_name_raises_keyerror():
    """Undeclared names fail loudly in multi mode instead of stacking bars."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        reporter.build_instances(
            [InstanceSpec("Instance 1"), InstanceSpec("Instance 2")]
        )

        with pytest.raises(KeyError):
            reporter.instance("GDELT")
        with pytest.raises(KeyError):
            with reporter.bind_instance("Instance 99"):
                pass


def test_build_instances_registers_every_spec_and_overall(monkeypatch):
    """build_instances creates one bar per spec plus the stickied overall bar."""
    writes = []
    monkeypatch.setattr(
        "src.cli_reporter.tqdm.write", lambda msg, file=None: writes.append(msg)
    )
    with CliReporter(file=io.StringIO()) as reporter:
        reporter.build_instances(
            [InstanceSpec("GDELT"), InstanceSpec("CyberScoop")],
            model_label="llama3.2:latest",
        )

        assert reporter.instance("GDELT") is not None
        assert reporter.instance("CyberScoop") is not None
        assert reporter.overall() is not None
    # Startup lines scroll above the bars.
    assert any("Building instances (2)" in w for w in writes)
    assert any("LLM Model loaded: llama3.2:latest" in w for w in writes)


def test_verbose_bar_annotates_model_endpoint():
    """Verbose mode ties an instance to its model endpoint in the bar label."""
    with CliReporter(file=io.StringIO(), disable=False, verbose=True) as reporter:
        bar = reporter.register_instance(
            "GDELT", model="llama3.2:latest", endpoint="http://localhost:11434"
        )

        assert "llama3.2:latest" in bar._bar.desc
        assert "http://localhost:11434" in bar._bar.desc


def test_bars_auto_disable_on_non_tty():
    """Bars stay inert when the target is not a real terminal (pipes/CI/tests)."""
    reporter = CliReporter(file=io.StringIO())  # disable defaults to auto
    bar = reporter.register_instance("GDELT", total=3)

    assert bar._bar.disable is True
    reporter.close()


# ---- log routing (above the bars) -------------------------------------


def test_log_info_and_error_write_above_bars(monkeypatch):
    """log/info/error all emit through tqdm.write so they scroll above bars."""
    writes = []
    monkeypatch.setattr(
        "src.cli_reporter.tqdm.write", lambda msg, file=None: writes.append(msg)
    )
    reporter = CliReporter(file=io.StringIO())

    reporter.info("hello")
    reporter.log("logged")
    reporter.error("boom")

    assert "hello" in writes
    assert "logged" in writes
    assert any("[ERROR] boom" in w for w in writes)
    reporter.close()


def test_detail_only_writes_when_verbose(monkeypatch):
    """Detail messages should only print when verbose mode is enabled."""
    writes = []
    monkeypatch.setattr(
        "src.cli_reporter.tqdm.write", lambda msg, file=None: writes.append(msg)
    )

    CliReporter(file=io.StringIO(), verbose=False).detail("hidden")
    assert writes == []

    CliReporter(file=io.StringIO(), verbose=True).detail("visible")
    assert "visible" in writes


def test_warning_is_hidden_by_default_but_counted(monkeypatch):
    """Default-mode warnings should increment stats without writing output."""
    writes = []
    monkeypatch.setattr(
        "src.cli_reporter.tqdm.write", lambda msg, file=None: writes.append(msg)
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
        "src.cli_reporter.tqdm.write", lambda msg, file=None: writes.append(msg)
    )
    reporter = CliReporter(file=io.StringIO(), verbose=True)
    stats = PipelineStats("test")

    reporter.warn("something happened", stats)

    assert any("[WARN] something happened" in w for w in writes)
    assert stats.warnings == 1


def test_error_always_writes_and_counts(monkeypatch):
    """Errors are always written and counted regardless of verbosity."""
    writes = []
    monkeypatch.setattr(
        "src.cli_reporter.tqdm.write", lambda msg, file=None: writes.append(msg)
    )
    reporter = CliReporter(file=io.StringIO())
    stats = PipelineStats("test")

    reporter.error("kaboom", stats)

    assert any("[ERROR] kaboom" in w for w in writes)
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
    """Return the summary table row that starts with ``label``."""
    return next(line for line in output.splitlines() if line.startswith(label))


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

    assert _summary_row(stream.getvalue(), "Time elapsed").endswith("1m 23s")


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
