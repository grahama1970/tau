"""Causal, route, join, and attention projections from committed journal prefixes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.replay import DagReplayState
from tau_coding.dag_viewer.receipt_index import IndexedReceipt, ReceiptIndex

SUBJECT_KINDS = frozenset(
    {"RUN", "NODE", "EDGE", "TERMINAL", "ROUTE", "JOIN", "ATTEMPT", "CORRECTION", "ATTENTION"}
)
ATTENTION_SEVERITY_RANK = {"BLOCKER": 0, "ACTION_REQUIRED": 1, "WARNING": 2}


@dataclass(frozen=True, slots=True)
class DagCausalModel:
    routes: tuple[dict[str, Any], ...]
    joins: tuple[dict[str, Any], ...]
    attention_items: tuple[dict[str, Any], ...]
    explanations: tuple[dict[str, Any], ...]
    explanation_ids: dict[tuple[str, str], str]

    def explanation(self, kind: str, subject_id: str) -> dict[str, Any]:
        if kind not in SUBJECT_KINDS:
            raise RuntimeError("dag_viewer_explanation_subject_kind_invalid")
        explanation_id = self.explanation_ids.get((kind, subject_id))
        if explanation_id is None:
            raise RuntimeError("dag_viewer_explanation_not_found")
        return next(
            item for item in self.explanations if item["explanation_id"] == explanation_id
        )


def build_causal_model(
    *,
    replay: DagReplayState,
    events: tuple[dict[str, Any], ...],
    receipts: ReceiptIndex,
    node_projections: list[dict[str, Any]],
    edge_projections: list[dict[str, Any]],
    terminal_projections: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
    projection_state: str,
) -> DagCausalModel:
    """Build bounded explanations without inferring authority from topology or runtime text."""

    authority_events = _authority_events(events)
    explanations: list[dict[str, Any]] = []
    ids: dict[tuple[str, str], str] = {}

    def explain(
        kind: str,
        subject_id: str,
        *,
        projected_state: str,
        reason_code: str,
        trigger_sequence: int,
        references: list[dict[str, Any]],
    ) -> str:
        explanation = _explanation(
            run_id=replay.run_id,
            as_of_sequence=replay.journal_sequence,
            kind=kind,
            subject_id=subject_id,
            projected_state=projected_state,
            reason_code=reason_code,
            trigger_sequence=trigger_sequence,
            references=references,
        )
        key = (kind, subject_id)
        explanation_id = str(explanation["explanation_id"])
        ids[key] = explanation_id
        explanations.append(explanation)
        return explanation_id

    run_reason = replay.run_verdict or replay.run_status
    run_sequence = _last_event_sequence(events, {"run_completed", "run_blocked"})
    explain(
        "RUN",
        replay.run_id,
        projected_state=replay.run_status,
        reason_code=str(run_reason),
        trigger_sequence=run_sequence,
        references=[_journal_reference(run_sequence)],
    )

    for node in node_projections:
        node_id = str(node["node_id"])
        sequence, reason = _node_cause(node_id, events, authority_events)
        node["causal_explanation_id"] = explain(
            "NODE",
            node_id,
            projected_state=str(node["scheduler"]["state"]),
            reason_code=reason,
            trigger_sequence=sequence,
            references=[_journal_reference(sequence)],
        )

    for edge in edge_projections:
        edge_id = str(edge["edge_id"])
        sequence, reason = _edge_cause(edge_id, authority_events)
        edge["reason_code"] = reason
        edge["last_change_sequence"] = sequence
        edge["causal_explanation_id"] = explain(
            "EDGE",
            edge_id,
            projected_state=str(edge["state"]),
            reason_code=reason,
            trigger_sequence=sequence,
            references=[_journal_reference(sequence)],
        )

    for terminal in terminal_projections:
        terminal_id = str(terminal["terminal_id"])
        sequence, reason = _terminal_cause(terminal_id, replay, authority_events)
        terminal["reason_code"] = reason
        terminal["last_change_sequence"] = sequence
        terminal["causal_explanation_id"] = explain(
            "TERMINAL",
            terminal_id,
            projected_state=str(terminal["state"]),
            reason_code=reason,
            trigger_sequence=sequence,
            references=[_journal_reference(sequence)],
        )

    routes = _route_projections(replay, authority_events, receipts, explain)
    joins = _join_projections(replay, authority_events, receipts, explain)

    for correction in corrections:
        incident_id = str(correction["incident_id"])
        sequence = int(correction["journal_sequence"])
        reason = str(correction.get("incident", {}).get("trigger", correction["state"]))
        correction["causal_explanation_id"] = explain(
            "CORRECTION",
            incident_id,
            projected_state=str(correction["state"]),
            reason_code=reason,
            trigger_sequence=sequence,
            references=[_journal_reference(sequence)],
        )

    attention = _attention_items(
        replay=replay,
        corrections=corrections,
        projection_state=projection_state,
        run_sequence=run_sequence,
        explain=explain,
    )
    return DagCausalModel(
        routes=tuple(routes),
        joins=tuple(joins),
        attention_items=tuple(attention),
        explanations=tuple(sorted(explanations, key=lambda item: item["explanation_id"])),
        explanation_ids=ids,
    )


def _authority_events(events: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for journal in events:
        if journal.get("event_type") not in {
            "scheduler_transition_committed",
            "scheduler_control_transition_committed",
        }:
            continue
        payload = journal.get("payload")
        transition = payload.get("transition") if isinstance(payload, dict) else None
        if not isinstance(transition, dict):
            continue
        sequence = int(journal["seq"])
        for item in transition.get("events", []):
            if isinstance(item, dict):
                flattened.append({**item, "journal_sequence": sequence})
        for item in transition.get("edge_settlements", []):
            if isinstance(item, dict):
                flattened.append(
                    {"event": "edge_settled", **item, "journal_sequence": sequence}
                )
        for item in transition.get("node_settlements", []):
            if isinstance(item, dict):
                flattened.append(
                    {"event": "node_settled", **item, "journal_sequence": sequence}
                )
        if isinstance(transition.get("block_run"), dict):
            flattened.append(
                {
                    "event": "run_blocked_by_transition",
                    **transition["block_run"],
                    "journal_sequence": sequence,
                }
            )
    return flattened


def _route_projections(
    replay: DagReplayState,
    authority_events: list[dict[str, Any]],
    receipts: ReceiptIndex,
    explain: Any,
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    edges_by_id = {edge.edge_id: edge for edge in replay.plan.control_edges}
    for frozen in replay.plan.route_contracts:
        contract = frozen.to_value()
        source = str(contract["source_node_id"])
        route_id = f"route:{source}"
        decision_event = next(
            (
                item
                for item in reversed(authority_events)
                if item.get("event") == "route_decided" and item.get("node_id") == source
            ),
            None,
        )
        selected_edges: list[str] = []
        skipped_edges: list[str] = []
        state = "PENDING"
        reason = "route_decision_not_committed"
        sequence = 0
        attempt = 0
        receipt_ref: dict[str, Any] | None = None
        if decision_event is not None:
            sequence = int(decision_event["journal_sequence"])
            attempt = int(decision_event.get("attempt", 0))
            receipt_ref = _receipt_reference(
                receipts, str(decision_event.get("route_decision_receipt", ""))
            )
            receipt = receipts.read_projection(str(receipt_ref["receipt_id"]))["receipt"]
            if receipt.get("schema") != "tau.dag_route_decision.v1":
                raise RuntimeError("dag_viewer_route_receipt_schema_invalid")
            targets = set(receipt.get("selected_targets", []))
            for edge_id in contract["ordered_edge_ids"]:
                edge = edges_by_id[str(edge_id)]
                destination = selected_edges if edge.target_id in targets else skipped_edges
                destination.append(edge.edge_id)
            if receipt.get("status") == "PASS":
                state = "SELECTED"
                reason = "route_selected"
            else:
                state = "BLOCKED"
                reason = str(receipt.get("failure_code") or "route_blocked")
        references = [
            {
                "kind": "PLAN_CONTRACT",
                "relation": "AUTHORIZED_BY",
                "reference_id": route_id,
                "sha256": contract["contract_sha256"],
            }
        ]
        if sequence:
            references.append(_journal_reference(sequence))
        if receipt_ref is not None:
            references.append(
                {
                    "kind": "TRANSITION_RECEIPT",
                    "relation": "EVIDENCED_BY",
                    "reference_id": str(receipt_ref["receipt_id"]),
                    "receipt_id": receipt_ref["receipt_id"],
                    "sha256": receipt_ref["sha256"],
                }
            )
        explanation_id = explain(
            "ROUTE",
            route_id,
            projected_state=state,
            reason_code=reason,
            trigger_sequence=sequence,
            references=references,
        )
        projected.append(
            {
                "schema": "tau.dag_route_projection.v1",
                "route_id": route_id,
                "source_node_id": source,
                "attempt": attempt,
                "mode": contract["mode"],
                "contract_sha256": contract["contract_sha256"],
                "state": state,
                "selected_edge_ids": selected_edges,
                "skipped_edge_ids": skipped_edges,
                "reason_code": reason,
                "decision_sequence": sequence or None,
                "decision_receipt_id": receipt_ref["receipt_id"] if receipt_ref else None,
                "decision_receipt_sha256": receipt_ref["sha256"] if receipt_ref else None,
                "causal_explanation_id": explanation_id,
            }
        )
    return projected


def _join_projections(
    replay: DagReplayState,
    authority_events: list[dict[str, Any]],
    receipts: ReceiptIndex,
    explain: Any,
) -> list[dict[str, Any]]:
    edges_by_id = {edge.edge_id: edge for edge in replay.plan.control_edges}
    projected: list[dict[str, Any]] = []
    for frozen in replay.plan.join_contracts:
        contract = frozen.to_value()
        join_id = str(contract["join_node_id"])
        contributions: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = [
            {
                "kind": "PLAN_CONTRACT",
                "relation": "AUTHORIZED_BY",
                "reference_id": join_id,
                "sha256": contract["policy_sha256"],
            }
        ]
        for event in authority_events:
            if event.get("event") != "terminal_contribution_recorded" or event.get(
                "join_node_id"
            ) != join_id:
                continue
            edge = next(
                (
                    edges_by_id[edge_id]
                    for edge_id in contract["incoming_edge_ids"]
                    if edges_by_id[edge_id].source_node_id == event.get("source_node_id")
                ),
                None,
            )
            if edge is None:
                raise RuntimeError("dag_viewer_join_contribution_edge_invalid")
            receipt_ref = _receipt_reference(receipts, str(event.get("receipt", "")))
            receipt = receipts.read_projection(str(receipt_ref["receipt_id"]))["receipt"]
            if receipt.get("schema") != "tau.dag_terminal_contribution.v1":
                raise RuntimeError("dag_viewer_join_contribution_schema_invalid")
            sequence = int(event["journal_sequence"])
            contributions.append(
                {
                    "edge_id": edge.edge_id,
                    "source_node_id": edge.source_node_id,
                    "state": receipt["state"],
                    "reason_code": receipt["reason_code"],
                    "contribution_receipt_id": receipt_ref["receipt_id"],
                    "contribution_receipt_sha256": receipt_ref["sha256"],
                    "contribution_sequence": sequence,
                }
            )
            references.extend(
                [
                    _journal_reference(sequence),
                    {
                        "kind": "TRANSITION_RECEIPT",
                        "relation": "EVIDENCED_BY",
                        "reference_id": str(receipt_ref["receipt_id"]),
                        "receipt_id": receipt_ref["receipt_id"],
                        "sha256": receipt_ref["sha256"],
                    },
                ]
            )
        decision_event = next(
            (
                item
                for item in reversed(authority_events)
                if item.get("event") == "join_decided" and item.get("join_node_id") == join_id
            ),
            None,
        )
        decision: str | None = None
        decision_sequence: int | None = None
        decision_receipt: dict[str, Any] | None = None
        state = "WAITING" if contributions else "PENDING"
        reason = "join_waiting_for_contributions" if contributions else "join_not_started"
        if decision_event is not None:
            decision_sequence = int(decision_event["journal_sequence"])
            decision_receipt = _receipt_reference(
                receipts, str(decision_event.get("join_decision_receipt", ""))
            )
            receipt = receipts.read_projection(str(decision_receipt["receipt_id"]))["receipt"]
            if receipt.get("schema") != "tau.dag_join_decision.v1":
                raise RuntimeError("dag_viewer_join_receipt_schema_invalid")
            decision = str(receipt["decision"])
            state = {"release": "RELEASED", "skip": "SKIPPED", "block": "BLOCKED"}[
                decision
            ]
            reason = str(receipt["reason_code"])
            references.extend(
                [
                    _journal_reference(decision_sequence),
                    {
                        "kind": "TRANSITION_RECEIPT",
                        "relation": "ADMITTED_BY",
                        "reference_id": str(decision_receipt["receipt_id"]),
                        "receipt_id": decision_receipt["receipt_id"],
                        "sha256": decision_receipt["sha256"],
                    },
                ]
            )
        deadline_events = [
            item
            for item in authority_events
            if item.get("join_node_id") == join_id
            and item.get("event") in {"join_deadline_armed", "join_timeout_expired"}
        ]
        deadline_state = (
            "EXPIRED"
            if any(item["event"] == "join_timeout_expired" for item in deadline_events)
            else "ARMED"
            if deadline_events
            else "NOT_ARMED"
        )
        trigger_sequence = decision_sequence or max(
            (int(item["contribution_sequence"]) for item in contributions), default=0
        )
        explanation_id = explain(
            "JOIN",
            join_id,
            projected_state=state,
            reason_code=reason,
            trigger_sequence=trigger_sequence,
            references=references,
        )
        projected.append(
            {
                "schema": "tau.dag_join_projection.v1",
                "join_node_id": join_id,
                "policy_sha256": contract["policy_sha256"],
                "state": state,
                "reason_code": reason,
                "deadline_state": deadline_state,
                "incoming": sorted(contributions, key=lambda item: item["edge_id"]),
                "decision": decision,
                "decision_sequence": decision_sequence,
                "decision_receipt_id": (
                    decision_receipt["receipt_id"] if decision_receipt else None
                ),
                "decision_receipt_sha256": (
                    decision_receipt["sha256"] if decision_receipt else None
                ),
                "causal_explanation_id": explanation_id,
            }
        )
    return projected


def _attention_items(
    *,
    replay: DagReplayState,
    corrections: list[dict[str, Any]],
    projection_state: str,
    run_sequence: int,
    explain: Any,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if replay.run_status == "RECONCILIATION_REQUIRED":
        candidates.append(
            _attention_candidate(
                subject={"kind": "RUN", "id": replay.run_id},
                severity="BLOCKER",
                reason="run_reconciliation_required",
                action="RECONCILE_EFFECT",
                sequence=run_sequence,
            )
        )
    elif replay.run_status == "BLOCKED":
        candidates.append(
            _attention_candidate(
                subject={"kind": "RUN", "id": replay.run_id},
                severity="ACTION_REQUIRED",
                reason=str(replay.run_verdict or "run_blocked"),
                action="REVIEW_BLOCKED_RUN",
                sequence=run_sequence,
            )
        )
    if projection_state == "STALE":
        candidates.append(
            _attention_candidate(
                subject={"kind": "RUN", "id": replay.run_id},
                severity="WARNING",
                reason="live_observation_stale",
                action="RESTORE_LIVE_OBSERVATION",
                sequence=replay.journal_sequence,
            )
        )
    for correction in corrections:
        state = str(correction["state"])
        if state not in {"UNCERTAIN", "REJECTED", "EXHAUSTED", "HUMAN_ROUTED"}:
            continue
        severity, action = {
            "UNCERTAIN": ("BLOCKER", "RECONCILE_EFFECT"),
            "REJECTED": ("ACTION_REQUIRED", "REVIEW_BLOCKED_RUN"),
            "EXHAUSTED": ("ACTION_REQUIRED", "DECIDE_EXHAUSTED_INCIDENT"),
            "HUMAN_ROUTED": ("ACTION_REQUIRED", "PROVIDE_APPROVAL"),
        }[state]
        candidates.append(
            _attention_candidate(
                subject={"kind": "CORRECTION", "id": correction["incident_id"]},
                severity=severity,
                reason=f"correction_{state.lower()}",
                action=action,
                sequence=int(correction["journal_sequence"]),
            )
        )
    for item in candidates:
        item["causal_explanation_id"] = explain(
            "ATTENTION",
            str(item["attention_id"]),
            projected_state=str(item["state"]),
            reason_code=str(item["reason_code"]),
            trigger_sequence=int(item["opened_sequence"]),
            references=[_journal_reference(int(item["opened_sequence"]))],
        )
    return sorted(
        candidates,
        key=lambda item: (
            0 if item["state"] == "OPEN" else 1,
            ATTENTION_SEVERITY_RANK[str(item["severity"])],
            int(item["opened_sequence"]),
            str(item["attention_id"]),
        ),
    )


def _attention_candidate(
    *, subject: dict[str, str], severity: str, reason: str, action: str, sequence: int
) -> dict[str, Any]:
    basis = {"subject": subject, "reason_code": reason, "opened_sequence": sequence}
    return {
        "schema": "tau.dag_attention_item.v1",
        "attention_id": f"attention-{canonical_sha256(basis).removeprefix('sha256:')[:24]}",
        "severity": severity,
        "state": "OPEN",
        "reason_code": reason,
        "subject": subject,
        "opened_sequence": sequence,
        "resolved_sequence": None,
        "required_action_code": action,
    }


def _explanation(
    *,
    run_id: str,
    as_of_sequence: int,
    kind: str,
    subject_id: str,
    projected_state: str,
    reason_code: str,
    trigger_sequence: int,
    references: list[dict[str, Any]],
) -> dict[str, Any]:
    subject = {"kind": kind, "id": subject_id}
    basis = {
        "run_id": run_id,
        "as_of_sequence": as_of_sequence,
        "subject": subject,
        "projected_state": projected_state,
        "reason_code": reason_code,
        "trigger_sequence": trigger_sequence,
        "references": references,
    }
    return {
        "schema": "tau.dag_causal_explanation.v1",
        "explanation_id": f"explanation-{canonical_sha256(basis).removeprefix('sha256:')[:32]}",
        **basis,
        "summary_code": f"{kind.lower()}_{projected_state.lower()}",
        "chain": [
            {
                "step": index + 1,
                "relation": reference["relation"],
                "reference_id": reference["reference_id"],
            }
            for index, reference in enumerate(references)
        ],
        "proof_scope": {
            "proves": [
                "Tau derived this explanation from the selected verified journal prefix."
            ],
            "does_not_prove": [
                "The agent output is semantically correct.",
                "The browser may mutate or continue the run.",
            ],
        },
    }


def _receipt_reference(receipts: ReceiptIndex, raw_path: str) -> dict[str, Any]:
    if not raw_path:
        raise RuntimeError("dag_viewer_transition_receipt_missing")
    matches: list[IndexedReceipt] = [
        entry for entry in receipts.entries if entry.path == Path(raw_path).expanduser().resolve()
    ]
    if len(matches) != 1:
        raise RuntimeError("dag_viewer_transition_receipt_not_indexed")
    entry = matches[0]
    return {"receipt_id": entry.receipt_id, "sha256": entry.sha256, "schema": entry.schema}


def _journal_reference(sequence: int) -> dict[str, Any]:
    return {
        "kind": "JOURNAL_EVENT",
        "relation": "CAUSED_BY",
        "reference_id": f"journal:{sequence}",
        "journal_sequence": sequence,
    }


def _last_event_sequence(events: tuple[dict[str, Any], ...], kinds: set[str]) -> int:
    return max((int(item["seq"]) for item in events if item.get("event_type") in kinds), default=1)


def _node_cause(
    node_id: str, events: tuple[dict[str, Any], ...], authority_events: list[dict[str, Any]]
) -> tuple[int, str]:
    settled = next(
        (
            item
            for item in reversed(authority_events)
            if item.get("event") == "node_settled" and item.get("node_id") == node_id
        ),
        None,
    )
    if settled:
        return int(settled["journal_sequence"]), str(settled.get("reason_code", "node_settled"))
    attempt_ids: set[str] = set()
    attempts: list[dict[str, Any]] = []
    for item in events:
        if item.get("entity_type") != "attempt":
            continue
        payload = item.get("payload")
        entity_id = str(item.get("entity_id", ""))
        if isinstance(payload, dict) and payload.get("node_id") == node_id:
            attempt_ids.add(entity_id)
        if entity_id in attempt_ids:
            attempts.append(item)
    if attempts:
        return int(attempts[-1]["seq"]), str(attempts[-1]["event_type"])
    return 1, "node_pending"


def _edge_cause(edge_id: str, authority_events: list[dict[str, Any]]) -> tuple[int, str]:
    event = next(
        (
            item
            for item in reversed(authority_events)
            if item.get("event") == "edge_settled" and item.get("edge_id") == edge_id
        ),
        None,
    )
    if event is None:
        return 1, "edge_pending"
    return int(event["journal_sequence"]), str(event.get("reason_code", "edge_settled"))


def _terminal_cause(
    terminal_id: str,
    replay: DagReplayState,
    authority_events: list[dict[str, Any]],
) -> tuple[int, str]:
    incoming_edge_ids = {
        edge.edge_id for edge in replay.plan.control_edges if edge.target_id == terminal_id
    }
    event = next(
        (
            item
            for item in reversed(authority_events)
            if (
                item.get("event") == "node_settled" and item.get("node_id") == terminal_id
            )
            or (
                item.get("event") == "edge_settled" and item.get("edge_id") in incoming_edge_ids
            )
        ),
        None,
    )
    if event is None:
        return 1, "terminal_pending"
    return int(event["journal_sequence"]), str(event.get("reason_code", "terminal_settled"))
