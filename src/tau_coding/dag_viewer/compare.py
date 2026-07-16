"""Exactly-two comparison of authoritative DAG journal projections."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.replay import HistoricalReplayResult
from tau_coding.dag_viewer.projection import build_dag_view_state
from tau_coding.dag_viewer.receipt_index import build_receipt_index
from tau_coding.dag_viewer.redaction import redact_for_viewer

ReplayLoader = Callable[[int], HistoricalReplayResult]
MAX_COMPARISON_CHANGES = 200
MAX_COMPARISON_BYTES = 512 * 1024
MAX_PROJECTION_DEPTH = 8
MAX_PROJECTION_ITEMS = 200
MAX_PROJECTION_STRING = 1024


def compare_sequences(
    *,
    left_sequence: int,
    right_sequence: int,
    at_sequence: int,
    load: ReplayLoader,
    run_dir: Path,
) -> dict[str, Any]:
    if left_sequence == right_sequence:
        raise RuntimeError("dag_viewer_comparison_sides_identical")
    if left_sequence > at_sequence or right_sequence > at_sequence:
        raise RuntimeError("dag_viewer_comparison_future_sequence")
    head_result = load(at_sequence)
    left_result = load(left_sequence)
    right_result = load(right_sequence)
    _same_run(head_result, left_result)
    _same_run(head_result, right_result)
    _same_run(left_result, right_result)
    return _comparison(
        kind="SEQUENCE_PAIR",
        left=_snapshot_side(left_result, run_dir=run_dir),
        right=_snapshot_side(right_result, run_dir=run_dir),
        as_of_sequence=at_sequence,
    )


def compare_attempts(
    *,
    node_id: str,
    left_attempt: int,
    right_attempt: int,
    load: ReplayLoader,
    at_sequence: int,
) -> dict[str, Any]:
    if left_attempt == right_attempt:
        raise RuntimeError("dag_viewer_comparison_sides_identical")
    result = load(at_sequence)
    attempts = {item.attempt: item for item in result.replay.attempts if item.node_id == node_id}
    if left_attempt not in attempts or right_attempt not in attempts:
        raise RuntimeError("dag_viewer_attempt_comparison_not_found")
    left = attempts[left_attempt]
    right = attempts[right_attempt]
    return _comparison(
        kind="ATTEMPT_PAIR",
        left=_attempt_side(result, left),
        right=_attempt_side(result, right),
        as_of_sequence=at_sequence,
    )


def compare_correction(
    *, incident_id: str, load: ReplayLoader, at_sequence: int, run_dir: Path
) -> dict[str, Any]:
    head_result = load(at_sequence)
    correction_events = [
        event
        for event in head_result.events
        if event.get("event_type") == "correction_state_committed"
        and event.get("entity_id") == incident_id
        and isinstance(event.get("payload"), Mapping)
    ]
    requested = next(
        (event for event in correction_events if event["payload"].get("state") == "REQUESTED"),
        None,
    )
    if requested is None or len(correction_events) < 2:
        raise RuntimeError("dag_viewer_correction_comparison_not_found")
    latest = correction_events[-1]
    left_result = load(int(requested["seq"]))
    right_result = load(int(latest["seq"]))
    return _comparison(
        kind="CORRECTION_BEFORE_AFTER",
        left=_correction_side(left_result, incident_id, run_dir=run_dir),
        right=_correction_side(right_result, incident_id, run_dir=run_dir),
        as_of_sequence=at_sequence,
    )


def _snapshot_side(result: HistoricalReplayResult, *, run_dir: Path) -> dict[str, Any]:
    snapshot, _ = build_dag_view_state(
        replay=result.replay,
        recent_events=result.events,
        view_mode=result.view_mode,
        selected_event_created_at=result.selected_event_created_at,
        receipt_index=build_receipt_index(
            run_dir, result.replay.transition_receipts
        ),
    )
    # A comparison never dereferences receipts; use only the stable projected state.
    projection = {
        "run_status": snapshot["run_status"],
        "run_verdict": snapshot["run_verdict"],
        "projection_state": snapshot["projection_state"],
        "nodes": [
            {
                "node_id": node["node_id"],
                "scheduler_state": node["scheduler"]["state"],
                "attempt": node["scheduler"]["attempt"],
                "admission_state": node["admission"]["state"],
                "accepted": node["admission"]["accepted"],
                "runtime_state": node["runtime"]["state"],
            }
            for node in snapshot["nodes"]
        ],
        "edges": snapshot["edges"],
        "terminals": snapshot["terminals"],
        "routes": snapshot["routes"],
        "joins": snapshot["joins"],
        "corrections": [
            {
                "incident_id": item["incident_id"],
                "state": item["state"],
                "journal_sequence": item["journal_sequence"],
            }
            for item in snapshot["corrections"]
        ],
        "attention_items": [
            {
                "attention_id": item["attention_id"],
                "state": item["state"],
                "severity": item["severity"],
                "reason_code": item["reason_code"],
            }
            for item in snapshot["attention_items"]
        ],
        "metrics": _not_recorded_metrics(),
    }
    return _side(
        run_id=result.replay.run_id,
        reference={"kind": "SEQUENCE", "sequence": result.selected_sequence},
        sequence=result.selected_sequence,
        projection=projection,
    )


def _attempt_side(result: HistoricalReplayResult, attempt: Any) -> dict[str, Any]:
    sequence = max(
        (
            int(event["seq"])
            for event in result.events
            if event.get("attempt_id") == attempt.attempt_id
        ),
        default=result.selected_sequence,
    )
    return _side(
        run_id=result.replay.run_id,
        reference={
            "kind": "ATTEMPT",
            "node_id": attempt.node_id,
            "attempt": attempt.attempt,
            "attempt_id": attempt.attempt_id,
        },
        sequence=sequence,
        projection={
            "node_id": attempt.node_id,
            "attempt": attempt.attempt,
            "attempt_id": attempt.attempt_id,
            "state": attempt.state,
            "effect_state": attempt.effect_state,
            "metrics": _not_recorded_metrics(),
        },
    )


def _correction_side(
    result: HistoricalReplayResult, incident_id: str, *, run_dir: Path
) -> dict[str, Any]:
    snapshot, _ = build_dag_view_state(
        replay=result.replay,
        recent_events=result.events,
        view_mode=result.view_mode,
        selected_event_created_at=result.selected_event_created_at,
        receipt_index=build_receipt_index(
            run_dir, result.replay.transition_receipts
        ),
    )
    correction = next(
        (item for item in snapshot["corrections"] if item["incident_id"] == incident_id),
        None,
    )
    if correction is None:
        raise RuntimeError("dag_viewer_correction_comparison_not_found")
    projection = {
        "incident_id": incident_id,
        "state": correction["state"],
        "node_id": correction["incident"].get("node_id"),
        "attempt": correction["incident"].get("attempt"),
        "trigger": correction["incident"].get("trigger"),
        "classification": correction["incident"].get("classification"),
        "action": correction["intent"].get("action") if correction["intent"] else None,
        "verification_verified": (
            correction["verification"].get("verified") if correction["verification"] else None
        ),
        "metrics": _not_recorded_metrics(),
    }
    return _side(
        run_id=result.replay.run_id,
        reference={"kind": "CORRECTION", "incident_id": incident_id, "state": correction["state"]},
        sequence=result.selected_sequence,
        projection=projection,
    )


def _side(
    *, run_id: str, reference: dict[str, Any], sequence: int, projection: dict[str, Any]
) -> dict[str, Any]:
    bounded, truncated = _bounded_value(projection)
    return {
        "run_id": run_id,
        "reference": reference,
        "sequence": sequence,
        "projection": bounded,
        "truncated": truncated,
    }


def _comparison(
    *, kind: str, left: dict[str, Any], right: dict[str, Any], as_of_sequence: int
) -> dict[str, Any]:
    for side in (left, right):
        bounded, side_truncated = _bounded_value(side["projection"])
        side["projection"] = bounded
        side["truncated"] = bool(side.get("truncated") or side_truncated)
    all_changes = _changes(left["projection"], right["projection"])
    changes = all_changes[:MAX_COMPARISON_CHANGES]
    truncated = bool(
        left.get("truncated")
        or right.get("truncated")
        or len(all_changes) > len(changes)
    )
    run_id = _run_id(left, right)
    payload = {
        "schema": "tau.dag_view_comparison.v1",
        "kind": kind,
        "run_id": run_id,
        "as_of_sequence": as_of_sequence,
        "left": left,
        "right": right,
        "changes": changes,
        "truncated": truncated,
        "proof_scope": {
            "proves": [
                "Tau compared exactly two bounded projections derived from one run's journal."
            ],
            "does_not_prove": [
                "Differences establish semantic correctness, causation, or model quality.",
                "Missing timing, token, or cost values were estimated.",
            ],
        },
    }
    while _serialized_bytes(payload) > MAX_COMPARISON_BYTES - 256 and changes:
        changes.pop()
        payload["truncated"] = True
    if _serialized_bytes(payload) > MAX_COMPARISON_BYTES - 256:
        raise RuntimeError("dag_viewer_comparison_too_large")
    redacted = redact_for_viewer(payload)
    if redacted.truncated:
        payload = dict(redacted.value)
        payload["truncated"] = True
    else:
        payload = dict(redacted.value)
    payload["comparison_sha256"] = canonical_sha256(payload)
    if _serialized_bytes(payload) > MAX_COMPARISON_BYTES:
        raise RuntimeError("dag_viewer_comparison_too_large")
    return payload


def _changes(left: Any, right: Any, path: str = "$") -> list[dict[str, Any]]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left:
                changes.append(
                    {
                        "field": child,
                        "change": "ADDED",
                        "right": right[key],
                        "causal_references": [],
                    }
                )
            elif key not in right:
                changes.append(
                    {
                        "field": child,
                        "change": "REMOVED",
                        "left": left[key],
                        "causal_references": [],
                    }
                )
            else:
                changes.extend(_changes(left[key], right[key], child))
        return changes
    if left == right:
        return []
    return [
        {
            "field": path,
            "change": "CHANGED",
            "left": left,
            "right": right,
            "causal_references": [],
        }
    ]


def _bounded_value(value: Any, *, depth: int = 0) -> tuple[Any, bool]:
    if depth > MAX_PROJECTION_DEPTH:
        return "[TRUNCATED:MAX_DEPTH]", True
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        truncated = len(items) > MAX_PROJECTION_ITEMS
        output: dict[str, Any] = {}
        for key, child in items[:MAX_PROJECTION_ITEMS]:
            bounded, child_truncated = _bounded_value(child, depth=depth + 1)
            output[str(key)] = bounded
            truncated = truncated or child_truncated
        return output, truncated
    if isinstance(value, (list, tuple)):
        truncated = len(value) > MAX_PROJECTION_ITEMS
        list_output: list[Any] = []
        for child in value[:MAX_PROJECTION_ITEMS]:
            bounded, child_truncated = _bounded_value(child, depth=depth + 1)
            list_output.append(bounded)
            truncated = truncated or child_truncated
        return list_output, truncated
    if isinstance(value, str) and len(value) > MAX_PROJECTION_STRING:
        return value[:MAX_PROJECTION_STRING] + "[TRUNCATED]", True
    return value, False


def _serialized_bytes(value: Any) -> int:
    return len((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def _not_recorded_metrics() -> dict[str, Any]:
    return {
        name: {"state": "NOT_RECORDED", "value": None, "source_reference": None}
        for name in ("duration_ms", "input_tokens", "output_tokens", "cost")
    }


def _same_run(left: HistoricalReplayResult, right: HistoricalReplayResult) -> None:
    if left.replay.run_id != right.replay.run_id:
        raise RuntimeError("dag_viewer_comparison_cross_run")


def _run_id(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    left_run = str(left.get("run_id", ""))
    right_run = str(right.get("run_id", ""))
    if not left_run or left_run != right_run:
        raise RuntimeError("dag_viewer_comparison_cross_run")
    return left_run
