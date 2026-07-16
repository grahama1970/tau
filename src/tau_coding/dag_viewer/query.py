"""Bounded query over browser-safe DAG viewer projections."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_viewer.receipt_index import ReceiptIndex

QUERY_KINDS = frozenset(
    {"NODE", "EDGE", "TERMINAL", "ROUTE", "JOIN", "CORRECTION", "ATTENTION", "EVENT", "RECEIPT"}
)
MAX_QUERY_TEXT = 200
MAX_QUERY_CURSOR = 2048
MAX_QUERY_LIMIT = 100


@dataclass(frozen=True, slots=True)
class DagViewQuery:
    at_sequence: int | None = None
    entity_kind: str | None = None
    entity_id: str | None = None
    node_id: str | None = None
    attempt: int | None = None
    event_type: str | None = None
    receipt_schema: str | None = None
    state: str | None = None
    attention_state: str | None = None
    attention_severity: str | None = None
    sequence_from: int | None = None
    sequence_to: int | None = None
    q: str | None = None
    limit: int = 50
    cursor: str | None = None

    def normalized(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "at_sequence": self.at_sequence,
                "entity_kind": self.entity_kind,
                "entity_id": self.entity_id,
                "node_id": self.node_id,
                "attempt": self.attempt,
                "event_type": self.event_type,
                "receipt_schema": self.receipt_schema,
                "state": self.state,
                "attention_state": self.attention_state,
                "attention_severity": self.attention_severity,
                "sequence_from": self.sequence_from,
                "sequence_to": self.sequence_to,
                "q": self.q,
                "limit": self.limit,
            }.items()
            if value is not None
        }


def query_dag_view(
    *,
    run_id: str,
    view_sequence: int,
    snapshot: Mapping[str, Any],
    events: tuple[dict[str, Any], ...],
    receipts: ReceiptIndex,
    query: DagViewQuery,
    cursor_key: bytes,
) -> dict[str, Any]:
    """Filter a closed projection set without scanning raw evidence fields."""

    items = _query_items(snapshot=snapshot, events=events, receipts=receipts)
    filtered = [item for item in items if _matches(item, query)]
    filtered.sort(key=_sort_key)
    start = 0
    if query.cursor:
        start = _decode_cursor(
            query.cursor,
            run_id=run_id,
            view_sequence=view_sequence,
            query=query,
            cursor_key=cursor_key,
        )
        if start > len(filtered):
            raise RuntimeError("dag_viewer_query_cursor_invalid")
    selected = filtered[start : start + query.limit]
    next_cursor = None
    if start + len(selected) < len(filtered) and selected:
        next_cursor = _encode_cursor(
            run_id=run_id,
            view_sequence=view_sequence,
            query=query,
            next_index=start + len(selected),
            cursor_key=cursor_key,
        )
    return {
        "schema": "tau.dag_view_query_result.v1",
        "run_id": run_id,
        "as_of_sequence": view_sequence,
        "query": query.normalized(),
        "items": selected,
        "next_cursor": next_cursor,
        "result_count": len(selected),
        "total_match_count": len(filtered),
        "proof_scope": {
            "proves": [
                "Tau filtered a closed set of redacted viewer projections at one journal sequence."
            ],
            "does_not_prove": [
                "The query searched raw prompts, terminal output, secrets, or unindexed artifacts.",
                "A matching item is semantically correct or admissible evidence.",
            ],
        },
    }


def _query_items(
    *,
    snapshot: Mapping[str, Any],
    events: tuple[dict[str, Any], ...],
    receipts: ReceiptIndex,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for node in snapshot.get("nodes", []):
        state = str(node["scheduler"]["state"])
        items.append(
            _item(
                "NODE",
                str(node["node_id"]),
                int(node.get("updated_sequence", snapshot["journal_sequence"])),
                state,
                node_id=str(node["node_id"]),
                attempt=int(node["scheduler"]["attempt"]),
                codes=[state, str(node["admission"]["state"]), str(node["runtime"]["state"])],
            )
        )
    for key, kind in (("edges", "EDGE"), ("terminals", "TERMINAL")):
        id_key = "edge_id" if kind == "EDGE" else "terminal_id"
        for value in snapshot.get(key, []):
            items.append(
                _item(
                    kind,
                    str(value[id_key]),
                    int(value.get("last_change_sequence", snapshot["journal_sequence"])),
                    str(value["state"]),
                    codes=[str(value.get("reason_code", ""))],
                )
            )
    for key, kind, id_key in (
        ("routes", "ROUTE", "route_id"),
        ("joins", "JOIN", "join_node_id"),
        ("corrections", "CORRECTION", "incident_id"),
        ("attention_items", "ATTENTION", "attention_id"),
    ):
        for value in snapshot.get(key, []):
            sequence = int(
                value.get("decision_sequence")
                or value.get("journal_sequence")
                or value.get("opened_sequence")
                or snapshot["journal_sequence"]
            )
            node_id = None
            if kind == "ROUTE":
                node_id = str(value.get("source_node_id", "")) or None
            elif kind == "JOIN":
                node_id = str(value.get("join_node_id", "")) or None
            elif kind == "CORRECTION":
                incident = value.get("incident")
                if isinstance(incident, Mapping):
                    node_id = str(incident.get("node_id", "")) or None
            elif kind == "ATTENTION":
                subject = value.get("subject")
                if isinstance(subject, Mapping) and subject.get("kind") == "NODE":
                    node_id = str(subject.get("id", "")) or None
            items.append(
                _item(
                    kind,
                    str(value[id_key]),
                    sequence,
                    str(value["state"]),
                    node_id=node_id,
                    attempt=_optional_int(value.get("attempt")),
                    attention_state=str(value["state"]) if kind == "ATTENTION" else None,
                    attention_severity=(
                        str(value["severity"]) if kind == "ATTENTION" else None
                    ),
                    codes=[
                        str(value.get("reason_code", "")),
                        str(value.get("required_action_code", "")),
                        str(value.get("decision", "")),
                    ],
                )
            )
    for event in events:
        items.append(
            _item(
                "EVENT",
                f"event:{event['seq']}",
                int(event["seq"]),
                str(event.get("event_type", "")),
                node_id=(
                    str(event["entity_id"])
                    if event.get("entity_type") == "node"
                    else None
                ),
                attempt=_attempt_from_event(event),
                event_type=str(event.get("event_type", "")),
                codes=[str(event.get("entity_type", "")), str(event.get("entity_id", ""))],
            )
        )
    receipt_sequences = _receipt_sequences(events=events, receipts=receipts)
    for receipt in receipts.public_entries():
        receipt_id = str(receipt["receipt_id"])
        if receipt_id not in receipt_sequences:
            raise RuntimeError("dag_viewer_receipt_sequence_missing")
        items.append(
            _item(
                "RECEIPT",
                receipt_id,
                receipt_sequences[receipt_id],
                "AVAILABLE",
                receipt_schema=str(receipt["schema"]),
                codes=[str(receipt["schema"])],
            )
        )
    return items


def _item(
    kind: str,
    entity_id: str,
    sequence: int,
    state: str,
    *,
    node_id: str | None = None,
    attempt: int | None = None,
    event_type: str | None = None,
    receipt_schema: str | None = None,
    attention_state: str | None = None,
    attention_severity: str | None = None,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    compact = [kind, entity_id, state, *(code for code in codes or [] if code)]
    return {
        "entity_kind": kind,
        "entity_id": entity_id,
        "node_id": node_id,
        "attempt": attempt,
        "event_type": event_type,
        "receipt_schema": receipt_schema,
        "state": state,
        "attention_state": attention_state,
        "attention_severity": attention_severity,
        "sequence": sequence,
        "preview": " · ".join(compact)[:512],
    }


def _matches(item: Mapping[str, Any], query: DagViewQuery) -> bool:
    equals = {
        "entity_kind": query.entity_kind,
        "entity_id": query.entity_id,
        "node_id": query.node_id,
        "attempt": query.attempt,
        "event_type": query.event_type,
        "receipt_schema": query.receipt_schema,
        "state": query.state,
        "attention_state": query.attention_state,
        "attention_severity": query.attention_severity,
    }
    if any(expected is not None and item.get(key) != expected for key, expected in equals.items()):
        return False
    sequence = int(item["sequence"])
    if query.sequence_from is not None and sequence < query.sequence_from:
        return False
    if query.sequence_to is not None and sequence > query.sequence_to:
        return False
    return not (
        query.q and query.q.casefold() not in str(item["preview"]).casefold()
    )


def _sort_key(item: Mapping[str, Any]) -> tuple[int, str, str]:
    return (-int(item["sequence"]), str(item["entity_kind"]), str(item["entity_id"]))


def _cursor_basis(*, run_id: str, view_sequence: int, query: DagViewQuery) -> str:
    return canonical_sha256(
        {"run_id": run_id, "view_sequence": view_sequence, "query": query.normalized()}
    )


def _encode_cursor(
    *,
    run_id: str,
    view_sequence: int,
    query: DagViewQuery,
    next_index: int,
    cursor_key: bytes,
) -> str:
    payload = {
        "basis": _cursor_basis(
            run_id=run_id, view_sequence=view_sequence, query=query
        ),
        "next_index": next_index,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(
        hmac.new(cursor_key, encoded.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{encoded}.{signature}"


def _decode_cursor(
    cursor: str,
    *,
    run_id: str,
    view_sequence: int,
    query: DagViewQuery,
    cursor_key: bytes,
) -> int:
    try:
        encoded, signature = cursor.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(cursor_key, encoded.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        next_index = payload["next_index"]
        if payload["basis"] != _cursor_basis(
            run_id=run_id, view_sequence=view_sequence, query=query
        ) or not isinstance(next_index, int) or next_index < 0:
            raise ValueError
        return next_index
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("dag_viewer_query_cursor_invalid") from exc


def _receipt_sequences(
    *, events: tuple[dict[str, Any], ...], receipts: ReceiptIndex
) -> dict[str, int]:
    by_identity = {
        (str(entry.path.resolve()), entry.sha256): entry.receipt_id
        for entry in receipts.entries
    }
    sequences: dict[str, int] = {}
    for event in events:
        payload = event.get("payload")
        transition = payload.get("transition") if isinstance(payload, Mapping) else None
        refs = transition.get("receipt_refs") if isinstance(transition, Mapping) else None
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            identity = (str(ref.get("path", "")), str(ref.get("file_sha256", "")))
            receipt_id = by_identity.get(identity)
            if receipt_id is not None:
                sequences.setdefault(receipt_id, int(event["seq"]))
    return sequences


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _attempt_from_event(event: Mapping[str, Any]) -> int | None:
    payload = event.get("payload")
    if isinstance(payload, Mapping) and isinstance(payload.get("attempt"), int):
        return int(payload["attempt"])
    return None
