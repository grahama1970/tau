from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlan, DagPlanNode
from tau_coding.dag_runtime.replay import replay_dag_run
from tau_coding.dag_runtime.run_store import SqliteDagRunReader, SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.dag_runtime.transition import (
    AllSuccessTransitionPolicy,
    DagDeadlineArm,
    DagEdgeSettlement,
    DagNodeCancellation,
    DagNodeSettlement,
    DagRunBlock,
    DagTransitionBatch,
    transition_batch_from_payload,
    transition_batch_to_payload,
)


def test_transition_payload_accepts_valid_effect_family(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["source", "review"])
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"schema":"receipt.v1","ok":true}\n', encoding="utf-8")
    digest = f"sha256:{hashlib.sha256(receipt.read_bytes()).hexdigest()}"
    payload = transition_batch_to_payload(
        DagTransitionBatch(
            edge_settlements=(
                DagEdgeSettlement(
                    edge_id=plan.control_edges[0].edge_id,
                    state="success",
                    reason_code="source_passed",
                ),
            ),
            node_settlements=(
                DagNodeSettlement(
                    node_id="review",
                    state="skipped",
                    reason_code="short_circuit",
                ),
            ),
            node_cancellations=(
                DagNodeCancellation(node_id="source", reason_code="cancelled_by_policy"),
            ),
            deadline_arms=(
                DagDeadlineArm(
                    deadline_id="deadline:review",
                    deadline_monotonic=time.monotonic() + 1,
                    reason_code="review_timeout",
                ),
            ),
            deadline_cancellations=("deadline:active",),
            receipt_paths=(str(receipt),),
            events=({"schema": "tau.transition_event.v1", "event": "accepted"},),
            block_run=DagRunBlock(
                failure_code="POLICY_BLOCKED",
                message="blocked for test",
                evidence={"node_id": "review"},
            ),
        )
    )

    batch = transition_batch_from_payload(
        payload,
        plan=plan,
        verify_receipts=True,
        active_deadlines={"deadline:active": time.monotonic()},
    )

    assert batch.receipt_paths == (str(receipt.resolve()),)
    assert payload["receipt_refs"][0]["file_sha256"] == digest


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda payload, plan, tmp_path: payload["edge_settlements"].append(
                {"edge_id": "missing-edge", "state": "success", "reason_code": "bad_edge"}
            ),
            "dag_transition_unknown_edge",
        ),
        (
            lambda payload, plan, tmp_path: payload["node_settlements"].append(
                {"node_id": "missing-node", "state": "success", "reason_code": "bad_node"}
            ),
            "dag_transition_unknown_node",
        ),
        (
            lambda payload, plan, tmp_path: payload["node_cancellations"].append(
                {"node_id": "missing-node", "reason_code": "bad_cancel"}
            ),
            "dag_transition_unknown_cancellation",
        ),
        (
            lambda payload, plan, tmp_path: payload["deadline_cancellations"].append(
                "missing-deadline"
            ),
            "dag_transition_unknown_deadline",
        ),
        (
            lambda payload, plan, tmp_path: payload["edge_settlements"].append(
                {
                    "edge_id": plan.control_edges[0].edge_id,
                    "state": "bogus",
                    "reason_code": "bad_state",
                }
            ),
            "dag_transition_edge_state_invalid",
        ),
        (
            lambda payload, plan, tmp_path: payload["edge_settlements"].extend(
                [
                    {
                        "edge_id": plan.control_edges[0].edge_id,
                        "state": "success",
                        "reason_code": "first",
                    },
                    {
                        "edge_id": plan.control_edges[0].edge_id,
                        "state": "success",
                        "reason_code": "second",
                    },
                ]
            ),
            "dag_transition_edge_duplicate",
        ),
        (
            lambda payload, plan, tmp_path: payload["edge_settlements"].extend(
                [
                    {
                        "edge_id": plan.control_edges[0].edge_id,
                        "state": "success",
                        "reason_code": "first",
                    },
                    {
                        "edge_id": plan.control_edges[0].edge_id,
                        "state": "failed",
                        "reason_code": "second",
                    },
                ]
            ),
            "dag_transition_edge_conflict",
        ),
        (
            lambda payload, plan, tmp_path: payload["deadline_arms"].append(
                {
                    "deadline_id": "deadline:bad",
                    "deadline_due_at_ms": "123",
                    "reason_code": "bad_deadline",
                }
            ),
            "dag_transition_deadline_due_invalid",
        ),
        (
            lambda payload, plan, tmp_path: payload["deadline_arms"].append(
                {
                    "deadline_id": "deadline:bad",
                    "deadline_due_at_ms": float("inf"),
                    "reason_code": "bad_deadline",
                }
            ),
            "dag_transition_deadline_due_invalid",
        ),
        (
            lambda payload, plan, tmp_path: payload["events"].append(
                {"schema": "tau.transition_event.v1", "value": float("nan")}
            ),
            "dag_transition_event_non_canonical",
        ),
        (
            lambda payload, plan, tmp_path: payload["receipt_refs"].append(
                {
                    "path": str(tmp_path / "missing.json"),
                    "file_sha256": "sha256:" + "0" * 64,
                }
            ),
            "dag_transition_receipt_missing",
        ),
        (
            lambda payload, plan, tmp_path: _append_mismatched_receipt(payload, tmp_path),
            "dag_transition_receipt_hash_mismatch",
        ),
    ],
)
def test_transition_payload_rejects_malformed_effects(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any], DagPlan, Path], None],
    match: str,
) -> None:
    plan = _plan(tmp_path, ["source", "review"])
    payload = transition_batch_to_payload(DagTransitionBatch())
    mutate(payload, plan, tmp_path)

    with pytest.raises(RuntimeError, match=match):
        transition_batch_from_payload(
            payload,
            plan=plan,
            verify_receipts=True,
            active_deadlines={},
        )


def test_live_scheduler_rejects_invalid_policy_transition_before_commit(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["source"])
    database = tmp_path / "dag-run.sqlite3"

    class InvalidCancellationPolicy(AllSuccessTransitionPolicy):
        def after_node_terminal(self, view, completion):  # type: ignore[no-untyped-def]
            del view, completion
            return DagTransitionBatch(
                node_cancellations=(
                    DagNodeCancellation(node_id="missing-node", reason_code="bad_cancel"),
                )
            )

    with SqliteDagRunStore(database) as store, pytest.raises(
        RuntimeError, match="dag_transition_unknown_cancellation"
    ):
        run_dag_plan(
            plan,
            run_store=store,
            run_id="run-1",
            transition_policy=InvalidCancellationPolicy(),
            execute_node=_pass_node,
        )

    with SqliteDagRunReader(database) as reader:
        event_types = [item.event_type for item in reader.load_events("run-1", limit=5000)]
    assert "scheduler_transition_committed" not in event_types


def test_replay_rechecks_receipt_hashes_before_accepting_transition(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ["source"])
    receipt = tmp_path / "transition-receipt.json"
    database = tmp_path / "dag-run.sqlite3"

    class ReceiptPolicy(AllSuccessTransitionPolicy):
        def after_node_terminal(self, view, completion):  # type: ignore[no-untyped-def]
            receipt.write_text('{"schema":"receipt.v1","value":1}\n', encoding="utf-8")
            base = super().after_node_terminal(view, completion)
            return replace(base, receipt_paths=(str(receipt),))

    with SqliteDagRunStore(database) as store:
        run_dag_plan(
            plan,
            run_store=store,
            run_id="run-1",
            transition_policy=ReceiptPolicy(),
            execute_node=_pass_node,
        )
    receipt.write_text('{"schema":"receipt.v1","value":2}\n', encoding="utf-8")

    with SqliteDagRunReader(database) as reader, pytest.raises(
        RuntimeError, match="dag_transition_receipt_hash_mismatch"
    ):
        replay_dag_run(
            plan=reader.load_plan("run-1"),
            run_record=reader.load_run_record("run-1"),
            events=tuple(item.to_mapping() for item in reader.load_events("run-1", limit=5000)),
            attempts=reader.load_attempts("run-1"),
            runtime_projections=reader.runtime_projections("run-1"),
        )


def _append_mismatched_receipt(payload: dict[str, Any], tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"schema":"receipt.v1","value":1}\n', encoding="utf-8")
    payload["receipt_refs"].append(
        {
            "path": str(receipt),
            "file_sha256": "sha256:" + "0" * 64,
        }
    )


def _pass_node(
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
    }


def _plan(tmp_path: Path, node_ids: list[str]) -> DagPlan:
    nodes: list[dict[str, object]] = []
    for index, node_id in enumerate(node_ids):
        dependencies = [node_ids[index - 1]] if index else []
        nodes.append(
            {
                "node_id": node_id,
                "role": node_id,
                "command": ["true"],
                "depends_on": dependencies,
                "accepted_context_from": dependencies,
                "receipt_path": str(tmp_path / f"{node_id}.json"),
                "timeout_seconds": 1,
                "max_attempts": 1,
            }
        )
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "transition-validation-test",
            "run_dir": str(tmp_path / "run"),
            "nodes": nodes,
        },
        source_path=tmp_path / "dag.json",
    )
