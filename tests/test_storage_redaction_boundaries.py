from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from tau_agent import AssistantMessage, MessageEntry, ToolCall, ToolResultMessage, UserMessage
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.handoff_dispatch import write_agent_handoff_command_dispatch_receipt
from tau_coding.session_export import export_session_html, export_session_jsonl


def test_dispatch_storage_boundaries_redact_command_stdout_and_runtime_capture(
    tmp_path: Path,
    monkeypatch,
) -> None:
    secret = "sk-issue174-abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setenv("TAU_ISSUE_174_SECRET", secret)
    receipt_dir = tmp_path / "dispatch"

    dispatch = write_agent_handoff_command_dispatch_receipt(
        _start_payload(),
        _secret_echo_command(secret),
        receipt_dir,
        timeout_s=10,
        active_goal_hash=str(_goal()["goal_hash"]),
    )

    assert dispatch.status == "COMPLETED"
    assert _tree_contains(receipt_dir, "runtime-capture.json")
    assert _tree_contains(receipt_dir, "dispatch-receipt.json")
    assert not _tree_contains_text(receipt_dir, secret)
    capture_paths = list(receipt_dir.rglob("runtime-capture.json"))
    assert capture_paths
    capture = json.loads(capture_paths[0].read_text(encoding="utf-8"))
    assert "stdout carried [REDACTED:SECRET_TOKEN]" in capture["stdout"]
    assert "--token=[REDACTED]" in " ".join(capture["command"])

    dispatch_receipt = json.loads((receipt_dir / "dispatch-receipt.json").read_text())
    serialized_dispatch = json.dumps(dispatch_receipt, sort_keys=True)
    assert secret not in serialized_dispatch
    assert "[REDACTED:SECRET_TOKEN]" in serialized_dispatch


def test_sqlite_run_store_redacts_staged_command_result(tmp_path: Path) -> None:
    secret = "sk-issue174-abcdefghijklmnopqrstuvwxyz"
    database = tmp_path / "dag-run.sqlite3"
    with SqliteDagRunStore(database) as store:
        plan = _plan(tmp_path)
        lease = store.acquire_run(
            plan=plan,
            run_id="issue-174-run",
            owner_id="tester",
        )
        attempt = store.reserve_attempt(
            lease,
            plan_sha256=plan.plan_sha256,
            node_id="worker",
            attempt=1,
        )
        store.mark_dispatched(lease, attempt.attempt_id)
        staged = store.stage_result(
            lease,
            attempt.attempt_id,
            {
                "schema": "tau.agent_dispatch_result.v1",
                "command_results": [
                    {
                        "command": ["curl", f"--token={secret}"],
                        "stdout": f"Bearer {secret}",
                        "stderr": f"password={secret}",
                    }
                ],
            },
        )

    assert secret not in json.dumps(staged, sort_keys=True)
    with sqlite3.connect(database) as connection:
        output_rows = connection.execute("SELECT staged_json FROM dag_attempt_outputs").fetchall()
        event_rows = connection.execute("SELECT payload_json FROM dag_run_events").fetchall()
    serialized_rows = "\n".join(row[0] for row in [*output_rows, *event_rows])
    assert secret not in serialized_rows
    assert "[REDACTED]" in serialized_rows


def test_session_exports_redact_tool_arguments_results_and_messages(tmp_path: Path) -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    entries = [
        MessageEntry(id="user", message=UserMessage(content=f"please use token={secret}")),
        MessageEntry(
            id="assistant",
            parent_id="user",
            message=AssistantMessage(
                content=f"I would call with Authorization: Bearer {secret}",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="fetch",
                        arguments={"command": ["curl", f"--token={secret}"]},
                    )
                ],
            ),
        ),
        MessageEntry(
            id="tool",
            parent_id="assistant",
            message=ToolResultMessage(
                tool_call_id="call-1",
                name="fetch",
                content=f"stdout api_key={secret}",
                ok=True,
                data={"stdout": f"Bearer {secret}"},
                details={"stderr": f"password={secret}"},
                error=f"client_secret={secret}",
            ),
        ),
    ]
    jsonl_path = tmp_path / "session.jsonl"
    html_path = tmp_path / "session.html"

    export_session_jsonl(entries, jsonl_path)
    export_session_html(entries, html_path)

    jsonl = jsonl_path.read_text(encoding="utf-8")
    html = html_path.read_text(encoding="utf-8")
    assert secret not in jsonl
    assert secret not in html
    assert "[REDACTED" in jsonl
    assert "[REDACTED" in html


def _secret_echo_command(command_secret: str) -> list[str]:
    response = {
        "schema": "tau.agent_handoff.v1",
        "github": _target(),
        "goal": _goal(),
        "previous_subagent": "coder",
        "context": {
            "summary": "stdout carried ${secret}",
            "artifacts": [],
        },
        "result": {
            "status": "PASS",
            "summary": "handler completed with ${secret}",
            "evidence": [{"kind": "handler_receipt", "path": "handler.json"}],
        },
        "rationale": "The local command produced the handoff.",
        "next_agent": {
            "name": "human",
            "executor": "human",
            "reason": "Stop at human.",
        },
        "required_evidence": ["handler_receipt"],
        "stop_condition": "Stop at human.",
    }
    code = (
        "import json, os; "
        "secret=os.environ['TAU_ISSUE_174_SECRET']; "
        f"payload={json.dumps(response)!r}; "
        "print(payload.replace('${secret}', secret))"
    )
    return [sys.executable, "-c", code, f"--token={command_secret}"]


def _tree_contains(root: Path, filename: str) -> bool:
    return any(path.name == filename for path in root.rglob("*"))


def _tree_contains_text(root: Path, needle: str) -> bool:
    for path in root.rglob("*"):
        if path.is_file() and needle in path.read_bytes().decode("utf-8", errors="ignore"):
            return True
    return False


def _start_payload() -> dict[str, object]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": _target(),
        "goal": _goal(),
        "previous_subagent": "human",
        "context": {"summary": "start", "artifacts": []},
        "result": {
            "status": "PASS",
            "summary": "start",
            "evidence": [{"kind": "start", "path": "start.json"}],
        },
        "rationale": "Dispatch the handler.",
        "next_agent": {
            "name": "coder",
            "executor": "local",
            "reason": "Run the local coder.",
        },
        "required_evidence": ["handler_receipt"],
        "stop_condition": "Stop at human.",
    }


def _plan(tmp_path: Path):
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "issue-174-run",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "worker",
                    "role": "worker",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "worker.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )


def _goal() -> dict[str, object]:
    return {
        "goal_id": "issue-174-storage-redaction",
        "goal_version": 1,
        "goal_hash": "sha256:issue-174-goal",
    }


def _target() -> dict[str, str]:
    return {"repo": "grahama1970/tau", "target": "issue-174-storage-redaction"}
