from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlan, FrozenJson, canonical_sha256
from tau_coding.dag_runtime.run_store import SqliteDagRunReader, SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.node_completion_boundary import (
    NODE_COMPLETION_BOUNDARY_SCHEMA,
    NODE_COMPLETION_BOUNDARY_POLICY_SCHEMA,
)


def test_required_node_completion_boundary_is_admitted_and_replayable(
    tmp_path: Path,
) -> None:
    plan = _plan_with_boundary(tmp_path)
    store = SqliteDagRunStore(tmp_path / "run.sqlite3")

    def execute(_node: object, _inputs: object, attempt: DagNodeAttempt) -> dict[str, Any]:
        return _pass_result(
            plan,
            node_id="worker",
            attempt=attempt,
            boundary=_valid_boundary(plan, attempt=attempt),
        )

    outcome = run_dag_plan(
        plan,
        execute_node=execute,
        run_store=store,
        run_id="run-boundary",
        lease_owner="tester",
    )

    assert outcome.status == "PASS"
    node_result = outcome.node_results[0]
    assert node_result["node_completion_boundary_validation"]["status"] == "PASS"
    rows = store.list_admissions(
        "run-boundary",
        receipt_kind=NODE_COMPLETION_BOUNDARY_SCHEMA,
    )
    assert len(rows) == 1
    admitted = store.load_admission("run-boundary", rows[0]["admission_id"])
    assert admitted["receipt_kind"] == NODE_COMPLETION_BOUNDARY_SCHEMA
    boundary = json.loads(Path(admitted["path"]).read_text(encoding="utf-8"))
    assert boundary["schema"] == NODE_COMPLETION_BOUNDARY_SCHEMA
    assert boundary["does_not_prove_completeness_or_correctness"] is True
    assert boundary["checked_scope"][0]["id"] == "checked-command"
    store.close()

    with SqliteDagRunReader(tmp_path / "run.sqlite3") as reader:
        readback = reader.load_admission("run-boundary", admitted["admission_id"])
        assert readback["sha256"] == admitted["sha256"]
        assert reader.list_admissions(
            "run-boundary",
            receipt_kind=NODE_COMPLETION_BOUNDARY_SCHEMA,
        ) == [readback]


def test_missing_required_node_completion_boundary_blocks_pass_claim(tmp_path: Path) -> None:
    plan = _plan_with_boundary(tmp_path)
    store = SqliteDagRunStore(tmp_path / "run.sqlite3")

    outcome = run_dag_plan(
        plan,
        execute_node=lambda _node, _inputs, attempt: _pass_result(
            plan,
            node_id="worker",
            attempt=attempt,
        ),
        run_store=store,
        run_id="run-missing",
        lease_owner="tester",
    )

    assert outcome.status == "BLOCKED"
    assert outcome.verdict == "NODE_COMPLETION_BOUNDARY_INVALID"
    result = outcome.node_results[0]
    assert result["status"] == "BLOCKED"
    assert "node_completion_boundary_missing" in result["alert_codes"]
    assert store.list_admissions(
        "run-missing",
        receipt_kind=NODE_COMPLETION_BOUNDARY_SCHEMA,
    ) == []


@pytest.mark.parametrize(
    ("field", "value", "alert_code"),
    [
        ("goal_hash", "sha256:wrong-goal", "node_completion_boundary_goal_mismatch"),
        ("plan_sha256", "sha256:wrong-plan", "node_completion_boundary_plan_mismatch"),
        ("node_id", "other-node", "node_completion_boundary_node_mismatch"),
        (
            "attempt_id",
            "attempt-wrong",
            "node_completion_boundary_attempt_mismatch",
        ),
    ],
)
def test_identity_mismatches_block_boundary_admission(
    tmp_path: Path,
    field: str,
    value: str,
    alert_code: str,
) -> None:
    plan = _plan_with_boundary(tmp_path)
    store = SqliteDagRunStore(tmp_path / "run.sqlite3")

    def execute(_node: object, _inputs: object, attempt: DagNodeAttempt) -> dict[str, Any]:
        boundary = _valid_boundary(plan, attempt=attempt)
        boundary[field] = value
        return _pass_result(plan, node_id="worker", attempt=attempt, boundary=boundary)

    outcome = run_dag_plan(
        plan,
        execute_node=execute,
        run_store=store,
        run_id=f"run-{field}",
        lease_owner="tester",
    )

    assert outcome.status == "BLOCKED"
    result = outcome.node_results[0]
    assert result["verdict"] == "NODE_COMPLETION_BOUNDARY_INVALID"
    assert alert_code in result["alert_codes"]


def test_policy_requires_non_empty_not_checked_and_evidence_gaps(tmp_path: Path) -> None:
    plan = _plan_with_boundary(
        tmp_path,
        policy={
            "schema": NODE_COMPLETION_BOUNDARY_POLICY_SCHEMA,
            "required_sections": [
                "checked_scope",
                "not_checked",
                "evidence_gaps",
                "proves",
                "does_not_prove",
            ],
            "non_empty_sections": ["not_checked", "evidence_gaps"],
        },
    )
    store = SqliteDagRunStore(tmp_path / "run.sqlite3")

    def execute(_node: object, _inputs: object, attempt: DagNodeAttempt) -> dict[str, Any]:
        boundary = _valid_boundary(plan, attempt=attempt)
        boundary["not_checked"] = []
        boundary["evidence_gaps"] = []
        return _pass_result(plan, node_id="worker", attempt=attempt, boundary=boundary)

    outcome = run_dag_plan(
        plan,
        execute_node=execute,
        run_store=store,
        run_id="run-policy",
        lease_owner="tester",
    )

    result = outcome.node_results[0]
    assert outcome.status == "BLOCKED"
    assert "node_completion_boundary_empty_required_section" in result["alert_codes"]
    assert any("not_checked must be non-empty" in error for error in result["errors"])
    assert any("evidence_gaps must be non-empty" in error for error in result["errors"])


def test_missing_required_section_and_invalid_typed_item_block(tmp_path: Path) -> None:
    plan = _plan_with_boundary(tmp_path)
    store = SqliteDagRunStore(tmp_path / "run.sqlite3")

    def execute(_node: object, _inputs: object, attempt: DagNodeAttempt) -> dict[str, Any]:
        boundary = _valid_boundary(plan, attempt=attempt)
        boundary.pop("assumptions")
        boundary["known_unknowns"] = [{"statement": "Missing stable id."}]
        return _pass_result(plan, node_id="worker", attempt=attempt, boundary=boundary)

    outcome = run_dag_plan(
        plan,
        execute_node=execute,
        run_store=store,
        run_id="run-malformed",
        lease_owner="tester",
    )

    result = outcome.node_results[0]
    assert outcome.status == "BLOCKED"
    assert "node_completion_boundary_missing_required_section" in result["alert_codes"]
    assert "node_completion_boundary_invalid_item" in result["alert_codes"]


def test_nodes_without_required_boundary_remain_backward_compatible(tmp_path: Path) -> None:
    plan = _base_plan(tmp_path)
    store = SqliteDagRunStore(tmp_path / "run.sqlite3")

    outcome = run_dag_plan(
        plan,
        execute_node=lambda _node, _inputs, attempt: _pass_result(
            plan,
            node_id="worker",
            attempt=attempt,
        ),
        run_store=store,
        run_id="run-compatible",
        lease_owner="tester",
    )

    assert outcome.status == "PASS"
    assert "node_completion_boundary_validation" not in outcome.node_results[0]


def test_required_boundary_blocks_without_durable_admission_store(tmp_path: Path) -> None:
    plan = _plan_with_boundary(tmp_path)

    def execute(_node: object, _inputs: object, attempt: DagNodeAttempt) -> dict[str, Any]:
        return _pass_result(
            plan,
            node_id="worker",
            attempt=attempt,
            boundary=_valid_boundary(plan, attempt=attempt),
        )

    outcome = run_dag_plan(plan, execute_node=execute)

    assert outcome.status == "BLOCKED"
    assert outcome.node_results[0]["verdict"] == "NODE_COMPLETION_BOUNDARY_INVALID"
    assert (
        "node_completion_boundary_admission_unavailable"
        in outcome.node_results[0]["alert_codes"]
    )


def _base_plan(tmp_path: Path) -> DagPlan:
    goal = {
        "goal_id": "issue-269",
        "goal_version": 1,
        "summary": "Prove node completion boundary behavior.",
        "completion_criteria": ["Boundary admission is checked."],
    }
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-boundary",
            "run_dir": str(tmp_path / "run"),
            "goal": {**goal, "goal_hash": canonical_sha256(goal)},
            "nodes": [
                {
                    "node_id": "worker",
                    "role": "worker",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "worker.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )


def _plan_with_boundary(
    tmp_path: Path,
    *,
    policy: dict[str, Any] | None = None,
) -> DagPlan:
    plan = _base_plan(tmp_path)
    node = plan.nodes[0]
    updated_node = replace(
        node,
        required_evidence=(NODE_COMPLETION_BOUNDARY_SCHEMA,),
        source_extensions=FrozenJson.from_value(
            {"node_completion_boundary_policy": policy}
            if policy is not None
            else {}
        ),
    )
    return replace(plan, nodes=(updated_node,)).with_computed_hash()


def _valid_boundary(plan: DagPlan, *, attempt: DagNodeAttempt) -> dict[str, Any]:
    item = {"id": "checked-command", "statement": "Checked the command result shape."}
    return {
        "schema": NODE_COMPLETION_BOUNDARY_SCHEMA,
        "goal_hash": plan.runtime_goal_hash,
        "plan_sha256": plan.plan_sha256,
        "node_id": "worker",
        "attempt_id": attempt.attempt_id,
        "checked_scope": [item],
        "not_checked": [
            {"id": "not-semantic-completeness", "statement": "Did not prove completeness."}
        ],
        "assumptions": [
            {"id": "assume-fixture", "statement": "Assumed this fixture is representative."}
        ],
        "known_unknowns": [
            {"id": "unknown-live-provider", "statement": "No provider semantics were checked."}
        ],
        "evidence_gaps": [
            {"id": "gap-live-run", "statement": "Live provider execution remains separate."}
        ],
        "recommended_followups": [
            {"id": "follow-live", "statement": "Run live provider sanity separately."}
        ],
        "proves": [
            {"id": "proves-shape", "statement": "The local node returned a PASS shape."}
        ],
        "does_not_prove": [
            {
                "id": "does-not-prove-correctness",
                "statement": "A self-report does not prove correctness.",
            }
        ],
    }


def _pass_result(
    plan: DagPlan,
    *,
    node_id: str,
    attempt: DagNodeAttempt,
    boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "accepted_output": {
            "source_node_id": node_id,
            "plan_sha256": plan.plan_sha256,
            "attempt_id": attempt.attempt_id,
        },
    }
    if boundary is not None:
        result["node_completion_boundary"] = boundary
    return result
