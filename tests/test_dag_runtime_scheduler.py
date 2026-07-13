from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlanNode
from tau_coding.dag_runtime.scheduler import run_dag_plan


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
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        del cancel_event
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
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        del accepted_inputs, cancel_event
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
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        del accepted_inputs
        barrier.wait(timeout=1)
        if node.node_id == "fail":
            return {"node_id": "fail", "status": "BLOCKED", "verdict": "FAILED"}
        if cancel_event.wait(timeout=1):
            sibling_cancelled.set()
        return {"node_id": "sibling", "status": "BLOCKED", "verdict": "CANCELLED"}

    result = run_dag_plan(plan, execute_node=execute, max_concurrency=2)

    assert result.status == "BLOCKED"
    assert sibling_cancelled.is_set()


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
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        del accepted_inputs, cancel_event
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


def test_base_scheduler_rejects_route_contract_without_route_adapter(tmp_path: Path) -> None:
    payload = _generic_spec(tmp_path, [_node(tmp_path, "producer")])
    plan = compile_generic_dag_plan(payload, source_path=tmp_path / "dag.json")
    plan = replace(plan, route_contracts=(plan.goal_binding,))

    with pytest.raises(RuntimeError, match="dag_plan_route_join_adapter_required"):
        run_dag_plan(plan, execute_node=lambda node, inputs, cancel: {})


def _generic_spec(
    tmp_path: Path, nodes: list[dict[str, object]]
) -> dict[str, object]:
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
