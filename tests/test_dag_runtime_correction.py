from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.correction import (
    CorrectionActionIntent,
    CorrectionIncident,
    run_correction_transaction,
)
from tau_coding.dag_runtime.model import DagPlan
from tau_coding.dag_runtime.run_store import DagRunStoreError, SqliteDagRunStore


class InjectedCorrectionCrash(RuntimeError):
    pass


def test_correction_resumes_with_verifier_only_after_applied_crash(tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite3"
    auth_state = tmp_path / "auth-state.json"
    auth_state.write_text(json.dumps({"state": "EXPIRED", "effect_count": 0}))
    incident = _incident()
    intent = _intent(incident)
    action_calls = 0
    verifier_calls = 0

    def apply_action(_intent: CorrectionActionIntent) -> dict[str, Any]:
        nonlocal action_calls
        action_calls += 1
        value = json.loads(auth_state.read_text())
        value["state"] = "VALID"
        value["effect_count"] += 1
        auth_state.write_text(json.dumps(value, sort_keys=True))
        return {"state": value["state"], "effect_count": value["effect_count"]}

    def verify_action(
        _intent: CorrectionActionIntent, _receipt: dict[str, Any]
    ) -> dict[str, Any]:
        nonlocal verifier_calls
        verifier_calls += 1
        value = json.loads(auth_state.read_text())
        return {"verified": value == {"state": "VALID", "effect_count": 1}}

    def crash_after_applied(phase: str, _payload: dict[str, Any]) -> None:
        if phase == "after_applied":
            raise InjectedCorrectionCrash

    plan = _plan(tmp_path)
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        with pytest.raises(InjectedCorrectionCrash):
            run_correction_transaction(
                store=store,
                lease=lease,
                incident=incident,
                intent=intent,
                apply_action=apply_action,
                verify_action=verify_action,
                fault_injector=crash_after_applied,
            )
        store.release_lease(lease)

    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-b")
        projection = run_correction_transaction(
            store=store,
            lease=lease,
            incident=incident,
            intent=intent,
            apply_action=apply_action,
            verify_action=verify_action,
        )

    assert projection.state == "VERIFIED"
    assert action_calls == 1
    assert verifier_calls == 1
    assert json.loads(auth_state.read_text()) == {"state": "VALID", "effect_count": 1}


def test_non_retryable_incident_routes_human_without_side_effect(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    incident = CorrectionIncident.create(
        run_id="run-1",
        node_id="worker",
        attempt=1,
        trigger="goal_hash_mismatch",
        classification="NON_RETRYABLE",
        goal_hash="sha256:goal",
    )
    intent = _intent(incident)
    action_calls = 0

    def apply_action(_intent: CorrectionActionIntent) -> dict[str, Any]:
        nonlocal action_calls
        action_calls += 1
        return {"unexpected": True}

    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        projection = run_correction_transaction(
            store=store,
            lease=lease,
            incident=incident,
            intent=intent,
            apply_action=apply_action,
            verify_action=lambda _intent, _receipt: {"verified": True},
        )

    assert projection.state == "HUMAN_ROUTED"
    assert action_calls == 0
    assert projection.action_receipt is None


def test_started_without_applied_becomes_uncertain_without_reapplying(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    incident = _incident()
    intent = _intent(incident)
    action_calls = 0

    def apply_action(_intent: CorrectionActionIntent) -> dict[str, Any]:
        nonlocal action_calls
        action_calls += 1
        return {"unexpected": True}

    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        with pytest.raises(InjectedCorrectionCrash):
            run_correction_transaction(
                store=store,
                lease=lease,
                incident=incident,
                intent=intent,
                apply_action=apply_action,
                verify_action=lambda _intent, _receipt: {"verified": True},
                fault_injector=lambda phase, _payload: (
                    (_ for _ in ()).throw(InjectedCorrectionCrash())
                    if phase == "after_started"
                    else None
                ),
            )
        store.release_lease(lease)
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-b")
        projection = run_correction_transaction(
            store=store,
            lease=lease,
            incident=incident,
            intent=intent,
            apply_action=apply_action,
            verify_action=lambda _intent, _receipt: {"verified": True},
        )

    assert projection.state == "UNCERTAIN"
    assert action_calls == 0


def test_conflicting_duplicate_correction_event_blocks(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    incident = _incident()
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        payload = {
            "schema": "tau.correction_journal_entry.v1",
            "incident_id": incident.incident_id,
            "state": "REQUESTED",
            "incident": incident.to_payload(),
        }
        sequence = store.append_correction_event(
            lease,
            event_key=f"correction:{incident.incident_id}:requested",
            incident_id=incident.incident_id,
            payload=payload,
        )
        assert store.append_correction_event(
            lease,
            event_key=f"correction:{incident.incident_id}:requested",
            incident_id=incident.incident_id,
            payload=payload,
        ) == sequence
        with pytest.raises(DagRunStoreError, match="dag_run_event_conflict"):
            store.append_correction_event(
                lease,
                event_key=f"correction:{incident.incident_id}:requested",
                incident_id=incident.incident_id,
                payload={**payload, "reason": "changed"},
            )


def _incident() -> CorrectionIncident:
    return CorrectionIncident.create(
        run_id="run-1",
        node_id="reviewer",
        attempt=2,
        trigger="provider_auth_required",
        classification="RETRYABLE",
        goal_hash="sha256:goal",
        observed_state={"auth": "EXPIRED"},
    )


def _intent(incident: CorrectionIncident) -> CorrectionActionIntent:
    return CorrectionActionIntent.create(
        incident=incident,
        capability="provider.repair_auth",
        action="refresh_local_provider_auth",
        target={"provider": "local-fixture"},
        policy_sha256="sha256:policy",
        authorized=True,
    )


def _plan(tmp_path: Path) -> DagPlan:
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "correction-test",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "reviewer",
                    "role": "reviewer",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "reviewer.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 2,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
