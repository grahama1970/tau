"""Build browser-neutral manifests and live snapshots from durable replay."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.correction import reduce_correction_projections
from tau_coding.dag_runtime.model import DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.replay import DagReplayState, replay_dag_run
from tau_coding.dag_runtime.run_store import DagJournalEvent, SqliteDagRunReader
from tau_coding.dag_viewer.redaction import redact_for_viewer

PROOF_SCOPE = {
    "proves": [
        "Tau projected verified SQLite journal state in authoritative sequence order.",
        "Scheduler, runtime, and receipt-admission state remain distinct.",
    ],
    "does_not_prove": [
        "Runtime text proves node completion.",
        "Agent or reviewer claims are semantically correct.",
        "The source DAG may be edited from the viewer.",
    ],
}


def load_dag_replay(
    *, run_dir: Path, run_id: str | None = None
) -> tuple[DagReplayState, tuple[dict[str, Any], ...]]:
    database = run_dir.expanduser().resolve() / "dag-run.sqlite3"
    with SqliteDagRunReader(database) as reader, reader.snapshot():
        run_ids = reader.run_ids()
        if run_id is None:
            if len(run_ids) != 1:
                raise RuntimeError("dag_viewer_run_id_ambiguous")
            run_id = run_ids[0]
        plan = reader.load_plan(run_id)
        event_pages: list[DagJournalEvent] = []
        cursor = 0
        latest_sequence = reader.latest_sequence(run_id)
        while cursor < latest_sequence:
            page = reader.load_events(run_id, after_sequence=cursor, limit=5000)
            if not page:
                raise RuntimeError("dag_viewer_journal_sequence_gap")
            event_pages.extend(page)
            cursor = page[-1].sequence
        events = tuple(event_pages)
        mappings = tuple(event.to_mapping() for event in events)
        replay = replay_dag_run(
            plan=plan,
            run_record=reader.load_run_record(run_id),
            events=mappings,
            attempts=reader.load_attempts(run_id),
            runtime_projections=reader.runtime_projections(run_id),
        )
    return replay, mappings


def build_dag_view_manifest(*, replay: DagReplayState, run_dir: Path) -> dict[str, Any]:
    source_path = run_dir / "source-dag.json"
    source_available = source_path.is_file()
    source: dict[str, Any] | None = None
    if source_available:
        try:
            loaded_source = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("dag_source_artifact_invalid") from exc
        if not isinstance(loaded_source, dict):
            raise RuntimeError("dag_source_artifact_invalid")
        if canonical_sha256(loaded_source) != replay.plan.source_payload_sha256:
            raise RuntimeError("dag_source_artifact_hash_mismatch")
        source = loaded_source
    graph = {
        "nodes": [node.to_payload() for node in replay.plan.nodes],
        "edges": [edge.to_payload() for edge in replay.plan.control_edges],
        "terminals": [item.to_payload() for item in replay.plan.terminal_endpoints],
        "routes": [item.to_value() for item in replay.plan.route_contracts],
        "joins": [item.to_value() for item in replay.plan.join_contracts],
    }
    payload: dict[str, Any] = {
        "schema": "tau.dag_view_manifest.v1",
        "run_id": replay.run_id,
        "plan_id": replay.plan.plan_id,
        "plan_sha256": replay.plan.plan_sha256,
        "source_schema": replay.plan.source_schema,
        "source_sha256": replay.plan.source_payload_sha256,
        "source_available": source_available,
        "source_redacted": False,
        "source_dag": source,
        "source_status": "AVAILABLE" if source_available else "SOURCE_DAG_NOT_RETAINED",
        "dag_plan": replay.plan.to_payload(),
        "graph": graph,
        "receipt_index": [],
        "proof_scope": PROOF_SCOPE,
    }
    redacted = redact_for_viewer(payload)
    result = dict(redacted.value)
    result["source_redacted"] = any(
        path.startswith("$.source_dag") for path in redacted.redacted_paths
    )
    result["redaction"] = {
        "redacted": redacted.redacted,
        "redacted_paths": list(redacted.redacted_paths),
        "truncated": redacted.truncated,
    }
    return result


def build_dag_live_snapshot(
    *, replay: DagReplayState, recent_events: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    corrections = reduce_correction_projections(recent_events)
    correction_by_node = {
        str(correction.incident.get("node_id")): correction for correction in corrections
    }
    latest_attempt = {
        node_id: max(
            (item for item in replay.attempts if item.node_id == node_id),
            key=lambda item: item.attempt,
            default=None,
        )
        for node_id, _ in replay.node_states
    }
    nodes: list[dict[str, Any]] = []
    runtime_by_endpoint = {item.endpoint_lease_sha256: item for item in replay.runtime_projections}
    for node_id, state in replay.node_states:
        accepted = state == "success"
        attempt = latest_attempt[node_id]
        scheduler_state, admission_state = _project_node_state(
            committed_state=state,
            attempt_state=attempt.state if attempt is not None else None,
        )
        plan_node = next(node for node in replay.plan.nodes if node.node_id == node_id)
        replay_result = next(
            (item for item in reversed(replay.results) if item.node_id == node_id), None
        )
        endpoint_hash = _find_endpoint_lease_sha256(replay_result.payload if replay_result else {})
        runtime = runtime_by_endpoint.get(endpoint_hash or "")
        nodes.append(
            {
                "node_id": node_id,
                "node_kind": plan_node.adapter_kind,
                "scheduler": {
                    "state": scheduler_state,
                    "attempt": attempt.attempt if attempt is not None else 0,
                    "max_attempts": plan_node.max_attempts,
                },
                "runtime": {
                    "state": runtime.state if runtime else "UNKNOWN",
                    "liveness": runtime.liveness if runtime else "UNKNOWN",
                    "confidence": runtime.confidence if runtime else "UNKNOWN",
                    "last_event_id": runtime.last_event_id if runtime else None,
                },
                "admission": {
                    "state": admission_state,
                    "accepted": accepted,
                    "receipt_refs": [],
                },
                "transaction": _transaction_projection(
                    plan_node=plan_node,
                    replay_result=replay_result.payload if replay_result else None,
                    recent_events=recent_events,
                    scheduler_attempt=attempt.attempt if attempt is not None else None,
                    accepted=accepted,
                    committed_state=state,
                ),
                "correction": _correction_payload(correction_by_node.get(node_id)),
                "updated_sequence": replay.journal_sequence,
            }
        )
    lease_stale = (
        replay.run_status == "RUNNING"
        and replay.lease_expires_at_ms is not None
        and replay.lease_expires_at_ms < time.time_ns() // 1_000_000
    )
    projection_state = (
        "RECONCILIATION_REQUIRED"
        if replay.run_status == "RECONCILIATION_REQUIRED"
        else "STALE"
        if lease_stale
        else "COMPLETE"
        if replay.run_status in {"PASS", "BLOCKED"}
        else "LIVE"
    )
    payload = {
        "schema": "tau.dag_live_snapshot.v1",
        "run_id": replay.run_id,
        "journal_sequence": replay.journal_sequence,
        "run_status": replay.run_status,
        "run_verdict": replay.run_verdict,
        "projection_state": projection_state,
        "nodes": nodes,
        "edges": [{"edge_id": key, "state": value} for key, value in replay.edge_states],
        "terminals": [
            {"terminal_id": key, "state": value} for key, value in replay.terminal_states
        ],
        "routes": [],
        "joins": [],
        "corrections": [_correction_payload(item) for item in corrections],
        "attention_items": [
            {
                "kind": "correction",
                "incident_id": item.incident_id,
                "node_id": item.incident.get("node_id"),
                "state": item.state,
                "required_action": "human_review",
            }
            for item in corrections
            if item.state in {"UNCERTAIN", "REJECTED", "HUMAN_ROUTED"}
        ],
        "recent_events": list(recent_events[-200:]),
        "proof_scope": PROOF_SCOPE,
    }
    redacted = redact_for_viewer(payload)
    result = dict(redacted.value)
    result["snapshot_sha256"] = canonical_sha256(result)
    result["redaction"] = {
        "redacted": redacted.redacted,
        "redacted_paths": list(redacted.redacted_paths),
        "truncated": redacted.truncated,
    }
    return result


def _correction_payload(correction: Any) -> dict[str, Any] | None:
    if correction is None:
        return None
    return {
        "incident_id": correction.incident_id,
        "state": correction.state,
        "journal_sequence": correction.journal_sequence,
        "incident": correction.incident,
        "intent": correction.intent,
        "action_receipt": correction.action_receipt,
        "verification": correction.verification,
    }


def build_dag_live_events(
    *,
    replay: DagReplayState,
    events: tuple[dict[str, Any], ...],
    after_sequence: int,
    limit: int,
) -> dict[str, Any]:
    payload = {
        "schema": "tau.dag_live_event.v1",
        "run_id": replay.run_id,
        "after_sequence": after_sequence,
        "events": [event for event in events if int(event["seq"]) > after_sequence][:limit],
    }
    redacted = redact_for_viewer(payload)
    result = dict(redacted.value)
    result["redaction"] = {
        "redacted": redacted.redacted,
        "redacted_paths": list(redacted.redacted_paths),
        "truncated": redacted.truncated,
    }
    return result


def _project_node_state(*, committed_state: str, attempt_state: str | None) -> tuple[str, str]:
    if committed_state == "success":
        return "settled", "accepted"
    if committed_state in {"skipped", "cancelled"}:
        return committed_state, "not_applicable"
    if committed_state in {"blocked", "failed", "timed_out"}:
        return committed_state, "rejected"
    if committed_state != "pending":
        return committed_state, "awaiting_receipt"
    attempt_projection = {
        "RESERVED": ("ready", "not_started"),
        "DISPATCHED": ("running", "awaiting_receipt"),
        "STAGED": ("validating", "validating"),
        "VALIDATED": ("committing", "validating"),
        "OUTPUT_COMMITTED": ("committing", "validating"),
        "RETRY_SCHEDULED": ("retry_pending", "rejected"),
        "UNCERTAIN": ("reconciliation_required", "rejected"),
    }
    if attempt_state is None:
        return "pending", "not_started"
    return attempt_projection.get(attempt_state, ("pending", "not_started"))


def _find_endpoint_lease_sha256(value: Any) -> str | None:
    if isinstance(value, dict):
        candidate = value.get("endpoint_lease_sha256")
        if isinstance(candidate, str) and candidate.startswith("sha256:"):
            return candidate
        for item in value.values():
            found = _find_endpoint_lease_sha256(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_endpoint_lease_sha256(item)
            if found:
                return found
    return None


def _transaction_projection(
    *,
    plan_node: DagPlanNode,
    replay_result: dict[str, Any] | None,
    recent_events: tuple[dict[str, Any], ...],
    scheduler_attempt: int | None,
    accepted: bool,
    committed_state: str,
) -> dict[str, Any] | None:
    if plan_node.adapter_kind != "generic_artifact_transaction":
        return None
    config = plan_node.adapter_config.to_value()
    transaction_config = config.get("transaction")
    transaction_id = (
        transaction_config.get("transaction_id")
        if isinstance(transaction_config, dict)
        else None
    )
    attempts: dict[int, dict[str, Any]] = {}
    accepted_manifest_sha256: str | None = None
    for event in recent_events:
        if (
            event.get("event_type") != "dag_diagnostic_event_appended"
            or event.get("entity_id") != plan_node.node_id
        ):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("authority") != "diagnostic_only":
            continue
        if payload.get("scheduler_attempt") != scheduler_attempt:
            continue
        attempt_number = payload.get("attempt")
        phase = payload.get("phase")
        evidence = payload.get("evidence")
        if not isinstance(attempt_number, int) or attempt_number < 1 or not isinstance(phase, str):
            continue
        evidence = evidence if isinstance(evidence, dict) else {}
        attempt = attempts.setdefault(attempt_number, {"attempt": attempt_number})
        if phase == "producer_started":
            attempt["producer_state"] = "RUNNING"
        elif phase == "producer_completed":
            attempt["producer_state"] = "PASS"
            attempt["candidate_manifest_sha256"] = evidence.get("candidate_manifest_sha256")
        elif phase == "validator_completed":
            attempt["validator_status"] = evidence.get("status")
        elif phase == "reviewer_started":
            attempt["reviewer_verdict"] = "RUNNING"
        elif phase == "reviewer_completed":
            attempt["reviewer_verdict"] = evidence.get("verdict")
            attempt["review_feedback_sha256"] = evidence.get("review_feedback_sha256")
        elif phase == "revision_committed":
            attempt["revision_instruction"] = evidence.get("instruction")
        elif phase == "accepted_manifest_written":
            candidate = evidence.get("accepted_manifest_sha256")
            if isinstance(candidate, str):
                accepted_manifest_sha256 = candidate

    if replay_result is not None:
        result_attempts = replay_result.get("attempts")
        if isinstance(result_attempts, list):
            for item in result_attempts:
                if not isinstance(item, dict) or not isinstance(item.get("attempt"), int):
                    continue
                attempt_number = int(item["attempt"])
                projected = attempts.setdefault(attempt_number, {"attempt": attempt_number})
                for source_key, target_key in (
                    ("candidate_manifest_sha256", "candidate_manifest_sha256"),
                    ("review_verdict", "reviewer_verdict"),
                    ("review_feedback_sha256", "review_feedback_sha256"),
                ):
                    if item.get(source_key) is not None:
                        projected[target_key] = item[source_key]
        result_sha256 = replay_result.get("accepted_manifest_sha256")
        if isinstance(result_sha256, str):
            accepted_manifest_sha256 = result_sha256
    ordered_attempts = [attempts[key] for key in sorted(attempts)]
    if accepted:
        transaction_state = "ACCEPTED"
    elif committed_state == "blocked":
        transaction_state = "BLOCKED"
    elif committed_state in {"failed", "timed_out"}:
        transaction_state = "REJECTED"
    else:
        transaction_state = "AWAITING_RECEIPT"
    return {
        "transaction_id": transaction_id,
        "current_attempt": max(attempts, default=0),
        "max_attempts": int(config.get("transaction_max_attempts", plan_node.max_attempts)),
        "state": transaction_state,
        "accepted_manifest_sha256": accepted_manifest_sha256,
        "attempts": ordered_attempts,
    }
