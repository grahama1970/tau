from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
from tau_coding.dag_runtime.scheduler import DagCorrectionRequest, run_dag_plan
from tau_coding.security_capability import capability_grant_sha256


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
        dag_id=plan.plan_id,
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


@pytest.mark.parametrize("invalid_grant", ["missing", "expired", "modified_binding"])
def test_correction_blocks_invalid_capability_grant_without_side_effect(
    tmp_path: Path, invalid_grant: str
) -> None:
    incident = _incident()
    grant = _grant(incident)
    if invalid_grant == "missing":
        grant = {}
    elif invalid_grant == "expired":
        grant = _grant(incident, expires_at=datetime(2020, 1, 1, tzinfo=UTC))
    else:
        grant["node_id"] = "another-node"
    intent = CorrectionActionIntent.create(
        incident=incident,
        capability="provider.repair_auth",
        action="refresh_local_provider_auth",
        target={"provider": "local-fixture"},
        policy_sha256="sha256:policy",
        capability_grant=grant,
    )
    action_calls = 0

    def apply_action(_intent: CorrectionActionIntent) -> dict[str, Any]:
        nonlocal action_calls
        action_calls += 1
        return {"unexpected": True}

    plan = _plan(tmp_path)
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


def test_scheduler_releases_retry_only_after_verified_correction_on_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "run.sqlite3"
    auth_state = tmp_path / "auth-state.json"
    auth_state.write_text(json.dumps({"state": "EXPIRED", "effect_count": 0}))
    plan = _plan(tmp_path)
    node_calls = 0
    action_calls = 0
    verifier_calls = 0
    committed_intent: CorrectionActionIntent | None = None

    def execute_node(*_args: object) -> dict[str, Any]:
        nonlocal node_calls
        node_calls += 1
        if node_calls == 1:
            return {
                "node_id": "reviewer",
                "status": "BLOCKED",
                "verdict": "PROVIDER_AUTH_REQUIRED",
                "retryable": True,
                "correction_required": True,
                "errors": ["local provider auth is stale"],
            }
        return {
            "node_id": "reviewer",
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"auth": "VALID"},
        }

    def correction_handler(request: DagCorrectionRequest):  # type: ignore[no-untyped-def]
        nonlocal committed_intent
        incident = CorrectionIncident.create(
            run_id=request.attempt.run_id,
            dag_id=request.plan.plan_id,
            node_id=request.node.node_id,
            attempt=request.attempt.attempt,
            trigger="provider_auth_required",
            classification="RETRYABLE",
            goal_hash=request.plan.runtime_goal_hash,
            observed_state={"auth": "EXPIRED"},
        )
        if committed_intent is None:
            committed_intent = CorrectionActionIntent.create(
                incident=incident,
                capability="provider.repair_auth",
                action="refresh_local_provider_auth",
                target={"provider": "local-fixture"},
                policy_sha256="sha256:policy",
                capability_grant=_grant(incident),
            )
        intent = committed_intent

        def apply_action(_intent: CorrectionActionIntent) -> dict[str, Any]:
            nonlocal action_calls
            action_calls += 1
            value = json.loads(auth_state.read_text())
            value["state"] = "VALID"
            value["effect_count"] += 1
            auth_state.write_text(json.dumps(value, sort_keys=True))
            return value

        def verify_action(
            _intent: CorrectionActionIntent, _receipt: dict[str, Any]
        ) -> dict[str, Any]:
            nonlocal verifier_calls
            verifier_calls += 1
            value = json.loads(auth_state.read_text())
            return {"verified": value == {"state": "VALID", "effect_count": 1}}

        return run_correction_transaction(
            store=request.run_store,
            lease=request.lease,
            incident=incident,
            intent=intent,
            apply_action=apply_action,
            verify_action=verify_action,
            fault_injector=lambda phase, _payload: (
                (_ for _ in ()).throw(InjectedCorrectionCrash())
                if phase == "after_applied" and verifier_calls == 0
                else None
            ),
        )

    with SqliteDagRunStore(database) as store, pytest.raises(InjectedCorrectionCrash):
        run_dag_plan(
            plan,
            execute_node=execute_node,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
            correction_handler=correction_handler,
        )

    with SqliteDagRunStore(database) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute_node,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
            correction_handler=correction_handler,
        )

    assert result.status == "PASS"
    assert result.verdict == "PASS"
    assert node_calls == 2
    assert action_calls == 1
    assert verifier_calls == 1
    assert json.loads(auth_state.read_text()) == {"state": "VALID", "effect_count": 1}


def test_scheduler_does_not_retry_correction_gated_failure_without_handler(
    tmp_path: Path,
) -> None:
    calls = 0

    def execute_node(*_args: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "node_id": "reviewer",
            "status": "BLOCKED",
            "verdict": "PROVIDER_AUTH_REQUIRED",
            "retryable": True,
            "correction_required": True,
            "errors": ["local provider auth is stale"],
        }

    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        result = run_dag_plan(
            _plan(tmp_path),
            execute_node=execute_node,
            run_store=store,
            run_id="run-1",
            lease_owner="owner-a",
        )

    assert result.status == "BLOCKED"
    assert calls == 1


def _incident() -> CorrectionIncident:
    return CorrectionIncident.create(
        run_id="run-1",
        dag_id="dag-test",
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
        capability_grant=_grant(incident),
    )


def _grant(
    incident: CorrectionIncident,
    *,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    grant: dict[str, Any] = {
        "schema": "tau.capability_grant.v1",
        "grant_id": f"grant:{incident.incident_id}",
        "request_sha256": "sha256:request",
        "run_id": incident.run_id,
        "dag_id": incident.dag_id,
        "node_id": incident.node_id,
        "attempt": incident.attempt,
        "actor_id": "human:operator",
        "goal_hash": incident.goal_hash,
        "security_context_sha256": "sha256:security-context",
        "policy_profile_sha256": "sha256:policy",
        "data_boundary_sha256": "sha256:boundary",
        "capability": "provider.repair_auth",
        "target": "refresh_local_provider_auth",
        "resource_scope": ["provider:local-fixture"],
        "maximum_effect": {"max_repairs": 1},
        "issued_at": "2026-07-16T00:00:00Z",
        "expires_at": (
            expires_at or datetime.now(UTC) + timedelta(minutes=5)
        ).isoformat().replace("+00:00", "Z"),
        "granting_authority": "tau.command_spec_policy.v1",
    }
    grant["grant_sha256"] = capability_grant_sha256(grant)
    return grant


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
