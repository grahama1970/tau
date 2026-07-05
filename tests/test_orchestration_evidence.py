import json
from pathlib import Path

from tau_coding.orchestration_evidence import build_orchestration_evidence


def test_orchestration_evidence_projects_traycer_inspired_features(tmp_path: Path) -> None:
    run_dir = _write_provider_dag_run(tmp_path)

    receipt = build_orchestration_evidence(run_dir)

    assert receipt["schema"] == "tau.orchestration_evidence_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["feature_counts"]["agent_lineage"] == 4
    assert receipt["feature_counts"]["execution_timeline"] == 6
    assert receipt["feature_counts"]["provider_capabilities"] == 2
    assert receipt["feature_counts"]["worktree_session_bindings"] == 4
    assert receipt["feature_counts"]["review_comments"] == 1
    assert receipt["feature_counts"]["agent_messages"] == 2
    assert receipt["features"]["agent_lineage"][2]["agent_id"] == "coder"
    assert receipt["features"]["agent_lineage"][2]["parent_agent_id"] == "orchestrator"
    assert receipt["features"]["provider_capabilities"][0]["schema"] == "tau.provider_capability.v1"
    assert receipt["features"]["worktree_session_bindings"][0]["schema"] == (
        "tau.worktree_session_binding.v1"
    )
    assert receipt["features"]["review_comments"][0]["schema"] == "tau.review_comment.v1"
    assert receipt["features"]["agent_messages"][0]["schema"] == "tau.agent_message.v1"
    assert receipt["features"]["doctor"]["schema"] == "tau.doctor_status_receipt.v1"
    assert receipt["features"]["doctor"]["status"] == "PASS"
    assert (run_dir / "orchestration-evidence-receipt.json").exists()


def test_orchestration_evidence_fails_closed_when_required_feature_missing(tmp_path: Path) -> None:
    run_dir = _write_provider_dag_run(tmp_path)
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")

    receipt = build_orchestration_evidence(run_dir, write_receipt=False)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "execution_timeline is empty" in receipt["errors"]
    assert "agent_messages is empty" in receipt["errors"]


def test_orchestration_evidence_marks_reviewer_comments_resolved_by_repair(
    tmp_path: Path,
) -> None:
    run_dir = _write_provider_dag_run(tmp_path)
    _append_second_attempt_repair_loop(run_dir)

    receipt = build_orchestration_evidence(run_dir, write_receipt=False)

    comments = receipt["features"]["review_comments"]
    messages = receipt["features"]["agent_messages"]
    assert receipt["ok"] is True
    assert len(comments) == 2
    assert comments[0]["status"] == "resolved_by_repair"
    assert comments[0]["repair_attempt"] == 2
    assert comments[0]["resolved_by"] == {
        "node_id": "reviewer",
        "attempt": 2,
        "status": "PASS",
    }
    assert comments[1]["status"] == "resolved"
    assert any(
        message["sender_agent_id"] == "reviewer"
        and message["receiver_agent_id"] == "coder"
        and "reviewer_requested_revision" in message["body"]
        for message in messages
    )


def _write_provider_dag_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    scratch = run_dir / "scratch-worktree"
    scratch.mkdir()
    (scratch / "message.txt").write_text("done\n", encoding="utf-8")
    logs = run_dir / "logs"
    logs.mkdir()
    (logs / "codex.visible.txt").write_text("codex\n", encoding="utf-8")
    (logs / "opencode.visible.txt").write_text("opencode\n", encoding="utf-8")
    receipts = run_dir / "receipts"
    receipts.mkdir()
    work_orders = run_dir / "work-orders"
    work_orders.mkdir()
    coder_receipt = receipts / "attempt-01-coder.json"
    reviewer_receipt = receipts / "attempt-01-reviewer.json"
    coder_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.provider_dag_node_receipt.v1",
                "node_id": "coder",
                "provider_id": "codex",
                "attempt": 1,
                "status": "PASS",
                "verdict": "PASS",
                "work_order_path": str(work_orders / "attempt-01-coder.json"),
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    reviewer_receipt.write_text(
        json.dumps(
            {
                "schema": "tau.provider_dag_node_receipt.v1",
                "node_id": "reviewer",
                "provider_id": "opencode",
                "attempt": 1,
                "status": "PASS",
                "verdict": "PASS",
                "work_order_path": str(work_orders / "attempt-01-reviewer.json"),
                "handoff_summary": "PASS: no issues found.",
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    dag_spec = {
        "schema": "tau.dag_run_spec.v1",
        "run_id": "run-1",
        "nodes": [],
    }
    _write_json(run_dir / "dag-spec.json", dag_spec)
    events = [
        {"schema": "tau.provider_dag_event.v1", "kind": "dag_spec_created", "run_id": "run-1"},
        {"schema": "tau.provider_dag_event.v1", "kind": "orchestrator_started", "run_id": "run-1"},
        {
            "schema": "tau.provider_dag_event.v1",
            "kind": "coder_dispatch",
            "run_id": "run-1",
            "attempt": 1,
            "provider_id": "codex",
            "pane_id": "w1:p1",
            "work_order_path": str(work_orders / "attempt-01-coder.json"),
            "receipt_path": str(coder_receipt),
        },
        {
            "schema": "tau.provider_dag_event.v1",
            "kind": "coder_receipt_validated",
            "run_id": "run-1",
            "attempt": 1,
            "receipt_path": str(coder_receipt),
        },
        {
            "schema": "tau.provider_dag_event.v1",
            "kind": "reviewer_dispatch",
            "run_id": "run-1",
            "attempt": 1,
            "provider_id": "opencode",
            "pane_id": "w1:p2",
            "work_order_path": str(work_orders / "attempt-01-reviewer.json"),
            "receipt_path": str(reviewer_receipt),
        },
        {
            "schema": "tau.provider_dag_event.v1",
            "kind": "reviewer_receipt_validated",
            "run_id": "run-1",
            "attempt": 1,
            "receipt_path": str(reviewer_receipt),
        },
    ]
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )
    provider_sessions = {
        "codex": {
            "provider_id": "codex",
            "role": "coder",
            "workspace_id": "w1",
            "pane_id": "w1:p1",
            "terminal_id": "term-c",
            "visible": True,
            "ready": True,
            "state": "ready",
            "source": "herdr_process_info",
            "foreground_command": "python",
            "visible_log_path": str(logs / "codex.visible.txt"),
        },
        "opencode": {
            "provider_id": "opencode",
            "role": "reviewer",
            "workspace_id": "w1",
            "pane_id": "w1:p2",
            "terminal_id": "term-r",
            "visible": True,
            "ready": True,
            "state": "ready",
            "source": "herdr_process_info",
            "foreground_command": "python",
            "visible_log_path": str(logs / "opencode.visible.txt"),
        },
    }
    visible_subagents = {
        "planner": {
            "provider_id": "tau",
            "role": "planner",
            "workspace_id": "w1",
            "pane_id": "w1:p3",
            "terminal_id": "term-p",
            "visible": True,
        },
        "orchestrator": {
            "provider_id": "tau",
            "role": "orchestrator",
            "workspace_id": "w1",
            "pane_id": "w1:p4",
            "terminal_id": "term-o",
            "visible": True,
        },
        "coder": provider_sessions["codex"],
        "reviewer": provider_sessions["opencode"],
    }
    manifest = {
        "schema": "tau.provider_dag_runtime_manifest.v1",
        "run_id": "run-1",
        "events_jsonl": str(run_dir / "events.jsonl"),
        "provider_sessions": provider_sessions,
        "visible_subagents": visible_subagents,
    }
    _write_json(run_dir / "runtime-manifest.json", manifest)
    run_receipt = {
        "schema": "tau.dag_run_receipt.v1",
        "ok": True,
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": True,
        "run_id": "run-1",
        "run_dir": str(run_dir),
        "runtime_manifest": str(run_dir / "runtime-manifest.json"),
        "events_jsonl": str(run_dir / "events.jsonl"),
        "scratch_worktree": str(scratch),
        "dag_spec": str(run_dir / "dag-spec.json"),
        "provider_sessions": provider_sessions,
        "visible_subagents": visible_subagents,
        "attempts": [
            {
                "attempt": 1,
                "coder_receipt_path": str(coder_receipt),
                "reviewer_receipt_path": str(reviewer_receipt),
            }
        ],
    }
    _write_json(run_dir / "run-receipt.json", run_receipt)
    return run_dir


def _append_second_attempt_repair_loop(run_dir: Path) -> None:
    receipts = run_dir / "receipts"
    work_orders = run_dir / "work-orders"
    reviewer_1 = receipts / "attempt-01-reviewer.json"
    reviewer_1_payload = json.loads(reviewer_1.read_text(encoding="utf-8"))
    reviewer_1_payload["verdict"] = "REVISE"
    reviewer_1_payload["handoff_summary"] = "REVISE: replace placeholder with final text."
    reviewer_1.write_text(json.dumps(reviewer_1_payload), encoding="utf-8")
    coder_2 = receipts / "attempt-02-coder.json"
    reviewer_2 = receipts / "attempt-02-reviewer.json"
    coder_2.write_text(
        json.dumps(
            {
                "schema": "tau.provider_dag_node_receipt.v1",
                "node_id": "coder",
                "provider_id": "codex",
                "attempt": 2,
                "status": "PASS",
                "verdict": "PASS",
                "work_order_path": str(work_orders / "attempt-02-coder.json"),
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    reviewer_2.write_text(
        json.dumps(
            {
                "schema": "tau.provider_dag_node_receipt.v1",
                "node_id": "reviewer",
                "provider_id": "opencode",
                "attempt": 2,
                "status": "PASS",
                "verdict": "PASS",
                "work_order_path": str(work_orders / "attempt-02-reviewer.json"),
                "handoff_summary": "PASS: repaired text is acceptable.",
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events.insert(
        6,
        {
            "schema": "tau.provider_dag_event.v1",
            "kind": "reviewer_requested_revision",
            "run_id": "run-1",
            "attempt": 1,
            "feedback": "REVISE: replace placeholder with final text.",
        },
    )
    events.extend(
        [
            {
                "schema": "tau.provider_dag_event.v1",
                "kind": "coder_dispatch",
                "run_id": "run-1",
                "attempt": 2,
                "provider_id": "codex",
                "pane_id": "w1:p1",
                "work_order_path": str(work_orders / "attempt-02-coder.json"),
                "receipt_path": str(coder_2),
            },
            {
                "schema": "tau.provider_dag_event.v1",
                "kind": "coder_receipt_validated",
                "run_id": "run-1",
                "attempt": 2,
                "receipt_path": str(coder_2),
            },
            {
                "schema": "tau.provider_dag_event.v1",
                "kind": "reviewer_dispatch",
                "run_id": "run-1",
                "attempt": 2,
                "provider_id": "opencode",
                "pane_id": "w1:p2",
                "work_order_path": str(work_orders / "attempt-02-reviewer.json"),
                "receipt_path": str(reviewer_2),
            },
            {
                "schema": "tau.provider_dag_event.v1",
                "kind": "reviewer_receipt_validated",
                "run_id": "run-1",
                "attempt": 2,
                "receipt_path": str(reviewer_2),
            },
        ]
    )
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )
    run_receipt = json.loads((run_dir / "run-receipt.json").read_text(encoding="utf-8"))
    run_receipt["attempts"] = [
        {
            "attempt": 1,
            "coder_receipt_path": str(receipts / "attempt-01-coder.json"),
            "reviewer_receipt_path": str(reviewer_1),
        },
        {
            "attempt": 2,
            "coder_receipt_path": str(coder_2),
            "reviewer_receipt_path": str(reviewer_2),
        },
    ]
    run_receipt["attempt_count"] = 2
    _write_json(run_dir / "run-receipt.json", run_receipt)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
