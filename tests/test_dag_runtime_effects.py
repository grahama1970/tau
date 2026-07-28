"""Accepted-effect lifecycle tests (#218)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.effects import EffectLedger, EffectStoreError
from tau_coding.dag_runtime.run_store import SqliteDagRunStore

IDENT = {
    "effect_type": "filesystem_publish",
    "effect_scope": "repo-x/out",
    "effect_key": "bundle-v1",
}


def _env(tmp_path: Path):
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "run-fx",
            "run_dir": str(tmp_path / "run"),
            "nodes": [{
                "node_id": "n", "role": "n", "command": ["true"],
                "depends_on": [], "accepted_context_from": [],
                "receipt_path": str(tmp_path / "n.json"),
                "timeout_seconds": 1, "max_attempts": 1,
            }],
        },
        source_path=tmp_path / "dag.json",
    )
    store = SqliteDagRunStore(tmp_path / "dag-run.sqlite3")
    lease = store.acquire_run(plan=plan, run_id="run-fx", owner_id="t", ttl_seconds=60)
    return store, lease, EffectLedger(store)


def test_lifecycle_intent_succeeded_accepted(tmp_path: Path) -> None:
    store, lease, fx = _env(tmp_path)
    fx.declare(lease, **IDENT, reconciliation="handler")
    handle = fx.acquire(lease, **IDENT, owner_attempt_id="attempt-1")
    assert handle is not None
    fx.mark_succeeded(lease, handle, evidence={
        "target_identity": "repo-x/out/bundle.json",
        "response_digest": "sha256:abc",
    })
    fx.mark_accepted(lease, handle)
    rows = fx.list_effects()
    assert rows[0]["state"] == "accepted"


def test_two_owners_cannot_both_acquire(tmp_path: Path) -> None:
    store, lease, fx = _env(tmp_path)
    fx.declare(lease, **IDENT, reconciliation="handler")
    first = fx.acquire(lease, **IDENT, owner_attempt_id="attempt-1")
    second = fx.acquire(lease, **IDENT, owner_attempt_id="attempt-2")
    assert first is not None
    assert second is None


def test_success_requires_external_evidence(tmp_path: Path) -> None:
    store, lease, fx = _env(tmp_path)
    fx.declare(lease, **IDENT, reconciliation="handler")
    handle = fx.acquire(lease, **IDENT, owner_attempt_id="attempt-1")
    with pytest.raises(EffectStoreError, match="local assertion is not success"):
        fx.mark_succeeded(lease, handle, evidence={"target_identity": "x"})


def test_stale_lease_moves_to_uncertain_and_blocks(tmp_path: Path) -> None:
    store, lease, fx = _env(tmp_path)
    fx.declare(lease, **IDENT, reconciliation="handler")
    handle = fx.acquire(lease, **IDENT, owner_attempt_id="attempt-1", ttl_seconds=0.0)
    assert handle is not None
    moved = fx.mark_uncertain_effects(lease)
    assert [m["effect_key"] for m in moved] == ["bundle-v1"]
    assert fx.list_effects()[0]["state"] == "uncertain"
    # stale token cannot transition anymore
    with pytest.raises(EffectStoreError, match="refused"):
        fx.mark_succeeded(lease, handle, evidence={
            "target_identity": "x", "operation_id": "op-1",
        })


def test_uncertain_is_reacquirable_for_reconciliation(tmp_path: Path) -> None:
    store, lease, fx = _env(tmp_path)
    fx.declare(lease, **IDENT, reconciliation="handler")
    fx.acquire(lease, **IDENT, owner_attempt_id="attempt-1", ttl_seconds=0.0)
    fx.mark_uncertain_effects(lease)
    handle = fx.acquire(lease, **IDENT, owner_attempt_id="attempt-2")
    assert handle is not None
    fx.mark_reconciled(lease, handle)
    assert fx.list_effects()[0]["state"] == "reconciled"


def test_bad_reconciliation_mode_refused(tmp_path: Path) -> None:
    store, lease, fx = _env(tmp_path)
    with pytest.raises(EffectStoreError, match="manual_reconciliation_only"):
        fx.declare(lease, **IDENT, reconciliation="hope")


def test_effect_identity_survives_new_run(tmp_path: Path) -> None:
    store, lease, fx = _env(tmp_path)
    fx.declare(lease, **IDENT, reconciliation="handler")
    h = fx.acquire(lease, **IDENT, owner_attempt_id="attempt-1")
    fx.mark_succeeded(lease, h, evidence={"target_identity": "t", "read_back": "ok"})
    fx.mark_accepted(lease, h)
    # a later run cannot re-own an accepted effect
    again = fx.acquire(lease, **IDENT, owner_attempt_id="attempt-run2")
    assert again is None
