"""Tests for cooperative shutdown signalling and early-exit behaviour."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from src.cli_reporter import PipelineStats
from src.classes import Vulnerability
from src.shared_utils import (
    _shutdown,
    collect_as_completed,
    exit_if_shutdown,
    pause_if_shutdown,
    request_pause,
    shutdown_executor,
)


def _make_vuln(vuln_id: str = "saved-record") -> Vulnerability:
    return Vulnerability(
        id=vuln_id,
        title="Saved Article",
        source_name="test",
        direct_link=f"https://example.com/{vuln_id}",
        subsector="drug_shortage",
        date_accessed="2024-01-01 00:00",
        date_published="2023-05-15",
        content="content",
    )


def test_request_pause_sets_flag_and_event():
    stats = PipelineStats("test")
    request_pause(stats)
    assert stats.paused is True
    assert _shutdown.is_set()


def test_pause_if_shutdown():
    stats = PipelineStats("test")
    assert pause_if_shutdown(stats) is False
    _shutdown.set()
    assert pause_if_shutdown(stats) is True
    assert stats.paused is True


def test_collect_as_completed_returns_promptly_on_shutdown():
    """Shutdown should not wait for slow in-flight workers."""
    results: list[str] = []
    slow_started = threading.Event()

    def slow():
        slow_started.set()
        time.sleep(2)
        return "slow"

    executor = ThreadPoolExecutor(max_workers=1)
    futures = [executor.submit(slow)]
    assert slow_started.wait(timeout=2)
    _shutdown.set()
    started = time.monotonic()
    try:
        collect_as_completed(futures, results.append)
    finally:
        shutdown_executor(executor)

    assert time.monotonic() - started < 0.5
    assert results == []


def test_collect_as_completed_harvests_finished_workers_on_shutdown():
    """Completed worker output must be kept when shutdown stops the pool early."""
    results: list[str] = []
    ready = threading.Event()

    def fast():
        ready.set()
        return "finished"

    def slow():
        ready.wait(timeout=2)
        time.sleep(2)
        return "slow"

    executor = ThreadPoolExecutor(max_workers=2)
    futures = [executor.submit(fast), executor.submit(slow)]
    assert ready.wait(timeout=2)
    _shutdown.set()
    try:
        collect_as_completed(futures, results.append)
    finally:
        shutdown_executor(executor)

    assert results == ["finished"]


def test_shutdown_executor_skips_wait_when_shutdown_requested():
    started = threading.Event()
    release = threading.Event()

    def work():
        started.set()
        release.wait(timeout=2)
        return "done"

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(work)
    assert started.wait(timeout=2)
    _shutdown.set()
    began = time.monotonic()
    shutdown_executor(executor)
    assert time.monotonic() - began < 0.5
    release.set()
    assert future.result(timeout=2) == "done"


def test_exit_if_shutdown_returns_code_when_not_shutdown():
    assert exit_if_shutdown(0) == 0
    assert exit_if_shutdown(1) == 1


def test_exit_if_shutdown_calls_os_exit_when_shutdown_requested():
    _shutdown.set()
    with patch("src.shared_utils.os._exit") as mock_exit:
        exit_if_shutdown(0)
    mock_exit.assert_called_once_with(0)


def test_orchestrator_writes_partial_gdelt_output_on_shutdown(tmp_path):
    """Ctrl-C style shutdown should persist records from workers that already finished."""
    import src.orchestrator as orchestrator

    saved = _make_vuln("worker-one")
    run_lock = threading.Lock()
    run_count = 0

    def fake_run(*_args, **kwargs):
        nonlocal run_count
        with run_lock:
            run_count += 1
            call_no = run_count
        stats = kwargs["stats"]
        if call_no == 1:
            stats.validated = 1
            return stats, [saved]
        request_pause(stats)
        time.sleep(2)
        return stats, []

    def trigger_shutdown_once_first_worker_finishes():
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with run_lock:
                if run_count >= 1:
                    request_pause(PipelineStats("shutdown-watcher"))
                    return
            time.sleep(0.01)
        pytest.fail("timed out waiting for first GDELT worker to finish")

    seeds = [
        {"url": "https://example.com/0", "source": "test"},
        {"url": "https://example.com/1", "source": "test"},
    ]
    output_file = tmp_path / "results.json"

    with (
        patch("src.orchestrator.ensure_model_available"),
        patch(
            "src.orchestrator.get_config_bool",
            side_effect=lambda _name, default=False: default,
        ),
        patch(
            "src.orchestrator.get_config_int",
            side_effect=lambda _name, default=None: default,
        ),
        patch(
            "src.orchestrator.get_config_value",
            side_effect=lambda _name, default=None: default,
        ),
        patch("src.orchestrator._collect_gdelt_seeds", return_value=seeds),
        patch("src.GDELT.runner.run", side_effect=fake_run),
        patch("src.cli_reporter.CliReporter.summary"),
        patch("src.orchestrator.exit_if_shutdown", side_effect=lambda code: code),
    ):
        watcher = threading.Thread(
            target=trigger_shutdown_once_first_worker_finishes,
            daemon=True,
        )
        watcher.start()
        result = orchestrator.main(
            [
                "--skip-html",
                "--models",
                "2",
                "--threads-per-model",
                "1",
                "-o",
                str(output_file),
            ]
        )
        watcher.join(timeout=2)

    assert result == 0
    written = json.loads(output_file.read_text(encoding="utf-8"))
    assert [record["id"] for record in written["sources"]] == ["worker-one"]
