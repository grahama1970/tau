"""Cooperative process-group execution for DagPlan adapters."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Literal


@dataclass(frozen=True, slots=True)
class CancellableSubprocessResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    termination_cause: Literal["exited", "cancelled", "timed_out"]
    stdin_delivery: Literal["not_requested", "confirmed", "indeterminate"]


def run_cancellable_subprocess(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    timeout_seconds: float | None = None,
    cancel_event: Event | None = None,
) -> CancellableSubprocessResult:
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
        stdin=subprocess.PIPE if input_text is not None else None,
        start_new_session=start_new_session,
        creationflags=creationflags,
    )
    stdin_delivery: Literal["not_requested", "confirmed", "indeterminate"] = (
        "not_requested" if input_text is None else "indeterminate"
    )
    stdin_thread: threading.Thread | None = None
    if input_text is not None:
        stdin_stream = process.stdin
        if stdin_stream is None:  # pragma: no cover - guarded by Popen arguments.
            raise RuntimeError("subprocess stdin pipe was not created")
        process.stdin = None

        def write_stdin() -> None:
            nonlocal stdin_delivery
            try:
                written = stdin_stream.write(input_text)
                stdin_stream.flush()
                if written == len(input_text):
                    stdin_delivery = "confirmed"
            except (BrokenPipeError, OSError):
                stdin_delivery = "indeterminate"
            finally:
                with suppress(BrokenPipeError, OSError):
                    stdin_stream.close()

        stdin_thread = threading.Thread(target=write_stdin, daemon=True)
        stdin_thread.start()
    started = time.monotonic()
    while True:
        cancelled = cancel_event is not None and cancel_event.is_set()
        timed_out = timeout_seconds is not None and time.monotonic() - started >= timeout_seconds
        if cancelled or timed_out:
            terminate_process_tree(process)
            stdout, stderr = process.communicate()
            if stdin_thread is not None:
                stdin_thread.join(timeout=1)
            reason = (
                "command cancelled by DAG scheduler"
                if cancelled
                else f"timed out after {timeout_seconds:.1f}s"
            )
            stderr = f"{stderr.rstrip()}\n{reason}".lstrip()
            return CancellableSubprocessResult(
                argv,
                130 if cancelled else 124,
                stdout=stdout,
                stderr=stderr,
                termination_cause="cancelled" if cancelled else "timed_out",
                stdin_delivery=stdin_delivery,
            )
        try:
            stdout, stderr = process.communicate(timeout=0.05)
        except subprocess.TimeoutExpired:
            continue
        if stdin_thread is not None:
            stdin_thread.join(timeout=1)
        return CancellableSubprocessResult(
            argv,
            process.returncode,
            stdout=stdout,
            stderr=stderr,
            termination_cause="exited",
            stdin_delivery=stdin_delivery,
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


__all__ = [
    "CancellableSubprocessResult",
    "process_group_options",
    "run_cancellable_subprocess",
    "terminate_process_tree",
]
