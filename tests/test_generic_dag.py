import hashlib
import json
import sys
from pathlib import Path

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.generic_dag import (
    GENERIC_DAG_NODE_RECEIPT_SCHEMA,
    GENERIC_DAG_SPEC_SCHEMA,
    inspect_generic_dag_run,
    resume_generic_dag_from_run,
    run_generic_dag,
    validate_generic_dag_spec,
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
    runtime_result = receipt["nodes"][0]["command_results"][0]
    assert runtime_result["runtime_backend"] == "local"
    assert runtime_result["runtime_endpoint_lease"]["backend"] == "local"
    expected_plan = compile_generic_dag_plan(
        json.loads(spec_path.read_text(encoding="utf-8")),
        source_path=spec_path,
    )
    assert (
        runtime_result["runtime_endpoint_lease"]["goal_hash"]
        == expected_plan.runtime_goal_hash
    )
    assert runtime_result["runtime_submit_receipt"]["delivery_status"] == "CONFIRMED"
    assert runtime_result["runtime_event"]["state"] == "EXITED"
    assert len(runtime_result["runtime_artifacts"]) == 4
    assert all(Path(path).is_file() for path in runtime_result["runtime_artifacts"])
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


def test_generic_dag_preserves_accepted_output_for_downstream_context(
    tmp_path: Path,
) -> None:
    first_receipt = tmp_path / "receipts" / "first.json"
    second_receipt = tmp_path / "receipts" / "second.json"
    accepted_output = {"schema": "example.output.v1", "summary": "useful result"}
    first = _node(
        tmp_path,
        "first",
        command=[
            sys.executable,
            "-c",
            _receipt_writer_code(
                first_receipt,
                node_id="first",
                accepted_output=accepted_output,
            ),
        ],
    )
    second_payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": "second",
        "status": "PASS",
        "verdict": "PASS",
        "artifacts": [],
        "commands_run": ["inspect context"],
        "handoff_summary": "context accepted",
        "errors": [],
        "policy_exceptions": [],
    }
    second_code = (
        "import json, os; from pathlib import Path; "
        "context=json.loads(Path(os.environ['TAU_GENERIC_DAG_CONTEXT']).read_text()); "
        f"assert context['accepted_inputs'][0] == {accepted_output!r}; "
        f"p=Path({str(second_receipt)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
        f"p.write_text(json.dumps({second_payload!r}))"
    )
    spec_path = _write_spec(
        tmp_path,
        [
            first,
            _node(
                tmp_path,
                "second",
                depends_on=["first"],
                command=[sys.executable, "-c", second_code],
            ),
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "PASS"
    assert receipt["nodes"][0]["accepted_output"] == accepted_output

    resumed = run_generic_dag(spec_path=spec_path, resume=True)
    assert resumed["nodes"][0]["resumed"] is True
    assert resumed["nodes"][0]["accepted_output"] == accepted_output


def test_generic_dag_validates_full_goal_and_preserves_legacy_hash(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path, [_node(tmp_path, "only")])
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    goal_without_hash = {
        "goal_id": "goal:test",
        "goal_version": 1,
        "summary": "Test full goal binding.",
        "completion_criteria": ["The node passes."],
    }
    goal_hash = canonical_sha256(goal_without_hash)
    payload["goal"] = {**goal_without_hash, "goal_hash": goal_hash}
    payload["goal_hash"] = goal_hash
    payload["nodes"][0]["command"] = [
        sys.executable,
        "-c",
        _receipt_writer_code(
            Path(str(payload["nodes"][0]["receipt_path"])),
            node_id="only",
            goal_hash=goal_hash,
        ),
    ]
    spec_path.write_text(json.dumps(payload), encoding="utf-8")

    assert run_generic_dag(spec_path=spec_path)["status"] == "PASS"

    legacy = _write_spec(tmp_path / "legacy", [_node(tmp_path / "legacy", "legacy")])
    legacy_payload = json.loads(legacy.read_text(encoding="utf-8"))
    legacy_payload["goal_hash"] = "sha256:legacy"
    legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")
    assert compile_generic_dag_plan(legacy_payload, source_path=legacy).goal_binding.to_value() == {
        "kind": "hash_only",
        "goal_hash": "sha256:legacy",
    }


def test_generic_dag_preserves_legacy_descriptive_goal_object(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path, [_node(tmp_path, "legacy-goal")])
    payload = json.loads(spec.read_text(encoding="utf-8"))
    payload["goal_hash"] = "sha256:legacy"
    payload["goal"] = {
        "version": 1,
        "sha256": "sha256:legacy-source",
        "statement": "Legacy descriptive goal metadata.",
    }
    spec.write_text(json.dumps(payload), encoding="utf-8")

    validate_generic_dag_spec(payload, source_path=spec)
    assert compile_generic_dag_plan(payload, source_path=spec).goal_binding.to_value() == {
        "kind": "hash_only",
        "goal_hash": "sha256:legacy",
    }


def test_generic_dag_rejects_full_goal_hash_mismatch(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path, [_node(tmp_path, "only")])
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    payload["goal"] = {
        "goal_id": "goal:test",
        "goal_version": 1,
        "goal_hash": "sha256:wrong",
        "summary": "Test full goal binding.",
        "completion_criteria": ["The node passes."],
    }
    payload["goal_hash"] = "sha256:different"
    spec_path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        run_generic_dag(spec_path=spec_path)
    except RuntimeError as exc:
        assert str(exc) == "generic DAG goal.goal_hash does not match canonical goal"
    else:
        raise AssertionError("full goal hash mismatch should fail closed")


def test_generic_dag_rejects_full_and_legacy_goal_hash_mismatch(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path, [_node(tmp_path, "only")])
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    goal_without_hash = {
        "goal_id": "goal:test",
        "goal_version": 1,
        "summary": "Test full goal binding.",
        "completion_criteria": ["The node passes."],
    }
    payload["goal"] = {
        **goal_without_hash,
        "goal_hash": canonical_sha256(goal_without_hash),
    }
    payload["goal_hash"] = "sha256:different"
    spec_path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        run_generic_dag(spec_path=spec_path)
    except RuntimeError as exc:
        assert str(exc) == "generic DAG goal_hash does not match goal.goal_hash"
    else:
        raise AssertionError("full and legacy goal hash mismatch should fail closed")


def test_generic_dag_honors_declared_concurrency(tmp_path: Path) -> None:
    left_receipt = tmp_path / "receipts" / "left.json"
    right_receipt = tmp_path / "receipts" / "right.json"
    spec_path = _write_spec(
        tmp_path,
        [
            _node(
                tmp_path,
                "left",
                command=[
                    sys.executable,
                    "-c",
                    "import time; time.sleep(0.2); "
                    + _receipt_writer_code(left_receipt, node_id="left"),
                ],
            ),
            _node(
                tmp_path,
                "right",
                command=[
                    sys.executable,
                    "-c",
                    "import time; time.sleep(0.2); "
                    + _receipt_writer_code(right_receipt, node_id="right"),
                ],
            ),
        ],
    )
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    payload["max_concurrency"] = 2
    spec_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "PASS"
    assert receipt["max_observed_concurrency"] == 2


def test_generic_dag_rejects_invalid_declared_concurrency(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path, [_node(tmp_path, "only")])
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    payload["max_concurrency"] = 0
    spec_path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        run_generic_dag(spec_path=spec_path)
    except RuntimeError as exc:
        assert str(exc) == "generic DAG spec max_concurrency must be a positive integer"
    else:
        raise AssertionError("invalid max_concurrency should fail closed")


def test_generic_dag_checkpoint_records_completed_node_before_next_dispatch(
    tmp_path: Path,
) -> None:
    captured_checkpoint = tmp_path / "captured-checkpoint.json"
    inspector_receipt = tmp_path / "receipts" / "inspector.json"
    inspector_payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": "inspector",
        "status": "PASS",
        "verdict": "PASS",
        "artifacts": [],
        "commands_run": ["capture checkpoint"],
        "handoff_summary": "captured progress",
        "errors": [],
        "policy_exceptions": [],
    }
    inspector_code = (
        "import json; "
        "from pathlib import Path; "
        f"checkpoint = Path({str(tmp_path / 'checkpoint.json')!r}); "
        f"captured = Path({str(captured_checkpoint)!r}); "
        "captured.write_text(checkpoint.read_text(encoding='utf-8'), encoding='utf-8'); "
        f"receipt = Path({str(inspector_receipt)!r}); "
        "receipt.parent.mkdir(parents=True, exist_ok=True); "
        f"receipt.write_text(json.dumps({inspector_payload!r}), encoding='utf-8')"
    )
    spec_path = _write_spec(
        tmp_path,
        [
            _node(tmp_path, "planner"),
            _node(
                tmp_path,
                "inspector",
                depends_on=["planner"],
                command=[sys.executable, "-c", inspector_code],
            ),
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "PASS"
    captured = json.loads(captured_checkpoint.read_text(encoding="utf-8"))
    assert captured["completed_nodes"] == ["planner"]
    assert captured["node_statuses"]["planner"]["status"] == "PASS"


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


def test_generic_dag_rejects_resumed_receipt_from_changed_goal(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipts" / "planner.json"
    payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": "planner",
        "status": "PASS",
        "verdict": "PASS",
        "goal_hash": "sha256:goal-v1",
        "artifacts": [],
        "commands_run": ["goal-bound writer"],
        "handoff_summary": "bound to the original goal",
        "errors": [],
        "policy_exceptions": [],
    }
    spec_path = _write_spec(
        tmp_path,
        [
            _node(
                tmp_path,
                "planner",
                command=[
                    sys.executable,
                    "-c",
                    _write_literal_json_code(receipt_path, payload),
                ],
            )
        ],
    )
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["goal_hash"] = "sha256:goal-v1"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    assert run_generic_dag(spec_path=spec_path)["status"] == "PASS"

    spec["goal_hash"] = "sha256:goal-v2"
    spec["run_dir"] = str(tmp_path / "changed-goal-run")
    spec["events_jsonl"] = str(tmp_path / "changed-goal-run" / "events.jsonl")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    receipt = run_generic_dag(spec_path=spec_path, resume=True)

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "INVALID_RECEIPT"
    assert receipt["nodes"][0]["resumed"] is False
    assert "goal_hash does not match the active DAG goal" in receipt["nodes"][0]["errors"]


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


def test_generic_dag_does_not_invent_timeout_from_exit_code(tmp_path: Path) -> None:
    spec_path = _write_spec(
        tmp_path,
        [
            {
                **_node(tmp_path, "explicit-exit"),
                "command": [sys.executable, "-c", "raise SystemExit(124)"],
                "max_attempts": 1,
            }
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "SUBAGENT_ERROR"
    command_result = receipt["nodes"][0]["command_results"][0]
    assert command_result["returncode"] == 124
    assert command_result["termination_cause"] == "exited"


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
    visible_log = tmp_path / "logs" / "provider.visible.txt"
    visible_log.parent.mkdir(parents=True, exist_ok=True)
    visible_log.write_text("provider output is visible\n", encoding="utf-8")
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
        "goal_hash": "sha256:goal",
        "attempt": 1,
        "workspace_id": "w1",
        "pane_id": "w1:p3",
        "terminal_id": "term-provider",
        "visible_log_path": str(visible_log),
        "visible_log_sha256": _sha256(visible_log),
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
    assert receipt["nodes"][0]["goal_hash"] == "sha256:goal"
    assert receipt["nodes"][0]["workspace_id"] == "w1"
    assert receipt["nodes"][0]["pane_id"] == "w1:p3"
    assert receipt["nodes"][0]["terminal_id"] == "term-provider"
    assert receipt["nodes"][0]["visible_log_path"] == str(visible_log)
    assert receipt["nodes"][0]["visible_log_sha256"] == _sha256(visible_log)
    assert receipt["nodes"][0]["artifacts"] == [{"kind": "run_dir", "path": "/tmp/provider-run"}]
    assert (
        "Tau can carry live provider-backed node evidence through the generic DAG receipt"
        in receipt["proof_scope"]["proves"]
    )
    summary = inspect_generic_dag_run(Path(str(receipt["run_dir"])))
    assert summary["nodes"][0]["provider_live"] is True
    assert summary["nodes"][0]["provider_status"] == "PASS"
    assert summary["nodes"][0]["provider_verdict"] == "PASS"
    assert summary["nodes"][0]["goal_hash"] == "sha256:goal"
    assert summary["nodes"][0]["attempt"] == 1
    assert summary["nodes"][0]["workspace_id"] == "w1"
    assert summary["nodes"][0]["pane_id"] == "w1:p3"
    assert summary["nodes"][0]["terminal_id"] == "term-provider"
    assert summary["nodes"][0]["visible_log_path"] == str(visible_log)
    assert summary["nodes"][0]["visible_log_sha256"] == _sha256(visible_log)
    assert summary["nodes"][0]["artifact_count"] == 1
    assert summary["nodes"][0]["artifacts"] == {"run_dir": "/tmp/provider-run"}


def test_generic_dag_rejects_provider_live_receipt_without_binding(
    tmp_path: Path,
) -> None:
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
        "artifacts": [],
        "commands_run": ["tau generic-provider-dag-node"],
        "handoff_summary": "unbound provider adapter claim",
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
            ),
            _node(tmp_path, "downstream", depends_on=["provider"]),
        ],
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "INVALID_RECEIPT"
    assert receipt["completed_node_count"] == 0
    assert [node["node_id"] for node in receipt["nodes"]] == ["provider"]
    errors = receipt["nodes"][0]["errors"]
    assert "goal_hash must be a non-empty string when provider_live is true" in errors
    assert "attempt must be a positive integer when provider_live is true" in errors
    assert "workspace_id must be a non-empty string when provider_live is true" in errors
    assert "pane_id must be a non-empty string when provider_live is true" in errors
    assert "terminal_id must be a non-empty string when provider_live is true" in errors
    assert "visible_log_path must be a non-empty string when provider_live is true" in errors
    assert "visible_log_sha256 must be a non-empty string when provider_live is true" in errors


def _write_spec(tmp_path: Path, nodes: list[dict[str, object]]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    accepted_output: dict[str, object] | None = None,
    goal_hash: str | None = None,
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
    if accepted_output is not None:
        payload["accepted_output"] = accepted_output
    if goal_hash is not None:
        payload["goal_hash"] = goal_hash
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
