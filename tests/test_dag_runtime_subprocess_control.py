"""Live local process-group cancellation tests for DagPlan adapters."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from tau_coding.dag_runtime.subprocess_control import run_cancellable_subprocess


def test_scheduler_cancellation_terminates_command_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "child-finished"
    child = (
        "import pathlib,time; time.sleep(0.8); "
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
    assert "timed out after 0.1s" in result.stderr
    assert time.monotonic() - started < 1
