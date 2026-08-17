"""Cooperative process-group execution for DagPlan adapters."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
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
    on_chunk: Callable[[Literal["stdout", "stderr"], str], None] | None = None,
) -> CancellableSubprocessResult:
    """Run one command and terminate its process group on cancellation or timeout.

    Output is drained by reader threads rather than a terminal ``communicate()``
    call, so partial output is observable while the command is still running.
    ``communicate(timeout=...)`` cannot expose partial data: it either returns
    everything at exit or raises ``TimeoutExpired`` and yields nothing, which
    made streaming impossible for every Tau node regardless of whether the
    underlying provider streamed. SciLLM streams by default, and that was being
    discarded here.

    ``on_chunk`` receives ``(stream_name, text)`` as bytes arrive. It runs on
    the reader thread, must not block, and must not raise; exceptions from it
    are suppressed so a misbehaving observer cannot fail the command or leak a
    partial result into the receipt. Reader threads also preserve the
    deadlock-avoidance property ``communicate()`` provided, because neither
    pipe can fill while the other is being consumed.
    """

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
    buffers: dict[str, list[str]] = {"stdout": [], "stderr": []}
    buffer_lock = threading.Lock()
    reader_threads: list[threading.Thread] = []

    def drain(name: Literal["stdout", "stderr"], stream: object) -> None:
        if stream is None:
            return
        # readline() rather than read(n): read(n) blocks until n characters or
        # EOF, which defeats incremental observation, while read(1) costs a
        # syscall per character. Subprocess text output is line-oriented, and so
        # is SSE, so a line is the natural streaming unit here.
        try:
            for chunk in iter(stream.readline, ""):  # type: ignore[attr-defined]
                if not chunk:
                    break
                with buffer_lock:
                    buffers[name].append(chunk)
                if on_chunk is not None:
                    with suppress(Exception):
                        on_chunk(name, chunk)
        except (OSError, ValueError):
            return
        finally:
            with suppress(OSError, ValueError):
                stream.close()  # type: ignore[attr-defined]

    for stream_name, stream_obj in (("stdout", process.stdout), ("stderr", process.stderr)):
        thread = threading.Thread(
            target=drain,
            args=(stream_name, stream_obj),
            daemon=True,
        )
        thread.start()
        reader_threads.append(thread)

    def collected() -> tuple[str, str]:
        with buffer_lock:
            return "".join(buffers["stdout"]), "".join(buffers["stderr"])

    started = time.monotonic()
    while True:
        cancelled = cancel_event is not None and cancel_event.is_set()
        timed_out = timeout_seconds is not None and time.monotonic() - started >= timeout_seconds
        if cancelled or timed_out:
            terminate_process_tree(process)
            for thread in reader_threads:
                thread.join(timeout=1)
            if stdin_thread is not None:
                stdin_thread.join(timeout=1)
            stdout, stderr = collected()
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
        if process.poll() is None:
            time.sleep(0.05)
            continue
        # Process exited: let the readers finish draining what is still buffered
        # in the pipes before assembling the final, receipt-bearing strings.
        for thread in reader_threads:
            thread.join(timeout=5)
        if stdin_thread is not None:
            stdin_thread.join(timeout=1)
        stdout, stderr = collected()
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
