"""Cooperative process-group execution for DagPlan adapters."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Event


def run_cancellable_subprocess(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
    cancel_event: Event | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one command and terminate its process group on cancellation or timeout."""

    argv = list(command)
    process = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    started = time.monotonic()
    while True:
        cancelled = cancel_event is not None and cancel_event.is_set()
        timed_out = (
            timeout_seconds is not None
            and time.monotonic() - started >= timeout_seconds
        )
        if cancelled or timed_out:
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            reason = (
                "command cancelled by DAG scheduler"
                if cancelled
                else f"timed out after {timeout_seconds:.1f}s"
            )
            stderr = f"{stderr.rstrip()}\n{reason}".lstrip()
            return subprocess.CompletedProcess(
                argv,
                130 if cancelled else 124,
                stdout=stdout,
                stderr=stderr,
            )
        try:
            stdout, stderr = process.communicate(timeout=0.05)
        except subprocess.TimeoutExpired:
            continue
        return subprocess.CompletedProcess(
            argv,
            process.returncode,
            stdout=stdout,
            stderr=stderr,
        )


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:  # pragma: no cover - Windows compatibility boundary.
        process.terminate()
    try:
        process.wait(timeout=0.5)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGKILL)
    else:  # pragma: no cover - Windows compatibility boundary.
        process.kill()
    process.wait(timeout=1)


__all__ = ["run_cancellable_subprocess"]
