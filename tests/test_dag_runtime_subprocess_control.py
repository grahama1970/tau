"""Live local process-group cancellation tests for DagPlan adapters."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from subprocess import CompletedProcess

import tau_coding.dag_runtime.subprocess_control as subprocess_control
from tau_coding.dag_runtime.subprocess_control import run_cancellable_subprocess


def test_scheduler_cancellation_terminates_command_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "child-finished"
    child = (
        "import pathlib,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(0.8); "
        f"pathlib.Path({str(marker)!r}).write_text('finished')"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(10)"
    )
    cancel_event = threading.Event()
    timer = threading.Timer(0.1, cancel_event.set)
    timer.start()
    started = time.monotonic()
    try:
        result = run_cancellable_subprocess(
            [sys.executable, "-c", parent],
            timeout_seconds=5,
            cancel_event=cancel_event,
        )
    finally:
        timer.cancel()
    elapsed = time.monotonic() - started

    assert result.returncode == 130
    assert result.termination_cause == "cancelled"
    assert "cancelled by DAG scheduler" in result.stderr
    assert elapsed < 1
    time.sleep(0.9)
    assert not marker.exists()


def test_timeout_terminates_command_process_group() -> None:
    started = time.monotonic()
    result = run_cancellable_subprocess(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        timeout_seconds=0.1,
    )

    assert result.returncode == 124
    assert result.termination_cause == "timed_out"
    assert "timed out after 0.1s" in result.stderr
    assert time.monotonic() - started < 1


def test_explicit_timeout_style_exit_codes_are_not_termination_causes() -> None:
    for returncode in (124, 130):
        result = run_cancellable_subprocess(
            [sys.executable, "-c", f"raise SystemExit({returncode})"],
            timeout_seconds=5,
        )

        assert result.returncode == returncode
        assert result.termination_cause == "exited"


def test_child_exit_without_stdin_read_is_indeterminate_without_thread_error() -> None:
    result = run_cancellable_subprocess(
        [sys.executable, "-c", "raise SystemExit(0)"],
        input_text="x" * 1_000_000,
        timeout_seconds=5,
    )

    assert result.returncode == 0
    assert result.stdin_delivery == "indeterminate"


def test_windows_process_tree_uses_new_group_and_taskkill(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    class Process:
        pid = 4321

        def poll(self):
            return 1

        def terminate(self):
            raise AssertionError("taskkill result should not fall back to parent-only termination")

    monkeypatch.setattr(subprocess_control.os, "name", "nt")
    monkeypatch.setattr(
        subprocess_control.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or CompletedProcess(command, 0),
    )

    assert subprocess_control.process_group_options() == (False, 0x00000200)
    subprocess_control._terminate_windows_process_tree(Process())  # type: ignore[arg-type]

    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]
