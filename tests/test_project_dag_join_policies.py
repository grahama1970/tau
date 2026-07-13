"""Live local ready-queue acceptance tests for declared DAG joins."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tau_coding.dag_join_decision import validate_join_decision
from tau_coding.project_dag import run_project_dag_contract


def test_conditional_skip_contributes_and_minimum_join_releases(tmp_path: Path) -> None:
    contract_path = _write_join_contract(
        tmp_path,
        policy="minimum_success_count",
        policy_parameters={"required_successes": 2},
        route="AB",
    )
    _write_response_spec(tmp_path, "router", _handoff("router", "branch-a", route="AB"))
    _write_response_spec(tmp_path, "branch-a", _handoff("branch-a", "join"))
    _write_response_spec(tmp_path, "branch-b", _handoff("branch-b", "join"))
    _write_response_spec(tmp_path, "branch-c", _handoff("branch-c", "join"))

    receipt = _run(tmp_path, contract_path)

    assert receipt["status"] == "PASS"
    assert receipt["selected_agents"][0] == "router"
    assert set(receipt["selected_agents"][1:]) == {"branch-a", "branch-b"}
    assert receipt["node_terminal_states"]["branch-c"] == "skipped"
    assert set(receipt["edge_terminal_states"].values()) >= {"success", "skipped"}
    contributions = _read_receipts(receipt["terminal_contribution_receipts"])
    assert sorted(item["state"] for item in contributions) == ["skipped", "success", "success"]
    decision = _read_receipts(receipt["join_decision_receipts"])[0]
    assert decision["decision"] == "release"
    assert decision["counts"]["success"] == 2
    assert decision["counts"]["skipped"] == 1
    join_event = next(
        index
        for index, event in enumerate(receipt["scheduler_events"])
        if event["event"] == "join_decided"
    )
    assert all(
        index < join_event
        for index, event in enumerate(receipt["scheduler_events"])
        if event["event"] == "terminal_contribution_recorded"
    )


def test_all_success_blocks_on_unselected_branch_without_stall(tmp_path: Path) -> None:
    contract_path = _write_join_contract(tmp_path, policy="all_success", route="AB")
    _write_response_spec(tmp_path, "router", _handoff("router", "branch-a", route="AB"))
    _write_response_spec(tmp_path, "branch-a", _handoff("branch-a", "join"))
    _write_response_spec(tmp_path, "branch-b", _handoff("branch-b", "join"))
    _write_response_spec(tmp_path, "branch-c", _handoff("branch-c", "join"))

    receipt = _run(tmp_path, contract_path)

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "JOIN_ALL_SUCCESS_NOT_MET"
    assert "ready_queue_stalled" not in [alert["code"] for alert in receipt["alerts"]]
    decision = _read_receipts(receipt["join_decision_receipts"])[0]
    assert decision["decision"] == "block"
    assert decision["counts"]["skipped"] == 1


def test_failed_branch_is_collected_by_all_terminal_instead_of_global_abort(
    tmp_path: Path,
) -> None:
    contract_path = _write_join_contract(tmp_path, policy="all_terminal", route="ALL")
    _write_response_spec(tmp_path, "router", _handoff("router", "branch-a", route="ALL"))
    _write_response_spec(tmp_path, "branch-a", _handoff("branch-a", "join"))
    _write_response_spec(
        tmp_path,
        "branch-b",
        _handoff("branch-b", "join"),
        exit_code=1,
    )
    _write_response_spec(tmp_path, "branch-c", _handoff("branch-c", "join"))

    receipt = _run(tmp_path, contract_path)

    assert receipt["status"] == "PASS"
    assert receipt["node_terminal_states"]["branch-b"] == "failed"
    decision = _read_receipts(receipt["join_decision_receipts"])[0]
    assert decision["decision"] == "release"
    assert decision["counts"]["failed"] == 1
    assert any(
        event["event"] == "branch_failure_contributed_to_join"
        and event["node_id"] == "branch-b"
        for event in receipt["scheduler_events"]
    )


def test_join_timeout_writes_timed_out_contribution_and_ignores_late_success(
    tmp_path: Path,
) -> None:
    contract_path = _write_join_contract(
        tmp_path,
        policy="all_terminal",
        route="ALL",
        timeout_seconds=1,
        branches=("branch-a", "branch-b"),
    )
    _write_response_spec(tmp_path, "router", _handoff("router", "branch-a", route="ALL"))
    _write_response_spec(tmp_path, "branch-a", _handoff("branch-a", "join"))
    _write_response_spec(
        tmp_path,
        "branch-b",
        _handoff("branch-b", "join"),
        sleep_seconds=1.2,
        command_timeout=3,
    )

    receipt = _run(tmp_path, contract_path)

    assert receipt["status"] == "PASS"
    decision = _read_receipts(receipt["join_decision_receipts"])[0]
    assert decision["counts"]["timed_out"] == 1
    assert any(
        event["event"] == "join_timeout_expired" for event in receipt["scheduler_events"]
    )
    assert any(
        event["event"] == "late_terminal_contribution_ignored"
        for event in receipt["scheduler_events"]
    )


def test_persisted_join_decision_replays_from_contribution_receipts(tmp_path: Path) -> None:
    contract_path = _write_join_contract(
        tmp_path,
        policy="quorum",
        policy_parameters={"quorum_fraction": {"numerator": 2, "denominator": 3}},
        route="AB",
    )
    _write_response_spec(tmp_path, "router", _handoff("router", "branch-a", route="AB"))
    _write_response_spec(tmp_path, "branch-a", _handoff("branch-a", "join"))
    _write_response_spec(tmp_path, "branch-b", _handoff("branch-b", "join"))
    _write_response_spec(tmp_path, "branch-c", _handoff("branch-c", "join"))
    receipt = _run(tmp_path, contract_path)

    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    join_policy = next(node["join"] for node in payload["nodes"] if node["id"] == "join")
    incoming_edges = [
        {
            "edge_index": index,
            "source_node_id": edge["from"],
            "target_node_id": edge["to"],
            "condition": edge.get("condition"),
        }
        for index, edge in enumerate(payload["edges"])
        if edge["to"] == "join"
    ]
    contributions = _read_receipts(receipt["terminal_contribution_receipts"])
    decision = _read_receipts(receipt["join_decision_receipts"])[0]

    replay = validate_join_decision(
        decision,
        dag_id=payload["dag_id"],
        goal_hash=payload["goal"]["goal_hash"],
        join_node_id="join",
        join_policy=join_policy,
        incoming_edges=incoming_edges,
        contributions=contributions,
    )
    assert replay == decision


def test_invalid_join_blocks_before_command_compilation(tmp_path: Path) -> None:
    contract_path = _write_join_contract(
        tmp_path,
        policy="quorum",
        route="ALL",
        policy_parameters={},
    )
    run_dir = tmp_path / "run"

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=run_dir,
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "JOIN_QUORUM_FRACTION_MISSING"
    assert receipt["selected_agents"] == []
    assert not (run_dir / "compiled-command-specs").exists()
    assert not (run_dir / "ready-queue").exists()


def _run(tmp_path: Path, contract_path: Path) -> dict[str, object]:
    return run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )


def _write_join_contract(
    tmp_path: Path,
    *,
    policy: str,
    route: str,
    policy_parameters: dict[str, object] | None = None,
    timeout_seconds: int = 5,
    branches: tuple[str, ...] = ("branch-a", "branch-b", "branch-c"),
) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    spec_root = tmp_path / "specs"
    conditions = {
        branch: {
            "schema": "tau.route_condition.v1",
            "op": "in",
            "field": "route",
            "value": ["ALL", branch.removeprefix("branch-").upper(), "AB"],
        }
        for branch in branches
    }
    if "branch-c" in conditions:
        conditions["branch-c"]["value"] = ["ALL", "C"]
    payload = {
        "schema": "tau.dag_contract.v1",
        "dag_id": f"join-{policy}",
        "goal": {
            "goal_id": "join-policy-test",
            "goal_version": 1,
            "goal_hash": "sha256:join-policy-goal",
        },
        "target": {"repo": "grahama1970/tau", "target": "join-policy-fixture"},
        "entry_node": "start",
        "terminal_nodes": ["human"],
        "limits": {
            "resume": True,
            "default_timeout_seconds": 5,
            "max_total_attempts": 8,
            "max_concurrency": len(branches),
        },
        "nodes": [
            {"id": "start", "agent": "goal-guardian", "executor": "scheduler"},
            {
                "id": "router",
                "agent": "router",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(spec_root / "router" / "tau-dispatch-command.json"),
                "route": {"mode": "fanout"},
            },
            *[
                {
                    "id": branch,
                    "agent": branch,
                    "executor": "local",
                    "max_attempts": 1,
                    "command_spec": str(spec_root / branch / "tau-dispatch-command.json"),
                }
                for branch in branches
            ],
            {
                "id": "join",
                "agent": "join",
                "executor": "local",
                "join": {
                    "schema": "tau.dag_join_policy.v1",
                    "policy": policy,
                    "timeout_seconds": timeout_seconds,
                    **(policy_parameters or {}),
                },
            },
        ],
        "edges": [
            {"from": "start", "to": "router"},
            *[
                {"from": "router", "to": branch, "condition": conditions[branch]}
                for branch in branches
            ],
            *[{"from": branch, "to": "join"} for branch in branches],
            {"from": "join", "to": "human"},
        ],
        "required_evidence": [],
        "fail_closed_on": ["unexpected_node", "unexpected_edge"],
        "fixture_route": route,
    }
    path = tmp_path / "join-contract.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _handoff(previous: str, next_agent: str, *, route: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "PASS",
        "summary": f"{previous} completed.",
        "evidence": [],
    }
    if route is not None:
        result["route"] = route
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {"repo": "grahama1970/tau", "target": "join-policy-fixture"},
        "goal": {
            "goal_id": "join-policy-test",
            "goal_version": 1,
            "goal_hash": "sha256:join-policy-goal",
        },
        "previous_subagent": previous,
        "context": {"summary": f"{previous} fixture", "artifacts": []},
        "result": result,
        "rationale": "The fixture exercises Tau's declared join policy.",
        "next_agent": {
            "name": next_agent,
            "executor": "local" if next_agent != "human" else "human",
            "reason": "Continue through the declared DAG.",
        },
        "required_evidence": [],
        "stop_condition": "Stop at the declared terminal.",
    }


def _write_response_spec(
    tmp_path: Path,
    agent: str,
    response: dict[str, object],
    *,
    sleep_seconds: float = 0.0,
    command_timeout: float = 5,
    exit_code: int = 0,
) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = ""
    if sleep_seconds:
        code += f"import time; time.sleep({sleep_seconds!r}); "
    code += f"print({json.dumps(json.dumps(response))}); raise SystemExit({exit_code})"
    spec_path.write_text(
        json.dumps(
            {
                "command": [sys.executable, "-c", code],
                "cwd": str(tmp_path),
                "timeout_s": command_timeout,
            }
        ),
        encoding="utf-8",
    )


def _read_receipts(paths: list[str]) -> list[dict[str, object]]:
    return [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
