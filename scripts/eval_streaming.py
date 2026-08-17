#!/usr/bin/env python3
"""Agentic eval: Tau must stream node output, not only report it at exit.

This exists because Tau's pytest suite cannot catch the regression it guards.
All 32 tests across test_dag_runtime_subprocess_control.py,
test_local_runtime_backend.py and test_skill_dag_adapter.py passed identically
before and after streaming was implemented, because they assert only on final
output. Reverting run_cancellable_subprocess to process.communicate() would
leave every one of them green while streaming silently died.

Each check therefore asserts a property that is false when output is only
available at process exit. Check 5 is a negative control: it runs the same
assertion against a deliberately non-streaming baseline and requires it to
FAIL, so a green result here cannot mean the assertion stopped discriminating.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from tau_coding.dag_runtime.model import FrozenJson
from tau_coding.dag_runtime.subprocess_control import run_cancellable_subprocess
from tau_coding.runtime_backends import LocalRuntimeBackend, local_runtime_request

EMIT_COUNT = 5
EMIT_INTERVAL = 0.4
# A streamed first chunk must precede exit by a real margin, not a scheduling
# accident. The emitter runs about EMIT_COUNT * EMIT_INTERVAL seconds.
MIN_LEAD_SECONDS = 1.0

failures: list[str] = []


def emitter_source(marker: Path | None = None) -> str:
    lines = ["import time"]
    if marker is not None:
        lines += ["import pathlib", f"pathlib.Path({str(marker)!r}).touch()"]
    for index in range(EMIT_COUNT):
        lines.append(f"print('chunk{index}', flush=True); time.sleep({EMIT_INTERVAL})")
    return "\n".join(lines)


def check(name: str, passed: bool, detail: str) -> None:
    print(f"{name}: {'PASS' if passed else 'FAIL'} ({detail})")
    if not passed:
        failures.append(name)


def check_streaming_before_exit() -> None:
    arrivals: list[float] = []
    start = time.monotonic()
    result = run_cancellable_subprocess(
        [sys.executable, "-c", emitter_source()],
        on_chunk=lambda _stream, _text: arrivals.append(time.monotonic() - start),
    )
    total = time.monotonic() - start
    lead = (total - arrivals[0]) if arrivals else 0.0
    check(
        "streaming observable before exit",
        bool(arrivals) and lead >= MIN_LEAD_SECONDS,
        f"{len(arrivals)} chunks, first at {arrivals[0]:.2f}s of {total:.2f}s, lead {lead:.2f}s",
    )
    check(
        "final stdout intact",
        result.stdout.split() == [f"chunk{i}" for i in range(EMIT_COUNT)],
        f"{len(result.stdout.split())} lines, rc={result.returncode}",
    )


def check_chunks_arrive_progressively() -> None:
    arrivals: list[float] = []
    start = time.monotonic()
    run_cancellable_subprocess(
        [sys.executable, "-c", emitter_source()],
        on_chunk=lambda _stream, _text: arrivals.append(time.monotonic() - start),
    )
    spread = (arrivals[-1] - arrivals[0]) if len(arrivals) > 1 else 0.0
    check(
        "chunks arrive progressively",
        spread >= MIN_LEAD_SECONDS,
        f"first {arrivals[0]:.2f}s last {arrivals[-1]:.2f}s spread {spread:.2f}s",
    )


def check_capture_mid_run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tau-eval-streaming-"))
    marker = tmp / "started"
    request = local_runtime_request(
        command=[sys.executable, "-c", emitter_source(marker)],
        run_id="eval-streaming",
        plan_revision="plan-v1",
        dag_id="eval-streaming",
        node_id="worker",
        attempt_id="worker:attempt-001",
        attempt_number=1,
        execution_token="token-1",
        work_order={"command": "eval-streaming"},
        goal={"goal_id": "eval-streaming"},
        cwd=tmp,
        artifact_dir=tmp / "runtime",
    )
    backend = LocalRuntimeBackend()
    endpoint = backend.spawn(
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
    worker = threading.Thread(
        target=lambda: backend.submit(
            endpoint, FrozenJson.from_value({"work_order_sha256": endpoint.work_order_sha256})
        )
    )
    worker.start()

    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    counts: list[int] = []
    complete_flags: list[bool] = []
    for _ in range(4):
        time.sleep(0.5)
        if not worker.is_alive():
            break
        payload = backend.capture(endpoint, lines=100).to_value()
        complete_flags.append(bool(payload.get("complete")))
        counts.append(len([ln for ln in str(payload.get("stdout", "")).splitlines() if ln]))

    worker.join(timeout=20)
    final = backend.capture(endpoint, lines=100).to_value()
    final_lines = [ln for ln in str(final.get("stdout", "")).splitlines() if ln]

    check(
        "capture answers mid run",
        bool(counts) and counts[0] < EMIT_COUNT,
        f"mid-run line counts {counts}",
    )
    check(
        "capture grows while running",
        counts == sorted(counts) and (not counts or counts[-1] >= counts[0]),
        f"line counts {counts}",
    )
    check(
        "partial never marked complete",
        all(flag is False for flag in complete_flags),
        f"complete flags {complete_flags}",
    )
    check(
        "final capture marked complete and intact",
        final.get("complete") is True and final_lines == [f"chunk{i}" for i in range(EMIT_COUNT)],
        f"complete={final.get('complete')} lines={len(final_lines)}",
    )


def check_negative_control() -> None:
    """The streaming assertion must FAIL against a non-streaming baseline.

    Without this, a broken assertion that always passes would look like proof
    that streaming works.
    """

    start = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-c", emitter_source()],
        capture_output=True,
        text=True,
        check=False,
    )
    total = time.monotonic() - start
    # subprocess.run exposes output only at exit, so the earliest any consumer
    # could observe a chunk is `total`, giving a lead of zero.
    lead = 0.0
    baseline_would_pass = lead >= MIN_LEAD_SECONDS
    check(
        "negative control rejects non-streaming baseline",
        not baseline_would_pass and bool(completed.stdout),
        f"batch lead {lead:.2f}s < required {MIN_LEAD_SECONDS:.2f}s over {total:.2f}s run",
    )


def main() -> int:
    print("tau streaming agentic eval")
    print(f"emitter: {EMIT_COUNT} lines every {EMIT_INTERVAL}s, required lead {MIN_LEAD_SECONDS}s")
    print()
    check_streaming_before_exit()
    check_chunks_arrive_progressively()
    check_capture_mid_run()
    check_negative_control()
    print()
    if failures:
        print(f"tau streaming eval: FAIL ({len(failures)} failed: {', '.join(failures)})")
        return 1
    print("tau streaming eval: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
