import io
import logging

from src.cli_reporter import (
    CliReporter,
    CliReporterLoggingHandler,
    InstanceSpec,
    PipelineStats,
    get_active_reporter,
    set_active_reporter,
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


def test_multiple_instances_and_overall_advance_independently():
    """Each instance bar plus the overall bar track their own counts."""
    with CliReporter(file=io.StringIO(), disable=False) as reporter:
        gdelt = reporter.register_instance("GDELT", total=10)
        site = reporter.register_instance("CyberScoop", total=5)
        reporter.set_overall_total(2)

        gdelt.advance(3)
        site.advance(1)
        reporter.advance_overall(1)

        assert gdelt._bar.n == 3
        assert site._bar.n == 1
        assert reporter.overall()._bar.n == 1


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


# ---- summary / stats (preserved contract) -----------------------------


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
    assert "GDELT:" in output
    assert "Discovered:     4" in output
    assert "Rejection rate: 50%" in output


def test_summary_prints_paused_state():
    """Paused stats should render an explicit paused line in the summary."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats("GDELT", paused=True)

    reporter.summary(stats)

    output = stream.getvalue()
    assert "GDELT:" in output
    assert "Paused:         yes" in output


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

    assert "Rejection rate: 67%" in stream.getvalue()


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

    assert "Rejection rate: 93%" in stream.getvalue()


def test_summary_rejection_rate_handles_no_outcomes():
    """No terminal outcomes should report 0% instead of dividing by zero."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats("Empty", processed=0)

    reporter.summary(stats)

    assert "Rejection rate: 0%" in stream.getvalue()


def test_summary_rejection_rate_handles_all_skipped_items():
    """All-skipped runs should be bounded and avoid the old zero denominator."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats("Skipped", processed=3, skipped=3)

    reporter.summary(stats)

    assert "Rejection rate: 100%" in stream.getvalue()


def test_summary_accepts_a_list_of_stats():
    """A list of stats prints each pipeline's section under one summary header."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.summary([PipelineStats("GDELT", discovered=2), PipelineStats("HTML")])

    output = stream.getvalue()
    assert "=== Run Summary ===" in output
    assert "GDELT:" in output
    assert "HTML:" in output
