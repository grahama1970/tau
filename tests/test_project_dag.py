import hashlib
import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.handoff_dispatch import TAU_COMMAND_SPEC_POLICY_SCHEMA
from tau_coding.project_dag import (
    DAG_ERROR_SCHEMA,
    DAG_PROGRESS_SCHEMA,
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


def test_project_dag_writes_live_subagent_progress_for_handoff_loop(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    progress_path = tmp_path / "run" / "dag-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert receipt["progress_path"] == str(progress_path)
    assert progress["schema"] == DAG_PROGRESS_SCHEMA
    assert progress["status"] == "PASS"
    assert progress["mocked"] is False
    assert progress["live"] is True
    assert progress["active_subagents"] == []
    assert progress["completed_subagents"] == [
        {"agent": "coder", "node_id": "coder"},
        {"agent": "reviewer", "node_id": "reviewer"},
    ]
    assert [
        (event["event"], event["selected_agent"])
        for event in progress["events"]
        if event["event"] == "step_started"
    ] == [("step_started", "coder"), ("step_started", "reviewer")]


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
        (tmp_path / "run" / "ready-queue" / "coder" / "attempt-001" / "request.json").read_text(
            encoding="utf-8"
        )
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
    retrying = [
        event["retrying"]
        for event in receipt["scheduler_events"]
        if event["event"] == "node_attempt_failed"
    ]
    assert retrying == [
        True,
        False,
    ]


def test_project_dag_ready_queue_blocks_pointless_unit_test_drift(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 3
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_pointless_unit_test_failure_spec(tmp_path, "coder")
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "POINTLESS_UNIT_TEST_DRIFT"
    assert receipt["alerts"][0]["code"] == "pointless_unit_test_drift"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "reroute",
        "next_agent": "reviewer",
        "reason": "Inspect missing or inconsistent evidence before normal continuation.",
    }
    assert len(receipt["course_correction_artifacts"]) == 1
    course_correction = json.loads(
        Path(receipt["course_correction_artifacts"][0]).read_text(encoding="utf-8")
    )
    assert course_correction["code"] == "pointless_unit_test_drift"
    assert course_correction["trigger"] == "pointless_unit_test_drift"
    assert course_correction["required_next_action"] == (
        "stop_test_churn_report_blocker_and_replan"
    )
    assert "run_more_unrelated_tests" in course_correction["forbidden_next_routes"]
    assert course_correction["required_action"]["skill_reference"] == "$brave-search"
    assert course_correction["blocked_report_required"] == {
        "required": True,
        "fields": [
            "blocker_summary",
            "attempted_fix",
            "why_test_churn_is_not_progress",
            "next_non_test_action",
            "brave_search_receipt_path",
        ],
        "reason": (
            "Blocked subagents must report the blocker and course correction "
            "instead of continuing non-essential deterministic unit tests."
        ),
    }


def test_project_dag_ready_queue_requires_brave_search_after_two_failed_attempts(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "coder":
            node["max_attempts"] = 3
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
    assert receipt["verdict"] == "BRAVE_SEARCH_REQUIRED_AFTER_TWO_ATTEMPTS"
    assert receipt["node_attempts"]["coder"] == 2
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "run_brave_search_then_retry",
        "next_agent": "goal-guardian",
        "reason": "Require $brave-search research before another attempt.",
    }
    assert len(receipt["course_correction_artifacts"]) == 1
    course_correction = json.loads(
        Path(receipt["course_correction_artifacts"][0]).read_text(encoding="utf-8")
    )
    assert course_correction["schema"] == "tau.course_correction.v1"
    assert course_correction["code"] == "brave_search_required_after_two_attempts"
    assert course_correction["trigger"] == "brave_search_required_after_two_attempts"
    assert course_correction["required_next_action"] == "run_brave_search_then_retry"
    assert "retry_without_research_receipt" in course_correction["forbidden_next_routes"]
    assert course_correction["required_action"]["skill"] == "brave-search"
    assert "brave_search.py" in course_correction["required_action"]["command"][1]
    assert course_correction["blocked_report_required"]["required"] is True
    assert "blocker_summary" in course_correction["blocked_report_required"]["fields"]


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


def test_project_dag_zero_trust_blocks_missing_data_boundary(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_DATA_BOUNDARY"
    assert receipt["alerts"][0]["code"] == "missing_data_boundary"
    assert receipt["dag_error"]["schema"] == DAG_ERROR_SCHEMA
    assert receipt["dag_error"]["failure_code"] == "missing_data_boundary"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_then_retry_or_reroute",
        "next_agent": "goal-guardian",
        "reason": "Repair zero-trust policy/data-boundary gates before DAG dispatch.",
    }
    assert Path(str(receipt["zero_trust_preflight_receipt"])).exists()


def test_project_dag_zero_trust_blocks_invalid_data_boundary(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    boundary_path = _write_data_boundary(tmp_path, {"classification": None})
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    payload["data_boundary"] = str(boundary_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["alerts"][0]["code"] == "missing_classification"
    assert "classification must be one of" in receipt["errors"][0]


def test_project_dag_zero_trust_allows_valid_public_boundary(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    payload["data_boundary"] = _public_data_boundary()
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"


def test_project_dag_memory_gate_blocks_missing_intent(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    policy = _zero_trust_policy()
    policy["memory"].update(
        {
            "intent_required": True,
            "evidence_case_required_for": ["COMPLIANCE"],
            "min_intent_confidence": 0.75,
        }
    )
    payload["policy_profile"] = policy
    payload["data_boundary"] = _public_data_boundary()
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_MEMORY_INTENT"
    assert receipt["alerts"][0]["code"] == "missing_memory_intent"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_memory_evidence_gate",
        "next_agent": "goal-guardian",
        "reason": (
            "Repair Graph Memory intent and create-evidence-case artifacts before "
            "zero-trust DAG dispatch."
        ),
    }
    assert Path(str(receipt["memory_intent_gate_receipt"])).exists()
    assert Path(str(receipt["evidence_case_gate_receipt"])).exists()


def test_project_dag_memory_gate_blocks_inline_intent_evidence(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    policy = _zero_trust_policy()
    policy["memory"].update({"intent_required": True, "min_intent_confidence": 0.75})
    intent = _memory_intent()
    intent["evidence"] = [{"claim": "inline evidence should be rejected"}]
    payload["policy_profile"] = policy
    payload["data_boundary"] = _public_data_boundary()
    payload["memory_intent"] = intent
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["alerts"][0]["code"] == "intent_contains_inline_evidence"


def test_project_dag_memory_gate_honors_policy_min_intent_confidence(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    policy = _zero_trust_policy()
    policy["memory"].update(
        {
            "intent_required": True,
            "evidence_case_required_for": ["COMPLIANCE"],
            "min_intent_confidence": 0.75,
        }
    )
    intent = _memory_intent()
    intent["confidence"] = 0.6
    payload["policy_profile"] = policy
    payload["data_boundary"] = _public_data_boundary()
    payload["memory_intent"] = intent
    payload["evidence_case"] = _memory_evidence_case()
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["alerts"][0]["code"] == "memory_intent_low_confidence"
    memory_receipt = json.loads(
        Path(str(receipt["memory_intent_gate_receipt"])).read_text(encoding="utf-8")
    )
    assert memory_receipt["alerts"][0]["evidence"] == {
        "confidence": 0.6,
        "minimum": 0.75,
    }


def test_project_dag_memory_gate_allows_valid_intent_and_evidence_case(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    policy = _zero_trust_policy()
    policy["memory"].update(
        {
            "intent_required": True,
            "evidence_case_required_for": ["COMPLIANCE"],
            "min_intent_confidence": 0.75,
        }
    )
    payload["policy_profile"] = policy
    payload["data_boundary"] = _public_data_boundary()
    payload["memory_intent"] = _memory_intent()
    payload["evidence_case"] = _memory_evidence_case()
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert Path(str(receipt["memory_intent_gate_receipt"])).exists()
    assert Path(str(receipt["evidence_case_gate_receipt"])).exists()


def test_project_dag_zero_trust_blocks_external_provider_when_policy_denies(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["policy_profile"] = str(_write_zero_trust_policy(tmp_path))
    payload["data_boundary"] = _public_data_boundary()
    payload["nodes"][0]["provider"] = {"adapter": "generic-provider-dag-node"}
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["alerts"][0]["code"] == "external_provider_denied"


def test_project_dag_blocks_underspecified_provider_sensitive_contract_before_dispatch(
    tmp_path: Path,
) -> None:
    contract_path = _write_provider_sensitive_contract(tmp_path, complete=False)

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    alert_codes = {alert["code"] for alert in receipt["alerts"]}
    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "PROVIDER_POLICY_MISSING"
    assert receipt["selected_agents"] == []
    assert not (tmp_path / "run" / "command-loop").exists()
    assert receipt["dag_error"]["schema"] == DAG_ERROR_SCHEMA
    assert receipt["dag_error"]["failure_code"] == "provider_policy_missing"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_provider_policy",
        "next_agent": "goal-guardian",
        "reason": (
            "Add explicit model_policy, prompt_contract, provider/auth/model route, "
            "and provider-route evidence before dispatch."
        ),
    }
    assert {
        "provider_policy_missing",
        "model_unspecified",
        "missing_prompt_contract",
        "missing_required_evidence",
    }.issubset(alert_codes)
    assert all(
        alert["evidence"]["node_id"] in {"panel-creator", "panel-reviewer"}
        for alert in receipt["alerts"]
    )


def test_project_dag_allows_provider_sensitive_contract_with_policy_prompt_and_evidence(
    tmp_path: Path,
) -> None:
    contract_path = _write_provider_sensitive_contract(tmp_path, complete=True)
    _write_stdin_capture_response_spec(
        tmp_path,
        "panel-creator",
        _persona_dream_provider_handoff(
            "panel-creator",
            "panel-reviewer",
            [
                {"kind": "storyboard_creator_receipt.json"},
                {"kind": "provider_route_receipt"},
            ],
        ),
    )
    _write_response_spec(
        tmp_path,
        "panel-reviewer",
        _persona_dream_provider_handoff(
            "panel-reviewer",
            "human",
            [
                {"kind": "storyboard_review_verdict.json"},
                {"kind": "provider_route_receipt"},
            ],
        ),
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["selected_agents"] == ["panel-creator", "panel-reviewer"]
    compiled_creator_spec = json.loads(
        (
            tmp_path
            / "run"
            / "compiled-command-specs"
            / "panel-creator"
            / "tau-dispatch-command.json"
        ).read_text(encoding="utf-8")
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
    model_policy = {
        "provider": "scillm",
        "auth": "codex-oauth",
        "model": "gpt-image-2",
    }
    prompt_contract = {
        "schema": "tau.prompt_contract.v1",
        "system_prompt": "Stay inside the immutable storyboard goal.",
        "user_template": "Use the provider route evidence before claiming PASS.",
    }
    assert compiled_creator_spec["tau_dag_node"]["model_policy"] == model_policy
    assert compiled_creator_spec["tau_dag_node"]["prompt_contract"] == prompt_contract
    assert compiled_creator_spec["tau_dag_node"]["required_evidence"] == [
        "storyboard_creator_receipt.json",
        "provider_route_receipt",
    ]
    assert request["context"]["model_policy"] == model_policy
    assert request["context"]["prompt_contract"] == prompt_contract
    assert request["context"]["tau_dag_node"]["model_policy"] == model_policy
    assert request["context"]["tau_dag_node"]["prompt_contract"] == prompt_contract


def test_project_dag_provider_sensitive_command_without_spec_timeout_uses_tau_policy(
    tmp_path: Path,
) -> None:
    contract_path = _write_provider_sensitive_contract(tmp_path, complete=True)
    _write_stdin_capture_response_spec(
        tmp_path,
        "panel-creator",
        _persona_dream_provider_handoff(
            "panel-creator",
            "panel-reviewer",
            [
                {"kind": "storyboard_creator_receipt.json"},
                {"kind": "provider_route_receipt"},
            ],
        ),
    )
    creator_spec_path = tmp_path / "specs" / "panel-creator" / "tau-dispatch-command.json"
    creator_spec = json.loads(creator_spec_path.read_text(encoding="utf-8"))
    creator_spec.pop("timeout_s")
    creator_spec_path.write_text(json.dumps(creator_spec), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "panel-reviewer",
        _persona_dream_provider_handoff(
            "panel-reviewer",
            "human",
            [
                {"kind": "storyboard_review_verdict.json"},
                {"kind": "provider_route_receipt"},
            ],
        ),
    )

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )
    loop_receipt = json.loads(
        Path(str(receipt["command_loop_receipt"])).read_text(encoding="utf-8")
    )
    command_result = loop_receipt["dispatches"][0]["command_results"][0]
    compiled_creator_spec = json.loads(
        (
            tmp_path
            / "run"
            / "compiled-command-specs"
            / "panel-creator"
            / "tau-dispatch-command.json"
        ).read_text(encoding="utf-8")
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

    assert receipt["ok"] is True
    assert command_result["timeout_s"] == 900.0
    assert command_result["timeout_s_source"] == "tau_provider_command_timeout_policy"
    assert command_result["timeout_policy"]["schema"] == "tau.provider_command_timeout_policy.v1"
    assert compiled_creator_spec["tau_dag_node"]["timeout_policy"]["timeout_s"] == 900.0
    assert request["context"]["tau_dag_node"]["timeout_policy"]["timeout_s"] == 900.0


def test_project_dag_memory_evidence_gate_allows_valid_artifacts(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    memory_path = _write_memory_intent(tmp_path)
    evidence_case_path = _write_evidence_case(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["memory_intent"] = str(memory_path)
    payload["evidence_case"] = str(evidence_case_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert Path(tmp_path / "run" / "memory-intent-gate-receipt.json").exists()
    assert Path(tmp_path / "run" / "evidence-case-gate-receipt.json").exists()


def test_project_dag_memory_evidence_gate_blocks_inline_evidence(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    memory_path = _write_memory_intent(
        tmp_path,
        overrides={"evidence": [{"statement": "inline evidence must not dispatch"}]},
    )
    evidence_case_path = _write_evidence_case(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["memory_intent"] = str(memory_path)
    payload["evidence_case"] = str(evidence_case_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "INLINE_MEMORY_EVIDENCE_REJECTED"
    assert receipt["selected_agents"] == []
    assert receipt["alerts"][0]["code"] == "inline_memory_evidence_rejected"
    assert receipt["memory_intent_gate_receipt"] == str(
        tmp_path / "run" / "memory-intent-gate-receipt.json"
    )
    assert receipt["evidence_case_gate_receipt"] == str(
        tmp_path / "run" / "evidence-case-gate-receipt.json"
    )
    assert receipt["dag_error"]["recommended_action"]["type"] == "repair_memory_intent"


def test_project_dag_memory_evidence_gate_blocks_clarify_route(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    memory_path = _write_memory_intent(tmp_path, overrides={"route": "CLARIFY"})
    evidence_case_path = _write_evidence_case(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["memory_intent"] = str(memory_path)
    payload["evidence_case"] = str(evidence_case_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MEMORY_ROUTE_NOT_DISPATCHABLE"
    assert receipt["selected_agents"] == []
    assert receipt["alerts"][0]["code"] == "memory_route_not_dispatchable"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "request_memory_clarification",
        "next_agent": "human",
        "reason": (
            "Memory routed to clarification or deflection; resolve that route before DAG dispatch."
        ),
    }


def test_project_dag_memory_evidence_gate_blocks_missing_case_hash(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    memory_path = _write_memory_intent(tmp_path)
    evidence_case_path = _write_evidence_case(tmp_path, overrides={"case_sha256": None})
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["memory_intent"] = str(memory_path)
    payload["evidence_case"] = str(evidence_case_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_EVIDENCE_CASE_HASH"
    assert receipt["alerts"][0]["code"] == "missing_evidence_case_hash"
    assert receipt["dag_error"]["recommended_action"]["type"] == "repair_evidence_case"


def test_project_dag_legacy_contract_still_runs_without_policy_profile(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    _write_response_spec(tmp_path, "coder", _handoff("coder", "reviewer", _creator_evidence()))
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is True
    assert receipt.get("zero_trust_preflight_receipt") is None


def test_project_dag_controlled_boundary_requires_itar_access_gate(tmp_path: Path) -> None:
    contract_path = _write_contract(tmp_path)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["data_boundary"] = {
        "schema": "tau.data_boundary.v1",
        "classification": "ITAR",
        "goal_hash": "sha256:active-goal",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
    }
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "MISSING_ITAR_ACCESS_PREFLIGHT"
    assert receipt["selected_agents"] == []
    assert receipt["alerts"][0]["code"] == "missing_itar_access_preflight"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_actor_access_gate",
        "next_agent": "goal-guardian",
        "reason": "Run or repair the ITAR actor/access preflight receipt before DAG dispatch.",
    }


def test_project_dag_dispatches_when_containment_gate_receipts_pass(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    itar_receipt = _write_gate_receipt(
        tmp_path / "itar-access-preflight-receipt.json",
        schema="tau.itar_access_preflight_receipt.v1",
        goal_hash="sha256:active-goal",
    )
    research_receipt = _write_gate_receipt(
        tmp_path / "research-query-safety-receipt.json",
        schema="tau.research_query_safety_receipt.v1",
        goal_hash="sha256:active-goal",
    )
    sandbox_receipt = _write_gate_receipt(
        tmp_path / "sandbox-run-receipt.json",
        schema="tau.sandbox_run_receipt.v1",
        goal_hash="sha256:active-goal",
    )
    package_receipt = _write_gate_receipt(
        tmp_path / "compliance-package-validation-receipt.json",
        schema="tau.compliance_package_validation_receipt.v1",
        goal_hash="sha256:active-goal",
        extra={"review_ready": True, "compliant": "NOT_CLAIMED"},
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["data_boundary"] = {
        "schema": "tau.data_boundary.v1",
        "classification": "ITAR",
        "goal_hash": "sha256:active-goal",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
    }
    payload["requires_external_research"] = True
    payload["requires_sandbox"] = True
    payload["requires_compliance_package_validation"] = True
    payload["itar_access_preflight_receipt"] = str(itar_receipt)
    payload["research_query_safety_receipt"] = str(research_receipt)
    payload["sandbox_run_receipt"] = str(sandbox_receipt)
    payload["compliance_package_validation_receipt"] = str(package_receipt)
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
    assert receipt["containment_gate_receipts"] == {
        "itar_access_preflight": str(itar_receipt.resolve()),
        "research_query_safety": str(research_receipt.resolve()),
        "sandbox_run": str(sandbox_receipt.resolve()),
        "compliance_package_validation": str(package_receipt.resolve()),
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
    assert (
        "fail_closed_on contains unknown invariant code(s): nebulous_goal"
        in error_payload["message"]
    )
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
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_evidence_manifest",
        "next_agent": "reviewer",
        "reason": "Repair or regenerate the typed evidence manifest before normal continuation.",
    }
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
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_evidence_manifest",
        "next_agent": "reviewer",
        "reason": "Repair or regenerate the typed evidence manifest before normal continuation.",
    }


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
    assert receipt["verdict"] == "COMMAND_POLICY_REJECTED"
    assert "command is denied by command policy: rm" in "\n".join(receipt["errors"])
    assert receipt["dag_error"]["failure_code"] == "command_policy_rejected"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_command_policy",
        "next_agent": "goal-guardian",
        "reason": "Repair the command spec or trust policy before retrying the DAG.",
    }


def test_project_dag_command_policy_blocks_implicit_cwd_when_roots_are_restricted(
    tmp_path: Path,
) -> None:
    contract_path = _write_contract(tmp_path)
    policy_path = _write_command_policy(tmp_path, allowed_roots=[Path(sys.executable).name])
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["command_policy"] = str(policy_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(
        tmp_path,
        "coder",
        _handoff("coder", "reviewer", _creator_evidence()),
        include_cwd=False,
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "COMMAND_POLICY_REJECTED"
    assert "cwd must be explicit when command policy allowed_cwd_roots is set" in "\n".join(
        receipt["errors"]
    )


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
    assert receipt["dag_error"]["failure_code"] == "command_policy_rejected"
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


def test_project_dag_blocks_provider_auth_failure_before_evidence_retry(
    tmp_path: Path,
) -> None:
    contract_path = _write_parallel_contract(tmp_path)
    _write_response_spec(
        tmp_path,
        "research-auditor",
        _handoff("research-auditor", "human", [{"kind": "source_summary"}]),
    )
    _write_response_spec(
        tmp_path,
        "coder",
        _handoff(
            "coder",
            "reviewer",
            [
                {
                    "kind": "storyboard_identity_review",
                    "schema": "persona_dream.identity_continuity_review.v1",
                    "status": "FAIL",
                    "blocking_findings": [
                        "identity review call failed: HTTP Error 401: Unauthorized",
                        "image generation failed: 403 PERMISSION_DENIED: leaked API key",
                    ],
                    "model_policy": {
                        "provider": "codex",
                        "auth": "codex-oauth",
                        "model": "gpt-5.5",
                    },
                }
            ],
        ),
    )
    _write_response_spec(tmp_path, "reviewer", _reviewer_handoff(goal_hash="sha256:active-goal"))

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "PROVIDER_AUTH_REQUIRED"
    assert receipt["alerts"][0]["code"] == "provider_auth_required"
    assert receipt["dag_error"]["failure_code"] == "provider_auth_required"
    assert receipt["dag_error"]["failed_node"] == "coder"
    assert receipt["dag_error"]["recommended_action"] == {
        "type": "repair_provider_auth",
        "next_agent": "goal-guardian",
        "reason": "Refresh provider OAuth/readiness before retrying the DAG node.",
    }
    assert len(receipt["course_correction_artifacts"]) == 1
    course_correction = json.loads(
        Path(receipt["course_correction_artifacts"][0]).read_text(encoding="utf-8")
    )
    assert course_correction["schema"] == "tau.course_correction.v1"
    assert course_correction["trigger"] == "provider_auth_required"
    assert (
        course_correction["required_next_action"]
        == "repair_provider_auth_then_retry_or_route_human"
    )
    assert "regenerate_artifacts_before_auth_repair" in course_correction[
        "forbidden_next_routes"
    ]
    assert "provider_auth_repair_receipt" in course_correction[
        "required_evidence_before_retry"
    ]
    assert course_correction["required_action"]["type"] == "repair_provider_auth"
    assert (
        course_correction["required_action"]["repair_function"]
        == "tau_coding.battle_scillm.preflight_battle_scillm_auth"
    )
    assert course_correction["required_action"]["required_receipt_schemas"] == [
        "tau.battle_scillm_auth_preflight.v1",
        "tau.provider_readiness_run_receipt.v1",
    ]
    assert (
        "identity review call failed: HTTP Error 401: Unauthorized"
        in course_correction["observed_state"]["errors"]
    )
    assert (
        "image generation failed: 403 PERMISSION_DENIED: leaked API key"
        in course_correction["observed_state"]["errors"]
    )


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


def _write_gate_receipt(
    path: Path,
    *,
    schema: str,
    goal_hash: str,
    extra: dict[str, object] | None = None,
) -> Path:
    payload: dict[str, object] = {
        "schema": schema,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "goal_hash": goal_hash,
        "receipt_path": str(path),
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
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


def _write_provider_sensitive_contract(tmp_path: Path, *, complete: bool) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    spec_root = tmp_path / "specs"
    creator_evidence = ["storyboard_creator_receipt.json"]
    reviewer_evidence = ["storyboard_review_verdict.json"]
    if complete:
        creator_evidence.append("provider_route_receipt")
        reviewer_evidence.append("provider_route_receipt")
    nodes: list[dict[str, object]] = [
        {
            "id": "panel-creator",
            "agent": "panel-creator",
            "executor": "local",
            "max_attempts": 2,
            "command_spec": str(spec_root / "panel-creator" / "tau-dispatch-command.json"),
            "required_evidence": creator_evidence,
        },
        {
            "id": "panel-reviewer",
            "agent": "panel-reviewer",
            "executor": "local",
            "max_attempts": 2,
            "command_spec": str(spec_root / "panel-reviewer" / "tau-dispatch-command.json"),
            "required_evidence": reviewer_evidence,
        },
        {
            "id": "human",
            "agent": "human",
            "executor": "human",
        },
    ]
    if complete:
        for node in nodes:
            if node["id"] == "human":
                continue
            node["model_policy"] = {
                "provider": "scillm",
                "auth": "codex-oauth",
                "model": "gpt-image-2",
            }
            node["prompt_contract"] = {
                "schema": "tau.prompt_contract.v1",
                "system_prompt": "Stay inside the immutable storyboard goal.",
                "user_template": "Use the provider route evidence before claiming PASS.",
            }
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "persona-dream-phase-07-storyboard-packet-panel-review",
        "provider_sensitive": True,
        "goal": {
            "goal_id": "persona-dream-phase-07-storyboard-panels-accepted",
            "goal_version": 1,
            "goal_hash": "sha256:phase07-storyboard-panels-accepted-20260705",
        },
        "target": {
            "repo": "grahama1970/agent-skills",
            "target": "skills/persona-dream/reports/pipeline-complete/phase_07_storyboard_live_tau",
        },
        "entry_node": "panel-creator",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": False,
            "default_timeout_seconds": 120,
            "max_total_attempts": 4,
        },
        "nodes": nodes,
        "edges": [
            {"from": "panel-creator", "to": "panel-reviewer"},
            {"from": "panel-reviewer", "to": "human"},
        ],
        "required_evidence": [
            "dag-receipt.json",
            *creator_evidence,
            *reviewer_evidence,
        ],
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
    path = tmp_path / "provider-sensitive-dag-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _persona_dream_provider_handoff(
    previous: str,
    next_agent: str,
    evidence: list[dict],
) -> dict:
    payload = _handoff(previous, next_agent, evidence)
    payload["github"] = {
        "repo": "grahama1970/agent-skills",
        "target": "skills/persona-dream/reports/pipeline-complete/phase_07_storyboard_live_tau",
    }
    payload["goal"] = {
        "goal_id": "persona-dream-phase-07-storyboard-panels-accepted",
        "goal_version": 1,
        "goal_hash": "sha256:phase07-storyboard-panels-accepted-20260705",
    }
    return payload


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


def _write_zero_trust_policy(tmp_path: Path) -> Path:
    path = tmp_path / "zero-trust-policy.json"
    path.write_text(json.dumps(_zero_trust_policy()), encoding="utf-8")
    return path


def _write_data_boundary(tmp_path: Path, overrides: dict[str, object] | None = None) -> Path:
    path = tmp_path / "data-boundary.json"
    payload = _public_data_boundary()
    if overrides:
        payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_memory_intent(
    tmp_path: Path,
    *,
    overrides: dict[str, object] | None = None,
) -> Path:
    payload: dict[str, object] = {
        "schema": "memory.intent.v1",
        "memory_first": True,
        "route": "ANSWER",
        "confidence": 0.91,
        "goal_hash": "sha256:active-goal",
        "target": {"repo": "grahama1970/tau", "target": "scratch-creator-reviewer"},
    }
    if overrides:
        payload.update(overrides)
    path = tmp_path / "memory-intent.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_evidence_case(
    tmp_path: Path,
    *,
    overrides: dict[str, object | None] | None = None,
) -> Path:
    payload: dict[str, object | None] = {
        "schema": "tau.evidence_case.v1",
        "case_id": "case-001",
        "case_sha256": "sha256:" + ("1" * 64),
        "goal_hash": "sha256:active-goal",
        "target": {"repo": "grahama1970/tau", "target": "scratch-creator-reviewer"},
        "support_artifacts": [],
    }
    if overrides:
        payload.update(overrides)
    path = tmp_path / "evidence-case.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _zero_trust_policy() -> dict:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "itar-zero-trust-local-only",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {"default": "deny", "allowed_domains": []},
        "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
        "research": {"external_search": "deny", "manual_sanitized_receipt": "allow_with_review"},
        "memory": {"read": "allow", "write": "approval_required"},
        "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
        "filesystem": {"write_allowlist": [], "read_denylist": []},
    }


def _public_data_boundary() -> dict:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
        "foreign_person_access": "allowed",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }


def _memory_intent() -> dict:
    return {
        "schema": "memory.intent.v1",
        "memory_first": True,
        "planner_only": True,
        "route": "COMPLIANCE",
        "confidence": 0.91,
        "recall_profile": "proof_retrieval",
        "required_artifacts": [],
        "tool_calls": [{"name": "create_evidence_case"}],
        "evidence_case_required": True,
    }


def _memory_evidence_case() -> dict:
    return {
        "schema": "memory.evidence_case.v1",
        "source": "graph-memory-operator:/create-evidence-case",
        "sha256": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
        "question": "Can Tau dispatch this zero-trust DAG?",
        "data_boundary": _public_data_boundary(),
        "policy_profile": {
            "schema": "tau.policy_profile.v1",
            "profile_id": "itar-zero-trust-local-only",
            "default_decision": "deny",
        },
    }


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
    include_cwd: bool = True,
) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = ""
    if sleep_seconds:
        code += f"import time; time.sleep({sleep_seconds!r}); "
    code += f"print({json.dumps(json.dumps(response))})"
    payload: dict[str, object] = {
        "command": [
            sys.executable,
            "-c",
            code,
        ],
        "timeout_s": timeout_s,
    }
    if include_cwd:
        payload["cwd"] = str(tmp_path)
    spec_path.write_text(
        json.dumps(payload),
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


def _write_pointless_unit_test_failure_spec(tmp_path: Path, agent: str) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        json.dumps(
            {
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "print('pytest tests/test_unrelated.py -q\\ncollected 1 item\\nFAILED'); "
                        "raise SystemExit(1)"
                    ),
                ],
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
