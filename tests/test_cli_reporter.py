import io

from src.cli_reporter import CliReporter, PipelineStats


def test_progress_uses_unicode_bar_by_default():
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.progress(1, 2)

    assert "Progress: [█████░░░░░] 50%" in stream.getvalue()


def test_progress_redraws_until_complete():
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.progress(1, 2, "items")
    reporter.progress(2, 2, "items")

    output = stream.getvalue()
    assert output.count("\n") == 1
    assert "\rProgress: [█████░░░░░] 50% items (1/2)" in output
    assert "\rProgress: [██████████] 100% items (2/2)" in output


def test_status_redraws_active_progress_line_below():
    """Interrupting messages print above the sticky bar; the bar is redrawn below."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.progress(1, 2, "items")
    reporter.status("done enough")

    output = stream.getvalue()
    # Message printed exactly once, sticky bar drawn before AND after it.
    assert output.count("done enough") == 1
    assert output.count("Progress: [█████░░░░░] 50% items (1/2)") == 2


def test_warning_is_hidden_by_default_but_counted():
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats("test")

    reporter.warn("something happened", stats)

    assert stream.getvalue() == ""
    assert stats.warnings == 1


def test_verbose_warning_prints_and_redraws_progress():
    stream = io.StringIO()
    reporter = CliReporter(verbose=True, stream=stream)

    reporter.progress(1, 2, "items")
    reporter.warn("something happened")

    output = stream.getvalue()
    assert "[WARN] something happened" in output
    # Bar redrawn once before the warning and once after.
    assert output.count("Progress: [█████░░░░░] 50% items (1/2)") == 2


def test_detail_only_prints_when_verbose():
    quiet_stream = io.StringIO()
    CliReporter(verbose=False, stream=quiet_stream).detail("hidden")

    verbose_stream = io.StringIO()
    CliReporter(verbose=True, stream=verbose_stream).detail("visible")

    assert "hidden" not in quiet_stream.getvalue()
    assert "visible" in verbose_stream.getvalue()


def test_info_prints_in_default_mode():
    stream = io.StringIO()
    CliReporter(verbose=False, stream=stream).info("important context")

    assert "important context" in stream.getvalue()


def test_tick_draws_sticky_counter_line():
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.tick("CyberScoop", page=2, processed=12, validated=1)
    reporter.tick("CyberScoop", page=2, processed=13, validated=1)
    reporter.finish_line()

    output = stream.getvalue()
    assert "\rCyberScoop | page=2 | processed=12 | validated=1" in output
    assert "\rCyberScoop | page=2 | processed=13 | validated=1" in output
    assert output.endswith("\n")


def test_summary_prints_core_counts():
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


def test_summary_treats_skipped_items_as_rejected():
    """Skipped items are still rejected, they should be counted in the rejection rate."""
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)
    stats = PipelineStats(
        "GDELT",
        processed=2,
        rejected=1,
        skipped=1,
    )

    reporter.summary(stats)

    output = stream.getvalue()
    assert "Rejection rate: 100%" in output
