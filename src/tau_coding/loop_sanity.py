"""Local sanity runner for Tau Loop2 receipt alignment."""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from pathlib import Path

from tau_agent import AgentEndEvent, AgentStartEvent, MessageDeltaEvent
from tau_coding.loop_monitor import check_loop_receipt_monitor_contract
from tau_coding.loop_receipt import LoopReceiptRecorder
from tau_coding.loop_validation import validate_loop_receipt_with_loop2_contracts


def run_loop2_sanity(
    *,
    root_dir: Path,
    repo: Path,
    loop2_src: Path | None = None,
) -> dict[str, object]:
    """Create one fixture receipt run and check its Loop2-shaped surfaces."""

    resolved_root = root_dir.expanduser().resolve()
    resolved_repo = repo.expanduser().resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)

    recorder = LoopReceiptRecorder.create(root_dir=resolved_root)
    check_command = _sanity_check_command()

    recorder.write_contract(
        node_id="tau-loop2-sanity",
        objective="Create and verify one fixture Tau Loop2 receipt run.",
        repo=resolved_repo,
        allowed_globs=["src/tau_coding/**", "tests/test_loop_*.py"],
        checks=[check_command],
        max_attempts=1,
        backend="fixture",
    )
    recorder.record(AgentStartEvent())
    recorder.record(MessageDeltaEvent(delta="tau loop2 sanity fixture run"))
    recorder.record(AgentEndEvent())
    recorder.emit_loop2_event("checks_started", node_id="tau-loop2-sanity", status="running")
    check = _run_sanity_check(recorder.run.run_dir, cwd=resolved_repo, command=check_command)
    check_ok = int(check["exit_code"]) == 0
    status = "PASS" if check_ok else "FAILED"
    recorder.emit_loop2_event(
        "check_finished",
        node_id="tau-loop2-sanity",
        status="completed" if check_ok else "failed",
        message=check_command,
        data=check,
    )
    recorder.emit_loop2_event(
        "checks_finished",
        node_id="tau-loop2-sanity",
        status="completed" if check_ok else "failed",
    )
    recorder.write_final_receipt(
        node_id="tau-loop2-sanity",
        mocked=True,
        live=False,
        status=status,
        provider="fixture",
        model="fixture",
        checks=[check],
        proof_scope="one fixture Tau Loop2 receipt sanity run",
        proves=[
            "Tau can create all six Loop2 receipt artifacts for one fixture run.",
            "Tau can run one local check and capture stdout/stderr artifacts.",
        ],
        does_not_prove=[
            "live provider behavior",
            "Scillm/OpenCode transport behavior",
            "semantic repair quality",
        ],
    )
    recorder.emit_loop2_event(
        "receipt_written",
        node_id="tau-loop2-sanity",
        status="completed" if status == "PASS" else "blocked",
    )
    recorder.write_transport_dag_evidence()
    recorder.write_node_result(
        node_id="tau-loop2-sanity",
        mocked=True,
        live=False,
        status=status,
        checks=[check],
    )

    validation = validate_loop_receipt_with_loop2_contracts(
        recorder.run.run_dir,
        loop2_src=loop2_src,
    )
    monitor = check_loop_receipt_monitor_contract(recorder.run.run_dir)
    return {
        "schema": "tau.loop2_sanity.v1",
        "ok": check_ok and validation.ok and monitor.ok,
        "run_dir": str(recorder.run.run_dir),
        "mocked": True,
        "live": False,
        "check": check,
        "loop2_contract_validation": {
            "ok": validation.ok,
            "checked_artifacts": list(validation.checked_artifacts),
            "errors": list(validation.errors),
        },
        "monitor_check": {
            "ok": monitor.ok,
            "checked_endpoints": list(monitor.checked_endpoints),
            "errors": list(monitor.errors),
        },
    }


def _run_sanity_check(run_dir: Path, *, cwd: Path, command: str) -> dict[str, object]:
    checks_dir = run_dir / "checks"
    checks_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = checks_dir / "sanity.stdout.txt"
    stderr_path = checks_dir / "sanity.stderr.txt"
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed_s = time.perf_counter() - started
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout_path": str(stdout_path.relative_to(run_dir)),
        "stderr_path": str(stderr_path.relative_to(run_dir)),
        "elapsed_s": elapsed_s,
    }


def _sanity_check_command() -> str:
    script = "print('tau loop2 sanity check')"
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
