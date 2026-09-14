from __future__ import annotations

import ast
import inspect
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime import boundary_registry, scheduler
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlanNode, DagPlanTerminal, FrozenJson, canonical_sha256
from tau_coding.dag_runtime.node_input_manifest import DagNodeDispatchAdmissionError
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.dag_runtime.transition import (
    AllSuccessTransitionPolicy,
    DagNodeCancellation,
    DagNodeSettlement,
    DagRunBlock,
    DagTransitionBatch,
)


def test_scheduler_boundary_registry_is_architecturally_enforced() -> None:
    registry_ids = {item.boundary_id for item in boundary_registry._BOUNDARIES}
    for code, boundary_id in boundary_registry._ORIGINAL_CODE_BOUNDARIES.items():
        assert boundary_registry.boundary_for_original_code(code).boundary_id == boundary_id
        assert boundary_id in registry_ids

    source = inspect.getsource(scheduler)
    tree = ast.parse(source)
    raw_attempt_blocks: list[tuple[str, int]] = []
    raw_blocked_assignments: list[tuple[str, int]] = []
    allowed_functions = {
        "_triaged_blocked_attempt_result",
        "_blocked_plan_validation_result",
        "fallback_boundary_failure",
        "_workspace_stale_read_blocked_result",
        "_boundary_blocked_result",
    }
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs = {
            key.value: value
            for key, value in zip(node.keys, node.values, strict=False)
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        status = pairs.get("status")
        if not (
            isinstance(status, ast.Constant)
            and status.value == "BLOCKED"
            and "node_id" in pairs
        ):
            continue
        parent = node
        function_name = "<module>"
        while parent in parents:
            parent = parents[parent]
            if isinstance(parent, ast.FunctionDef):
                function_name = parent.name
                break
        if function_name not in allowed_functions:
            raw_attempt_blocks.append((function_name, node.lineno))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (isinstance(value, ast.Constant) and value.value == "BLOCKED"):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            key = target.slice
            if not (isinstance(key, ast.Constant) and key.value == "status"):
                continue
            parent = node
            function_name = "<module>"
            while parent in parents:
                parent = parents[parent]
                if isinstance(parent, ast.FunctionDef):
                    function_name = parent.name
                    break
            if function_name not in allowed_functions:
                raw_blocked_assignments.append((function_name, node.lineno))
    assert raw_attempt_blocks == []
    assert raw_blocked_assignments == []


def test_scheduler_restores_finished_blocked_boundary_metadata_without_redispatch(
    tmp_path: Path,
) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(tmp_path, [_node(tmp_path, "producer")]),
        source_path=tmp_path / "dag.json",
    )
    store = SqliteDagRunStore(tmp_path / "dag.sqlite3")

    def malformed(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, execution
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "FAIL",
            "accepted_output": {"source_node_id": node.node_id},
        }

    first = run_dag_plan(
        plan,
        execute_node=malformed,
        run_store=store,
        run_id="restore-blocked",
        allow_lease_takeover=True,
    )
    first_boundary = first.node_results[0]["diagnostics"]["scheduler_boundary"]

    redispatched: list[str] = []

    def forbidden(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, execution
        redispatched.append(node.node_id)
        raise AssertionError("finished blocked run redispatched")

    replayed = run_dag_plan(
        plan,
        execute_node=forbidden,
        run_store=store,
        run_id="restore-blocked",
        allow_lease_takeover=True,
    )
    replayed_boundary = replayed.node_results[0]["diagnostics"]["scheduler_boundary"]

    assert redispatched == []
    assert replayed.status == "BLOCKED"
    assert replayed.node_results[0]["node_id"] == "producer"
    assert replayed_boundary["boundary_id"] == first_boundary["boundary_id"]
    assert replayed_boundary["repair_category"] == first_boundary["repair_category"]
    assert (
        replayed_boundary["failure"]["failure_family"]
        == first_boundary["failure"]["failure_family"]
    )


def test_dag_plan_scheduler_runs_independent_nodes_concurrently(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(
            tmp_path,
            [
                _node(tmp_path, "left"),
                _node(tmp_path, "right"),
                _node(tmp_path, "join", depends_on=["left", "right"]),
            ],
        ),
        source_path=tmp_path / "dag.json",
    )
    barrier = threading.Barrier(2)
    observed_inputs: dict[str, tuple[str, ...]] = {}

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del execution
        if node.node_id in {"left", "right"}:
            barrier.wait(timeout=1)
            time.sleep(0.01)
        observed_inputs[node.node_id] = tuple(
            str(item["source_node_id"]) for item in accepted_inputs
        )
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": node.node_id},
        }

    result = run_dag_plan(plan, execute_node=execute, max_concurrency=2)

    assert result.status == "PASS"
    assert result.max_observed_concurrency == 2
    assert set(result.completed_node_ids) == {"left", "right", "join"}
    assert observed_inputs["join"] == ("left", "right")


def test_scheduler_preserves_declared_accepted_input_order(tmp_path: Path) -> None:
    payload = _generic_spec(
        tmp_path,
        [
            _node(tmp_path, "z-source"),
            _node(tmp_path, "a-source"),
            _node(tmp_path, "consumer", depends_on=["z-source", "a-source"]),
        ],
    )
    payload["nodes"][2]["accepted_context_from"] = [  # type: ignore[index]
        "z-source",
        "a-source",
    ]
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")
    observed: list[str] = []

    def execute(node, accepted_inputs, execution):  # type: ignore[no-untyped-def]
        del execution
        if node.node_id == "consumer":
            observed.extend(str(item["source_node_id"]) for item in accepted_inputs)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": node.node_id},
        }

    result = run_dag_plan(plan, execute_node=execute, max_concurrency=2)

    assert result.status == "PASS"
    assert observed == ["z-source", "a-source"]


def test_dag_plan_scheduler_blocks_downstream_after_adapter_failure(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(
            tmp_path,
            [
                _node(tmp_path, "producer"),
                _node(tmp_path, "consumer", depends_on=["producer"]),
            ],
        ),
        source_path=tmp_path / "dag.json",
    )
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, execution
        called.append(node.node_id)
        return {
            "node_id": node.node_id,
            "status": "BLOCKED",
            "verdict": "INVALID_RECEIPT",
            "errors": ["receipt schema mismatch"],
        }

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "BLOCKED"
    assert result.verdict == "INVALID_RECEIPT"
    assert called == ["producer"]



def test_scheduler_compacts_large_command_results_before_attempt_admission(
    tmp_path: Path,
) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(tmp_path, [_node(tmp_path, "producer")]),
        source_path=tmp_path / "dag.json",
    )

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, execution
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": node.node_id},
            "command_results": [
                {
                    "command": "python - <<'PY'",
                    "exit_code": 0,
                    "stdout": "x" * 32000,
                    "stderr": "",
                }
            ],
        }

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "PASS"
    command_result = result.node_results[0]["command_results"][0]
    assert "stdout" not in command_result
    assert command_result["stdout_bytes"] == 32000
    assert command_result["full_result_bytes"] > 32000
    assert "stdout_sha256" in command_result
    assert command_result["exit_code"] == 0

def test_scheduler_rejects_invalid_dispatch_before_adapter_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(tmp_path, [_node(tmp_path, "producer")]),
        source_path=tmp_path / "dag.json",
    )
    called: list[str] = []

    def reject_dispatch(**kwargs: Any) -> None:
        del kwargs
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_goal_hash_mismatch", "$.goal_hash"
        )

    def classify(signal: str, *, layer: str) -> dict[str, Any]:
        assert layer == "tau"
        assert signal == "dag_node_dispatch_goal_hash_mismatch:$.goal_hash"
        return {
            "code": "tau_unclassified_33333333",
            "layer": "tau",
            "cause": "dispatch envelope goal hash mismatch",
            "next_command": "rebuild dispatch envelope from scheduler state",
            "ambiguous": True,
        }

    monkeypatch.setattr(
        "tau_coding.dag_runtime.scheduler.validate_node_dispatch_projection_against_scheduler_state",
        reject_dispatch,
    )
    monkeypatch.setattr("tau_coding.dag_runtime.scheduler.classify_tau_failure", classify)

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, execution
        called.append(node.node_id)
        return {"node_id": node.node_id, "status": "PASS", "verdict": "PASS"}

    result = run_dag_plan(plan, execute_node=execute)

    assert called == []
    assert result.status == "BLOCKED"
    assert result.node_results[0]["status"] == "BLOCKED"
    assert "dag_node_dispatch_goal_hash_mismatch" in result.node_results[0]["alert_codes"]
    failure = result.node_results[0]["diagnostics"]["scheduler_boundary"]["failure"]
    assert failure["original_code"] == "dag_node_dispatch_goal_hash_mismatch"


def test_scheduler_blocks_downstream_after_malformed_attempt_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(
            tmp_path,
            [
                _node(tmp_path, "producer"),
                _node(tmp_path, "consumer", depends_on=["producer"]),
            ],
        ),
        source_path=tmp_path / "dag.json",
    )
    called: list[str] = []

    def classify(signal: str, *, layer: str) -> dict[str, Any]:
        assert layer == "tau"
        assert signal == "dag_attempt_result_pass_verdict_mismatch:$.verdict"
        return {
            "code": "tau_unclassified_11111111",
            "layer": "tau",
            "cause": "malformed DAG node result",
            "next_command": "file a repair ticket",
            "ambiguous": True,
        }

    monkeypatch.setattr("tau_coding.dag_runtime.scheduler.classify_tau_failure", classify)

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, execution
        called.append(node.node_id)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "FAIL",
            "accepted_output": {"source_node_id": node.node_id},
        }

    result = run_dag_plan(plan, execute_node=execute)

    assert called == ["producer"]
    assert result.status == "BLOCKED"
    assert result.verdict == "triage_contract_invalid"
    assert result.completed_node_ids == ()
    assert result.node_results[0]["status"] == "BLOCKED"
    assert result.node_results[0]["errors"] == [
        "Classifier output failed Tau triage contract validation."
    ]
    assert result.node_results[0]["alert_codes"] == [
        "triage_contract_invalid",
        "dag_attempt_result_pass_verdict_mismatch",
        "attempt_result_admission",
    ]
    boundary = result.node_results[0]["diagnostics"]["scheduler_boundary"]
    assert boundary["boundary_id"] == "attempt_result_admission"
    assert boundary["repair_category"] == "repairable_contract_failure"
    failure = boundary["failure"]
    assert failure["schema"] == "tau.internal_failure.v1"
    assert failure["original_code"] == "dag_attempt_result_pass_verdict_mismatch"
    assert failure["path"] == "$.verdict"
    assert failure["boundary_id"] == "attempt_result_admission"
    assert failure["repair_category"] == "repairable_contract_failure"
    assert failure["triage"]["code"] == "triage_contract_invalid"
    assert failure["triage"]["disposition"] == "CONTRACT_INVALID"
    assert failure["triage"]["requires_human"] is True
    assert "next_command" not in failure["triage"]


def test_scheduler_triages_adapter_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(tmp_path, [_node(tmp_path, "producer")]),
        source_path=tmp_path / "dag.json",
    )

    def classify(signal: str, *, layer: str) -> dict[str, Any]:
        assert layer == "tau"
        assert signal == "dag_node_future_exception:RuntimeError:adapter exploded"
        return {
            "code": "tau_unclassified_22222222",
            "layer": "tau",
            "cause": "adapter exploded",
            "next_command": "run debugger",
            "ambiguous": True,
        }

    monkeypatch.setattr("tau_coding.dag_runtime.scheduler.classify_tau_failure", classify)

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del node, accepted_inputs, execution
        raise RuntimeError("adapter exploded")

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "BLOCKED"
    assert result.verdict == "triage_contract_invalid"
    boundary = result.node_results[0]["diagnostics"]["scheduler_boundary"]
    assert boundary["failure"]["original_code"] == "ADAPTER_EXECUTION_FAILED"
    assert boundary["failure"]["boundary_id"] == "adapter_future_execution"
    assert boundary["repair_category"] == "repairable_infrastructure_failure"
    triage = boundary["failure"]["triage"]
    assert triage["code"] == "triage_contract_invalid"
    assert triage["disposition"] == "CONTRACT_INVALID"
    assert triage["requires_human"] is True
    assert "next_command" not in triage


def test_scheduler_boundary_recursive_failure_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(tmp_path, [_node(tmp_path, "producer")]),
        source_path=tmp_path / "dag.json",
    )

    def classify(signal: str, *, layer: str) -> dict[str, Any]:
        del signal, layer
        raise RuntimeError("classifier down")

    monkeypatch.setattr("tau_coding.dag_runtime.scheduler.classify_tau_failure", classify)

    def execute(node, accepted_inputs, execution):  # type: ignore[no-untyped-def]
        del node, accepted_inputs, execution
        raise RuntimeError("adapter exploded")

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "BLOCKED"
    boundary = result.node_results[0]["diagnostics"]["scheduler_boundary"]
    assert boundary["boundary_id"] == "recursive_failure_object_admission"
    assert result.node_results[0]["retryable"] is False
    assert boundary["failure"]["classification_code"] == "tau_scheduler_boundary_fallback"


def test_dag_plan_scheduler_signals_running_sibling_after_failure(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(tmp_path, [_node(tmp_path, "fail"), _node(tmp_path, "sibling")]),
        source_path=tmp_path / "dag.json",
    )
    barrier = threading.Barrier(2)
    sibling_cancelled = threading.Event()

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs
        barrier.wait(timeout=1)
        if node.node_id == "fail":
            return {"node_id": "fail", "status": "BLOCKED", "verdict": "FAILED"}
        if execution.cancel_event.wait(timeout=1):
            sibling_cancelled.set()
        return {"node_id": "sibling", "status": "BLOCKED", "verdict": "CANCELLED"}

    result = run_dag_plan(plan, execute_node=execute, max_concurrency=2)

    assert result.status == "BLOCKED"
    assert sibling_cancelled.is_set()
    assert dict(result.node_states)["sibling"] == "cancelled"


def test_dag_plan_scheduler_preserves_completed_sibling_after_failure(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(tmp_path, [_node(tmp_path, "fail"), _node(tmp_path, "pass")]),
        source_path=tmp_path / "dag.json",
    )
    barrier = threading.Barrier(2)
    events: list[dict[str, Any]] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, execution
        barrier.wait(timeout=1)
        if node.node_id == "fail":
            return {"node_id": "fail", "status": "BLOCKED", "verdict": "FAILED"}
        return {
            "node_id": "pass",
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": "pass"},
        }

    result = run_dag_plan(
        plan,
        execute_node=execute,
        max_concurrency=2,
        event_sink=events.append,
    )

    assert result.status == "BLOCKED"
    assert result.completed_node_ids == ("pass",)
    assert {item["node_id"]: item["verdict"] for item in result.node_results} == {
        "fail": "FAILED",
        "pass": "PASS",
    }
    assert {item["event"] for item in events if item.get("node_id") == "pass"} == {
        "node_started",
        "node_completed",
    }


def test_dag_plan_scheduler_owns_bounded_retries(tmp_path: Path) -> None:
    payload = _generic_spec(tmp_path, [_node(tmp_path, "flaky")])
    payload["nodes"][0]["max_attempts"] = 2  # type: ignore[index]
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")
    observed_attempts: list[int] = []
    events: list[dict[str, Any]] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del node, accepted_inputs
        observed_attempts.append(execution.attempt)
        if execution.attempt == 1:
            return {
                "node_id": "flaky",
                "status": "BLOCKED",
                "verdict": "TRANSIENT_FAILURE",
                "errors": ["first attempt failed"],
            }
        return {
            "node_id": "flaky",
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": "flaky"},
        }

    result = run_dag_plan(plan, execute_node=execute, event_sink=events.append)

    assert result.status == "PASS"
    assert observed_attempts == [1, 2]
    assert result.node_results[0]["attempt_count"] == 2
    assert [item["verdict"] for item in result.node_results[0]["scheduler_attempts"]] == [
        "TRANSIENT_FAILURE",
        "PASS",
    ]
    assert [item["event"] for item in events].count("node_retry_scheduled") == 1


def test_scheduler_does_not_duplicate_command_history_across_retries(tmp_path: Path) -> None:
    payload = _generic_spec(tmp_path, [_node(tmp_path, "flaky")])
    payload["nodes"][0]["max_attempts"] = 3  # type: ignore[index]
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")

    def execute(node, accepted_inputs, execution):  # type: ignore[no-untyped-def]
        del node, accepted_inputs
        passed = execution.attempt == 3
        return {
            "node_id": "flaky",
            "status": "PASS" if passed else "BLOCKED",
            "verdict": "PASS" if passed else "TRANSIENT_FAILURE",
            "accepted_output": {"source_node_id": "flaky"} if passed else None,
            "extensions": {"command_results": [{"attempt": execution.attempt}]},
        }

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "PASS"
    assert result.node_results[0]["extensions"]["command_results"] == [
        {"attempt": 1},
        {"attempt": 2},
        {"attempt": 3},
    ]


def test_scheduler_compacts_large_dispatch_before_attempt_result_admission(
    tmp_path: Path,
) -> None:
    payload = _generic_spec(tmp_path, [_node(tmp_path, "producer")])
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")
    large_dispatch = {
        "schema": "tau.agent_handoff_command_dispatch_receipt.v1",
        "status": "COMPLETED",
        "ok": True,
        "command_results": [
            {
                "command": ["python", "worker.py", "x" * 20_000],
                "returncode": 0,
                "stdout": "x" * 40_000,
                "stderr": "",
                "runtime_capture": {"stdout": "y" * 40_000},
            }
            for _ in range(20)
        ],
        "artifacts": [str(tmp_path / f"artifact-{index}.txt") for index in range(200)],
    }

    def execute(node, accepted_inputs, execution):  # type: ignore[no-untyped-def]
        del accepted_inputs, execution
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": node.node_id},
            "dispatch": large_dispatch,
        }

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "PASS"
    runtime = result.node_results[0]["extensions"]["runtime"]
    dispatch = runtime["dispatch"]
    assert dispatch["full_dispatch_bytes"] > 80_000
    assert dispatch["full_dispatch_sha256"] == canonical_sha256(large_dispatch)
    assert "stdout" not in dispatch["command_results"][0]
    assert dispatch["command_results"][0]["command"]["truncated"] is True
    assert dispatch["command_results"][-1]["omitted_count"] == 12
    assert dispatch["artifacts"]["truncated"] is True
    assert len(json.dumps(result.node_results[0]["extensions"]).encode("utf-8")) < 16_384


def test_dag_plan_scheduler_respects_non_retryable_adapter_result(tmp_path: Path) -> None:
    payload = _generic_spec(tmp_path, [_node(tmp_path, "blocked")])
    payload["nodes"][0]["max_attempts"] = 3  # type: ignore[index]
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")
    calls = 0

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        nonlocal calls
        del node, accepted_inputs, execution
        calls += 1
        return {
            "node_id": "blocked",
            "status": "BLOCKED",
            "verdict": "POLICY_DENIED",
            "retryable": False,
        }

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "BLOCKED"
    assert calls == 1


def test_dag_plan_scheduler_settles_declared_terminal_without_executing_it(
    tmp_path: Path,
) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(
            tmp_path,
            [
                _node(tmp_path, "producer"),
                _node(tmp_path, "human", depends_on=["producer"]),
            ],
        ),
        source_path=tmp_path / "dag.json",
    )
    plan = replace(
        plan,
        terminal_endpoints=(DagPlanTerminal("human", "declared_node", "declared"),),
    ).with_computed_hash()
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, execution
        called.append(node.node_id)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": node.node_id},
        }

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "PASS"
    assert result.completed_node_ids == ("producer",)
    assert called == ["producer"]


def test_base_scheduler_rejects_route_contract_without_route_adapter(tmp_path: Path) -> None:
    payload = _generic_spec(
        tmp_path,
        [
            _node(tmp_path, "producer"),
            _node(tmp_path, "consumer", depends_on=["producer"]),
        ],
    )
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")
    route_contract = {
        "source_node_id": "producer",
        "ordered_edge_ids": [plan.control_edges[0].edge_id],
    }
    route_contract["contract_sha256"] = canonical_sha256(route_contract)
    plan = replace(
        plan,
        route_contracts=(FrozenJson.from_value(route_contract),),
    ).with_computed_hash()

    with pytest.raises(RuntimeError, match="dag_transition_policy_required"):
        run_dag_plan(plan, execute_node=lambda node, inputs, execution: {})


def test_scheduler_applies_pre_start_node_cancellation_before_dispatch(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(tmp_path, [_node(tmp_path, "suppressed")]),
        source_path=tmp_path / "dag.json",
    )
    called: list[str] = []

    class CancelBeforeStartPolicy(AllSuccessTransitionPolicy):
        def before_node_start(self, view, node_id, attempt):  # type: ignore[no-untyped-def]
            del view, attempt
            return DagTransitionBatch(
                node_cancellations=(
                    DagNodeCancellation(node_id=node_id, reason_code="policy_suppressed"),
                ),
                block_run=DagRunBlock(
                    failure_code="POLICY_SUPPRESSED",
                    message="Policy suppressed dispatch.",
                    evidence={"node_id": node_id},
                ),
            )

    result = run_dag_plan(
        plan,
        execute_node=lambda node, inputs, execution: called.append(node.node_id) or {},
        transition_policy=CancelBeforeStartPolicy(),
    )

    assert called == []
    assert result.status == "BLOCKED"
    assert result.verdict == "POLICY_SUPPRESSED"
    assert dict(result.node_states) == {"suppressed": "cancelled"}
    assert result.node_results[0]["status"] == "CANCELLED"


def test_scheduler_applies_pre_start_node_settlement_before_dispatch(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(tmp_path, [_node(tmp_path, "already-satisfied")]),
        source_path=tmp_path / "dag.json",
    )
    called: list[str] = []

    class SettleBeforeStartPolicy(AllSuccessTransitionPolicy):
        def before_node_start(self, view, node_id, attempt):  # type: ignore[no-untyped-def]
            del view, attempt
            return DagTransitionBatch(
                node_settlements=(
                    DagNodeSettlement(
                        node_id=node_id,
                        state="success",
                        reason_code="accepted_existing_result",
                    ),
                )
            )

    result = run_dag_plan(
        plan,
        execute_node=lambda node, inputs, execution: called.append(node.node_id) or {},
        transition_policy=SettleBeforeStartPolicy(),
    )

    assert called == []
    assert result.status == "PASS"
    assert result.completed_node_ids == ("already-satisfied",)
    assert dict(result.node_states) == {"already-satisfied": "success"}


def test_scheduler_does_not_retry_cancelled_running_node(tmp_path: Path) -> None:
    payload = _generic_spec(
        tmp_path,
        [_node(tmp_path, "fast"), _node(tmp_path, "slow")],
    )
    payload["nodes"][1]["max_attempts"] = 3  # type: ignore[index]
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")
    slow_started = threading.Event()
    slow_calls = 0

    class CancelSlowPolicy(AllSuccessTransitionPolicy):
        def after_node_terminal(self, view, completion):  # type: ignore[no-untyped-def]
            if completion.node_id == "fast":
                return DagTransitionBatch(
                    node_cancellations=(
                        DagNodeCancellation(node_id="slow", reason_code="short_circuit"),
                    )
                )
            return super().after_node_terminal(view, completion)

    def execute(node, accepted_inputs, execution):  # type: ignore[no-untyped-def]
        nonlocal slow_calls
        del accepted_inputs
        if node.node_id == "fast":
            assert slow_started.wait(timeout=1)
            return {"node_id": "fast", "status": "PASS", "verdict": "PASS"}
        slow_calls += 1
        slow_started.set()
        assert execution.cancel_event.wait(timeout=1)
        return {
            "node_id": "slow",
            "status": "BLOCKED",
            "verdict": "CANCELLED",
            "retryable": True,
        }

    result = run_dag_plan(
        plan,
        execute_node=execute,
        transition_policy=CancelSlowPolicy(),
        max_concurrency=2,
    )

    assert slow_calls == 1
    assert result.status == "PASS"
    assert dict(result.node_states)["slow"] == "cancelled"


def test_pre_start_block_cancels_and_collects_already_running_root(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(
        _generic_spec(
            tmp_path,
            [_node(tmp_path, "a-running"), _node(tmp_path, "b-blocked")],
        ),
        source_path=tmp_path / "dag.json",
    )
    running_cancelled = threading.Event()
    called: list[str] = []

    class BlockSecondRootPolicy(AllSuccessTransitionPolicy):
        def before_node_start(self, view, node_id, attempt):  # type: ignore[no-untyped-def]
            del view, attempt
            if node_id == "b-blocked":
                return DagTransitionBatch(
                    block_run=DagRunBlock(
                        failure_code="POLICY_BLOCKED",
                        message="Second root is not admissible.",
                        evidence={"node_id": node_id},
                    )
                )
            return DagTransitionBatch()

    def execute(node, accepted_inputs, execution):  # type: ignore[no-untyped-def]
        del accepted_inputs
        called.append(node.node_id)
        assert execution.cancel_event.wait(timeout=1)
        running_cancelled.set()
        return {"node_id": node.node_id, "status": "BLOCKED", "verdict": "CANCELLED"}

    result = run_dag_plan(
        plan,
        execute_node=execute,
        transition_policy=BlockSecondRootPolicy(),
        max_concurrency=2,
    )

    assert called == ["a-running"]
    assert running_cancelled.is_set()
    assert result.status == "BLOCKED"
    assert result.verdict == "POLICY_BLOCKED"
    assert dict(result.node_states) == {
        "a-running": "cancelled",
        "b-blocked": "pending",
    }


def _generic_spec(tmp_path: Path, nodes: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "scheduler-test",
        "run_dir": str(tmp_path / "run"),
        "nodes": nodes,
    }


def _node(
    tmp_path: Path, node_id: str, *, depends_on: list[str] | None = None
) -> dict[str, object]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": depends_on or [],
        "accepted_context_from": depends_on or [],
        "receipt_path": str(tmp_path / "receipts" / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": 1,
    }
