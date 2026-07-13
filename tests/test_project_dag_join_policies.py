"""Live local ready-queue acceptance tests for declared DAG joins."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import tau_coding.project_dag as project_dag_module
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


def test_join_timeout_terminates_running_branch_without_waiting_for_command_timeout(
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
        sleep_seconds=5,
        command_timeout=10,
    )

    started = time.monotonic()
    receipt = _run(tmp_path, contract_path)
    elapsed = time.monotonic() - started

    assert receipt["status"] == "PASS"
    assert elapsed < 2.5
    assert receipt["node_terminal_states"]["branch-b"] == "cancelled"
    branch_dispatch = next(
        dispatch
        for dispatch in receipt["dispatches"]
        if dispatch.get("selected_agent") == "branch-b"
    )
    assert branch_dispatch["stop_reason"] == "command_cancelled"


def test_join_timeout_is_armed_before_any_branch_contributes(tmp_path: Path) -> None:
    contract_path = _write_join_contract(
        tmp_path,
        policy="all_terminal",
        route="ALL",
        timeout_seconds=1,
        branches=("branch-a", "branch-b"),
    )
    _write_response_spec(tmp_path, "router", _handoff("router", "branch-a", route="ALL"))
    for branch in ("branch-a", "branch-b"):
        _write_response_spec(
            tmp_path,
            branch,
            _handoff(branch, "join"),
            sleep_seconds=5,
            command_timeout=10,
        )

    started = time.monotonic()
    receipt = _run(tmp_path, contract_path)
    elapsed = time.monotonic() - started

    assert receipt["status"] == "PASS"
    assert elapsed < 2.5
    decision = _read_receipts(receipt["join_decision_receipts"])[0]
    assert decision["counts"]["timed_out"] == 2
    assert all(
        contribution["reason_code"] == "join_timeout"
        for contribution in _read_receipts(receipt["terminal_contribution_receipts"])
    )
    assert any(
        event["event"] == "join_deadline_armed" and event["origin"] == "source_start"
        for event in receipt["scheduler_events"]
    )


def test_fatal_join_block_cancels_unrelated_running_command(tmp_path: Path) -> None:
    contract_path = _write_join_contract(
        tmp_path,
        policy="all_success",
        route="ALL",
        branches=("branch-a", "branch-b"),
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["limits"]["max_concurrency"] = 3
    payload["nodes"].append(
        {
            "id": "unrelated",
            "agent": "unrelated",
            "executor": "local",
            "max_attempts": 1,
            "command_spec": str(
                tmp_path / "specs" / "unrelated" / "tau-dispatch-command.json"
            ),
        }
    )
    payload["edges"].extend(
        [
            {
                "from": "router",
                "to": "unrelated",
                "condition": {
                    "schema": "tau.route_condition.v1",
                    "op": "in",
                    "field": "route",
                    "value": ["ALL"],
                },
            },
            {"from": "unrelated", "to": "human"},
        ]
    )
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    marker = tmp_path / "unrelated-finished"
    _write_response_spec(tmp_path, "router", _handoff("router", "branch-a", route="ALL"))
    _write_response_spec(
        tmp_path,
        "branch-a",
        _handoff("branch-a", "join"),
        exit_code=1,
    )
    _write_response_spec(
        tmp_path,
        "branch-b",
        _handoff("branch-b", "join"),
        sleep_seconds=5,
        command_timeout=10,
    )
    _write_sleep_then_marker_spec(
        tmp_path,
        agent="unrelated",
        response=_handoff("unrelated", "human"),
        marker=marker,
        sleep_seconds=5,
    )

    started = time.monotonic()
    receipt = _run(tmp_path, contract_path)
    elapsed = time.monotonic() - started

    assert receipt["status"] == "BLOCKED"
    assert elapsed < 2.5
    assert not marker.exists()
    started_nodes = {
        event["node_id"]
        for event in receipt["scheduler_events"]
        if event["event"] == "node_started"
    }
    assert "unrelated" in started_nodes
    cancellation = next(
        event
        for event in receipt["scheduler_events"]
        if event["event"] == "scheduler_cancellation_signaled"
    )
    assert "unrelated" in cancellation["node_ids"]


def test_short_circuit_batches_cancelled_contributions_before_final_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branches = tuple(f"branch-{index}" for index in range(8))
    contract_path = _write_join_contract(
        tmp_path,
        policy="any_success",
        route="ALL",
        branches=branches,
    )
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["limits"]["max_concurrency"] = 1
    contract_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_response_spec(tmp_path, "router", _handoff("router", branches[0], route="ALL"))
    _write_response_spec(tmp_path, branches[0], _handoff(branches[0], "join"))
    for branch in branches[1:]:
        _write_response_spec(
            tmp_path,
            branch,
            _handoff(branch, "join"),
            sleep_seconds=5,
            command_timeout=10,
        )
    evaluations: list[str] = []
    original = project_dag_module.evaluate_join_decision

    def tracked_evaluation(**kwargs: Any) -> dict[str, Any]:
        result = original(**kwargs)
        evaluations.append(str(result["status"]))
        return result

    monkeypatch.setattr(project_dag_module, "evaluate_join_decision", tracked_evaluation)

    receipt = _run(tmp_path, contract_path)

    assert receipt["status"] == "PASS"
    assert evaluations == ["TERMINAL_INTENT", "PASS"]
    assert len(receipt["terminal_contribution_receipts"]) == len(branches)
    assert len(set(receipt["terminal_contribution_receipts"])) == len(branches)
    assert receipt["selected_agents"] == ["router", branches[0]]
    assert sum(
        event["event"] == "unstarted_join_source_suppressed"
        for event in receipt["scheduler_events"]
    ) == len(branches) - 1


def test_non_success_terminal_edge_does_not_count_as_activated_route(tmp_path: Path) -> None:
    contract_path = _write_join_contract(
        tmp_path,
        policy="all_success",
        route="AB",
    )
    _write_response_spec(tmp_path, "router", _handoff("router", "branch-a", route="AB"))
    _write_response_spec(tmp_path, "branch-a", _handoff("branch-a", "join"))
    _write_response_spec(tmp_path, "branch-b", _handoff("branch-b", "join"))
    _write_response_spec(tmp_path, "branch-c", _handoff("branch-c", "join"))

    receipt = _run(tmp_path, contract_path)

    assert receipt["status"] == "BLOCKED"
    assert receipt["activated_terminals"] == []


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


def test_join_source_with_non_join_outgoing_path_blocks_before_dispatch(tmp_path: Path) -> None:
    contract_path = _write_join_contract(tmp_path, policy="all_terminal", route="ALL")
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["edges"].append({"from": "branch-a", "to": "human"})
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = run_project_dag_contract(
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        agents_root=tmp_path / "agents",
        scheduler="bounded-ready-queue",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "JOIN_SOURCE_OUTGOING_NOT_EXCLUSIVE"
    assert receipt["selected_agents"] == []
    assert not (tmp_path / "run" / "ready-queue").exists()


def test_duplicate_source_join_edge_is_rejected_by_dag_contract(tmp_path: Path) -> None:
    contract_path = _write_join_contract(tmp_path, policy="all_terminal", route="ALL")
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    payload["edges"].append({"from": "branch-a", "to": "join"})
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="duplicate edge: branch-a->join"):
        run_project_dag_contract(
            contract_path=contract_path,
            receipt_dir=tmp_path / "run",
            agents_root=tmp_path / "agents",
            scheduler="bounded-ready-queue",
        )

    assert not (tmp_path / "run" / "ready-queue").exists()


def test_virtual_sources_contribute_without_command_attempt_binding(tmp_path: Path) -> None:
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "virtual-join-sources",
        "goal": {
            "goal_id": "virtual-join-sources",
            "goal_version": 1,
            "goal_hash": "sha256:virtual-join-sources",
        },
        "target": {"repo": "grahama1970/tau", "target": "virtual-join-sources"},
        "entry_node": "start",
        "terminal_nodes": ["human"],
        "limits": {"resume": True, "default_timeout_seconds": 5, "max_total_attempts": 1},
        "nodes": [
            {"id": "start", "agent": "goal-guardian", "executor": "scheduler"},
            {"id": "virtual-a", "agent": "virtual-a", "executor": "local"},
            {"id": "virtual-b", "agent": "virtual-b", "executor": "local"},
            {
                "id": "join",
                "agent": "join",
                "executor": "local",
                "join": {
                    "schema": "tau.dag_join_policy.v1",
                    "policy": "all_success",
                    "timeout_seconds": 5,
                },
            },
        ],
        "edges": [
            {"from": "start", "to": "virtual-a"},
            {"from": "start", "to": "virtual-b"},
            {"from": "virtual-a", "to": "join"},
            {"from": "virtual-b", "to": "join"},
            {"from": "join", "to": "human"},
        ],
        "required_evidence": [],
        "fail_closed_on": ["unexpected_node", "unexpected_edge"],
    }
    contract_path = tmp_path / "virtual-contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    receipt = _run(tmp_path, contract_path)

    assert receipt["status"] == "PASS"
    contributions = _read_receipts(receipt["terminal_contribution_receipts"])
    assert {item["basis"]["kind"] for item in contributions} == {
        "virtual_node_completed"
    }
    assert all(item["source_binding"] == {} for item in contributions)


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


def _write_sleep_then_marker_spec(
    tmp_path: Path,
    *,
    agent: str,
    response: dict[str, object],
    marker: Path,
    sleep_seconds: float,
) -> None:
    spec_path = tmp_path / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = (
        f"import pathlib, time; time.sleep({sleep_seconds!r}); "
        f"pathlib.Path({str(marker)!r}).write_text('finished'); "
        f"print({json.dumps(json.dumps(response))})"
    )
    spec_path.write_text(
        json.dumps(
            {
                "command": [sys.executable, "-c", code],
                "cwd": str(tmp_path),
                "timeout_s": 10,
            }
        ),
        encoding="utf-8",
    )


def _read_receipts(paths: list[str]) -> list[dict[str, object]]:
    return [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
