import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.project_dag import DAG_RECEIPT_SCHEMA, run_project_dag_contract


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


def _write_response_spec(
    tmp_path: Path,
    agent: str,
    response: dict[str, object],
    *,
    sleep_seconds: float = 0.0,
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
