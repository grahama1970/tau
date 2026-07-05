import hashlib
import json
import sys
from pathlib import Path

from tau_coding.generic_dag import (
    GENERIC_DAG_NODE_RECEIPT_SCHEMA,
    GENERIC_DAG_SPEC_SCHEMA,
    inspect_generic_dag_run,
    resume_generic_dag_from_run,
    run_generic_dag,
)


def test_generic_dag_runs_dependency_ordered_subprocess_workers(tmp_path: Path) -> None:
    spec_path = _write_spec(
        tmp_path,
        [
            _node(tmp_path, "planner"),
            _node(tmp_path, "coder", depends_on=["planner"]),
            _node(tmp_path, "reviewer", depends_on=["coder"]),
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["schema"] == "tau.generic_dag_run_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is False
    assert receipt["provider_live"] is False
    assert receipt["node_count"] == 3
    assert receipt["completed_node_count"] == 3
    assert receipt["resume_requested"] is True
    assert receipt["resume_source"] == {
        "mode": "spec_path",
        "spec_path": str(spec_path.resolve()),
    }
    assert Path(str(receipt["checkpoint_path"])).exists()
    assert Path(str(receipt["current_state_path"])).exists()
    assert [node["node_id"] for node in receipt["nodes"]] == ["planner", "coder", "reviewer"]
    assert all(node["status"] == "PASS" for node in receipt["nodes"])
    checkpoint = json.loads(Path(str(receipt["checkpoint_path"])).read_text(encoding="utf-8"))
    assert checkpoint["schema"] == "tau.generic_dag_checkpoint.v1"
    assert checkpoint["status"] == "PASS"
    assert checkpoint["spec_path"] == str(spec_path.resolve())
    assert checkpoint["completed_nodes"] == ["coder", "planner", "reviewer"]
    assert checkpoint["ready_nodes"] == []
    assert checkpoint["blocked_nodes"] == []

    events = _read_events(Path(str(receipt["events_jsonl"])))
    dispatch_order = [event["node_id"] for event in events if event["kind"] == "node_dispatch"]
    validated_order = [
        event["node_id"] for event in events if event["kind"] == "node_receipt_validated"
    ]
    assert dispatch_order == ["planner", "coder", "reviewer"]
    assert validated_order == ["planner", "coder", "reviewer"]


def test_generic_dag_fails_closed_on_invalid_node_receipt(tmp_path: Path) -> None:
    bad_receipt = tmp_path / "receipts" / "bad.json"
    spec_path = _write_spec(
        tmp_path,
        [
            {
                **_node(tmp_path, "bad"),
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        f"Path({str(bad_receipt)!r}).parent.mkdir(parents=True, exist_ok=True); "
                        f"Path({str(bad_receipt)!r}).write_text('{{\"schema\":\"wrong\"}}')"
                    ),
                ],
            },
            _node(tmp_path, "downstream", depends_on=["bad"]),
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "INVALID_RECEIPT"
    assert receipt["completed_node_count"] == 0
    assert [node["node_id"] for node in receipt["nodes"]] == ["bad"]
    assert "schema must be tau.generic_dag_node_receipt.v1" in receipt["nodes"][0]["errors"]
    checkpoint = json.loads(Path(str(receipt["checkpoint_path"])).read_text(encoding="utf-8"))
    assert checkpoint["status"] == "BLOCKED"
    assert checkpoint["verdict"] == "INVALID_RECEIPT"
    assert checkpoint["blocked_nodes"] == ["bad"]
    assert checkpoint["ready_nodes"] == []


def test_generic_dag_resumes_from_existing_valid_receipts(tmp_path: Path) -> None:
    planner_receipt = tmp_path / "receipts" / "planner.json"
    planner_receipt.parent.mkdir(parents=True, exist_ok=True)
    _write_node_receipt(planner_receipt, node_id="planner")
    spec_path = _write_spec(
        tmp_path,
        [
            _node(
                tmp_path,
                "planner",
                command=[
                    sys.executable,
                    "-c",
                    "raise SystemExit('planner command should have been resumed')",
                ],
            ),
            _node(tmp_path, "coder", depends_on=["planner"]),
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path, resume=True)

    assert receipt["ok"] is True
    assert receipt["nodes"][0]["node_id"] == "planner"
    assert receipt["nodes"][0]["resumed"] is True
    assert receipt["nodes"][0]["attempt_count"] == 0
    assert receipt["nodes"][1]["node_id"] == "coder"
    assert receipt["nodes"][1]["resumed"] is False


def test_generic_dag_resumes_from_run_directory_metadata(tmp_path: Path) -> None:
    planner_receipt = tmp_path / "receipts" / "planner.json"
    planner_receipt.parent.mkdir(parents=True, exist_ok=True)
    _write_node_receipt(planner_receipt, node_id="planner")
    spec_path = _write_spec(
        tmp_path,
        [
            _node(
                tmp_path,
                "planner",
                command=[
                    sys.executable,
                    "-c",
                    "raise SystemExit('planner command should have been recovered')",
                ],
            ),
            _node(tmp_path, "coder", depends_on=["planner"]),
        ],
    )
    first = run_generic_dag(spec_path=spec_path, resume=True)

    second = resume_generic_dag_from_run(Path(str(first["run_dir"])))

    assert second["ok"] is True
    assert second["nodes"][0]["node_id"] == "planner"
    assert second["nodes"][0]["resumed"] is True
    assert second["nodes"][0]["attempt_count"] == 0
    assert second["nodes"][1]["node_id"] == "coder"
    assert second["nodes"][1]["resumed"] is True
    assert second["resume_source"] == {
        "mode": "run_metadata",
        "run_dir": str(Path(str(first["run_dir"])).resolve()),
        "metadata_path": str(Path(str(first["run_dir"])).resolve() / "current-state.json"),
        "spec_path": str(spec_path.resolve()),
    }


def test_generic_dag_accepts_matching_work_order_hash(tmp_path: Path) -> None:
    work_order = tmp_path / "work-orders" / "planner.json"
    work_order.parent.mkdir(parents=True, exist_ok=True)
    work_order.write_text('{"task":"plan current work"}\n', encoding="utf-8")
    receipt_path = tmp_path / "receipts" / "planner.json"
    spec_path = _write_spec(
        tmp_path,
        [
            _node(
                tmp_path,
                "planner",
                work_order_path=work_order,
                command=[
                    sys.executable,
                    "-c",
                    _receipt_writer_code(
                        receipt_path,
                        node_id="planner",
                        work_order_path=work_order,
                    ),
                ],
            )
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["ok"] is True
    assert receipt["nodes"][0]["status"] == "PASS"
    assert receipt["nodes"][0]["work_order_sha256"] == _sha256(work_order)


def test_generic_dag_rejects_stale_work_order_receipt_on_resume(tmp_path: Path) -> None:
    work_order = tmp_path / "work-orders" / "planner.json"
    work_order.parent.mkdir(parents=True, exist_ok=True)
    work_order.write_text('{"task":"old work"}\n', encoding="utf-8")
    planner_receipt = tmp_path / "receipts" / "planner.json"
    planner_receipt.parent.mkdir(parents=True, exist_ok=True)
    _write_node_receipt(
        planner_receipt,
        node_id="planner",
        work_order_sha256=_sha256(work_order),
    )
    work_order.write_text('{"task":"changed work"}\n', encoding="utf-8")
    spec_path = _write_spec(
        tmp_path,
        [
            _node(
                tmp_path,
                "planner",
                work_order_path=work_order,
                command=[
                    sys.executable,
                    "-c",
                    "raise SystemExit('stale receipt should not be resumed')",
                ],
            )
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path, resume=True)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "SUBAGENT_ERROR"
    assert receipt["nodes"][0]["resumed"] is False
    assert receipt["nodes"][0]["attempt_count"] == 1
    assert "stale receipt should not be resumed" in receipt["nodes"][0]["errors"][0]


def test_generic_dag_fails_closed_after_timeout_attempts(tmp_path: Path) -> None:
    spec_path = _write_spec(
        tmp_path,
        [
            {
                **_node(tmp_path, "slow"),
                "command": [sys.executable, "-c", "import time; time.sleep(5)"],
                "timeout_seconds": 0.1,
                "max_attempts": 2,
            }
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "SUBAGENT_TIMEOUT"
    assert receipt["completed_node_count"] == 0
    node = receipt["nodes"][0]
    assert node["node_id"] == "slow"
    assert node["status"] == "BLOCKED"
    assert node["verdict"] == "SUBAGENT_TIMEOUT"
    assert node["attempt_count"] == 2
    assert node["resumed"] is False
    assert len(node["command_results"]) == 2
    assert all(result["returncode"] == 124 for result in node["command_results"])
    assert "timed out after 0.1s" in node["errors"][0]


def test_generic_dag_rejects_cycles(tmp_path: Path) -> None:
    spec_path = _write_spec(
        tmp_path,
        [
            _node(tmp_path, "a", depends_on=["b"]),
            _node(tmp_path, "b", depends_on=["a"]),
        ],
    )

    try:
        run_generic_dag(spec_path=spec_path)
    except RuntimeError as exc:
        assert "DAG cycle detected" in str(exc)
    else:
        raise AssertionError("cycle should fail closed before execution")


def test_generic_dag_inspect_summarizes_run(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path, [_node(tmp_path, "planner")])
    receipt = run_generic_dag(spec_path=spec_path)

    summary = inspect_generic_dag_run(Path(str(receipt["run_dir"])))

    assert summary["schema"] == "tau.generic_dag_inspect.v1"
    assert summary["ok"] is True
    assert summary["status"] == "PASS"
    assert summary["spec_path"] == str(spec_path.resolve())
    assert summary["resume_requested"] is True
    assert summary["resume_source"] == {
        "mode": "spec_path",
        "spec_path": str(spec_path.resolve()),
    }
    assert summary["events_count"] == 4
    assert summary["event_kind_counts"] == {
        "dag_finished": 1,
        "dag_started": 1,
        "node_dispatch": 1,
        "node_receipt_validated": 1,
    }
    assert summary["resumed_node_count"] == 0
    assert summary["dispatched_node_count"] == 1
    assert summary["blocked_node_count"] == 0
    assert summary["checkpoint_path"] == str(tmp_path / "checkpoint.json")
    assert summary["current_state_path"] == str(tmp_path / "current-state.json")
    assert summary["checkpoint"] == {
        "schema": "tau.generic_dag_checkpoint.v1",
        "status": "PASS",
        "verdict": "PASS",
        "active_node_id": None,
        "completed_nodes": ["planner"],
        "ready_nodes": [],
        "blocked_nodes": [],
    }
    assert len(summary["nodes"]) == 1
    node_summary = summary["nodes"][0]
    assert node_summary["node_id"] == "planner"
    assert node_summary["role"] == "planner"
    assert node_summary["status"] == "PASS"
    assert node_summary["verdict"] == "PASS"
    assert node_summary["attempt_count"] == 1
    assert node_summary["receipt_path"] == str(tmp_path / "receipts" / "planner.json")
    assert node_summary["work_order_path"] is None
    assert node_summary["work_order_sha256"] is None
    assert node_summary["resumed"] is False
    assert node_summary["live"] is None
    assert node_summary["provider_live"] is None
    assert node_summary["provider_status"] is None
    assert node_summary["provider_verdict"] is None
    assert isinstance(node_summary["started_at"], str)
    assert isinstance(node_summary["finished_at"], str)
    assert node_summary["duration_seconds"] >= 0
    assert node_summary["artifact_count"] == 0
    assert node_summary["artifacts"] == {}


def test_generic_dag_inspect_reports_resume_aggregates(tmp_path: Path) -> None:
    planner_receipt = tmp_path / "receipts" / "planner.json"
    planner_receipt.parent.mkdir(parents=True, exist_ok=True)
    _write_node_receipt(planner_receipt, node_id="planner")
    spec_path = _write_spec(
        tmp_path,
        [
            _node(
                tmp_path,
                "planner",
                command=[
                    sys.executable,
                    "-c",
                    "raise SystemExit('planner command should have been resumed')",
                ],
            ),
            _node(tmp_path, "coder", depends_on=["planner"]),
        ],
    )
    receipt = run_generic_dag(spec_path=spec_path, resume=True)

    summary = inspect_generic_dag_run(Path(str(receipt["run_dir"])))

    assert summary["resumed_node_count"] == 1
    assert summary["dispatched_node_count"] == 1
    assert summary["blocked_node_count"] == 0
    assert summary["event_kind_counts"]["node_resumed"] == 1
    assert summary["event_kind_counts"]["node_dispatch"] == 1
    assert summary["nodes"][0]["node_id"] == "planner"
    assert summary["nodes"][0]["resumed"] is True
    assert summary["nodes"][1]["node_id"] == "coder"
    assert summary["nodes"][1]["resumed"] is False


def test_generic_dag_propagates_provider_live_node_evidence(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipts" / "provider.json"
    payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": "provider",
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": True,
        "provider_status": "PASS",
        "provider_verdict": "PASS",
        "artifacts": [{"kind": "run_dir", "path": "/tmp/provider-run"}],
        "commands_run": ["tau generic-provider-dag-node"],
        "handoff_summary": "provider adapter passed",
        "errors": [],
        "policy_exceptions": [],
    }
    spec_path = _write_spec(
        tmp_path,
        [
            _node(
                tmp_path,
                "provider",
                command=[
                    sys.executable,
                    "-c",
                    _write_literal_json_code(receipt_path, payload),
                ],
            )
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["live"] is True
    assert receipt["provider_live"] is True
    assert receipt["nodes"][0]["provider_live"] is True
    assert receipt["nodes"][0]["provider_status"] == "PASS"
    assert receipt["nodes"][0]["artifacts"] == [{"kind": "run_dir", "path": "/tmp/provider-run"}]
    assert (
        "Tau can carry live provider-backed node evidence through the generic DAG receipt"
        in receipt["proof_scope"]["proves"]
    )
    summary = inspect_generic_dag_run(Path(str(receipt["run_dir"])))
    assert summary["nodes"][0]["provider_live"] is True
    assert summary["nodes"][0]["provider_status"] == "PASS"
    assert summary["nodes"][0]["provider_verdict"] == "PASS"
    assert summary["nodes"][0]["artifact_count"] == 1
    assert summary["nodes"][0]["artifacts"] == {"run_dir": "/tmp/provider-run"}


def _write_spec(tmp_path: Path, nodes: list[dict[str, object]]) -> Path:
    spec = {
        "schema": GENERIC_DAG_SPEC_SCHEMA,
        "run_id": "run-generic-dag-test",
        "run_dir": str(tmp_path),
        "events_jsonl": str(tmp_path / "events.jsonl"),
        "nodes": nodes,
    }
    spec_path = tmp_path / "dag-spec.json"
    spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    return spec_path


def _node(
    tmp_path: Path,
    node_id: str,
    *,
    depends_on: list[str] | None = None,
    command: list[str] | None = None,
    work_order_path: Path | None = None,
) -> dict[str, object]:
    receipt_path = tmp_path / "receipts" / f"{node_id}.json"
    node = {
        "node_id": node_id,
        "role": node_id,
        "depends_on": depends_on or [],
        "receipt_path": str(receipt_path),
        "timeout_seconds": 20,
        "max_attempts": 1,
        "command": command
        or [
            sys.executable,
            "-c",
            _receipt_writer_code(receipt_path, node_id=node_id, work_order_path=work_order_path),
        ],
    }
    if work_order_path is not None:
        node["work_order_path"] = str(work_order_path)
    return node


def _receipt_writer_code(
    receipt_path: Path,
    *,
    node_id: str,
    work_order_path: Path | None = None,
) -> str:
    payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "artifacts": [],
        "commands_run": ["python receipt writer"],
        "handoff_summary": f"{node_id} finished",
        "errors": [],
        "policy_exceptions": [],
    }
    if work_order_path is not None:
        payload["work_order_sha256"] = _sha256(work_order_path)
    return (
        "import json; "
        "from pathlib import Path; "
        f"path = Path({str(receipt_path)!r}); "
        "path.parent.mkdir(parents=True, exist_ok=True); "
        f"path.write_text(json.dumps({payload!r}, sort_keys=True), encoding='utf-8')"
    )


def _write_literal_json_code(receipt_path: Path, payload: dict[str, object]) -> str:
    return (
        "import json; "
        "from pathlib import Path; "
        f"path = Path({str(receipt_path)!r}); "
        "path.parent.mkdir(parents=True, exist_ok=True); "
        f"path.write_text(json.dumps({payload!r}, sort_keys=True), encoding='utf-8')"
    )


def _write_node_receipt(
    path: Path,
    *,
    node_id: str,
    work_order_sha256: str | None = None,
) -> None:
    payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "artifacts": [],
        "commands_run": [],
        "handoff_summary": f"{node_id} preexisting receipt",
        "errors": [],
        "policy_exceptions": [],
    }
    if work_order_sha256 is not None:
        payload["work_order_sha256"] = work_order_sha256
    path.write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )


def _read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
