"""Cooperative process-group execution for DagPlan adapters."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
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
    start_new_session, creationflags = process_group_options()
    process = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=start_new_session,
        creationflags=creationflags,
    )
    started = time.monotonic()
    while True:
        cancelled = cancel_event is not None and cancel_event.is_set()
        timed_out = (
            timeout_seconds is not None
            and time.monotonic() - started >= timeout_seconds
        )
        if cancelled or timed_out:
            terminate_process_tree(process)
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


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate the command and all descendants within its isolated process group."""

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        time.sleep(0.5)
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        if process.poll() is None:
            process.wait(timeout=1)
        return
    if process.poll() is None:  # pragma: no cover - platform boundary.
        _terminate_windows_process_tree(process)
        process.wait(timeout=1)


def process_group_options() -> tuple[bool, int]:
    if os.name == "posix":
        return True, 0
    return False, getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def _terminate_windows_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate the Windows process tree rooted at the command process."""

    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if process.poll() is None:
        process.terminate()


__all__ = ["process_group_options", "run_cancellable_subprocess", "terminate_process_tree"]
