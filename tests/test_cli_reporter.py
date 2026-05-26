import io

from src.cli_reporter import CliReporter, PipelineStats


def test_progress_uses_unicode_bar_by_default():
    stream = io.StringIO()
    reporter = CliReporter(stream=stream)

    reporter.progress(1, 2)

    assert "Progress: [█████░░░░░] 50%" in stream.getvalue()


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
