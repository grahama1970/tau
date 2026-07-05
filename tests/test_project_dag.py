import hashlib
import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.handoff_dispatch import TAU_COMMAND_SPEC_POLICY_SCHEMA
from tau_coding.project_dag import (
    DAG_ERROR_SCHEMA,
    DAG_RECEIPT_SCHEMA,
    FAIL_CLOSED_REGISTRY_SCHEMA,
    fail_closed_registry_payload,
    run_project_dag_contract,
    write_fail_closed_registry_receipt,
)


def test_project_dag_runs_creator_reviewer_loop(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["schema"] == DAG_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["selected_agents"] == ["coder", "reviewer"]
    assert receipt["observed_edges"] == [
        {
            "from_agent": "coder",
            "from_node": "coder",
            "to_agent": "reviewer",
            "to_node": "reviewer",
        },
        {
            "from_agent": "reviewer",
            "from_node": "reviewer",
            "to_agent": "human",
            "to_node": "human",
        },
    ]
    assert receipt["reviewer_verdicts"] == [
        {
            "goal_hash": "sha256:active-goal",
            "kind": "reviewer_verdict",
            "reviewed_node_id": "coder",
            "verdict": "PASS",
        }
    ]
    assert Path(str(receipt["command_loop_receipt"])).exists()


def test_project_dag_allows_repeated_agent_roles_with_node_addressing(
    tmp_path: Path,
) -> None:
    contract_path = _write_repeated_reviewer_contract(tmp_path)
    _write_response_spec(
        tmp_path,
        "coder",
        _repeated_reviewer_handoff("coder", "reviewer-a", _creator_evidence()),
    )
    _write_response_spec(
        tmp_path,
        "reviewer-a",
        _repeated_reviewer_handoff(
            "reviewer-a",
            "reviewer-b",
            [
                {
                    "kind": "reviewer_verdict",
                    "reviewed_node_id": "coder",
                    "goal_hash": "sha256:active-goal",
                    "verdict": "PASS",
                }
            ],
        ),
    )
    _write_response_spec(
        tmp_path,
        "reviewer-b",
        _repeated_reviewer_handoff(
            "reviewer-b",
            "human",
            [
                {
                    "kind": "reviewer_verdict",
                    "reviewed_node_id": "coder",
                    "goal_hash": "sha256:active-goal",
                    "verdict": "PASS",
                }
            ],
        ),
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["selected_agents"] == ["coder", "reviewer-a", "reviewer-b"]
    assert receipt["observed_edges"] == [
        {
            "from_agent": "coder",
            "from_node": "coder",
            "to_agent": "reviewer",
            "to_node": "reviewer-a",
        },
        {
            "from_agent": "reviewer",
            "from_node": "reviewer-a",
            "to_agent": "reviewer",
            "to_node": "reviewer-b",
        },
        {
            "from_agent": "reviewer",
            "from_node": "reviewer-b",
            "to_agent": "human",
            "to_node": "human",
        },
    ]


def test_project_dag_blocks_unexpected_edge(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "UNEXPECTED_EDGE"
    assert receipt["alerts"][0]["code"] == "unexpected_edge"
    assert receipt["dag_error"]["schema"] == DAG_ERROR_SCHEMA
    assert receipt["dag_error"]["failure_code"] == "unexpected_edge"
    assert receipt["dag_error"]["failed_node"] == "coder"
    assert receipt["dag_error"]["failed_agent"] == "coder"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "reroute",
        "next_agent": "goal-guardian",
        "reason": "Reconcile DAG route, goal, or target drift before continuing.",
    }


def test_project_dag_blocks_reviewer_goal_hash_mismatch(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:stale-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "REVIEWER_GOAL_HASH_MISMATCH"
    assert receipt["alerts"][0]["code"] == "reviewer_goal_hash_mismatch"


def test_cli_dag_run_dispatches_project_dag_contract(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "cli-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == DAG_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert payload["selected_agents"] == ["coder", "reviewer"]
    assert payload["reviewer_verdicts"][0]["goal_hash"] == "sha256:active-goal"


def test_cli_dag_run_dispatches_yaml_project_dag_contract(tmp_path: Path) -> None:
    contract_path = _write_yaml_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "cli-yaml-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == DAG_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert payload["contract_path"].endswith(".dag.yml")
    assert payload["selected_agents"] == ["coder", "reviewer"]


def test_project_dag_propagates_safe_context_to_command_stdin(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["context"] = {
        "persona_dream_panel": {
            "panel_id": "panel_contract",
            "run_root": "/tmp/persona-dream-active-run",
            "image_path": "/tmp/persona-dream-active-run/artifacts/panel.png",
        },
        "artifacts": ["/tmp/contract-artifact.json"],
    }
    payload["nodes"][0]["context"] = {
        "persona_dream_panel": {
            "panel_id": "panel_node",
            "run_root": "/tmp/persona-dream-node-run",
            "image_path": "/tmp/persona-dream-node-run/artifacts/panel.png",
            "panel_prompt": "Use active Embry/Kai storyboard context.",
        }
    }
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_stdin_capture_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "reviewer", _creator_evidence()),
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    request = json.loads(
        (
            tmp_path
            / "run"
            / "command-loop"
            / "command-artifacts"
            / "command-loop-step-001"
            / "request.json"
        ).read_text(encoding="utf-8")
    )
    context = request["context"]
    assert receipt["ok"] is True
    assert context["persona_dream_panel"] == {
        "panel_id": "panel_node",
        "run_root": "/tmp/persona-dream-node-run",
        "image_path": "/tmp/persona-dream-node-run/artifacts/panel.png",
        "panel_prompt": "Use active Embry/Kai storyboard context.",
    }
    assert context["artifacts"] == [str(contract_path.resolve()), "/tmp/contract-artifact.json"]
    assert context["summary"] == "Dispatch DAG contract creator-reviewer-test."


def test_project_dag_bounded_ready_queue_runs_independent_nodes_concurrently(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
        sleep_seconds=0.25,
    )
    _write_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "human", _creator_evidence()),
        sleep_seconds=0.25,
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["scheduler"] == "bounded-ready-queue"
    assert receipt["max_observed_concurrency"] >= 2
    assert set(receipt["selected_agents"]) == {"research-auditor", "coder", "reviewer"}
    assert receipt["node_attempts"] == {
        "coder": 1,
        "research": 1,
        "reviewer": 1,
    }
    assert receipt["reviewer_verdicts"][0]["goal_hash"] == "sha256:active-goal"
    assert any(
        event["event"] == "virtual_node_completed" and event["node_id"] == "start"
        for event in receipt["scheduler_events"]
    )


def test_project_dag_ready_queue_propagates_node_context_to_command_stdin(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["context"] = {
                "persona_dream_panel": {
                    "panel_id": "panel_ready_queue",
                    "run_root": "/tmp/persona-dream-ready-queue-run",
                    "image_path": "/tmp/persona-dream-ready-queue-run/artifacts/panel.png",
                }
            }
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_stdin_capture_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "human", _creator_evidence()),
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    request = json.loads(
        (
            tmp_path
            / "run"
            / "ready-queue"
            / "coder"
            / "attempt-001"
            / "request.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["ok"] is True
    assert request["context"]["persona_dream_panel"] == {
        "panel_id": "panel_ready_queue",
        "run_root": "/tmp/persona-dream-ready-queue-run",
        "image_path": "/tmp/persona-dream-ready-queue-run/artifacts/panel.png",
    }


def test_project_dag_bounded_ready_queue_recovers_after_timeout_retry(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 2
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
        sleep_seconds=0.25,
    )
    _write_flaky_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "human", _creator_evidence()),
        first_failure="timeout",
        timeout_s=0.05,
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["node_attempts"]["coder"] == 2
    assert receipt["max_observed_concurrency"] >= 2
    assert [
        event
        for event in receipt["scheduler_events"]
        if event["event"] == "node_attempt_failed" and event["node_id"] == "coder"
    ][0]["stop_reason"] == "command_timeout"


def test_project_dag_bounded_ready_queue_recovers_after_non_json_retry(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 2
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_flaky_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "human", _creator_evidence()),
        first_failure="non-json",
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["node_attempts"]["coder"] == 2
    assert [
        event
        for event in receipt["scheduler_events"]
        if event["event"] == "node_attempt_failed" and event["node_id"] == "coder"
    ][0]["stop_reason"] == "invalid_command_json"


def test_project_dag_bounded_ready_queue_blocks_after_max_retries(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 2
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_always_non_json_spec(tmp_path, "coder")
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "INVALID_COMMAND_JSON"
    assert receipt["node_attempts"]["coder"] == 2
    assert receipt["alerts"][0]["evidence"]["attempts"] == 2
    assert receipt["dag_error"]["schema"] == DAG_ERROR_SCHEMA
    assert receipt["dag_error"]["failure_code"] == "invalid_command_json"
    assert receipt["dag_error"]["failed_node"] == "coder"
    assert receipt["dag_error"]["failed_agent"] == "coder"
    assert receipt["dag_error"]["attempts"] == 2
    assert receipt["dag_error"]["max_attempts"] == 2
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_then_retry_or_reroute",
        "next_agent": "goal-guardian",
        "reason": "Repair the node command or subagent response contract before retrying.",
    }
    assert [event["retrying"] for event in receipt["scheduler_events"] if event["event"] == "node_attempt_failed"] == [
        True,
        False,
    ]


def test_project_dag_bounded_ready_queue_blocks_provider_nodes(tmp_path: Path) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_response_spec(tmp_path, "coder", _handoff("coder", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["executor"] = "provider"
            node["provider"] = {"adapter": "generic-provider-dag-node"}
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "NON_LOCAL_READY_QUEUE_NODE_NOT_ALLOWED"
    assert {alert["code"] for alert in receipt["alerts"]} == {
        "non_local_ready_queue_node_not_allowed",
        "provider_node_not_allowed",
    }


def test_cli_dag_run_bad_project_contract_returns_course_correction_json(tmp_path: Path) -> None:
    contract_path = tmp_path / "bad-project-dag.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "tau.dag_contract.v1",
                "dag_id": "bad-contract",
                "goal": {"goal_id": "bad", "goal_hash": "sha256:bad"},
                "target": {"repo": "grahama1970/tau"},
                "nodes": [],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "bad-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["schema"] == DAG_ERROR_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "dag_contract_invalid"
    assert payload["verdict"] == "DAG_CONTRACT_INVALID"
    assert "goal.goal_version must be an integer or string" in payload["message"]
    assert "target must be a non-empty string" in payload["message"]
    assert payload["recommended_action"] == {
        "type": "repair_then_retry_or_reroute",
        "next_agent": "goal-guardian",
        "reason": "Repair the DAG contract so it satisfies tau.dag_contract.v1 before dispatch.",
    }
    assert payload["proof_scope"]["proves"] == [
        "Tau rejected a malformed or incomplete DAG contract before dispatch.",
        "Tau packaged the contract failure as a project-agent course-correction payload.",
        "No DAG route, goal, target, command, or handoff was executed.",
    ]


def test_fail_closed_registry_payload_names_executable_invariants() -> None:
    payload = fail_closed_registry_payload()

    assert payload["schema"] == FAIL_CLOSED_REGISTRY_SCHEMA
    assert payload["ok"] is True
    assert payload["status"] == "ACTIVE"
    assert payload["mocked"] is False
    assert payload["live"] is False
    assert payload["provider_live"] is False
    assert payload["invariant_count"] == len(payload["invariants"])
    assert payload["invariants"]["goal_hash_mismatch"] == {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.handoff.active_goal_hash",
    }
    assert payload["invariants"]["unexpected_edge"] == {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.observed_edges",
    }
    assert payload["invariants"]["reviewer_goal_hash_mismatch"] == {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.dag.reviewer_goal_hash",
    }
    assert payload["invariants"]["missing_work_order_sha256"] == {
        "severity": "BLOCK",
        "implemented_by": "tau.validators.provider_work_order.sha256",
    }
    assert "Unknown fail_closed_on codes fail closed" in payload["proof_scope"]["proves"][2]


def test_fail_closed_registry_receipt_can_be_written(tmp_path: Path) -> None:
    output_path = tmp_path / "fail-closed-registry.json"

    payload = write_fail_closed_registry_receipt(output_path)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload == written
    assert payload["schema"] == FAIL_CLOSED_REGISTRY_SCHEMA
    assert payload["receipt_path"] == str(output_path.resolve())
    assert payload["invariants"]["target_changed"]["implemented_by"] == (
        "tau.validators.handoff.github_target"
    )


def test_cli_dag_run_unknown_fail_closed_code_returns_course_correction_json(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["fail_closed_on"].append("nebulous_goal")
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "unknown-fail-closed-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    error_payload = json.loads(result.output)

    assert result.exit_code == 1
    assert error_payload["schema"] == DAG_ERROR_SCHEMA
    assert error_payload["failure_code"] == "dag_contract_invalid"
    assert "fail_closed_on contains unknown invariant code(s): nebulous_goal" in error_payload["message"]
    assert "goal_hash_mismatch" in error_payload["message"]
    assert error_payload["recommended_action"]["next_agent"] == "goal-guardian"


def test_cli_dag_run_missing_command_spec_returns_course_correction_json(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "dag-run",
            str(contract_path),
            "--receipt-dir",
            str(tmp_path / "missing-spec-run"),
            "--agents-root",
            str(tmp_path / "agents"),
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["schema"] == DAG_ERROR_SCHEMA
    assert payload["failure_code"] == "dag_contract_invalid"
    assert "command_spec for node coder does not exist" in payload["message"]
    assert payload["evidence"]["alert_codes"] == ["dag_contract_invalid"]


def test_project_dag_evidence_manifest_allows_dispatch_when_required_kinds_exist(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    manifest = _write_evidence_manifest(tmp_path, ["creator_artifact", "reviewer_verdict"])
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["evidence_manifest"] = str(manifest)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["selected_agents"] == ["coder", "reviewer"]
    assert receipt["evidence_validation_receipt"] == str(
        tmp_path / "run" / "evidence-validation-receipt.json"
    )
    assert Path(str(receipt["evidence_validation_receipt"])).exists()


def test_project_dag_evidence_manifest_blocks_missing_required_kind(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    manifest = _write_evidence_manifest(tmp_path, ["creator_artifact"])
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["evidence_manifest"] = str(manifest)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "EVIDENCE_MANIFEST_MISSING_REQUIRED_EVIDENCE"
    assert receipt["selected_agents"] == []
    assert receipt["alerts"][0]["code"] == "evidence_manifest_missing_required_evidence"
    assert receipt["alerts"][0]["evidence"]["missing"] == ["reviewer_verdict"]
    assert receipt["dag_error"]["failure_code"] == "evidence_manifest_missing_required_evidence"
    assert Path(str(receipt["evidence_validation_receipt"])).exists()


def test_project_dag_evidence_manifest_blocks_invalid_manifest_before_dispatch(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    manifest = _write_evidence_manifest(
        tmp_path,
        ["creator_artifact", "reviewer_verdict"],
        bad_sha_for="reviewer_verdict",
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["evidence_manifest"] = str(manifest)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "EVIDENCE_MANIFEST_INVALID"
    assert receipt["selected_agents"] == []
    assert receipt["alerts"][0]["code"] == "evidence_manifest_invalid"
    assert "items[1].sha256 mismatch" in receipt["alerts"][0]["evidence"]["errors"][0]
    assert receipt["dag_error"]["failure_code"] == "evidence_manifest_invalid"


def test_project_dag_command_policy_records_hashes_in_command_results(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    policy_path = _write_command_policy(tmp_path, allowed_roots=[Path(sys.executable).name])
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["command_policy"] = str(policy_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )
    loop_receipt = json.loads(
        Path(str(receipt["command_loop_receipt"])).read_text(encoding="utf-8")
    )
    command_result = loop_receipt["dispatches"][0]["command_results"][0]

    assert receipt["ok"] is True
    assert command_result["command_policy_path"] == str(policy_path.resolve())
    assert str(command_result["command_policy_sha256"]).startswith("sha256:")
    assert str(command_result["command_spec_sha256"]).startswith("sha256:")


def test_project_dag_command_policy_blocks_denied_command_before_dispatch(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    policy_path = _write_command_policy(
        tmp_path,
        allowed_roots=[Path(sys.executable).name, "rm"],
        denied=["rm"],
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["command_policy"] = str(policy_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))
    coder_spec = tmp_path / "specs" / "coder" / "tau-dispatch-command.json"
    coder_spec.write_text(
        json.dumps({"command": ["rm", "-rf", "/tmp/tau-denied"], "timeout_s": 5}),
        encoding="utf-8",
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_AGENT_COMMAND_SPEC"
    assert "command is denied by command policy: rm" in "\n".join(receipt["errors"])
    assert receipt["dag_error"]["failure_code"] == "missing_agent_command_spec"


def test_project_dag_ready_queue_command_policy_blocks_denied_command(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    policy_path = _write_command_policy(
        tmp_path,
        allowed_roots=[Path(sys.executable).name, "rm"],
        denied=["rm"],
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["command_policy"] = str(policy_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_response_spec(tmp_path, "coder", _handoff("coder", "human", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))
    coder_spec = tmp_path / "specs" / "coder" / "tau-dispatch-command.json"
    coder_spec.write_text(
        json.dumps({"command": ["rm", "-rf", "/tmp/tau-denied"], "timeout_s": 5}),
        encoding="utf-8",
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["dag_error"]["failed_node"] == "coder"
    assert "command is denied by command policy: rm" in "\n".join(receipt["errors"])


def test_project_dag_blocks_missing_required_evidence_with_reviewer_action(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", []))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_REQUIRED_EVIDENCE"
    assert receipt["dag_error"]["failure_code"] == "missing_required_evidence"
    assert receipt["dag_error"]["failed_node"] == "coder"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "reroute",
        "next_agent": "reviewer",
        "reason": "Inspect missing or inconsistent evidence before normal continuation.",
    }


def test_project_dag_blocks_reviewer_target_mismatch(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    reviewer = _reviewer_handoff(goal_hash="sha256:active-goal")
    reviewer["result"]["evidence"][0]["reviewed_node_id"] = "different-node"  # type: ignore[index]
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", reviewer)

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "REVIEWER_TARGET_MISMATCH"
    assert receipt["dag_error"]["failure_code"] == "reviewer_target_mismatch"
    assert receipt["dag_error"]["failed_node"] == "reviewer"


def test_project_dag_bounded_ready_queue_blocks_cycles(tmp_path: Path) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["edges"].append({"from": "reviewer", "to": "start"})
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "CYCLE_DETECTED"
    assert receipt["dag_error"]["failure_code"] == "cycle_detected"
    assert receipt["dag_error"]["recommended_action"]["next_agent"] == "goal-guardian"


def test_project_dag_bounded_ready_queue_blocks_mutating_nodes(tmp_path: Path) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["mutates"] = True
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MUTATING_NODE_NOT_ALLOWED"
    assert receipt["dag_error"]["failure_code"] == "mutating_node_not_allowed"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "request_policy_gate",
        "next_agent": "goal-guardian",
        "reason": "This branch requires an explicit policy or branch-lock gate before execution.",
    }


def _write_contract(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    spec_root = tmp_path / "specs"
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "creator-reviewer-test",
        "goal": {
            "goal_id": "creator-reviewer-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-creator-reviewer",
        },
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 3,
        },
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "coder" / "tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "reviewer" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
                "reviewer": {
                    "reviews_node": "coder",
                    "requires_goal_hash": True,
                },
            },
        ],
        "edges": [
            {"from": "coder", "to": "reviewer"},
            {"from": "reviewer", "to": "human"},
        ],
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "max_attempts_exceeded",
            "malformed_handoff",
        ],
    }
    path = tmp_path / "dag-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _write_repeated_reviewer_contract(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    spec_root = tmp_path / "specs"
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "repeated-reviewer-test",
        "goal": {
            "goal_id": "repeated-reviewer-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-repeated-reviewer",
        },
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 4,
        },
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "coder" / "tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
            },
            {
                "id": "reviewer-a",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "reviewer-a" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
                "reviewer": {
                    "reviews_node": "coder",
                    "requires_goal_hash": True,
                },
            },
            {
                "id": "reviewer-b",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "reviewer-b" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
                "reviewer": {
                    "reviews_node": "coder",
                    "requires_goal_hash": True,
                },
            },
        ],
        "edges": [
            {"from": "coder", "to": "reviewer-a"},
            {"from": "reviewer-a", "to": "reviewer-b"},
            {"from": "reviewer-b", "to": "human"},
        ],
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "max_attempts_exceeded",
            "malformed_handoff",
        ],
    }
    path = tmp_path / "repeated-reviewer-dag-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _write_yaml_contract(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    spec_root = tmp_path / "specs"
    path = tmp_path / "creator-reviewer.dag.yml"
    path.write_text(
        f"""schema: tau.dag_contract.v1
dag_id: creator-reviewer-test
goal:
  goal_id: creator-reviewer-test
  goal_version: 1
  goal_hash: sha256:active-goal
target:
  repo: grahama1970/tau
  target: scratch-creator-reviewer
entry_node: coder
terminal_nodes:
  - human
limits:
  resume: true
  default_timeout_seconds: 30
  max_total_attempts: 3
nodes:
  - id: coder
    agent: coder
    executor: local
    max_attempts: 1
    command_spec: {spec_root / "coder" / "tau-dispatch-command.json"}
    required_evidence:
      - creator_artifact
  - id: reviewer
    agent: reviewer
    executor: local
    max_attempts: 1
    command_spec: {spec_root / "reviewer" / "tau-dispatch-command.json"}
    required_evidence:
      - reviewer_verdict
    reviewer:
      reviews_node: coder
      requires_goal_hash: true
edges:
  - from: coder
    to: reviewer
  - from: reviewer
    to: human
required_evidence:
  - creator_artifact
  - reviewer_verdict
fail_closed_on:
  - goal_hash_mismatch
  - target_changed
  - unexpected_node
  - unexpected_edge
  - missing_required_evidence
  - max_attempts_exceeded
  - malformed_handoff
""",
        encoding="utf-8",
    )
    return path


def _write_parallel_contract(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    spec_root = tmp_path / "specs"
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "parallel-creator-reviewer-test",
        "goal": {
            "goal_id": "creator-reviewer-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "scratch-creator-reviewer",
        },
        "entry_node": "start",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 30,
            "max_total_attempts": 4,
            "max_concurrency": 2,
        },
        "nodes": [
            {
                "id": "start",
                "agent": "goal-guardian",
                "executor": "scheduler",
                "max_attempts": 1,
                "required_evidence": [],
            },
            {
                "id": "research",
                "agent": "research-auditor",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "research-auditor" / "tau-dispatch-command.json"),
                "required_evidence": ["source_summary"],
            },
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "coder" / "tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
            },
            {
                "id": "reviewer",
                "agent": "reviewer",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "reviewer" / "tau-dispatch-command.json"),
                "required_evidence": ["reviewer_verdict"],
                "reviewer": {
                    "reviews_node": "coder",
                    "requires_goal_hash": True,
                },
            },
        ],
        "edges": [
            {"from": "start", "to": "research"},
            {"from": "start", "to": "coder"},
            {"from": "research", "to": "reviewer"},
            {"from": "coder", "to": "reviewer"},
            {"from": "reviewer", "to": "human"},
        ],
        "required_evidence": ["source_summary", "creator_artifact", "reviewer_verdict"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "missing_required_join",
            "max_attempts_exceeded",
            "malformed_handoff",
        ],
    }
    path = tmp_path / "parallel-dag-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _write_evidence_manifest(
    tmp_path: Path,
    kinds: list[str],
    *,
    bad_sha_for: str | None = None,
) -> Path:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, object]] = []
    for kind in kinds:
        evidence_path = evidence_dir / f"{kind}.json"
        schema = f"tau.{kind}.v1"
        evidence_path.write_text(
            json.dumps(
                {
                    "schema": schema,
                    "kind": kind,
                    "goal_hash": "sha256:active-goal",
                    "status": "PASS",
                }
            ),
            encoding="utf-8",
        )
        digest = f"sha256:{hashlib.sha256(evidence_path.read_bytes()).hexdigest()}"
        if kind == bad_sha_for:
            digest = "sha256:" + ("0" * 64)
        items.append(
            {
                "kind": kind,
                "path": str(evidence_path),
                "sha256": digest,
                "schema": schema,
                "validator": f"tau evidence-validate {kind}",
                "valid": True,
            }
        )
    manifest_path = tmp_path / "evidence-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "tau.evidence_manifest.v1",
                "run_id": "run-001",
                "dag_id": "creator-reviewer-test",
                "goal_hash": "sha256:active-goal",
                "items": items,
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def _write_command_policy(
    tmp_path: Path,
    *,
    allowed_roots: list[str],
    denied: list[str] | None = None,
) -> Path:
    policy_path = tmp_path / "command-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema": TAU_COMMAND_SPEC_POLICY_SCHEMA,
                "allowed_command_roots": allowed_roots,
                "denied_commands": denied or [],
                "allowed_cwd_roots": [str(tmp_path)],
            }
        ),
        encoding="utf-8",
    )
    return policy_path


def _write_response_spec(
    tmp_path: Path,
    agent: str,
    response: dict[str, object],
    *,
    sleep_seconds: float = 0.0,
    timeout_s: float = 5,
) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = ""
    if sleep_seconds:
        code += f"import time; time.sleep({sleep_seconds!r}); "
    code += f"print({json.dumps(json.dumps(response))})"
    spec_path.write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    code,
                ],
                "timeout_s": timeout_s,
            }
        ),
        encoding="utf-8",
    )


def _write_stdin_capture_response_spec(
    tmp_path: Path,
    agent: str,
    response: dict[str, object],
    *,
    timeout_s: float = 5,
) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = f"""
import json
import os
import sys
from pathlib import Path

payload = json.loads(sys.stdin.readline())
artifact_dir = Path(os.environ["TAU_HANDOFF_COMMAND_ARTIFACT_DIR"])
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "request.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
print({json.dumps(json.dumps(response))})
"""
    spec_path.write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    code,
                ],
                "timeout_s": timeout_s,
            }
        ),
        encoding="utf-8",
    )


def _write_flaky_response_spec(
    tmp_path: Path,
    agent: str,
    response: dict[str, object],
    *,
    first_failure: str,
    timeout_s: float = 5,
) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    state_path = spec_path.parent / "attempt-count.txt"
    if first_failure == "timeout":
        failure_code = "import time; time.sleep(0.2)"
    elif first_failure == "non-json":
        failure_code = "print('not json')"
    else:  # pragma: no cover - helper contract guard.
        raise AssertionError(f"unknown first_failure: {first_failure}")
    code = f"""
from pathlib import Path
import json
state = Path({str(state_path)!r})
count = int(state.read_text() or '0') if state.exists() else 0
state.write_text(str(count + 1))
if count == 0:
    {failure_code}
else:
    print({json.dumps(json.dumps(response))})
"""
    spec_path.write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    code,
                ],
                "timeout_s": timeout_s,
            }
        ),
        encoding="utf-8",
    )


def _write_always_non_json_spec(tmp_path: Path, agent: str) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        json.dumps(
            {
                "command": [sys.executable, "-c", "print('not json')"],
                "timeout_s": 5,
            }
        ),
        encoding="utf-8",
    )


def _handoff(
    previous_subagent: str,
    next_agent: str,
    evidence: list[object],
) -> dict[str, object]:
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": "grahama1970/tau",
            "target": "scratch-creator-reviewer",
        },
        "goal": {
            "goal_id": "creator-reviewer-test",
            "goal_version": 1,
            "goal_hash": "sha256:active-goal",
        },
        "previous_subagent": previous_subagent,
        "context": {
            "summary": f"{previous_subagent} node response.",
            "artifacts": [],
        },
        "result": {
            "status": "PASS",
            "summary": f"{previous_subagent} completed.",
            "evidence": evidence,
        },
        "rationale": "The DAG contract controls the next route.",
        "next_agent": {
            "name": next_agent,
            "executor": "human" if next_agent == "human" else "local",
            "reason": "Continue along the DAG route.",
        },
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "stop_condition": "Stop at human.",
    }


def _repeated_reviewer_handoff(
    previous_subagent: str,
    next_agent: str,
    evidence: list[object],
) -> dict[str, object]:
    payload = _handoff(previous_subagent, next_agent, evidence)
    payload["github"] = {
        "repo": "grahama1970/tau",
        "target": "scratch-repeated-reviewer",
    }
    payload["goal"] = {
        "goal_id": "repeated-reviewer-test",
        "goal_version": 1,
        "goal_hash": "sha256:active-goal",
    }
    return payload


def _creator_evidence() -> list[object]:
    return [{"kind": "creator_artifact", "path": "/tmp/creator-artifact.txt"}]


def _reviewer_handoff(*, goal_hash: str) -> dict[str, object]:
    response = _handoff(
        "reviewer",
        "human",
        [
            {
                "kind": "reviewer_verdict",
                "reviewed_node_id": "coder",
                "goal_hash": goal_hash,
                "verdict": "PASS",
            }
        ],
    )
    response["goal"] = {
        "goal_id": "creator-reviewer-test",
        "goal_version": 1,
        "goal_hash": "sha256:active-goal",
    }
    return response
