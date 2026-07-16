"""Deterministic causal, route, join, and attention projection tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.dag_runtime.replay import DagReplayState
from tau_coding.dag_runtime.transition import DagCommittedReceipt
from tau_coding.dag_viewer.projection import build_dag_view_state
from tau_coding.dag_viewer.receipt_index import ReceiptIndex, build_receipt_index


def _write(path: Path, payload: dict[str, Any]) -> DagCommittedReceipt:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    digest = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    return DagCommittedReceipt(str(path.resolve()), digest)


def _fixture(tmp_path: Path) -> tuple[DagReplayState, tuple[dict[str, Any], ...], ReceiptIndex]:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "causal-run",
            "run_dir": str(tmp_path),
            "nodes": [
                {
                    "node_id": "router",
                    "role": "router",
                    "command": ["true"],
                    "receipt_path": str(tmp_path / "router.json"),
                },
                {
                    "node_id": "branch-a",
                    "role": "branch",
                    "command": ["true"],
                    "depends_on": ["router"],
                    "receipt_path": str(tmp_path / "a.json"),
                },
                {
                    "node_id": "branch-b",
                    "role": "branch",
                    "command": ["true"],
                    "depends_on": ["router"],
                    "receipt_path": str(tmp_path / "b.json"),
                },
                {
                    "node_id": "join",
                    "role": "join",
                    "command": ["true"],
                    "depends_on": ["branch-a", "branch-b"],
                    "receipt_path": str(tmp_path / "join.json"),
                },
            ],
        },
        source_path=tmp_path / "dag.json",
    )
    by_pair = {(edge.source_node_id, edge.target_id): edge for edge in plan.control_edges}
    route_edges = [by_pair[("router", "branch-a")], by_pair[("router", "branch-b")]]
    join_edges = [by_pair[("branch-a", "join")], by_pair[("branch-b", "join")]]
    route_contract = {
        "source_node_id": "router",
        "mode": "exclusive",
        "ordered_edge_ids": [edge.edge_id for edge in route_edges],
    }
    route_contract["contract_sha256"] = canonical_sha256(route_contract)
    policy = {
        "schema": "tau.dag_join_policy.v1",
        "policy": "minimum_success_count",
        "timeout_seconds": 30,
        "required_successes": 1,
    }
    join_contract = {
        "join_node_id": "join",
        "incoming_edge_ids": [edge.edge_id for edge in join_edges],
        "policy": policy,
        "policy_sha256": canonical_sha256(policy),
    }
    plan = replace(
        plan,
        route_contracts=(FrozenJson.from_value(route_contract),),
        join_contracts=(FrozenJson.from_value(join_contract),),
    ).with_computed_hash()

    route_path = tmp_path / "route-decisions" / "router.json"
    contribution_a_path = tmp_path / "terminal-contributions" / "a.json"
    contribution_b_path = tmp_path / "terminal-contributions" / "b.json"
    join_path = tmp_path / "join-decisions" / "join.json"
    refs = (
        _write(
            route_path,
            {
                "schema": "tau.dag_route_decision.v1",
                "status": "PASS",
                "failure_code": None,
                "mode": "exclusive",
                "selected_targets": ["branch-a"],
            },
        ),
        _write(
            contribution_a_path,
            {
                "schema": "tau.dag_terminal_contribution.v1",
                "state": "success",
                "reason_code": "source_passed",
            },
        ),
        _write(
            contribution_b_path,
            {
                "schema": "tau.dag_terminal_contribution.v1",
                "state": "skipped",
                "reason_code": "route_unselected",
            },
        ),
        _write(
            join_path,
            {
                "schema": "tau.dag_join_decision.v1",
                "decision": "release",
                "reason_code": "join_minimum_success_count_met",
            },
        ),
    )
    transition = {
        "schema": "tau.dag_transition_batch.v1",
        "edge_settlements": [
            {
                "edge_id": route_edges[0].edge_id,
                "state": "success",
                "reason_code": "route_selected",
            },
            {
                "edge_id": route_edges[1].edge_id,
                "state": "skipped",
                "reason_code": "route_unselected",
            },
        ],
        "node_settlements": [
            {
                "node_id": "join",
                "terminal_state": "success",
                "reason_code": "join_minimum_success_count_met",
            }
        ],
        "node_cancellations": [],
        "deadline_arms": [],
        "deadline_cancellations": ["join"],
        "receipt_refs": [
            {"path": ref.path, "file_sha256": ref.file_sha256} for ref in refs
        ],
        "events": [
            {
                "event": "route_decided",
                "node_id": "router",
                "attempt": 1,
                "status": "PASS",
                "selected_targets": ["branch-a"],
                "route_decision_receipt": str(route_path.resolve()),
            },
            {
                "event": "terminal_contribution_recorded",
                "join_node_id": "join",
                "source_node_id": "branch-a",
                "state": "success",
                "receipt": str(contribution_a_path.resolve()),
            },
            {
                "event": "terminal_contribution_recorded",
                "join_node_id": "join",
                "source_node_id": "branch-b",
                "state": "skipped",
                "receipt": str(contribution_b_path.resolve()),
            },
            {
                "event": "join_decided",
                "join_node_id": "join",
                "decision": "release",
                "reason_code": "join_minimum_success_count_met",
                "join_decision_receipt": str(join_path.resolve()),
            },
        ],
        "block_run": None,
    }
    events = (
        {
            "seq": 1,
            "event_type": "run_created",
            "entity_type": "run",
            "entity_id": "causal-run",
            "payload": {"plan_sha256": plan.plan_sha256},
        },
        {
            "seq": 2,
            "event_type": "scheduler_transition_committed",
            "entity_type": "attempt",
            "entity_id": "attempt-1",
            "payload": {"transition": transition},
        },
    )
    replay = DagReplayState(
        run_id="causal-run",
        plan=plan,
        journal_sequence=2,
        run_status="PASS",
        run_verdict="PASS",
        node_states=(
            ("router", "success"),
            ("branch-a", "success"),
            ("branch-b", "skipped"),
            ("join", "success"),
        ),
        edge_states=tuple(
            (edge.edge_id, "success" if edge in {route_edges[0], *join_edges} else "skipped")
            for edge in plan.control_edges
        ),
        terminal_states=(),
        attempts=(),
        results=(),
        runtime_projections=(),
        transition_receipts=refs,
        replay_events=events,
        deadline_monotonic=(),
        lease_owner=None,
        lease_epoch=1,
        lease_expires_at_ms=None,
        block=None,
    )
    return replay, events, build_receipt_index(tmp_path, refs)


def test_committed_route_and_join_project_receipt_bound_causal_state(tmp_path: Path) -> None:
    replay, events, receipts = _fixture(tmp_path)
    snapshot, causal = build_dag_view_state(
        replay=replay, recent_events=events, receipt_index=receipts
    )

    route = snapshot["routes"][0]
    assert route["state"] == "SELECTED"
    assert len(route["selected_edge_ids"]) == len(route["skipped_edge_ids"]) == 1
    assert route["decision_sequence"] == 2
    assert route["decision_receipt_sha256"].startswith("sha256:")
    join = snapshot["joins"][0]
    assert join["state"] == "RELEASED"
    assert [item["state"] for item in join["incoming"]] == ["success", "skipped"]
    assert join["decision_sequence"] == 2
    explanation = causal.explanation("ROUTE", route["route_id"])
    assert explanation["trigger_sequence"] == 2
    assert {item["kind"] for item in explanation["references"]} == {
        "PLAN_CONTRACT",
        "JOURNAL_EVENT",
        "TRANSITION_RECEIPT",
    }
    assert str(tmp_path) not in json.dumps(snapshot)
    assert str(tmp_path) not in json.dumps(explanation)


def test_topology_without_committed_decisions_remains_pending(tmp_path: Path) -> None:
    replay, events, _ = _fixture(tmp_path)
    pending = replace(
        replay,
        journal_sequence=1,
        run_status="RUNNING",
        run_verdict=None,
        node_states=tuple((node_id, "pending") for node_id, _ in replay.node_states),
        edge_states=tuple((edge_id, "pending") for edge_id, _ in replay.edge_states),
        transition_receipts=(),
        replay_events=events[:1],
    )
    snapshot, _ = build_dag_view_state(
        replay=pending,
        recent_events=events[:1],
        receipt_index=ReceiptIndex(tmp_path, ()),
    )
    assert snapshot["routes"][0]["state"] == "PENDING"
    assert snapshot["routes"][0]["selected_edge_ids"] == []
    assert snapshot["joins"][0]["state"] == "PENDING"
    assert snapshot["joins"][0]["incoming"] == []


def test_blocked_run_emits_deterministically_ordered_read_only_attention(tmp_path: Path) -> None:
    replay, events, receipts = _fixture(tmp_path)
    blocked = replace(replay, run_status="BLOCKED", run_verdict="JOIN_BLOCKED")
    snapshot, causal = build_dag_view_state(
        replay=blocked, recent_events=events, receipt_index=receipts
    )
    attention = snapshot["attention_items"]
    assert len(attention) == 1
    assert attention[0]["required_action_code"] == "REVIEW_BLOCKED_RUN"
    assert attention[0]["state"] == "OPEN"
    assert snapshot["highest_priority_attention_id"] == attention[0]["attention_id"]
    explanation = causal.explanation("ATTENTION", attention[0]["attention_id"])
    assert explanation["reason_code"] == "JOIN_BLOCKED"
    assert "assignee" not in attention[0]
    assert "acknowledged" not in attention[0]
