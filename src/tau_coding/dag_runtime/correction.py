"""Durable, bounded correction transactions over Tau's canonical journal."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.run_store import (
    CORRECTION_JOURNAL_ENTRY_SCHEMA,
    REPAIR_CATEGORY_JOURNAL_ENTRY_SCHEMA,
    DagRunLease,
    SqliteDagRunStore,
)
from tau_coding.security_capability import validate_capability_grant

CORRECTION_INCIDENT_SCHEMA = "tau.correction_incident.v1"
CORRECTION_ACTION_INTENT_SCHEMA = "tau.correction_action_intent.v1"
CORRECTION_ACTION_RECEIPT_SCHEMA = "tau.correction_action_receipt.v1"
CORRECTION_VERIFICATION_SCHEMA = "tau.correction_verification.v1"
REPAIR_CATEGORY_SCHEMA = "tau.dag_repair_category.v1"
REPAIR_CATEGORY_STATES = frozenset(
    {
        "OPEN",
        "REPAIRING",
        "REVALIDATING",
        "RESOLVED",
        "SAME_NODE_RERUN",
        "ESCALATED_HUMAN",
        "TERMINAL",
    }
)
OPEN_REPAIR_CATEGORY_STATES = frozenset({"OPEN", "REPAIRING", "REVALIDATING"})
REPAIR_CATEGORY_CLOSED_STATES = frozenset(
    {"RESOLVED", "SAME_NODE_RERUN", "ESCALATED_HUMAN", "TERMINAL"}
)
REPAIR_FAMILIES = frozenset(
    {
        "result_contract_invalid",
        "adapter_execution_failed",
        "worker_assignment_failed",
        "lease_or_resource_failure",
        "stale_workspace_read",
        "route_contract_invalid",
        "join_contract_invalid",
        "effect_gate_failed",
        "checkpoint_or_replay_invalid",
        "triage_unavailable",
        "unknown_internal_failure",
    }
)
ALLOWED_REPAIR_HANDLER_IDS = frozenset(
    {
        "$pipeline-self-repair",
        "pipeline-self-repair",
        "scheduler.correction_handler",
        "tau.dag_runtime.correction_handler",
    }
)
_REPAIR_CATEGORY_ALLOWED_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"OPEN"}),
    "OPEN": frozenset({"REPAIRING", "ESCALATED_HUMAN", "TERMINAL"}),
    "REPAIRING": frozenset({"REVALIDATING", "ESCALATED_HUMAN", "TERMINAL"}),
    "REVALIDATING": frozenset({"RESOLVED", "ESCALATED_HUMAN", "TERMINAL"}),
    "RESOLVED": frozenset({"SAME_NODE_RERUN"}),
}

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
class RepairCategoryRecord:
    repair_id: str
    run_id: str
    plan_sha256: str
    goal_version: int
    goal_hash: str
    node_id: str
    failing_attempt_id: str
    failing_attempt: int
    checkpoint_ref: str
    original_failure_code: str
    classification_code: str
    repair_family: str
    repair_handler_id: str
    repair_args_digest: str
    repair_attempt_budget: int
    state: str
    required_closure_evidence: tuple[str, ...]
    closure_evidence_refs: tuple[str, ...]
    resulting_attempt_id: str | None
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        plan_sha256: str,
        goal_version: int,
        goal_hash: str,
        node_id: str,
        failing_attempt_id: str,
        failing_attempt: int,
        checkpoint_ref: str,
        original_failure_code: str,
        classification_code: str,
        repair_family: str,
        repair_handler_id: str,
        repair_args: Mapping[str, Any],
        repair_attempt_budget: int,
        required_closure_evidence: tuple[str, ...] | None = None,
        created_at: datetime | None = None,
    ) -> RepairCategoryRecord:
        if failing_attempt < 1:
            raise CorrectionTransactionError("repair_category_attempt_invalid")
        if repair_attempt_budget < 0:
            raise CorrectionTransactionError("repair_category_budget_invalid")
        if repair_family not in REPAIR_FAMILIES:
            raise CorrectionTransactionError("repair_family_invalid", repair_family)
        normalized_handler = repair_handler_id.strip()
        if not normalized_handler:
            raise CorrectionTransactionError("repair_handler_missing")
        evidence = required_closure_evidence or (
            "correction_action_receipt_sha256",
            "correction_verification_result_sha256",
        )
        basis = {
            "schema": REPAIR_CATEGORY_SCHEMA,
            "run_id": run_id,
            "plan_sha256": plan_sha256,
            "goal_version": goal_version,
            "goal_hash": goal_hash,
            "node_id": node_id,
            "failing_attempt_id": failing_attempt_id,
            "failing_attempt": failing_attempt,
            "checkpoint_ref": checkpoint_ref,
            "original_failure_code": original_failure_code,
            "classification_code": classification_code,
            "repair_family": repair_family,
            "repair_handler_id": normalized_handler,
            "repair_args_digest": canonical_sha256(dict(repair_args)),
        }
        digest = canonical_sha256(basis).removeprefix("sha256:")
        now = (created_at or datetime.now(UTC)).isoformat()
        return cls(
            repair_id=f"repair-{digest[:32]}",
            run_id=run_id,
            plan_sha256=plan_sha256,
            goal_version=goal_version,
            goal_hash=goal_hash,
            node_id=node_id,
            failing_attempt_id=failing_attempt_id,
            failing_attempt=failing_attempt,
            checkpoint_ref=checkpoint_ref,
            original_failure_code=original_failure_code,
            classification_code=classification_code,
            repair_family=repair_family,
            repair_handler_id=normalized_handler,
            repair_args_digest=str(basis["repair_args_digest"]),
            repair_attempt_budget=repair_attempt_budget,
            state="OPEN",
            required_closure_evidence=tuple(evidence),
            closure_evidence_refs=(),
            resulting_attempt_id=None,
            created_at=now,
            updated_at=now,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": REPAIR_CATEGORY_SCHEMA,
            "repair_id": self.repair_id,
            "run_id": self.run_id,
            "plan_sha256": self.plan_sha256,
            "goal_version": self.goal_version,
            "goal_hash": self.goal_hash,
            "node_id": self.node_id,
            "failing_attempt_id": self.failing_attempt_id,
            "failing_attempt": self.failing_attempt,
            "checkpoint": {"ref": self.checkpoint_ref},
            "original_failure_code": self.original_failure_code,
            "classification_code": self.classification_code,
            "repair_family": self.repair_family,
            "repair_handler_id": self.repair_handler_id,
            "repair_args_digest": self.repair_args_digest,
            "repair_attempt_budget": self.repair_attempt_budget,
            "state": self.state,
            "required_closure_evidence": list(self.required_closure_evidence),
            "closure_evidence_refs": list(self.closure_evidence_refs),
            "resulting_attempt_id": self.resulting_attempt_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def with_state(
        self,
        state: str,
        *,
        closure_evidence_refs: tuple[str, ...] | None = None,
        resulting_attempt_id: str | None = None,
        updated_at: datetime | None = None,
    ) -> RepairCategoryRecord:
        if state not in REPAIR_CATEGORY_STATES:
            raise CorrectionTransactionError("repair_category_state_invalid", state)
        return RepairCategoryRecord(
            repair_id=self.repair_id,
            run_id=self.run_id,
            plan_sha256=self.plan_sha256,
            goal_version=self.goal_version,
            goal_hash=self.goal_hash,
            node_id=self.node_id,
            failing_attempt_id=self.failing_attempt_id,
            failing_attempt=self.failing_attempt,
            checkpoint_ref=self.checkpoint_ref,
            original_failure_code=self.original_failure_code,
            classification_code=self.classification_code,
            repair_family=self.repair_family,
            repair_handler_id=self.repair_handler_id,
            repair_args_digest=self.repair_args_digest,
            repair_attempt_budget=self.repair_attempt_budget,
            state=state,
            required_closure_evidence=self.required_closure_evidence,
            closure_evidence_refs=(
                self.closure_evidence_refs
                if closure_evidence_refs is None
                else closure_evidence_refs
            ),
            resulting_attempt_id=(
                self.resulting_attempt_id
                if resulting_attempt_id is None
                else resulting_attempt_id
            ),
            created_at=self.created_at,
            updated_at=(updated_at or datetime.now(UTC)).isoformat(),
        )


@dataclass(frozen=True, slots=True)
class RepairCategoryProjection:
    repair_id: str
    state: str
    journal_sequence: int
    record: dict[str, Any]
    transitions: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class CorrectionIncident:
    incident_id: str
    run_id: str
    dag_id: str
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
        dag_id: str,
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
            "dag_id": dag_id,
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
            dag_id=dag_id,
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
            "dag_id": self.dag_id,
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
    capability_grant: Mapping[str, Any]
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
        capability_grant: Mapping[str, Any],
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
            "capability_grant_sha256": capability_grant.get("grant_sha256"),
        }
        digest = canonical_sha256(basis).removeprefix("sha256:")
        return cls(
            action_id=f"correction-action-{digest[:32]}",
            incident_id=incident.incident_id,
            capability=capability,
            action=action,
            target=dict(target),
            policy_sha256=policy_sha256,
            capability_grant=dict(capability_grant),
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
            "capability_grant": dict(self.capability_grant),
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
    checked_at: datetime | None = None,
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
        grant_errors = validate_capability_grant(
            intent.capability_grant,
            expected_bindings={
                "run_id": incident.run_id,
                "dag_id": incident.dag_id,
                "node_id": incident.node_id,
                "attempt": incident.attempt,
                "goal_hash": incident.goal_hash,
                "policy_profile_sha256": intent.policy_sha256,
                "capability": intent.capability,
                "target": intent.action,
            },
            checked_at=checked_at or datetime.now(UTC),
        )
        if incident.classification != "RETRYABLE" or grant_errors:
            reason = (
                "incident_not_retryable"
                if incident.classification != "RETRYABLE"
                else f"correction_action_not_authorized:{','.join(grant_errors)}"
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

    return _reduce_correction_projection(store.load_events(run_id), incident_id)


def reduce_correction_projections(
    events: tuple[Mapping[str, Any], ...],
) -> tuple[CorrectionStateProjection, ...]:
    """Reduce all correction lineages from an authoritative event sequence."""

    incident_ids = sorted(
        {
            str(event["entity_id"])
            for event in events
            if event.get("event_type") == "correction_state_committed"
            and event.get("entity_type") == "correction"
        }
    )
    projections: list[CorrectionStateProjection] = []
    for incident_id in incident_ids:
        projection = _reduce_correction_projection(events, incident_id)
        if projection is not None:
            projections.append(projection)
    return tuple(projections)


def classify_repair_family(original_code: str, classification_code: str) -> str:
    """Map DAG failure codes to the closed repair-family vocabulary."""

    code = f"{original_code} {classification_code}".lower()
    if "worker_assignment" in code:
        return "worker_assignment_failed"
    if "resource" in code or "lease" in code:
        return "lease_or_resource_failure"
    if "stale" in code or "workspace_read" in code:
        return "stale_workspace_read"
    if "route" in code:
        return "route_contract_invalid"
    if "join" in code:
        return "join_contract_invalid"
    if "effect" in code or "admission" in code or "gate" in code:
        return "effect_gate_failed"
    if "checkpoint" in code or "replay" in code or "lineage" in code:
        return "checkpoint_or_replay_invalid"
    if "triage" in code and ("unavailable" in code or "unclassified" in code):
        return "triage_unavailable"
    if "adapter" in code or "execution" in code or "future_exception" in code:
        return "adapter_execution_failed"
    if "contract" in code or "result" in code or "schema" in code or "invalid" in code:
        return "result_contract_invalid"
    return "unknown_internal_failure"


def repair_category_from_failure(
    *,
    run_id: str,
    plan_sha256: str,
    goal_binding: Mapping[str, Any],
    goal_hash: str,
    node_id: str,
    failing_attempt_id: str,
    failing_attempt: int,
    result: Mapping[str, Any],
    repair_handler_id: str,
    repair_attempt_budget: int,
) -> RepairCategoryRecord:
    """Build the durable category identity for one failed semantic node attempt."""

    failure = result.get("failure")
    failure_payload = dict(failure) if isinstance(failure, Mapping) else {}
    original_code = str(
        failure_payload.get("original_code")
        or result.get("original_failure_code")
        or result.get("verdict")
    )
    classification_code = str(
        result.get("classification_code")
        or result.get("verdict")
        or failure_payload.get("classification_code")
        or "unknown_internal_failure"
    )
    checkpoint = result.get("checkpoint")
    checkpoint_ref = ""
    if isinstance(checkpoint, Mapping):
        checkpoint_ref = str(checkpoint.get("ref") or checkpoint.get("path") or "")
    if not checkpoint_ref:
        checkpoint_ref = str(result.get("checkpoint_ref") or failing_attempt_id)
    goal_version_value = goal_binding.get("goal_version", 1)
    try:
        goal_version = int(goal_version_value)
    except (TypeError, ValueError):
        goal_version = 1
    repair_args = {
        "run_id": run_id,
        "plan_sha256": plan_sha256,
        "goal_hash": goal_hash,
        "node_id": node_id,
        "attempt_id": failing_attempt_id,
        "attempt": failing_attempt,
        "verdict": result.get("verdict"),
        "failure": failure_payload,
        "checkpoint_ref": checkpoint_ref,
    }
    return RepairCategoryRecord.create(
        run_id=run_id,
        plan_sha256=plan_sha256,
        goal_version=goal_version,
        goal_hash=goal_hash,
        node_id=node_id,
        failing_attempt_id=failing_attempt_id,
        failing_attempt=failing_attempt,
        checkpoint_ref=checkpoint_ref,
        original_failure_code=original_code,
        classification_code=classification_code,
        repair_family=classify_repair_family(original_code, classification_code),
        repair_handler_id=repair_handler_id,
        repair_args=repair_args,
        repair_attempt_budget=repair_attempt_budget,
    )


def ensure_repair_category_open(
    *,
    store: SqliteDagRunStore,
    lease: DagRunLease,
    category: RepairCategoryRecord,
) -> RepairCategoryProjection:
    """Create the single durable OPEN category if replay has not already seen it."""

    projection = load_repair_category_projection(store, category.repair_id, lease.run_id)
    if projection is not None:
        return projection
    return transition_repair_category(
        store=store,
        lease=lease,
        category=category,
        state="OPEN",
        reason="repairable_node_failure_classified",
    )


def transition_repair_category(
    *,
    store: SqliteDagRunStore,
    lease: DagRunLease,
    category: RepairCategoryRecord | RepairCategoryProjection,
    state: str,
    reason: str,
    closure_evidence_refs: tuple[str, ...] | None = None,
    resulting_attempt_id: str | None = None,
    correction_projection: CorrectionStateProjection | None = None,
) -> RepairCategoryProjection:
    """Append one repair-category transition after checking closed state flow."""

    current = (
        category
        if isinstance(category, RepairCategoryProjection)
        else load_repair_category_projection(store, category.repair_id, lease.run_id)
    )
    prior_state = current.state if isinstance(current, RepairCategoryProjection) else None
    if prior_state == state:
        return current
    if state not in _REPAIR_CATEGORY_ALLOWED_TRANSITIONS.get(prior_state, frozenset()):
        raise CorrectionTransactionError(
            "repair_category_transition_invalid", f"{prior_state}->{state}"
        )
    base_record = (
        _record_from_projection(current)
        if isinstance(current, RepairCategoryProjection)
        else category
    )
    updated = base_record.with_state(
        state,
        closure_evidence_refs=closure_evidence_refs,
        resulting_attempt_id=resulting_attempt_id,
    )
    transition = {
        "from_state": prior_state,
        "to_state": state,
        "reason": reason,
    }
    if correction_projection is not None:
        transition["correction_incident_id"] = correction_projection.incident_id
        transition["correction_state"] = correction_projection.state
        transition["correction_journal_sequence"] = correction_projection.journal_sequence
    payload = {
        "schema": REPAIR_CATEGORY_JOURNAL_ENTRY_SCHEMA,
        "repair_id": updated.repair_id,
        "state": state,
        "repair": updated.to_payload(),
        "transition": transition,
    }
    store.append_repair_category_event(
        lease,
        event_key=f"repair-category:{updated.repair_id}:{state.lower()}",
        repair_id=updated.repair_id,
        payload=payload,
    )
    return _required_repair_category_projection(store, updated.repair_id, lease.run_id)


def closure_evidence_refs_from_correction(
    projection: CorrectionStateProjection,
) -> tuple[str, ...]:
    """Return deterministic closure refs only when correction verification is valid."""

    if projection.state != "VERIFIED":
        return ()
    action_receipt = projection.action_receipt
    verification = projection.verification
    if not isinstance(action_receipt, Mapping) or not isinstance(verification, Mapping):
        return ()
    result = verification.get("result")
    if not isinstance(result, Mapping) or result.get("verified") is not True:
        return ()
    if verification.get("result_sha256") != canonical_sha256(dict(result)):
        return ()
    if _contains_model_statement_only_evidence(result):
        return ()
    action_digest = canonical_sha256(dict(action_receipt))
    verification_digest = canonical_sha256(dict(verification))
    return (
        f"correction_action_receipt:{action_digest}",
        f"correction_verification:{verification_digest}",
    )


def load_repair_category_projection(
    store: SqliteDagRunStore,
    repair_id: str,
    run_id: str,
) -> RepairCategoryProjection | None:
    """Reduce one durable repair category from verified canonical journal events."""

    return _reduce_repair_category_projection(store.load_events(run_id), repair_id)


def reduce_repair_category_projections(
    events: tuple[Mapping[str, Any], ...],
) -> tuple[RepairCategoryProjection, ...]:
    """Reduce all repair categories from an authoritative event sequence."""

    repair_ids = sorted(
        {
            str(event["entity_id"])
            for event in events
            if event.get("event_type") == "repair_category_state_committed"
            and event.get("entity_type") == "repair_category"
        }
    )
    projections: list[RepairCategoryProjection] = []
    for repair_id in repair_ids:
        projection = _reduce_repair_category_projection(events, repair_id)
        if projection is not None:
            projections.append(projection)
    return tuple(projections)


def _reduce_repair_category_projection(
    events: tuple[Mapping[str, Any], ...], repair_id: str
) -> RepairCategoryProjection | None:
    state: str | None = None
    record: dict[str, Any] | None = None
    transitions: list[dict[str, Any]] = []
    sequence = 0
    for event in events:
        if event["event_type"] != "repair_category_state_committed":
            continue
        if event["entity_id"] != repair_id:
            continue
        payload = event["payload"]
        if payload.get("schema") != REPAIR_CATEGORY_JOURNAL_ENTRY_SCHEMA:
            raise CorrectionTransactionError("repair_category_journal_entry_schema_invalid")
        next_state = payload.get("state")
        if next_state not in REPAIR_CATEGORY_STATES:
            raise CorrectionTransactionError("repair_category_state_invalid", str(next_state))
        if next_state not in _REPAIR_CATEGORY_ALLOWED_TRANSITIONS.get(state, frozenset()):
            raise CorrectionTransactionError(
                "repair_category_transition_invalid", f"{state}->{next_state}"
            )
        candidate_record = payload.get("repair")
        if not isinstance(candidate_record, Mapping):
            raise CorrectionTransactionError("repair_category_record_missing", repair_id)
        candidate = dict(candidate_record)
        if candidate.get("schema") != REPAIR_CATEGORY_SCHEMA:
            raise CorrectionTransactionError("repair_category_schema_invalid", repair_id)
        if candidate.get("repair_id") != repair_id:
            raise CorrectionTransactionError("repair_category_binding_mismatch", repair_id)
        if record is not None:
            _assert_same_repair_category_identity(record, candidate)
        state = str(next_state)
        record = candidate
        transition = payload.get("transition")
        if isinstance(transition, Mapping):
            transitions.append(dict(transition))
        sequence = int(event["seq"])
    if state is None:
        return None
    if record is None:
        raise CorrectionTransactionError("repair_category_record_missing", repair_id)
    return RepairCategoryProjection(
        repair_id=repair_id,
        state=state,
        journal_sequence=sequence,
        record=record,
        transitions=tuple(transitions),
    )


def _assert_same_repair_category_identity(
    prior: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    immutable_fields = (
        "repair_id",
        "run_id",
        "plan_sha256",
        "goal_version",
        "goal_hash",
        "node_id",
        "failing_attempt_id",
        "failing_attempt",
        "checkpoint",
        "original_failure_code",
        "classification_code",
        "repair_family",
        "repair_handler_id",
        "repair_args_digest",
        "repair_attempt_budget",
        "required_closure_evidence",
        "created_at",
    )
    for field in immutable_fields:
        if prior.get(field) != candidate.get(field):
            raise CorrectionTransactionError("repair_category_record_conflict", field)


def _record_from_projection(projection: RepairCategoryProjection) -> RepairCategoryRecord:
    payload = projection.record
    checkpoint = payload.get("checkpoint")
    checkpoint_ref = ""
    if isinstance(checkpoint, Mapping):
        checkpoint_ref = str(checkpoint.get("ref") or "")
    return RepairCategoryRecord(
        repair_id=str(payload["repair_id"]),
        run_id=str(payload["run_id"]),
        plan_sha256=str(payload["plan_sha256"]),
        goal_version=int(payload.get("goal_version", 1)),
        goal_hash=str(payload["goal_hash"]),
        node_id=str(payload["node_id"]),
        failing_attempt_id=str(payload["failing_attempt_id"]),
        failing_attempt=int(payload["failing_attempt"]),
        checkpoint_ref=checkpoint_ref,
        original_failure_code=str(payload["original_failure_code"]),
        classification_code=str(payload["classification_code"]),
        repair_family=str(payload["repair_family"]),
        repair_handler_id=str(payload["repair_handler_id"]),
        repair_args_digest=str(payload["repair_args_digest"]),
        repair_attempt_budget=int(payload["repair_attempt_budget"]),
        state=str(payload["state"]),
        required_closure_evidence=tuple(
            str(item) for item in payload.get("required_closure_evidence", [])
        ),
        closure_evidence_refs=tuple(
            str(item) for item in payload.get("closure_evidence_refs", [])
        ),
        resulting_attempt_id=(
            str(payload["resulting_attempt_id"])
            if payload.get("resulting_attempt_id") is not None
            else None
        ),
        created_at=str(payload["created_at"]),
        updated_at=str(payload["updated_at"]),
    )


def _required_repair_category_projection(
    store: SqliteDagRunStore, repair_id: str, run_id: str
) -> RepairCategoryProjection:
    projection = load_repair_category_projection(store, repair_id, run_id)
    if projection is None:
        raise CorrectionTransactionError("repair_category_projection_missing", repair_id)
    return projection


def _contains_model_statement_only_evidence(value: Mapping[str, Any]) -> bool:
    evidence = value.get("evidence")
    if isinstance(evidence, Mapping):
        return evidence.get("kind") == "model_statement"
    if isinstance(evidence, list):
        return any(
            isinstance(item, Mapping) and item.get("kind") == "model_statement"
            for item in evidence
        )
    return False


def _reduce_correction_projection(
    events: tuple[Mapping[str, Any], ...], incident_id: str
) -> CorrectionStateProjection | None:

    state: str | None = None
    incident: dict[str, Any] | None = None
    intent: dict[str, Any] | None = None
    action_receipt: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    sequence = 0
    for event in events:
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
