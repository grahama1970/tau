"""Durable, bounded correction transactions over Tau's canonical journal."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.run_store import (
    CORRECTION_JOURNAL_ENTRY_SCHEMA,
    DagRunLease,
    SqliteDagRunStore,
)

CORRECTION_INCIDENT_SCHEMA = "tau.correction_incident.v1"
CORRECTION_ACTION_INTENT_SCHEMA = "tau.correction_action_intent.v1"
CORRECTION_ACTION_RECEIPT_SCHEMA = "tau.correction_action_receipt.v1"
CORRECTION_VERIFICATION_SCHEMA = "tau.correction_verification.v1"

CORRECTION_STATES = frozenset(
    {
        "REQUESTED",
        "INTENT_COMMITTED",
        "STARTED",
        "APPLIED",
        "VERIFIED",
        "REJECTED",
        "EXHAUSTED",
        "HUMAN_ROUTED",
        "UNCERTAIN",
    }
)
TERMINAL_CORRECTION_STATES = frozenset(
    {"VERIFIED", "REJECTED", "EXHAUSTED", "HUMAN_ROUTED", "UNCERTAIN"}
)
_ALLOWED_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"REQUESTED"}),
    "REQUESTED": frozenset({"INTENT_COMMITTED", "HUMAN_ROUTED", "EXHAUSTED"}),
    "INTENT_COMMITTED": frozenset({"STARTED"}),
    "STARTED": frozenset({"APPLIED", "UNCERTAIN"}),
    "APPLIED": frozenset({"VERIFIED", "REJECTED"}),
}


class CorrectionTransactionError(RuntimeError):
    """Fail-closed correction error with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class CorrectionIncident:
    incident_id: str
    run_id: str
    node_id: str
    attempt: int
    trigger: str
    classification: str
    goal_hash: str
    observed_state: Mapping[str, Any]

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        node_id: str,
        attempt: int,
        trigger: str,
        classification: str,
        goal_hash: str,
        observed_state: Mapping[str, Any] | None = None,
    ) -> CorrectionIncident:
        if attempt < 1:
            raise CorrectionTransactionError("correction_attempt_invalid")
        if classification not in {
            "RETRYABLE",
            "NON_RETRYABLE",
            "APPROVAL_REQUIRED",
            "UNCERTAIN",
            "EXHAUSTED",
        }:
            raise CorrectionTransactionError(
                "correction_incident_classification_invalid", classification
            )
        basis = {
            "schema": CORRECTION_INCIDENT_SCHEMA,
            "run_id": run_id,
            "node_id": node_id,
            "attempt": attempt,
            "trigger": trigger,
            "classification": classification,
            "goal_hash": goal_hash,
            "observed_state": dict(observed_state or {}),
        }
        digest = canonical_sha256(basis).removeprefix("sha256:")
        return cls(
            incident_id=f"incident-{digest[:32]}",
            run_id=run_id,
            node_id=node_id,
            attempt=attempt,
            trigger=trigger,
            classification=classification,
            goal_hash=goal_hash,
            observed_state=dict(observed_state or {}),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": CORRECTION_INCIDENT_SCHEMA,
            "incident_id": self.incident_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "attempt": self.attempt,
            "trigger": self.trigger,
            "classification": self.classification,
            "goal_hash": self.goal_hash,
            "observed_state": dict(self.observed_state),
        }


@dataclass(frozen=True, slots=True)
class CorrectionActionIntent:
    action_id: str
    incident_id: str
    capability: str
    action: str
    target: Mapping[str, Any]
    policy_sha256: str
    authorized: bool
    idempotency_key: str

    @classmethod
    def create(
        cls,
        *,
        incident: CorrectionIncident,
        capability: str,
        action: str,
        target: Mapping[str, Any],
        policy_sha256: str,
        authorized: bool,
    ) -> CorrectionActionIntent:
        basis = {
            "schema": CORRECTION_ACTION_INTENT_SCHEMA,
            "run_id": incident.run_id,
            "node_id": incident.node_id,
            "attempt": incident.attempt,
            "incident_id": incident.incident_id,
            "capability": capability,
            "action": action,
            "target": dict(target),
            "policy_sha256": policy_sha256,
        }
        digest = canonical_sha256(basis).removeprefix("sha256:")
        return cls(
            action_id=f"correction-action-{digest[:32]}",
            incident_id=incident.incident_id,
            capability=capability,
            action=action,
            target=dict(target),
            policy_sha256=policy_sha256,
            authorized=authorized,
            idempotency_key=canonical_sha256({**basis, "purpose": "correction_effect"}),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": CORRECTION_ACTION_INTENT_SCHEMA,
            "action_id": self.action_id,
            "incident_id": self.incident_id,
            "capability": self.capability,
            "action": self.action,
            "target": dict(self.target),
            "policy_sha256": self.policy_sha256,
            "authorized": self.authorized,
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class CorrectionStateProjection:
    incident_id: str
    state: str
    journal_sequence: int
    incident: dict[str, Any]
    intent: dict[str, Any] | None
    action_receipt: dict[str, Any] | None
    verification: dict[str, Any] | None


CorrectionAction = Callable[[CorrectionActionIntent], Mapping[str, Any]]
CorrectionVerifier = Callable[
    [CorrectionActionIntent, Mapping[str, Any]], Mapping[str, Any]
]
CorrectionFaultInjector = Callable[[str, Mapping[str, Any]], None]


def run_correction_transaction(
    *,
    store: SqliteDagRunStore,
    lease: DagRunLease,
    incident: CorrectionIncident,
    intent: CorrectionActionIntent,
    apply_action: CorrectionAction,
    verify_action: CorrectionVerifier,
    fault_injector: CorrectionFaultInjector | None = None,
) -> CorrectionStateProjection:
    """Run or resume one correction without reapplying an applied effect."""

    if incident.run_id != lease.run_id:
        raise CorrectionTransactionError("correction_run_binding_mismatch")
    if intent.incident_id != incident.incident_id:
        raise CorrectionTransactionError("correction_intent_binding_mismatch")
    store.assert_active_lease(lease)
    projection = load_correction_projection(store, incident.incident_id, lease.run_id)
    if projection is None:
        _append_state(store, lease, incident, "REQUESTED", incident=incident.to_payload())
        projection = load_correction_projection(store, incident.incident_id, lease.run_id)
        assert projection is not None
    _assert_projection_inputs(projection, incident, intent)
    if projection.state in TERMINAL_CORRECTION_STATES:
        return projection

    if projection.state == "REQUESTED":
        if incident.classification != "RETRYABLE" or not intent.authorized:
            reason = (
                "incident_not_retryable"
                if incident.classification != "RETRYABLE"
                else "correction_action_not_authorized"
            )
            _append_state(store, lease, incident, "HUMAN_ROUTED", reason=reason)
            return _required_projection(store, incident)
        _append_state(
            store,
            lease,
            incident,
            "INTENT_COMMITTED",
            intent=intent.to_payload(),
        )
        projection = _required_projection(store, incident)

    started_now = False
    if projection.state == "INTENT_COMMITTED":
        _append_state(store, lease, incident, "STARTED", intent=intent.to_payload())
        started_now = True
        projection = _required_projection(store, incident)
        _inject_fault(fault_injector, "after_started", projection)
    elif projection.state == "STARTED":
        _append_state(
            store,
            lease,
            incident,
            "UNCERTAIN",
            reason="correction_effect_state_unknown_after_restart",
        )
        return _required_projection(store, incident)

    if started_now:
        result = dict(apply_action(intent))
        action_receipt = {
            "schema": CORRECTION_ACTION_RECEIPT_SCHEMA,
            "incident_id": incident.incident_id,
            "action_id": intent.action_id,
            "idempotency_key": intent.idempotency_key,
            "result": result,
            "result_sha256": canonical_sha256(result),
        }
        _append_state(
            store,
            lease,
            incident,
            "APPLIED",
            intent=intent.to_payload(),
            action_receipt=action_receipt,
        )
        projection = _required_projection(store, incident)
        _inject_fault(fault_injector, "after_applied", projection)

    if projection.state != "APPLIED" or projection.action_receipt is None:
        raise CorrectionTransactionError("correction_projection_invalid", projection.state)
    verification_result = dict(verify_action(intent, projection.action_receipt))
    verified = verification_result.get("verified") is True
    verification = {
        "schema": CORRECTION_VERIFICATION_SCHEMA,
        "incident_id": incident.incident_id,
        "action_id": intent.action_id,
        "verified": verified,
        "result": verification_result,
        "result_sha256": canonical_sha256(verification_result),
    }
    _append_state(
        store,
        lease,
        incident,
        "VERIFIED" if verified else "REJECTED",
        verification=verification,
    )
    return _required_projection(store, incident)


def load_correction_projection(
    store: SqliteDagRunStore,
    incident_id: str,
    run_id: str,
) -> CorrectionStateProjection | None:
    """Reduce one correction solely from verified canonical journal events."""

    state: str | None = None
    incident: dict[str, Any] | None = None
    intent: dict[str, Any] | None = None
    action_receipt: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    sequence = 0
    for event in store.load_events(run_id):
        if event["event_type"] != "correction_state_committed":
            continue
        if event["entity_id"] != incident_id:
            continue
        payload = event["payload"]
        if payload.get("schema") != CORRECTION_JOURNAL_ENTRY_SCHEMA:
            raise CorrectionTransactionError("correction_journal_entry_schema_invalid")
        next_state = payload.get("state")
        if next_state not in CORRECTION_STATES:
            raise CorrectionTransactionError("correction_state_invalid", str(next_state))
        if next_state not in _ALLOWED_TRANSITIONS.get(state, frozenset()):
            raise CorrectionTransactionError(
                "correction_transition_invalid", f"{state}->{next_state}"
            )
        state = str(next_state)
        sequence = int(event["seq"])
        candidate_incident = payload.get("incident")
        if isinstance(candidate_incident, dict):
            if incident is not None and incident != candidate_incident:
                raise CorrectionTransactionError("correction_incident_conflict")
            incident = candidate_incident
        candidate_intent = payload.get("intent")
        if isinstance(candidate_intent, dict):
            if intent is not None and intent != candidate_intent:
                raise CorrectionTransactionError("correction_intent_conflict")
            intent = candidate_intent
        candidate_receipt = payload.get("action_receipt")
        if isinstance(candidate_receipt, dict):
            action_receipt = candidate_receipt
        candidate_verification = payload.get("verification")
        if isinstance(candidate_verification, dict):
            verification = candidate_verification
    if state is None:
        return None
    if incident is None:
        raise CorrectionTransactionError("correction_incident_missing")
    return CorrectionStateProjection(
        incident_id=incident_id,
        state=state,
        journal_sequence=sequence,
        incident=incident,
        intent=intent,
        action_receipt=action_receipt,
        verification=verification,
    )


def _append_state(
    store: SqliteDagRunStore,
    lease: DagRunLease,
    incident_record: CorrectionIncident,
    state: str,
    **fields: Any,
) -> int:
    payload = {
        "schema": CORRECTION_JOURNAL_ENTRY_SCHEMA,
        "incident_id": incident_record.incident_id,
        "state": state,
        **fields,
    }
    return store.append_correction_event(
        lease,
        event_key=f"correction:{incident_record.incident_id}:{state.lower()}",
        incident_id=incident_record.incident_id,
        payload=payload,
    )


def _required_projection(
    store: SqliteDagRunStore, incident: CorrectionIncident
) -> CorrectionStateProjection:
    projection = load_correction_projection(store, incident.incident_id, incident.run_id)
    if projection is None:
        raise CorrectionTransactionError("correction_projection_missing")
    return projection


def _assert_projection_inputs(
    projection: CorrectionStateProjection,
    incident: CorrectionIncident,
    intent: CorrectionActionIntent,
) -> None:
    if projection.incident != incident.to_payload():
        raise CorrectionTransactionError("correction_incident_conflict")
    if projection.intent is not None and projection.intent != intent.to_payload():
        raise CorrectionTransactionError("correction_intent_conflict")


def _inject_fault(
    fault_injector: CorrectionFaultInjector | None,
    phase: str,
    projection: CorrectionStateProjection,
) -> None:
    if fault_injector is not None:
        fault_injector(
            phase,
            {
                "incident_id": projection.incident_id,
                "state": projection.state,
                "journal_sequence": projection.journal_sequence,
            },
        )
