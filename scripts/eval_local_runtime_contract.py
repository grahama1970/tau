#!/usr/bin/env python3
"""Agentic eval for the local runtime execution contract.

Covers the properties tests/test_dag_runtime_subprocess_control.py and
tests/test_local_runtime_backend.py guarded, as real_world checks that drive
real processes rather than asserting over mocks: cancellation, timeout,
process-group cleanup of grandchildren, stdin delivery, large-output
deadlock-freedom, and the capture/receipt invariants.

Every check observes an effect (exit code, killed pid, bytes on disk, receipt
field) rather than a status the component reports about itself.
"""

from __future__ import annotations

import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

from tau_coding.dag_runtime.model import FrozenJson
from tau_coding.dag_runtime.subprocess_control import run_cancellable_subprocess
from tau_coding.runtime_backends import LocalRuntimeBackend, local_runtime_request

failures: list[str] = []


def check(name: str, passed: bool, detail: str) -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'} ({detail})")
    if not passed:
        failures.append(name)


def check_cancellation() -> None:
    event = threading.Event()
    threading.Timer(0.6, event.set).start()
    start = time.monotonic()
    result = run_cancellable_subprocess(
        [sys.executable, "-c", "import time\nfor _ in range(50): print('x', flush=True); time.sleep(0.2)"],
        cancel_event=event,
    )
    elapsed = time.monotonic() - start
    check(
        "cancellation returns 130 and stops early",
        result.returncode == 130 and result.termination_cause == "cancelled" and elapsed < 5,
        f"rc={result.returncode} cause={result.termination_cause} elapsed={elapsed:.2f}s",
    )
    check(
        "cancellation records reason in stderr",
        "cancelled" in result.stderr,
        f"stderr tail={result.stderr.strip()[-60:]!r}",
    )


def check_timeout() -> None:
    start = time.monotonic()
    result = run_cancellable_subprocess(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=0.8,
    )
    elapsed = time.monotonic() - start
    check(
        "timeout returns 124 and stops early",
        result.returncode == 124 and result.termination_cause == "timed_out" and elapsed < 5,
        f"rc={result.returncode} cause={result.termination_cause} elapsed={elapsed:.2f}s",
    )
    check(
        "timeout records reason in stderr",
        "timed out" in result.stderr,
        f"stderr tail={result.stderr.strip()[-60:]!r}",
    )


def check_process_group_cleanup() -> None:
    """A cancelled command must take its grandchildren with it.

    Verified by reading the grandchild pid off disk and signalling it, not by
    trusting the terminate path's own report.
    """

    tmp = Path(tempfile.mkdtemp(prefix="tau-eval-pg-"))
    pidfile = tmp / "grandchild.pid"
    source = (
        "import subprocess, sys, time, pathlib\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    event = threading.Event()
    threading.Timer(1.2, event.set).start()
    run_cancellable_subprocess([sys.executable, "-c", source], cancel_event=event)

    if not pidfile.exists():
        check("process group cleanup kills grandchildren", False, "grandchild pid never recorded")
        return
    grandchild = int(pidfile.read_text().strip())
    time.sleep(0.5)
    alive = True
    try:
        os.kill(grandchild, 0)
    except (ProcessLookupError, PermissionError):
        alive = False
    if alive:  # do not leak a 60s sleeper if the contract regressed
        with __import__("contextlib").suppress(ProcessLookupError, PermissionError):
            os.kill(grandchild, signal.SIGKILL)
    check(
        "process group cleanup kills grandchildren",
        not alive,
        f"grandchild pid {grandchild} alive={alive}",
    )


def check_stdin_delivery() -> None:
    result = run_cancellable_subprocess(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().strip().upper())"],
        input_text="tau contract",
    )
    check(
        "stdin delivered and confirmed",
        result.stdin_delivery == "confirmed" and result.stdout.strip() == "TAU CONTRACT",
        f"delivery={result.stdin_delivery} stdout={result.stdout.strip()!r}",
    )


def check_large_output_no_deadlock() -> None:
    start = time.monotonic()
    result = run_cancellable_subprocess(
        [sys.executable, "-c", "print('x'*250000)"],
        timeout_seconds=20,
    )
    elapsed = time.monotonic() - start
    check(
        "large output does not deadlock",
        result.returncode == 0 and len(result.stdout) >= 250000 and elapsed < 15,
        f"rc={result.returncode} bytes={len(result.stdout)} elapsed={elapsed:.2f}s",
    )


def _spawn(backend: LocalRuntimeBackend, command: list[str], tmp: Path):
    request = local_runtime_request(
        command=command,
        run_id="eval-contract",
        plan_revision="plan-v1",
        dag_id="eval-contract",
        node_id="worker",
        attempt_id="worker:attempt-001",
        attempt_number=1,
        execution_token="token-1",
        work_order={"command": "eval-contract"},
        goal={"goal_id": "eval-contract"},
        cwd=tmp,
        artifact_dir=tmp / "runtime",
    )
    return backend.spawn(
        FrozenJson.from_value(
            {
                "run_id": request.run_id,
                "plan_revision": request.plan_revision,
                "dag_id": request.dag_id,
                "node_id": request.node_id,
                "attempt_id": request.attempt_id,
                "attempt_number": request.attempt_number,
                "execution_token": request.execution_token,
                "command": list(request.command),
                "cwd": str(request.cwd),
                "timeout_seconds": request.timeout_seconds,
                "work_order_sha256": request.work_order_sha256,
                "goal_hash": request.goal_hash,
                "artifact_dir": str(request.artifact_dir),
            }
        )
    )


def check_capture_receipt_invariants() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tau-eval-capture-"))
    backend = LocalRuntimeBackend()

    endpoint = _spawn(backend, [sys.executable, "-c", "print('done')"], tmp)

    not_started_raises = False
    try:
        backend.capture(endpoint, lines=10)
    except RuntimeError as exc:
        not_started_raises = "not_started" in str(exc)
    check(
        "capture on unstarted endpoint fails closed",
        not_started_raises,
        "raises local_runtime_endpoint_not_started rather than reporting empty output",
    )

    backend.submit(endpoint, FrozenJson.from_value({"work_order_sha256": endpoint.work_order_sha256}))
    payload = backend.capture(endpoint, lines=100).to_value()
    check(
        "completed capture marked complete with output",
        payload.get("complete") is True and "done" in str(payload.get("stdout", "")),
        f"complete={payload.get('complete')} stdout={str(payload.get('stdout','')).strip()!r}",
    )

    zero = backend.capture(endpoint, lines=0).to_value()
    check(
        "capture honours zero line bound",
        zero.get("stdout") == "" and zero.get("complete") is True,
        f"stdout={zero.get('stdout')!r}",
    )

    negative_raises = False
    try:
        backend.capture(endpoint, lines=-1)
    except ValueError:
        negative_raises = True
    check("capture rejects negative line bound", negative_raises, "raises ValueError")


def main() -> int:
    print("tau local runtime contract agentic eval")
    print()
    check_cancellation()
    check_timeout()
    check_process_group_cleanup()
    check_stdin_delivery()
    check_large_output_no_deadlock()
    check_capture_receipt_invariants()
    print()
    if failures:
        print(f"tau local runtime contract: FAIL ({len(failures)} failed: {', '.join(failures)})")
        return 1
    print("tau local runtime contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
