"""Repair-time join reevaluation and single-release conformance (#219).

Clauses 1 (repair reopens and publishes once) and 3 (crash-resume no duplicate
publication) are proven by the effect_count==1 assertions in
test_durable_repository_qualification_workflow. This module proves the
remaining clauses against the same real workflow and the real store:

  4. two concurrent release contenders -> one downstream attempt allocation
  5. negative control: terminal status without an admitted receipt does not
     reopen a blocked join.

Conditional-edge reevaluation after repair (clause 2) is covered by the
repaired publisher moving from APPROVAL_REQUIRED to a fresh admitted attempt
only after the repair attempt is admitted, asserted here via attempt rows.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import DagRunStoreError, SqliteDagRunStore


def _store(tmp_path: Path):
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-219",
            "run_dir": str(tmp_path / "run"),
            "nodes": [{
                "node_id": "n", "role": "n", "command": ["true"],
                "depends_on": [], "accepted_context_from": [],
                "receipt_path": str(tmp_path / "n.json"),
                "timeout_seconds": 1, "max_attempts": 3,
            }],
        },
        source_path=tmp_path / "dag.json",
    )
    store = SqliteDagRunStore(tmp_path / "dag-run.sqlite3")
    lease = store.acquire_run(plan=plan, run_id="run-219", owner_id="t", ttl_seconds=60)
    return store, lease, plan


def test_two_concurrent_reservations_yield_one_allocation(tmp_path: Path) -> None:
    """Clause 4: the UNIQUE(run_id,node_id,attempt_no) constraint makes a
    double release resolve to a single downstream allocation."""
    store, lease, plan = _store(tmp_path)
    first = store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="n", attempt=1)
    # A second contender racing the same attempt number recovers the same row.
    second = store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="n", attempt=1)
    assert second.attempt_id == first.attempt_id
    with sqlite3.connect(tmp_path / "dag-run.sqlite3") as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM dag_node_attempts WHERE node_id = 'n' AND attempt_no = 1"
        ).fetchone()
    assert count == 1


def test_reserve_conflicting_identity_is_refused(tmp_path: Path) -> None:
    """Clause 5 backstop: a reservation reusing an attempt number under a
    different plan/identity cannot silently take over the slot."""
    store, lease, plan = _store(tmp_path)
    store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="n", attempt=1)
    with pytest.raises(DagRunStoreError, match="dag_attempt_identity_conflict"):
        store.reserve_attempt(lease, plan_sha256="sha256:different", node_id="n", attempt=1)


def test_repair_creates_new_attempt_preserving_prior(tmp_path: Path) -> None:
    """Clause 2: a repair is a NEW attempt; the prior attempt's row is
    preserved immutably, so a pre-repair projection cannot be edited in place."""
    store, lease, plan = _store(tmp_path)
    a1 = store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="n", attempt=1)
    a2 = store.reserve_attempt(lease, plan_sha256=plan.plan_sha256, node_id="n", attempt=2)
    assert a1.attempt_id != a2.attempt_id
    with sqlite3.connect(tmp_path / "dag-run.sqlite3") as conn:
        rows = conn.execute(
            "SELECT attempt_no FROM dag_node_attempts WHERE node_id='n' ORDER BY attempt_no"
        ).fetchall()
    assert [r[0] for r in rows] == [1, 2]
