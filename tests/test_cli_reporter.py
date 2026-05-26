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


def test_print_finishes_active_progress_line():
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.progress(1, 2, "items")
    reporter.info("done enough")

    assert stream.getvalue().endswith("items (1/2)\ndone enough\n")


def test_warning_under_active_progress_is_indented():
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.progress(1, 2, "items")
    reporter.warn("something happened")

    assert stream.getvalue().endswith("items (1/2)\n\t[WARN] something happened")


def test_progress_after_warning_repaints_original_bar_line():
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.progress(1, 3, "items")
    reporter.warn("something happened")
    reporter.progress(2, 3, "items")

    output = stream.getvalue()
    assert "\n\t[WARN] something happened" in output
    assert "\033[1A\rProgress: [██████░░░░] 67% items (2/3)\033[1B\r" in output


def test_detail_only_prints_when_verbose():
    quiet_stream = io.StringIO()
    CliReporter(verbose=False, stream=quiet_stream).detail("hidden")

    verbose_stream = io.StringIO()
    CliReporter(verbose=True, stream=verbose_stream).detail("visible")

    assert "hidden" not in quiet_stream.getvalue()
    assert "visible" in verbose_stream.getvalue()


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
