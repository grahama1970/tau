import hashlib
import json
from pathlib import Path

from tau_coding.run_status import build_run_status


def test_run_status_summarizes_generic_dag_checkpoint(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "run-receipt.json",
        {
            "schema": "tau.generic_dag_run_receipt.v1",
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": False,
            "run_id": "run-1",
            "spec_path": str(tmp_path / "dag-spec.json"),
            "resume_requested": True,
            "resume_source": {
                "mode": "run_metadata",
                "run_dir": str(tmp_path),
                "metadata_path": str(tmp_path / "current-state.json"),
                "spec_path": str(tmp_path / "dag-spec.json"),
            },
            "events_jsonl": str(tmp_path / "events.jsonl"),
            "checkpoint_path": str(tmp_path / "checkpoint.json"),
            "node_count": 2,
            "completed_node_count": 2,
            "nodes": [
                {
                    "node_id": "a",
                    "role": "planner",
                    "status": "PASS",
                    "verdict": "PASS",
                    "attempt_count": 1,
                    "resumed": False,
                    "started_at": "2026-07-03T23:30:00Z",
                    "finished_at": "2026-07-03T23:30:01Z",
                    "duration_seconds": 1.0,
                    "receipt_path": str(tmp_path / "receipts" / "a.json"),
                },
                {
                    "node_id": "b",
                    "role": "coder",
                    "status": "PASS",
                    "verdict": "PASS",
                    "attempt_count": 0,
                    "resumed": True,
                    "live": True,
                    "provider_live": True,
                    "provider_status": "PASS",
                    "provider_verdict": "PASS",
                    "artifacts": [{"kind": "run_dir", "path": "/tmp/provider-run"}],
                    "errors": [],
                    "receipt_path": str(tmp_path / "receipts" / "b.json"),
                },
            ],
        },
    )
    _write_json(
        tmp_path / "checkpoint.json",
        {
            "schema": "tau.generic_dag_checkpoint.v1",
            "status": "PASS",
            "verdict": "PASS",
            "completed_nodes": ["a", "b"],
            "ready_nodes": [],
            "blocked_nodes": [],
        },
    )
    (tmp_path / "events.jsonl").write_text('{"kind":"dag_started"}\n', encoding="utf-8")

    status = build_run_status(tmp_path)

    assert status["schema"] == "tau.run_status.v1"
    assert status["ok"] is True
    assert status["detected_type"] == "generic_dag"
    assert status["missing_required_artifacts"] == []
    assert status["run_receipt"]["node_count"] == 2
    assert status["generic_dag"]["spec_path"] == str(tmp_path / "dag-spec.json")
    assert status["generic_dag"]["resume_requested"] is True
    assert status["generic_dag"]["resume_source"]["mode"] == "run_metadata"
    assert status["generic_dag"]["resume_source"]["metadata_path"] == str(
        tmp_path / "current-state.json"
    )
    assert status["generic_dag"]["resumed_node_count"] == 1
    assert status["generic_dag"]["dispatched_node_count"] == 1
    assert status["generic_dag"]["blocked_node_count"] == 0
    assert status["generic_dag"]["nodes"][0]["started_at"] == "2026-07-03T23:30:00Z"
    assert status["generic_dag"]["nodes"][0]["finished_at"] == "2026-07-03T23:30:01Z"
    assert status["generic_dag"]["nodes"][0]["duration_seconds"] == 1.0
    assert status["generic_dag"]["nodes"][1]["provider_live"] is True
    assert status["generic_dag"]["nodes"][1]["provider_status"] == "PASS"
    assert status["generic_dag"]["nodes"][1]["artifact_count"] == 1
    assert status["generic_dag"]["nodes"][1]["artifacts"] == {"run_dir": "/tmp/provider-run"}
    assert status["generic_dag"]["nodes"][1]["error_count"] == 0
    assert status["generic_dag"]["nodes"][1]["errors"] == []
    assert status["checkpoint"]["completed_nodes"] == ["a", "b"]
    assert status["events"]["count"] == 1


def test_run_status_summarizes_blocked_generic_dag_work_order_node(
    tmp_path: Path,
) -> None:
    work_order = tmp_path / "work-orders" / "planner.json"
    _write_json(work_order, {"task": "changed work"})
    _write_json(
        tmp_path / "run-receipt.json",
        {
            "schema": "tau.generic_dag_run_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "verdict": "SUBAGENT_ERROR",
            "mocked": False,
            "live": False,
            "run_id": "run-stale",
            "events_jsonl": str(tmp_path / "events.jsonl"),
            "checkpoint_path": str(tmp_path / "checkpoint.json"),
            "node_count": 1,
            "completed_node_count": 0,
            "nodes": [
                {
                    "node_id": "planner",
                    "role": "planner",
                    "status": "BLOCKED",
                    "verdict": "SUBAGENT_ERROR",
                    "attempt_count": 1,
                    "resumed": False,
                    "receipt_path": str(tmp_path / "receipts" / "planner.json"),
                    "work_order_path": str(work_order),
                    "work_order_sha256": "abc123",
                    "errors": ["stale work-order receipt should not be resumed"],
                }
            ],
        },
    )
    _write_json(
        tmp_path / "checkpoint.json",
        {
            "schema": "tau.generic_dag_checkpoint.v1",
            "status": "BLOCKED",
            "verdict": "SUBAGENT_ERROR",
            "completed_nodes": [],
            "ready_nodes": [],
            "blocked_nodes": ["planner"],
        },
    )
    (tmp_path / "events.jsonl").write_text('{"kind":"dag_started"}\n', encoding="utf-8")

    status = build_run_status(tmp_path)

    assert status["ok"] is False
    assert status["status"] == "BLOCKED"
    assert status["detected_type"] == "generic_dag"
    assert status["missing_required_artifacts"] == []
    assert status["generic_dag"]["blocked_node_count"] == 1
    assert status["generic_dag"]["dispatched_node_count"] == 1
    assert status["generic_dag"]["resumed_node_count"] == 0
    assert status["generic_dag"]["nodes"][0]["work_order_path"] == str(work_order)
    assert status["generic_dag"]["nodes"][0]["work_order_sha256"] == "abc123"
    assert status["generic_dag"]["nodes"][0]["error_count"] == 1
    assert status["generic_dag"]["nodes"][0]["errors"] == [
        "stale work-order receipt should not be resumed"
    ]


def test_run_status_summarizes_provider_dag_receipt(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"kind":"coder_dispatch"}\n', encoding="utf-8")
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    coder_receipt = receipts / "attempt-01-coder.json"
    reviewer_receipt = receipts / "attempt-01-reviewer.json"
    _write_json(
        coder_receipt,
        {
            "schema": "tau.provider_dag_node_receipt.v1",
            "status": "PASS",
            "verdict": "PASS",
            "dag_id": "provider-run",
            "goal_hash": "sha256:goal",
            "node_id": "coder",
            "provider_id": "codex",
            "attempt": 1,
            "workspace_id": "w1",
            "pane_id": "w1:p1",
            "terminal_id": "term-codex",
            "work_order_path": str(tmp_path / "work-orders" / "attempt-01-coder.json"),
            "work_order_sha256": "sha256:coder-work-order",
            "visible_log_path": str(tmp_path / "logs" / "codex.visible.txt"),
            "visible_log_sha256": "sha256:codex-visible-log",
            "errors": [],
        },
    )
    _write_json(
        reviewer_receipt,
        {
            "schema": "tau.provider_dag_node_receipt.v1",
            "status": "PASS",
            "verdict": "PASS",
            "dag_id": "provider-run",
            "goal_hash": "sha256:goal",
            "node_id": "reviewer",
            "provider_id": "opencode",
            "attempt": 1,
            "workspace_id": "w1",
            "pane_id": "w1:p2",
            "terminal_id": "term-opencode",
            "work_order_path": str(tmp_path / "work-orders" / "attempt-01-reviewer.json"),
            "work_order_sha256": "sha256:reviewer-work-order",
            "visible_log_path": str(tmp_path / "logs" / "opencode.visible.txt"),
            "visible_log_sha256": "sha256:opencode-visible-log",
            "errors": [],
        },
    )
    _write_json(
        tmp_path / "runtime-manifest.json",
        {
            "schema": "tau.provider_dag_runtime_manifest.v1",
            "run_id": "provider-run",
            "events_jsonl": str(events),
        },
    )
    _write_json(
        tmp_path / "run-receipt.json",
        {
            "schema": "tau.dag_run_receipt.v1",
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": True,
            "run_id": "provider-run",
            "scratch_worktree": str(tmp_path / "scratch-worktree"),
            "attempt_count": 1,
            "max_attempts": 2,
            "alerts": [
                {
                    "schema": "tau.provider_dag_alert.v1",
                    "severity": "BLOCK",
                    "code": "coder_receipt_timeout",
                    "node_id": "coder",
                    "attempt": 1,
                    "message": (
                        "Provider DAG coder node did not produce a bound receipt before timeout."
                    ),
                    "errors": ["node_receipt_timeout: coder receipt did not appear before timeout"],
                    "recommended_action": {
                        "type": "reroute",
                        "next_agent": "goal-guardian",
                        "reason": (
                            "Provider DAG coder node did not produce a bound receipt before "
                            "timeout."
                        ),
                    },
                }
            ],
            "provider_sessions": {
                "codex": {
                    "role": "coder",
                    "provider_id": "codex",
                    "workspace_id": "w1",
                    "pane_id": "w1:p1",
                    "terminal_id": "term-codex",
                    "visible": True,
                    "ready": True,
                    "state": "ready",
                },
                "opencode": {
                    "role": "reviewer",
                    "provider_id": "opencode",
                    "workspace_id": "w1",
                    "pane_id": "w1:p2",
                    "terminal_id": "term-opencode",
                    "visible": True,
                    "ready": True,
                    "state": "ready",
                },
            },
            "visible_subagents": {
                "planner": {
                    "role": "planner",
                    "workspace_id": "w1",
                    "pane_id": "w1:p3",
                    "terminal_id": "term-planner",
                    "visible": True,
                },
                "orchestrator": {
                    "role": "orchestrator",
                    "workspace_id": "w1",
                    "pane_id": "w1:p4",
                    "terminal_id": "term-orchestrator",
                    "visible": True,
                },
            },
            "attempts": [
                {
                    "attempt": 1,
                    "coder_status": "PASS",
                    "coder_verdict": "PASS",
                    "coder_receipt_path": str(coder_receipt),
                    "reviewer_status": "PASS",
                    "reviewer_verdict": "PASS",
                    "reviewer_receipt_path": str(reviewer_receipt),
                    "errors": [],
                }
            ],
            "herdr_cleanup_receipt": str(tmp_path / "herdr-cleanup-receipt.json"),
            "herdr_cleanup": {
                "status": "PASS",
                "ok": True,
                "mocked": False,
                "live": False,
                "mode": "dry-run",
                "resource_count": 1,
                "candidate_count": 1,
                "applied_action_count": 1,
                "post_verified_absent_count": 1,
            },
            "orchestration_evidence_receipt": str(
                tmp_path / "orchestration-evidence-receipt.json"
            ),
            "orchestration_evidence": {
                "status": "PASS",
                "mocked": False,
                "live": True,
                "provider_live": True,
                "feature_counts": {"agent_lineage": 4},
            },
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["detected_type"] == "provider_dag"
    assert status["missing_required_artifacts"] == []
    assert status["events"]["count"] == 1
    assert status["provider_dag"]["attempt_count"] == 1
    assert status["provider_dag"]["provider_session_count"] == 2
    assert status["provider_dag"]["visible_subagent_count"] == 2
    assert status["provider_dag"]["alert_count"] == 1
    assert status["provider_dag"]["blocking_alert_count"] == 1
    assert status["provider_dag"]["alerts"] == [
        {
            "severity": "BLOCK",
            "code": "coder_receipt_timeout",
            "node_id": "coder",
            "attempt": 1,
            "message": "Provider DAG coder node did not produce a bound receipt before timeout.",
            "error_count": 1,
            "recommended_action": {
                "type": "reroute",
                "next_agent": "goal-guardian",
                "reason": "Provider DAG coder node did not produce a bound receipt before timeout.",
            },
        }
    ]
    assert status["provider_dag"]["provider_sessions"]["codex"]["pane_id"] == "w1:p1"
    assert status["provider_dag"]["visible_subagents"]["planner"]["visible"] is True
    assert status["provider_dag"]["herdr_cleanup"]["mode"] == "dry-run"
    assert status["provider_dag"]["herdr_cleanup"]["resource_count"] == 1
    assert status["provider_dag"]["herdr_cleanup"]["applied_action_count"] == 1
    assert status["provider_dag"]["herdr_cleanup"]["post_verified_absent_count"] == 1
    assert status["provider_dag"]["orchestration_evidence"]["feature_counts"]["agent_lineage"] == 4
    attempt = status["provider_dag"]["attempts"][0]
    assert attempt["coder_receipt"]["visible_log_path"] == str(
        tmp_path / "logs" / "codex.visible.txt"
    )
    assert attempt["coder_receipt"]["visible_log_sha256"] == "sha256:codex-visible-log"
    assert attempt["coder_receipt"]["work_order_sha256"] == "sha256:coder-work-order"
    assert attempt["reviewer_receipt"]["visible_log_path"] == str(
        tmp_path / "logs" / "opencode.visible.txt"
    )
    assert attempt["reviewer_receipt"]["visible_log_sha256"] == "sha256:opencode-visible-log"
    assert attempt["reviewer_receipt"]["work_order_sha256"] == "sha256:reviewer-work-order"


def test_run_status_summarizes_provider_dag_planner_receipt(tmp_path: Path) -> None:
    dag_spec = tmp_path / "dag-spec.json"
    events = tmp_path / "events.jsonl"
    scratch = tmp_path / "scratch-worktree"
    target = scratch / "message.txt"
    _write_json(dag_spec, {"schema": "tau.dag_run_spec.v1", "run_id": "planner-run"})
    events.write_text('{"kind":"dag_spec_created"}\n', encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("planned target\n", encoding="utf-8")
    _write_json(
        tmp_path / "planner-receipt.json",
        {
            "schema": "tau.dag_planner_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "run_id": "planner-run",
            "repo": str(tmp_path),
            "dag_spec": str(dag_spec),
            "events_jsonl": str(events),
            "scratch_worktree": str(scratch),
            "target_file": str(target),
            "max_attempts": 2,
            "proof_controls": {"coder_mode": "codex"},
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["detected_type"] == "provider_dag_planner"
    assert status["missing_required_artifacts"] == []
    assert status["provider_dag_planner"]["dag_spec"] == str(dag_spec)
    assert status["provider_dag_planner"]["target_file"] == str(target)
    assert status["provider_dag_planner"]["proof_controls"]["coder_mode"] == "codex"


def test_run_status_summarizes_provider_lifecycle_states(tmp_path: Path) -> None:
    readiness = tmp_path / "readiness"
    readiness.mkdir()
    readiness_path = readiness / "codex.readiness.json"
    state_path = readiness / "codex.session-state.json"
    _write_json(
        readiness_path,
        {
            "schema": "tau.provider_readiness.v1",
            "provider_id": "codex",
            "workspace_id": "w1",
            "pane_id": "w1:p5",
            "terminal_id": "term",
            "state": "ready",
            "ready": True,
            "source": "herdr_provider_integration",
            "diagnostics": {
                "visible_prompt_observed": True,
                "visible_prompt_is_gate": False,
            },
            "evidence": {
                "provider_readiness_path": str(readiness_path),
                "provider_session_state_path": str(state_path),
            },
        },
    )
    _write_json(
        state_path,
        {
            "schema": "tau.provider_session_state.v1",
            "provider_id": "codex",
            "workspace_id": "w1",
            "pane_id": "w1:p5",
            "terminal_id": "term",
            "state": "ready",
            "ready": True,
            "source": "herdr_provider_integration",
            "observed_at": "2026-07-03T00:00:00Z",
            "process": {"alive": True, "command": "codex"},
            "auth": {"status": "unknown"},
            "interstitial": {"present": False, "kind": None},
            "provider_api": {"available": True},
            "evidence": {
                "visible_log_path": "/tmp/codex.visible.txt",
                "provider_readiness_path": str(readiness_path),
                "provider_event_log_path": "/tmp/codex.events.jsonl",
            },
        },
    )
    _write_json(
        tmp_path / "runtime-manifest.json",
        {
            "schema": "tau.provider_readiness_runtime_manifest.v1",
            "run_id": "run-life",
            "readiness_records": [str(readiness_path)],
            "provider_session_states": [str(state_path)],
            "workstation_manifest": "/tmp/workstation.json",
            "inspect_path": "/tmp/inspect.json",
        },
    )
    _write_json(
        tmp_path / "run-receipt.json",
        {
            "schema": "tau.provider_readiness_run_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "all_provider_structured_ready": True,
        },
    )

    status = build_run_status(tmp_path)

    assert status["detected_type"] == "provider_readiness"
    assert status["live"] is True
    assert status["runtime_manifest"]["provider_session_state_count"] == 1
    assert status["provider_session_states"][0]["provider_id"] == "codex"
    assert status["provider_session_states"][0]["state"] == "ready"
    assert status["provider_session_states"][0]["source"] == "herdr_provider_integration"
    assert status["provider_session_states"][0]["observed_at"] == "2026-07-03T00:00:00Z"
    assert status["provider_session_states"][0]["auth_status"] == "unknown"
    assert status["provider_session_states"][0]["interstitial_present"] is False
    assert status["provider_session_states"][0]["provider_api_available"] is True
    assert status["provider_session_states"][0]["provider_readiness_path"] == str(readiness_path)
    assert status["provider_session_states"][0]["provider_readiness_sha256"] == hashlib.sha256(
        readiness_path.read_bytes()
    ).hexdigest()
    assert status["provider_session_states"][0]["provider_session_state_path"] == str(state_path)
    assert status["provider_session_states"][0]["provider_session_state_sha256"] == hashlib.sha256(
        state_path.read_bytes()
    ).hexdigest()
    assert status["provider_session_states"][0]["provider_event_log_path"] == (
        "/tmp/codex.events.jsonl"
    )
    assert status["provider_readiness"]["readiness_record_count"] == 1
    assert status["provider_readiness"]["provider_session_state_count"] == 1
    assert status["provider_readiness"]["ready_count"] == 1
    assert status["provider_readiness"]["state_counts"] == {"ready": 1}
    assert status["provider_readiness"]["readiness"][0]["visible_prompt_is_gate"] is False
    assert status["provider_readiness"]["readiness"][0]["provider_readiness_sha256"] == (
        hashlib.sha256(readiness_path.read_bytes()).hexdigest()
    )
    assert status["provider_readiness"]["readiness"][0]["provider_session_state_sha256"] == (
        hashlib.sha256(state_path.read_bytes()).hexdigest()
    )


def test_run_status_summarizes_provider_pane_allocation(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"kind":"provider_pane_started"}\n', encoding="utf-8")
    _write_json(
        tmp_path / "runtime-manifest.json",
        {
            "schema": "tau.provider_pane_runtime_manifest.v1",
            "run_id": "pane-run",
            "events_jsonl": str(events),
            "workstation_manifest": "/tmp/workstation.json",
            "inspect_path": "/tmp/inspect.json",
            "providers": [
                {
                    "provider_id": "codex",
                    "role": "codex",
                    "pane_id": "w1:p1",
                    "terminal_id": "term-codex",
                    "work_order_path": "/tmp/codex.json",
                    "ready_prompt_observed": True,
                    "readiness_actions": ["codex_update_prompt_skipped"],
                    "visible_log": "/tmp/codex.visible.txt",
                    "read_returncode": 0,
                },
                {
                    "provider_id": "opencode",
                    "role": "opencode",
                    "pane_id": "w1:p2",
                    "terminal_id": "term-opencode",
                    "work_order_path": "/tmp/opencode.json",
                    "ready_prompt_observed": False,
                    "readiness_actions": [],
                    "visible_log": "/tmp/opencode.visible.txt",
                    "read_returncode": 0,
                },
            ],
        },
    )
    _write_json(
        tmp_path / "run-receipt.json",
        {
            "schema": "tau.provider_pane_run_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "mocked": False,
            "live": True,
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is False
    assert status["detected_type"] == "provider_pane"
    assert status["missing_required_artifacts"] == []
    assert status["events"]["count"] == 1
    assert status["provider_pane"]["provider_count"] == 2
    assert status["provider_pane"]["ready_prompt_observed_count"] == 1
    assert status["provider_pane"]["visible_prompt_is_gate"] is True
    assert status["provider_pane"]["providers"][0]["provider_id"] == "codex"
    assert status["provider_pane"]["providers"][1]["ready_prompt_observed"] is False


def test_run_status_summarizes_approval_gate(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "approval-gate-receipt.json",
        {
            "schema": "tau.approval_gate_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "mocked": False,
            "live": False,
            "approved": False,
            "requested_action": "github_ticket_closure",
            "approval_packet": str(tmp_path / "approval.json"),
            "approval_packet_sha256": "sha256-test",
            "packet_summary": {
                "schema": "tau.human_approval_packet.v1",
                "approved": True,
                "action": "working_tree_mutation",
                "human_id": "human:graham",
                "target_id": "issue-123",
                "evidence_count": 1,
                "expires_at": "2026-07-04T00:00:00Z",
            },
            "errors": ["action mismatch"],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is False
    assert status["status"] == "BLOCKED"
    assert status["detected_type"] == "approval_gate"
    assert status["missing_required_artifacts"] == []
    assert status["approval_gate"]["requested_action"] == "github_ticket_closure"
    assert status["approval_gate"]["approval_packet"] == str(tmp_path / "approval.json")
    assert status["approval_gate"]["approval_packet_sha256"] == "sha256-test"
    assert status["approval_gate"]["packet_summary"]["action"] == "working_tree_mutation"
    assert status["approval_gate"]["packet_summary"]["human_id"] == "human:graham"
    assert status["approval_gate"]["packet_summary"]["target_id"] == "issue-123"
    assert status["approval_gate"]["packet_summary"]["expires_at"] == "2026-07-04T00:00:00Z"
    assert status["approval_gate"]["errors"] == ["action mismatch"]


def test_run_status_summarizes_standalone_cleanup_receipt(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "runtime-manifest.json",
        {
            "schema": "tau.provider_dag_runtime_manifest.v1",
            "provider_sessions": {
                "codex": {
                    "workspace_id": "w-clean",
                    "pane_id": "w-clean:p5",
                }
            },
        },
    )
    _write_json(
        tmp_path / "herdr-cleanup-receipt.json",
        {
            "schema": "tau.herdr_cleanup_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "mode": "dry-run",
            "runtime_manifest": str(tmp_path / "runtime-manifest.json"),
            "runtime_manifest_sha256": "manifest-sha-test",
            "resource_count": 1,
            "candidate_count": 1,
            "workspace_lease": str(tmp_path / "herdr-workspace-lease.json"),
            "workspace_lease_sha256": "workspace-lease-sha-test",
            "session_ownership": str(tmp_path / "herdr-session-ownership.json"),
            "session_ownership_sha256": "session-ownership-sha-test",
            "applied_actions": [{"post_verified_absent": True}],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["detected_type"] == "herdr_cleanup"
    assert status["missing_required_artifacts"] == []
    assert status["cleanup"]["mode"] == "dry-run"
    assert status["cleanup"]["runtime_manifest"] == str(tmp_path / "runtime-manifest.json")
    assert status["cleanup"]["runtime_manifest_sha256"] == "manifest-sha-test"
    assert status["cleanup"]["candidate_count"] == 1
    assert status["cleanup"]["workspace_lease"] == str(tmp_path / "herdr-workspace-lease.json")
    assert status["cleanup"]["workspace_lease_sha256"] == "workspace-lease-sha-test"
    assert status["cleanup"]["session_ownership"] == str(
        tmp_path / "herdr-session-ownership.json"
    )
    assert status["cleanup"]["session_ownership_sha256"] == "session-ownership-sha-test"
    assert status["cleanup"]["applied_action_count"] == 1
    assert status["cleanup"]["applied_session_stop_count"] == 0
    assert status["cleanup"]["applied_workspace_close_count"] == 0
    assert status["cleanup"]["post_verified_absent_count"] == 1


def test_run_status_summarizes_herdr_gc_receipt_over_approval(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "approval-gate-receipt.json",
        {
            "schema": "tau.approval_gate_receipt.v1",
            "ok": True,
            "status": "PASS",
            "approved": True,
            "requested_action": "herdr_gc_apply",
            "approval_packet": str(tmp_path / "approval.json"),
            "approval_packet_sha256": "sha256-approval",
            "packet_summary": {"action": "herdr_gc_apply"},
            "errors": [],
        },
    )
    _write_json(
        tmp_path / "herdr-gc-receipt.json",
        {
            "schema": "tau.herdr_gc_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "mode": "apply",
            "run_dir": str(tmp_path),
            "herdr_bin": "herdr",
            "herdr_surface": "real",
            "approval_required": True,
            "approval_receipt": str(tmp_path / "approval-gate-receipt.json"),
            "approval_receipt_sha256": "sha256-gate",
            "workspace_count": 1,
            "candidate_count": 0,
            "skipped_count": 0,
            "applied_action_count": 0,
            "post_verified_absent_count": 0,
            "command_results": [{"argv": ["herdr", "workspace", "list"], "returncode": 0}],
            "alerts": [],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["live"] is True
    assert status["detected_type"] == "herdr_gc"
    assert status["missing_required_artifacts"] == []
    assert status["approval_gate"]["requested_action"] == "herdr_gc_apply"
    assert status["herdr_gc"] == {
        "schema": "tau.herdr_gc_receipt.v1",
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": True,
        "mode": "apply",
        "run_dir": str(tmp_path),
        "herdr_bin": "herdr",
        "herdr_surface": "real",
        "approval_required": True,
        "approval_receipt": str(tmp_path / "approval-gate-receipt.json"),
        "approval_receipt_sha256": "sha256-gate",
        "workspace_count": 1,
        "candidate_count": 0,
        "skipped_count": 0,
        "applied_action_count": 0,
        "post_verified_absent_count": 0,
        "command_result_count": 1,
        "alerts": [],
    }


def test_run_status_summarizes_route_memory_sync_over_approval(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "approval-gate-receipt.json",
        {
            "schema": "tau.approval_gate_receipt.v1",
            "ok": True,
            "status": "PASS",
            "approved": True,
            "requested_action": "memory_upsert",
            "approval_packet": str(tmp_path / "approval.json"),
            "approval_packet_sha256": "sha256-approval",
            "packet_summary": {"action": "memory_upsert"},
            "errors": [],
        },
    )
    _write_json(
        tmp_path / "dag-route-memory-candidate-receipt.json",
        {
            "schema": "tau.dag_route_memory_candidate_receipt.v1",
            "ok": True,
            "status": "PASS",
            "dag_id": "dag-1",
            "goal_hash": "sha256:goal",
            "accepted_candidate_count": 1,
            "rejected_candidate_count": 0,
            "sync_status": "NOT_SYNCED",
            "memory_sync": False,
            "route_mutation": False,
            "dag_mutation": False,
            "provider_calls": False,
            "alerts": [],
        },
    )
    _write_json(
        tmp_path / "dag-route-memory-sync-receipt.json",
        {
            "schema": "tau.dag_route_memory_sync_receipt.v1",
            "ok": True,
            "status": "PASS",
            "dag_id": "dag-1",
            "goal_hash": "sha256:goal",
            "collection": "tau_route_memory",
            "memory_url": "http://127.0.0.1:8601",
            "apply": True,
            "memory_sync": True,
            "sync_status": "SYNCED",
            "projected_document_count": 1,
            "memory_response": {
                "collection": "tau_route_memory",
                "inserted": 1,
                "updated": 0,
                "total": 1,
                "errors": [],
            },
            "approval_receipt": str(tmp_path / "approval-gate-receipt.json"),
            "approval_receipt_sha256": "sha256-gate",
            "alerts": [],
            "route_mutation": False,
            "dag_mutation": False,
            "provider_calls": False,
        },
    )
    _write_json(
        tmp_path / "memory-readback.json",
        {
            "schema": "tau.memory_readback_proof.v1",
            "ok": True,
            "status": "PASS",
            "collection": "tau_route_memory",
            "memory_url": "http://127.0.0.1:8601",
            "endpoint": "POST /list",
            "document_count_returned": 6,
            "found_count": 1,
            "missing_keys": [],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["detected_type"] == "route_memory"
    assert status["missing_required_artifacts"] == []
    assert status["approval_gate"]["requested_action"] == "memory_upsert"
    assert status["route_memory"]["candidate"]["accepted_candidate_count"] == 1
    assert status["route_memory"]["sync"]["sync_status"] == "SYNCED"
    assert status["route_memory"]["sync"]["memory_sync"] is True
    assert status["route_memory"]["sync"]["memory_response"] == {
        "collection": "tau_route_memory",
        "inserted": 1,
        "updated": 0,
        "total": 1,
        "error_count": 0,
    }
    assert status["route_memory"]["readback"]["found_count"] == 1
    assert status["route_memory"]["readback"]["missing_keys"] == []


def test_run_status_summarizes_dag_expansion_apply_receipts(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "dag-expansion-validation-receipt.json",
        {
            "schema": "tau.dag_expansion_validation_receipt.v1",
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dag_id": "dag-1",
            "goal_hash": "sha256:goal",
            "proposal": str(tmp_path / "proposal.json"),
            "proposal_sha256": "sha256-proposal",
            "preview_path": str(tmp_path / "expanded-dag.preview.json"),
            "preview_sha256": "sha256-preview",
            "alerts": [],
            "errors": [],
        },
    )
    _write_json(
        tmp_path / "dag-expansion-policy-receipt.json",
        {
            "schema": "tau.dag_expansion_policy_receipt.v1",
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dag_id": "dag-1",
            "goal_hash": "sha256:goal",
            "validation_receipt": str(tmp_path / "dag-expansion-validation-receipt.json"),
            "validation_receipt_sha256": "sha256-validation",
            "alerts": [],
            "errors": [],
        },
    )
    _write_json(
        tmp_path / "dag-expansion-apply-receipt.json",
        {
            "schema": "tau.dag_expansion_apply_receipt.v1",
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dag_id": "dag-1",
            "goal_hash": "sha256:goal",
            "validation_receipt": str(tmp_path / "dag-expansion-validation-receipt.json"),
            "validation_receipt_sha256": "sha256-validation",
            "policy_receipt": str(tmp_path / "dag-expansion-policy-receipt.json"),
            "policy_receipt_sha256": "sha256-policy",
            "expanded_dag": str(tmp_path / "expanded-dag.applied.json"),
            "expanded_dag_sha256": "sha256-expanded",
            "alerts": [],
            "errors": [],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["detected_type"] == "dag_expansion"
    assert status["missing_required_artifacts"] == []
    assert status["dag_expansion"]["validation"]["preview_sha256"] == "sha256-preview"
    assert status["dag_expansion"]["policy"]["validation_receipt_sha256"] == "sha256-validation"
    assert status["dag_expansion"]["apply"]["policy_receipt_sha256"] == "sha256-policy"
    assert status["dag_expansion"]["apply"]["expanded_dag_sha256"] == "sha256-expanded"


def test_run_status_summarizes_short_dag_expansion_apply_receipt(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "apply-receipt.json",
        {
            "schema": "tau.dag_expansion_apply_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "verdict": "PREVIEW_HASH_MISMATCH",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dag_id": "dag-1",
            "goal_hash": "sha256:goal",
            "preview_path": str(tmp_path / "expanded-dag.preview.json"),
            "preview_sha256": "sha256-original",
            "alerts": [{"code": "preview_hash_mismatch", "severity": "BLOCK"}],
            "errors": [],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is False
    assert status["status"] == "BLOCKED"
    assert status["detected_type"] == "dag_expansion"
    assert status["missing_required_artifacts"] == []
    assert status["dag_expansion"]["apply"]["verdict"] == "PREVIEW_HASH_MISMATCH"
    assert status["dag_expansion"]["apply"]["alert_codes"] == ["preview_hash_mismatch"]


def test_run_status_summarizes_standalone_orchestration_evidence_receipt(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "orchestration-evidence-receipt.json",
        {
            "schema": "tau.orchestration_evidence_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "feature_counts": {
                "agent_lineage": 4,
                "execution_timeline": 6,
                "provider_capabilities": 2,
            },
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["detected_type"] == "orchestration_evidence"
    assert status["missing_required_artifacts"] == []
    assert status["orchestration_evidence"]["feature_counts"]["agent_lineage"] == 4


def test_run_status_summarizes_dag_stress_suite_receipt(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "suite-receipt.json",
        {
            "schema": "tau.dag_stress_suite_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "execution": "local_deterministic_tau_scheduler",
            "rung_count": 2,
            "passed_rungs": 1,
            "expected_blocked_rungs": 1,
            "unexpected_rungs": [],
            "rungs": [
                {
                    "rung_id": "one-pass",
                    "status": "PASS",
                    "expected_status": "PASS",
                    "verdict": "PASS",
                    "attempt_count": 1,
                    "event_count": 4,
                },
                {
                    "rung_id": "timeout",
                    "status": "BLOCKED",
                    "expected_status": "BLOCKED",
                    "verdict": "SUBAGENT_TIMEOUT",
                    "attempt_count": 1,
                    "event_count": 3,
                },
            ],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["detected_type"] == "dag_stress"
    assert status["missing_required_artifacts"] == []
    assert status["dag_stress"]["rung_count"] == 2
    assert status["dag_stress"]["blocked_rung_count"] == 1
    assert status["dag_stress"]["rungs"][1]["verdict"] == "SUBAGENT_TIMEOUT"


def test_run_status_summarizes_dag_stress_campaign_receipt(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "campaign-receipt.json",
        {
            "schema": "tau.dag_stress_campaign_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "execution": "local_deterministic_tau_scheduler_campaign",
            "max_budget": 2,
            "repetitions": 2,
            "suite_count": 4,
            "total_rungs": 40,
            "failed_suite_count": 0,
            "status_counts": {"PASS": 12, "BLOCKED": 28},
            "verdict_counts": {"PASS": 12, "MODEL_UNAVAILABLE": 4},
            "grading_dimensions": ["timeout_classification"],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["detected_type"] == "dag_stress_campaign"
    assert status["missing_required_artifacts"] == []
    assert status["dag_stress_campaign"]["suite_count"] == 4
    assert status["dag_stress_campaign"]["total_rungs"] == 40
    assert status["dag_stress_campaign"]["verdict_counts"]["MODEL_UNAVAILABLE"] == 4


def test_run_status_reports_missing_required_artifacts(tmp_path: Path) -> None:
    status = build_run_status(tmp_path)

    assert status["ok"] is False
    assert status["status"] == "MISSING"
    assert status["detected_type"] == "unknown"
    assert status["missing_required_artifacts"] == ["run_receipt", "runtime_manifest"]


def test_run_status_summarizes_real_world_sanity_post_cleanup(tmp_path: Path) -> None:
    provider_run_dir = tmp_path / "provider-run"
    cleanup_receipt = provider_run_dir / "herdr-cleanup-receipt.json"
    browser_screenshot = tmp_path / "browser-cdp-proof" / "tau-browser-cdp-proof.png"
    browser_screenshot.parent.mkdir(parents=True, exist_ok=True)
    browser_screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")
    _write_json(
        tmp_path / "browser-cdp-proof" / "browser-cdp-proof-receipt.json",
        {
            "schema": "tau.browser_cdp_proof.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "verdict": "PASS",
            "surface": "local Tau browser proof page",
            "transport": {"kind": "surf", "tab_id": "837357620"},
            "artifacts": {
                "html": str(tmp_path / "browser-cdp-proof" / "tau-browser-cdp-proof.html"),
                "receipt": str(tmp_path / "browser-cdp-proof" / "browser-cdp-proof-receipt.json"),
                "screenshot_png": str(browser_screenshot),
            },
            "screenshot": {
                "path": str(browser_screenshot),
                "sha256": "sha256:browser-proof",
                "width": 1200,
                "height": 596,
                "size_bytes": 8,
            },
            "visible_assertions": {
                "page_text_contains_handoff_schema": True,
                "page_text_contains_receipt_schema": True,
                "screenshot_nonempty": True,
            },
            "errors": [],
        },
    )
    _write_json(
        tmp_path / "real-world-sanity-receipt.json",
        {
            "schema": "tau.real_world_sanity_suite_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": "mixed",
            "provider_live": True,
            "check_count": 3,
            "failed_check_count": 0,
            "completed_at": "2026-07-03T21:03:02Z",
            "checks": [
                {
                    "check_id": "advanced.provider_readiness",
                    "level": "advanced",
                    "status": "PASS",
                    "ok": True,
                    "mocked": False,
                    "live": True,
                    "provider_live": True,
                    "attempt_count": 1,
                    "receipt_summary": {
                        "schema": "tau.provider_readiness_run_receipt.v1",
                        "status": "PASS",
                    },
                    "post_cleanup": {
                        "schema": "tau.real_world_sanity_post_cleanup.v1",
                        "status": "PASS",
                        "ok": True,
                        "mocked": False,
                        "live": True,
                        "mode": "apply",
                        "run_dir": str(provider_run_dir),
                        "receipt_path": str(cleanup_receipt),
                        "receipt_summary": {
                            "schema": "tau.herdr_cleanup_receipt.v1",
                            "status": "PASS",
                            "live": True,
                            "applied_action_count": 1,
                            "post_verified_absent_count": 1,
                        },
                        "errors": [],
                    },
                },
                {
                    "check_id": "medium.generic_dag_timeout_fail_closed",
                    "level": "medium",
                    "status": "PASS",
                    "ok": True,
                    "mocked": False,
                    "live": False,
                    "provider_live": False,
                    "attempt_count": 1,
                    "receipt_summary": {
                        "schema": "tau.generic_dag_run_receipt.v1",
                        "status": "BLOCKED",
                        "verdict": "SUBAGENT_TIMEOUT",
                        "spec_path": str(tmp_path / "timeout-dag-spec.json"),
                        "node_count": 1,
                        "completed_node_count": 0,
                        "resumed_node_count": 0,
                        "dispatched_node_count": 1,
                        "blocked_node_count": 1,
                        "timed_node_count": 1,
                        "node_error_counts": {"slow": 1},
                    },
                    "post_cleanup": None,
                },
                {
                    "check_id": "advanced.browser_cdp_proof",
                    "level": "advanced",
                    "status": "PASS",
                    "ok": True,
                    "mocked": False,
                    "live": True,
                    "provider_live": False,
                    "attempt_count": 1,
                    "receipt_summary": {
                        "schema": "tau.browser_cdp_proof.v1",
                        "status": "PASS",
                        "ok": True,
                        "mocked": False,
                        "live": True,
                        "provider_live": False,
                    },
                    "post_cleanup": None,
                },
            ],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["live"] == "mixed"
    assert status["detected_type"] == "real_world_sanity"
    assert status["missing_required_artifacts"] == []
    assert status["real_world_sanity"]["check_count"] == 3
    assert status["real_world_sanity"]["post_cleanup_count"] == 1
    assert status["real_world_sanity"]["live_post_cleanup_count"] == 1
    assert status["real_world_sanity"]["generic_dag_node_totals"] == {
        "node_count": 1,
        "completed_node_count": 0,
        "resumed_node_count": 0,
        "dispatched_node_count": 1,
        "blocked_node_count": 1,
        "timed_node_count": 1,
        "node_error_count": 1,
        "checks_with_blocked_nodes": [str(tmp_path / "timeout-dag-spec.json")],
        "checks_with_errors": [str(tmp_path / "timeout-dag-spec.json")],
    }
    check = status["real_world_sanity"]["checks"][0]
    assert check["check_id"] == "advanced.provider_readiness"
    assert check["post_cleanup"]["mode"] == "apply"
    assert check["post_cleanup"]["receipt_path"] == str(cleanup_receipt)
    assert check["post_cleanup"]["cleanup_applied_action_count"] == 1
    assert check["post_cleanup"]["cleanup_post_verified_absent_count"] == 1
    assert status["browser_cdp_proof"]["status"] == "PASS"
    assert status["browser_cdp_proof"]["screenshot_path"] == str(browser_screenshot)
    assert status["browser_cdp_proof"]["screenshot_sha256"] == "sha256:browser-proof"
    assert status["browser_cdp_proof"]["screenshot_width"] == 1200
    assert status["browser_cdp_proof"]["screenshot_height"] == 596
    assert status["browser_cdp_proof"]["visible_assertion_count"] == 3
    assert status["browser_cdp_proof"]["visible_assertion_pass_count"] == 3


def test_run_status_summarizes_github_apply_policy_receipt(tmp_path: Path) -> None:
    approval = tmp_path / "approval-gate-receipt.json"
    redaction = tmp_path / "github-redaction-receipt.json"
    _write_json(
        tmp_path / "github-apply-policy-receipt.json",
        {
            "schema": "tau.github_apply_policy_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": False,
            "provider_live": False,
            "target": {"repo": "grahama1970/tau", "target": "issue#47"},
            "actions": ["comment", "label"],
            "requirements": {
                "approval_packet": True,
                "preflight": True,
                "redaction": True,
            },
            "preflight_ready": True,
            "approval_receipt": str(approval),
            "redaction_receipt": str(redaction),
            "checks": [
                {"code": "repo_allowlist", "ok": True},
                {"code": "approval_receipt", "ok": True},
            ],
            "errors": [],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["detected_type"] == "github_apply_policy"
    assert status["missing_required_artifacts"] == []
    assert status["github_apply_policy"] == {
        "schema": "tau.github_apply_policy_receipt.v1",
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "target": {"repo": "grahama1970/tau", "target": "issue#47"},
        "actions": ["comment", "label"],
        "requirements": {
            "approval_packet": True,
            "preflight": True,
            "redaction": True,
        },
        "preflight_ready": True,
        "approval_receipt": str(approval),
        "redaction_receipt": str(redaction),
        "check_count": 2,
        "failed_checks": [],
        "errors": [],
    }


def test_run_status_summarizes_github_handoff_transport_receipt(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "github-transport-missing-policy-receipt.json",
        {
            "schema": "tau.github_handoff_transport_receipt.v1",
            "status": "BLOCKED",
            "ok": False,
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dry_run": False,
            "applied": False,
            "target": {"repo": "grahama1970/tau", "target": "issue#47"},
            "commands": [],
            "command_results": [],
            "preflight_results": [],
            "errors": [
                "GitHub --apply requires --github-apply-policy-receipt with a PASS receipt."
            ],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is False
    assert status["status"] == "BLOCKED"
    assert status["detected_type"] == "github_handoff_transport"
    assert status["missing_required_artifacts"] == []
    assert status["github_handoff_transport"] == {
        "schema": "tau.github_handoff_transport_receipt.v1",
        "status": "BLOCKED",
        "ok": False,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "dry_run": False,
        "applied": False,
        "target": {"repo": "grahama1970/tau", "target": "issue#47"},
        "command_count": 0,
        "command_result_count": 0,
        "preflight_result_count": 0,
        "errors": [
            "GitHub --apply requires --github-apply-policy-receipt with a PASS receipt."
        ],
    }


def test_run_status_summarizes_research_source_receipt(tmp_path: Path) -> None:
    source_packet = tmp_path / "research-source-packet.json"
    _write_json(
        tmp_path / "research-source-receipt.json",
        {
            "schema": "tau.research_source_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": False,
            "provider_live": False,
            "source_packet": str(source_packet),
            "source_packet_sha256": "sha256:packet",
            "source_type": "paper",
            "method": "arxiv",
            "classification": "design_input",
            "source_count": 2,
            "arxiv_source_count": 2,
            "review_required": True,
            "errors": [],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["detected_type"] == "research_source"
    assert status["missing_required_artifacts"] == []
    assert status["research_source"] == {
        "schema": "tau.research_source_receipt.v1",
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "source_packet": str(source_packet),
        "source_packet_sha256": "sha256:packet",
        "source_type": "paper",
        "method": "arxiv",
        "classification": "design_input",
        "source_count": 2,
        "arxiv_source_count": 2,
        "review_required": True,
        "errors": [],
    }


def test_run_status_summarizes_project_dag_evidence_validation_failure(
    tmp_path: Path,
) -> None:
    manifest = tmp_path.parent / "evidence-manifest.json"
    _write_json(
        tmp_path / "dag-receipt.json",
        {
            "schema": "tau.dag_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "verdict": "EVIDENCE_MANIFEST_INVALID",
            "mocked": False,
            "live": True,
            "dag_id": "project-dag-evidence-drift",
            "goal_hash": "sha256:goal",
            "observed_edges": [],
            "node_attempts": {},
            "errors": [
                "items[1].goal_hash mismatch: expected sha256:goal, observed sha256:stale"
            ],
        },
    )
    _write_json(
        tmp_path / "evidence-validation-receipt.json",
        {
            "schema": "tau.evidence_validation_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "mocked": False,
            "live": True,
            "dag_id": "project-dag-evidence-drift",
            "manifest_path": str(manifest),
            "manifest_sha256": "sha256:manifest",
            "item_count": 2,
            "valid_item_count": 1,
            "invalid_item_count": 1,
            "errors": [
                "items[1].goal_hash mismatch: expected sha256:goal, observed sha256:stale"
            ],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is False
    assert status["status"] == "BLOCKED"
    assert status["live"] is True
    assert status["detected_type"] == "project_dag"
    assert status["missing_required_artifacts"] == []
    assert status["project_dag"]["verdict"] == "EVIDENCE_MANIFEST_INVALID"
    assert status["project_dag"]["error_count"] == 1
    assert status["project_dag"]["node_attempt_count"] == 0
    assert status["evidence_validation"] == {
        "schema": "tau.evidence_validation_receipt.v1",
        "status": "BLOCKED",
        "ok": False,
        "mocked": False,
        "live": True,
        "dag_id": "project-dag-evidence-drift",
        "manifest_path": str(manifest),
        "manifest_sha256": "sha256:manifest",
        "item_count": 2,
        "valid_item_count": 1,
        "invalid_item_count": 1,
        "error_count": 1,
        "errors": [
            "items[1].goal_hash mismatch: expected sha256:goal, observed sha256:stale"
        ],
    }


def test_run_status_summarizes_project_dag_progress_before_final_receipt(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "dag-progress.json",
        {
            "schema": "tau.dag_progress.v1",
            "ok": True,
            "status": "RUNNING",
            "verdict": None,
            "mocked": False,
            "live": True,
            "provider_live": False,
            "scheduler": "handoff-loop",
            "dag_id": "project-dag-running",
            "active_goal_hash": "sha256:goal",
            "entry_node": "coder",
            "terminal_nodes": ["human"],
            "node_count": 2,
            "active_subagents": [{"node_id": "coder", "agent": "coder", "attempt": 1}],
            "completed_subagents": [],
            "node_progress": [
                {
                    "node_id": "coder",
                    "agent": "coder",
                    "status": "RUNNING",
                    "attempt": 1,
                    "last_event_at": "2026-07-06T18:00:00Z",
                },
                {
                    "node_id": "reviewer",
                    "agent": "reviewer",
                    "status": "PENDING",
                    "attempt": 0,
                    "last_event_at": None,
                },
            ],
            "event_count": 1,
            "last_event": {
                "event": "step_started",
                "loop_step": 1,
                "selected_agent": "coder",
                "status": "RUNNING",
                "ts": "2026-07-06T18:00:00Z",
            },
            "events": [],
            "updated_at": "2026-07-06T18:00:00Z",
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "RUNNING"
    assert status["live"] is True
    assert status["detected_type"] == "project_dag"
    assert status["missing_required_artifacts"] == []
    assert status["project_dag"] is None
    assert status["artifacts"]["project_dag_progress"] == str(tmp_path / "dag-progress.json")
    assert status["project_dag_progress"]["active_subagents"] == [
        {"node_id": "coder", "agent": "coder", "attempt": 1}
    ]
    assert status["project_dag_progress"]["completed_subagent_count"] == 0
    assert status["project_dag_progress"]["node_progress"][1]["status"] == "PENDING"


def test_run_status_exports_dag_viewer_link_for_project_dag_run_root(
    tmp_path: Path,
) -> None:
    contract_path = tmp_path / "dag-contract.json"
    receipt_path = tmp_path / "run" / "dag-receipt.json"
    _write_json(
        contract_path,
        {
            "schema": "tau.dag_contract.v1",
            "dag_id": "project-dag-viewer",
            "goal": {"goal_hash": "sha256:goal"},
            "target": {"repo": "grahama1970/tau", "target": "scratch"},
            "entry_node": "coder",
            "terminal_nodes": ["human"],
            "nodes": [],
            "edges": [],
        },
    )
    _write_json(
        receipt_path,
        {
            "schema": "tau.dag_receipt.v1",
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "dag_id": "project-dag-viewer",
            "active_goal_hash": "sha256:goal",
            "target": {"repo": "grahama1970/tau", "target": "scratch"},
            "observed_edges": [{"from": "coder", "to": "human"}],
            "node_attempts": {"coder": 1},
            "errors": [],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is True
    assert status["status"] == "PASS"
    assert status["detected_type"] == "project_dag"
    assert status["missing_required_artifacts"] == []
    assert status["artifacts"]["project_dag_contract"] == str(contract_path)
    assert status["artifacts"]["project_dag_receipt"] == str(receipt_path)
    assert status["project_dag"]["dag_id"] == "project-dag-viewer"
    assert status["dag_viewer"]["schema"] == "tau.dag_viewer_link.v1"
    assert status["dag_viewer"]["available"] is True
    assert status["dag_viewer"]["source"] == "dag-contract.json + dag-receipt.json"
    assert status["dag_viewer"]["url"].startswith("http://localhost:3002/#tau/dag?run=")
    assert "%2F" in status["dag_viewer"]["url"]
    assert status["dag_viewer"]["contract_path"] == str(contract_path)
    assert status["dag_viewer"]["receipt_path"] == str(receipt_path)
    assert status["dag_viewer"]["contract_sha256"] == _sha256(contract_path)
    assert status["dag_viewer"]["receipt_sha256"] == _sha256(receipt_path)
    assert status["dag_viewer"]["dag_id"] == "project-dag-viewer"
    assert status["dag_viewer"]["goal_hash"] == "sha256:goal"
    assert status["dag_viewer"]["receipt_status"] == "PASS"
    assert status["dag_viewer"]["mocked"] is False
    assert status["dag_viewer"]["live"] is True
    assert status["dag_viewer"]["provider_live"] is False


def test_run_status_summarizes_project_dag_command_policy_rejection(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "dag-receipt.json",
        {
            "schema": "tau.dag_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "verdict": "COMMAND_POLICY_REJECTED",
            "mocked": False,
            "live": True,
            "dag_id": "project-dag-command-policy-network",
            "goal_hash": "sha256:goal",
            "observed_edges": [],
            "node_attempts": {},
            "errors": [
                "agent dispatch command spec /tmp/coder/tau-dispatch-command.json "
                "requires network but command policy does not allow network"
            ],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is False
    assert status["status"] == "BLOCKED"
    assert status["live"] is True
    assert status["detected_type"] == "project_dag"
    assert status["missing_required_artifacts"] == []
    assert status["project_dag"]["verdict"] == "COMMAND_POLICY_REJECTED"
    assert status["project_dag"]["error_count"] == 1
    assert status["project_dag"]["errors"] == [
        "agent dispatch command spec /tmp/coder/tau-dispatch-command.json "
        "requires network but command policy does not allow network"
    ]
    assert status["evidence_validation"] is None


def test_run_status_summarizes_project_dag_blocking_alerts(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "dag-receipt.json",
        {
            "schema": "tau.dag_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "verdict": "INVALID_COMMAND_JSON",
            "mocked": False,
            "live": True,
            "dag_id": "project-dag-max-retries",
            "goal": {"goal_hash": "sha256:goal"},
            "target": {
                "repo": "grahama1970/tau",
                "target": "scratch-project-dag-max-retries",
            },
            "observed_edges": [],
            "node_attempts": {"coder": 2, "research": 1},
            "errors": [
                "command stdout was not JSON: Expecting value: line 1 column 1 (char 0)"
            ],
            "alerts": [
                {
                    "code": "invalid_command_json",
                    "severity": "BLOCK",
                    "message": "Ready-queue node dispatch did not pass after max_attempts.",
                    "evidence": {
                        "node_id": "coder",
                        "attempts": 2,
                        "max_attempts": 2,
                        "errors": [
                            "command stdout was not JSON: Expecting value: line 1 column 1 (char 0)"
                        ],
                    },
                }
            ],
        },
    )

    status = build_run_status(tmp_path)

    assert status["ok"] is False
    assert status["status"] == "BLOCKED"
    assert status["detected_type"] == "project_dag"
    assert status["project_dag"]["goal_hash"] == "sha256:goal"
    assert status["project_dag"]["alert_count"] == 1
    assert status["project_dag"]["blocking_alert_count"] == 1
    assert status["project_dag"]["alerts"] == [
        {
            "code": "invalid_command_json",
            "severity": "BLOCK",
            "message": "Ready-queue node dispatch did not pass after max_attempts.",
            "node_id": "coder",
            "attempts": 2,
            "max_attempts": 2,
            "errors": [
                "command stdout was not JSON: Expecting value: line 1 column 1 (char 0)"
            ],
            "recommended_action": {
                "next_agent": "goal-guardian",
                "reason": "Ready-queue node dispatch did not pass after max_attempts.",
                "type": "reroute",
            },
        }
    ]


def test_run_status_summarizes_coding_evidence_receipts(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipts" / "coding" / "test-run-receipt.json"
    _write_json(
        receipt_path,
        {
            "schema": "tau.test_run_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "goal_hash": "sha256:goal",
            "policy_profile_sha256": "sha256:policy",
            "data_boundary_sha256": "sha256:boundary",
        },
    )

    status = build_run_status(tmp_path)

    assert status["coding_evidence"]["receipt_count"] == 1
    assert status["coding_evidence"]["receipts"] == [
        {
            "relative_path": "receipts/coding/test-run-receipt.json",
            "schema": "tau.test_run_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": False,
            "provider_live": False,
            "sha256": f"sha256:{_sha256(receipt_path)}",
            "goal_hash": "sha256:goal",
            "policy_profile_sha256": "sha256:policy",
            "data_boundary_sha256": "sha256:boundary",
            "attempt_count": None,
            "passed_attempt_count": None,
            "trigger": None,
            "node_id": None,
            "agent": None,
            "required_next_action": None,
            "uri": None,
            "github_read_kind": None,
            "read_only": None,
            "mutation_allowed": None,
            "debug_adapter": None,
            "debug_target": None,
            "adapter_available": None,
            "log_artifact_count": None,
            "variable_redaction_count": None,
            "dry_run": None,
            "apply_requested": None,
            "apply_eligible": None,
            "changed_file_count": None,
            "group_count": None,
            "evidence_receipt_count": None,
            "approval_required": None,
            "high_risk_path_count": None,
            "lsp_language_server": None,
            "file_count": None,
            "diagnostic_count": None,
            "diagnostics_increased": None,
            "reference_count": None,
            "rename_symbol": None,
            "rename_new_name": None,
            "rename_applied": None,
            "planned_edit_count": None,
            "policy_read_denied_count": None,
            "policy_write_denied_count": None,
        }
    ]
    assert "tau.test_run_receipt.v1" in status["coding_evidence"]["supported_schemas"]
    assert "Code correctness." in status["coding_evidence"]["does_not_prove"]


def test_run_status_summarizes_skill_composition_redteam_receipt(tmp_path: Path) -> None:
    receipt_path = tmp_path / "skill-composition-redteam-receipt.json"
    _write_json(
        receipt_path,
        {
            "schema": "tau.skill_composition_redteam_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "attempt_count": 7,
            "passed_attempt_count": 7,
        },
    )

    status = build_run_status(tmp_path)

    assert status["coding_evidence"]["receipt_count"] == 1
    assert status["coding_evidence"]["receipts"][0] == {
        "relative_path": "skill-composition-redteam-receipt.json",
        "schema": "tau.skill_composition_redteam_receipt.v1",
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "sha256": f"sha256:{_sha256(receipt_path)}",
        "goal_hash": None,
        "policy_profile_sha256": None,
        "data_boundary_sha256": None,
        "attempt_count": 7,
        "passed_attempt_count": 7,
        "trigger": None,
        "node_id": None,
        "agent": None,
        "required_next_action": None,
        "uri": None,
        "github_read_kind": None,
        "read_only": None,
        "mutation_allowed": None,
        "debug_adapter": None,
        "debug_target": None,
        "adapter_available": None,
        "log_artifact_count": None,
        "variable_redaction_count": None,
        "dry_run": None,
        "apply_requested": None,
        "apply_eligible": None,
        "changed_file_count": None,
        "group_count": None,
        "evidence_receipt_count": None,
        "approval_required": None,
        "high_risk_path_count": None,
        "lsp_language_server": None,
        "file_count": None,
        "diagnostic_count": None,
        "diagnostics_increased": None,
        "reference_count": None,
        "rename_symbol": None,
        "rename_new_name": None,
        "rename_applied": None,
        "planned_edit_count": None,
        "policy_read_denied_count": None,
        "policy_write_denied_count": None,
    }
    assert (
        "tau.skill_composition_redteam_receipt.v1"
        in status["coding_evidence"]["supported_schemas"]
    )


def test_run_status_summarizes_course_correction_routing_fields(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipts" / "course-correction-receipt.json"
    _write_json(
        receipt_path,
        {
            "schema": "tau.course_correction.v1",
            "ok": False,
            "status": "REQUIRED",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": "sha256:goal",
            "trigger": "patch_stale",
            "node_id": "coder",
            "agent": "coder",
            "attempt": 2,
            "required_next_action": "retry_node",
        },
    )

    status = build_run_status(tmp_path)

    assert status["coding_evidence"]["receipt_count"] == 1
    assert status["coding_evidence"]["receipts"][0] == {
        "relative_path": "receipts/course-correction-receipt.json",
        "schema": "tau.course_correction.v1",
        "status": "REQUIRED",
        "ok": False,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "sha256": f"sha256:{_sha256(receipt_path)}",
        "goal_hash": "sha256:goal",
        "policy_profile_sha256": None,
        "data_boundary_sha256": None,
        "attempt_count": None,
        "passed_attempt_count": None,
        "trigger": "patch_stale",
        "node_id": "coder",
        "agent": "coder",
        "required_next_action": "retry_node",
        "uri": None,
        "github_read_kind": None,
        "read_only": None,
        "mutation_allowed": None,
        "debug_adapter": None,
        "debug_target": None,
        "adapter_available": None,
        "log_artifact_count": None,
        "variable_redaction_count": None,
        "dry_run": None,
        "apply_requested": None,
        "apply_eligible": None,
        "changed_file_count": None,
        "group_count": None,
        "evidence_receipt_count": None,
        "approval_required": None,
        "high_risk_path_count": None,
        "lsp_language_server": None,
        "file_count": None,
        "diagnostic_count": None,
        "diagnostics_increased": None,
        "reference_count": None,
        "rename_symbol": None,
        "rename_new_name": None,
        "rename_applied": None,
        "planned_edit_count": None,
        "policy_read_denied_count": None,
        "policy_write_denied_count": None,
    }


def test_run_status_summarizes_github_read_boundaries(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipts" / "github-read-receipt.json"
    _write_json(
        receipt_path,
        {
            "schema": "tau.github_read_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "goal_hash": "sha256:goal",
            "uri": "issue://grahama1970/tau/67",
            "parsed": {
                "kind": "issue",
                "owner": "grahama1970",
                "repo": "tau",
                "number": 67,
            },
            "read_only": True,
            "mutation_allowed": False,
        },
    )

    status = build_run_status(tmp_path)

    assert status["coding_evidence"]["receipt_count"] == 1
    assert status["coding_evidence"]["receipts"][0] == {
        "relative_path": "receipts/github-read-receipt.json",
        "schema": "tau.github_read_receipt.v1",
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": False,
        "provider_live": False,
        "sha256": f"sha256:{_sha256(receipt_path)}",
        "goal_hash": "sha256:goal",
        "policy_profile_sha256": None,
        "data_boundary_sha256": None,
        "attempt_count": None,
        "passed_attempt_count": None,
        "trigger": None,
        "node_id": None,
        "agent": None,
        "required_next_action": None,
        "uri": "issue://grahama1970/tau/67",
        "github_read_kind": "issue",
        "read_only": True,
        "mutation_allowed": False,
        "debug_adapter": None,
        "debug_target": None,
        "adapter_available": None,
        "log_artifact_count": None,
        "variable_redaction_count": None,
        "dry_run": None,
        "apply_requested": None,
        "apply_eligible": None,
        "changed_file_count": None,
        "group_count": None,
        "evidence_receipt_count": None,
        "approval_required": None,
        "high_risk_path_count": None,
        "lsp_language_server": None,
        "file_count": None,
        "diagnostic_count": None,
        "diagnostics_increased": None,
        "reference_count": None,
        "rename_symbol": None,
        "rename_new_name": None,
        "rename_applied": None,
        "planned_edit_count": None,
        "policy_read_denied_count": None,
        "policy_write_denied_count": None,
    }


def test_run_status_summarizes_debug_session_evidence_fields(tmp_path: Path) -> None:
    stdout_path = tmp_path / "receipts" / "debug.stdout.txt"
    stderr_path = tmp_path / "receipts" / "debug.stderr.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text("stopped at answer\n", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    receipt_path = tmp_path / "receipts" / "debug-session-receipt.json"
    _write_json(
        receipt_path,
        {
            "schema": "tau.debug_session_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": "sha256:goal",
            "adapter": "debugpy",
            "target": "python -m pytest tests/test_example.py",
            "adapter_available": True,
            "log_artifacts": [
                {"label": "stdout", "path": str(stdout_path)},
                {"label": "stderr", "path": str(stderr_path)},
            ],
            "variable_redaction_count": 1,
        },
    )

    status = build_run_status(tmp_path)

    assert status["coding_evidence"]["receipt_count"] == 1
    assert status["coding_evidence"]["receipts"][0] == {
        "relative_path": "receipts/debug-session-receipt.json",
        "schema": "tau.debug_session_receipt.v1",
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "sha256": f"sha256:{_sha256(receipt_path)}",
        "goal_hash": "sha256:goal",
        "policy_profile_sha256": None,
        "data_boundary_sha256": None,
        "attempt_count": None,
        "passed_attempt_count": None,
        "trigger": None,
        "node_id": None,
        "agent": None,
        "required_next_action": None,
        "uri": None,
        "github_read_kind": None,
        "read_only": None,
        "mutation_allowed": None,
        "debug_adapter": "debugpy",
        "debug_target": "python -m pytest tests/test_example.py",
        "adapter_available": True,
        "log_artifact_count": 2,
        "variable_redaction_count": 1,
        "dry_run": None,
        "apply_requested": None,
        "apply_eligible": None,
        "changed_file_count": None,
        "group_count": None,
        "evidence_receipt_count": None,
        "approval_required": None,
        "high_risk_path_count": None,
        "lsp_language_server": None,
        "file_count": None,
        "diagnostic_count": None,
        "diagnostics_increased": None,
        "reference_count": None,
        "rename_symbol": None,
        "rename_new_name": None,
        "rename_applied": None,
        "planned_edit_count": None,
        "policy_read_denied_count": None,
        "policy_write_denied_count": None,
    }


def test_run_status_summarizes_commit_plan_review_fields(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipts" / "commit-plan-receipt.json"
    _write_json(
        receipt_path,
        {
            "schema": "tau.commit_plan_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": "sha256:goal",
            "dry_run": True,
            "apply_requested": False,
            "apply_eligible": False,
            "changed_file_count": 3,
            "group_count": 2,
            "evidence_receipt_count": 1,
            "approval_required": True,
            "high_risk_paths": [{"path": "pyproject.toml"}],
        },
    )

    status = build_run_status(tmp_path)

    assert status["coding_evidence"]["receipt_count"] == 1
    assert status["coding_evidence"]["receipts"][0] == {
        "relative_path": "receipts/commit-plan-receipt.json",
        "schema": "tau.commit_plan_receipt.v1",
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "sha256": f"sha256:{_sha256(receipt_path)}",
        "goal_hash": "sha256:goal",
        "policy_profile_sha256": None,
        "data_boundary_sha256": None,
        "attempt_count": None,
        "passed_attempt_count": None,
        "trigger": None,
        "node_id": None,
        "agent": None,
        "required_next_action": None,
        "uri": None,
        "github_read_kind": None,
        "read_only": None,
        "mutation_allowed": None,
        "debug_adapter": None,
        "debug_target": None,
        "adapter_available": None,
        "log_artifact_count": None,
        "variable_redaction_count": None,
        "dry_run": True,
        "apply_requested": False,
        "apply_eligible": False,
        "changed_file_count": 3,
        "group_count": 2,
        "evidence_receipt_count": 1,
        "approval_required": True,
        "high_risk_path_count": 1,
        "lsp_language_server": None,
        "file_count": None,
        "diagnostic_count": None,
        "diagnostics_increased": None,
        "reference_count": None,
        "rename_symbol": None,
        "rename_new_name": None,
        "rename_applied": None,
        "planned_edit_count": None,
        "policy_read_denied_count": None,
        "policy_write_denied_count": None,
    }


def test_run_status_summarizes_lsp_diagnostics_fields(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipts" / "lsp-diagnostics-receipt.json"
    _write_json(
        receipt_path,
        {
            "schema": "tau.lsp_diagnostics_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": "sha256:goal",
            "language_server_used": "ruff_json_adapter",
            "file_count": 4,
            "diagnostic_count": 2,
            "diagnostics_increased": True,
            "policy_read_denied_paths": ["secrets/app.py"],
        },
    )

    status = build_run_status(tmp_path)

    assert status["coding_evidence"]["receipt_count"] == 1
    assert status["coding_evidence"]["receipts"][0] == {
        "relative_path": "receipts/lsp-diagnostics-receipt.json",
        "schema": "tau.lsp_diagnostics_receipt.v1",
        "status": "BLOCKED",
        "ok": False,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "sha256": f"sha256:{_sha256(receipt_path)}",
        "goal_hash": "sha256:goal",
        "policy_profile_sha256": None,
        "data_boundary_sha256": None,
        "attempt_count": None,
        "passed_attempt_count": None,
        "trigger": None,
        "node_id": None,
        "agent": None,
        "required_next_action": None,
        "uri": None,
        "github_read_kind": None,
        "read_only": None,
        "mutation_allowed": None,
        "debug_adapter": None,
        "debug_target": None,
        "adapter_available": None,
        "log_artifact_count": None,
        "variable_redaction_count": None,
        "dry_run": None,
        "apply_requested": None,
        "apply_eligible": None,
        "changed_file_count": None,
        "group_count": None,
        "evidence_receipt_count": None,
        "approval_required": None,
        "high_risk_path_count": None,
        "lsp_language_server": "ruff_json_adapter",
        "file_count": 4,
        "diagnostic_count": 2,
        "diagnostics_increased": True,
        "reference_count": None,
        "rename_symbol": None,
        "rename_new_name": None,
        "rename_applied": None,
        "planned_edit_count": None,
        "policy_read_denied_count": 1,
        "policy_write_denied_count": None,
    }


def test_run_status_summarizes_lsp_rename_plan_fields(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipts" / "lsp-rename-plan-receipt.json"
    _write_json(
        receipt_path,
        {
            "schema": "tau.lsp_rename_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": "sha256:goal",
            "language_server_used": "python_ast_symbol_adapter",
            "reference_count": 3,
            "symbol": "target",
            "new_name": "renamed",
            "applied": False,
            "planned_edits": [{"file": "src/app.py"}, {"file": "tests/test_app.py"}],
            "policy_write_denied_paths": [],
        },
    )

    status = build_run_status(tmp_path)

    assert status["coding_evidence"]["receipt_count"] == 1
    assert status["coding_evidence"]["receipts"][0] == {
        "relative_path": "receipts/lsp-rename-plan-receipt.json",
        "schema": "tau.lsp_rename_receipt.v1",
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "sha256": f"sha256:{_sha256(receipt_path)}",
        "goal_hash": "sha256:goal",
        "policy_profile_sha256": None,
        "data_boundary_sha256": None,
        "attempt_count": None,
        "passed_attempt_count": None,
        "trigger": None,
        "node_id": None,
        "agent": None,
        "required_next_action": None,
        "uri": None,
        "github_read_kind": None,
        "read_only": None,
        "mutation_allowed": None,
        "debug_adapter": None,
        "debug_target": None,
        "adapter_available": None,
        "log_artifact_count": None,
        "variable_redaction_count": None,
        "dry_run": None,
        "apply_requested": None,
        "apply_eligible": None,
        "changed_file_count": None,
        "group_count": None,
        "evidence_receipt_count": None,
        "approval_required": None,
        "high_risk_path_count": None,
        "lsp_language_server": "python_ast_symbol_adapter",
        "file_count": None,
        "diagnostic_count": None,
        "diagnostics_increased": None,
        "reference_count": 3,
        "rename_symbol": "target",
        "rename_new_name": "renamed",
        "rename_applied": False,
        "planned_edit_count": 2,
        "policy_read_denied_count": None,
        "policy_write_denied_count": 0,
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
