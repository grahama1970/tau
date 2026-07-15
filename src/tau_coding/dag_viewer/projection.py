"""Build browser-neutral manifests and live snapshots from durable replay."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
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
    with SqliteDagRunReader(database) as reader:
        run_ids = reader.run_ids()
        if run_id is None:
            if len(run_ids) != 1:
                raise RuntimeError("dag_viewer_run_id_ambiguous")
            run_id = run_ids[0]
        plan = reader.load_plan(run_id)
        event_pages: list[DagJournalEvent] = []
        cursor = 0
        while cursor < reader.latest_sequence(run_id):
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
                "transaction": None,
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
        "attention_items": [],
        "recent_events": list(recent_events),
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
