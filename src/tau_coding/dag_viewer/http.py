"""Small HTTP response and query helpers for the local DAG viewer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

from tau_coding.dag_viewer.query import (
    MAX_QUERY_CURSOR,
    MAX_QUERY_LIMIT,
    MAX_QUERY_TEXT,
    QUERY_KINDS,
    DagViewQuery,
)


@dataclass(frozen=True, slots=True)
class ViewerHttpResponse:
    status: int
    body: bytes
    content_type: str
    headers: dict[str, str] = field(default_factory=dict)


def json_response(payload: object, *, status: int = 200) -> ViewerHttpResponse:
    return ViewerHttpResponse(
        status=status,
        body=(json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(),
        content_type="application/json; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def html_response(html: str) -> ViewerHttpResponse:
    return ViewerHttpResponse(
        status=200,
        body=html.encode(),
        content_type="text/html; charset=utf-8",
    )


def viewer_error(code: str, message: str, *, status: int = 400) -> ViewerHttpResponse:
    return json_response(
        {
            "schema": "tau.dag_viewer_error.v1",
            "status": "BLOCKED",
            "code": code,
            "message": message,
            "details": {},
        },
        status=status,
    )


def parse_event_query(query: str) -> tuple[int, int | None, int]:
    values = parse_qs(query, keep_blank_values=True, strict_parsing=False)
    unknown = set(values) - {"after_sequence", "before_sequence", "limit"}
    if unknown or any(len(items) != 1 for items in values.values()):
        raise RuntimeError("dag_viewer_event_range_invalid")
    try:
        after = int(values.get("after_sequence", ["0"])[0])
        before_raw = values.get("before_sequence", [None])[0]
        before = int(before_raw) if before_raw is not None else None
        limit = int(values.get("limit", ["200"])[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("dag_viewer_event_range_invalid") from exc
    if after < 0 or before is not None and before < 1 or not 1 <= limit <= 500:
        raise RuntimeError("dag_viewer_event_range_invalid")
    if before is not None and before <= after:
        raise RuntimeError("dag_viewer_event_range_invalid")
    return after, before, limit


def parse_at_sequence(query: str) -> int | None:
    values = parse_qs(query, keep_blank_values=True, strict_parsing=False)
    if set(values) - {"at_sequence"} or any(len(items) != 1 for items in values.values()):
        raise RuntimeError("dag_viewer_sequence_invalid")
    if "at_sequence" not in values:
        return None
    try:
        sequence = int(values["at_sequence"][0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("dag_viewer_sequence_invalid") from exc
    if sequence < 1:
        raise RuntimeError("dag_viewer_sequence_invalid")
    return sequence


def parse_view_query(query: str) -> DagViewQuery:
    values = parse_qs(query, keep_blank_values=True, strict_parsing=False)
    allowed = {
        "at_sequence",
        "entity_kind",
        "entity_id",
        "node_id",
        "attempt",
        "event_type",
        "receipt_schema",
        "state",
        "attention_state",
        "attention_severity",
        "sequence_from",
        "sequence_to",
        "q",
        "limit",
        "cursor",
    }
    if set(values) - allowed or any(len(items) != 1 for items in values.values()):
        raise RuntimeError("dag_viewer_query_invalid")

    def text_value(name: str, *, max_length: int = MAX_QUERY_TEXT) -> str | None:
        value = values.get(name, [None])[0]
        if value is None:
            return None
        if not value or len(value) > max_length or any(ord(char) < 32 for char in value):
            raise RuntimeError("dag_viewer_query_invalid")
        return value

    def int_value(name: str, *, minimum: int = 1) -> int | None:
        raw = values.get(name, [None])[0]
        if raw is None:
            return None
        try:
            parsed = int(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("dag_viewer_query_invalid") from exc
        if parsed < minimum:
            raise RuntimeError("dag_viewer_query_invalid")
        return parsed

    kind = text_value("entity_kind")
    if kind is not None:
        kind = kind.upper()
        if kind not in QUERY_KINDS:
            raise RuntimeError("dag_viewer_query_invalid")
    attention_state = text_value("attention_state")
    if attention_state is not None and attention_state not in {"OPEN", "RESOLVED"}:
        raise RuntimeError("dag_viewer_query_invalid")
    severity = text_value("attention_severity")
    if severity is not None and severity not in {"BLOCKER", "ACTION_REQUIRED", "WARNING"}:
        raise RuntimeError("dag_viewer_query_invalid")
    sequence_from = int_value("sequence_from")
    sequence_to = int_value("sequence_to")
    if sequence_from is not None and sequence_to is not None and sequence_from > sequence_to:
        raise RuntimeError("dag_viewer_query_invalid")
    limit = int_value("limit") or 50
    if limit > MAX_QUERY_LIMIT:
        raise RuntimeError("dag_viewer_query_invalid")
    return DagViewQuery(
        at_sequence=int_value("at_sequence"),
        entity_kind=kind,
        entity_id=text_value("entity_id"),
        node_id=text_value("node_id"),
        attempt=int_value("attempt"),
        event_type=text_value("event_type"),
        receipt_schema=text_value("receipt_schema"),
        state=text_value("state"),
        attention_state=attention_state,
        attention_severity=severity,
        sequence_from=sequence_from,
        sequence_to=sequence_to,
        q=text_value("q"),
        limit=limit,
        cursor=text_value("cursor", max_length=MAX_QUERY_CURSOR),
    )


def parse_compare_query(query: str) -> dict[str, Any]:
    values = parse_qs(query, keep_blank_values=True, strict_parsing=False)
    if any(len(items) != 1 for items in values.values()):
        raise RuntimeError("dag_viewer_comparison_invalid")
    kind = values.get("kind", [None])[0]
    allowed_by_kind = {
        "SEQUENCE_PAIR": {"kind", "at_sequence", "left_sequence", "right_sequence"},
        "ATTEMPT_PAIR": {"kind", "at_sequence", "node_id", "left_attempt", "right_attempt"},
        "CORRECTION_BEFORE_AFTER": {"kind", "at_sequence", "incident_id"},
    }
    if kind not in allowed_by_kind or set(values) != allowed_by_kind[kind]:
        raise RuntimeError("dag_viewer_comparison_invalid")
    result: dict[str, Any] = {"kind": kind}
    for key, items in values.items():
        if key == "kind":
            continue
        value = items[0]
        if not value or len(value) > MAX_QUERY_TEXT:
            raise RuntimeError("dag_viewer_comparison_invalid")
        if key.endswith("sequence") or key.endswith("attempt"):
            try:
                number = int(value)
            except ValueError as exc:
                raise RuntimeError("dag_viewer_comparison_invalid") from exc
            if number < 1:
                raise RuntimeError("dag_viewer_comparison_invalid")
            result[key] = number
        else:
            result[key] = value
    return result


def security_headers(*, html: bool) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if html:
        headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
    return headers


def error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    text = str(exc).split(":", 1)[0]
    return text if text.startswith("dag_") else "dag_viewer_store_invalid"


def public_error_message(code: str) -> str:
    messages: dict[str, str] = {
        "dag_run_store_missing": "The run store does not exist.",
        "dag_viewer_run_id_ambiguous": "The run ID must be selected explicitly.",
        "dag_viewer_receipt_not_found": "The requested receipt is not indexed.",
        "dag_viewer_receipt_hash_mismatch": "The indexed receipt changed after startup.",
        "dag_viewer_receipt_path_escape": "The indexed receipt path left the run directory.",
        "dag_viewer_receipt_symlink_escape": "A receipt symlink left the run directory.",
        "dag_viewer_event_range_invalid": "The requested event range is invalid.",
        "dag_viewer_sequence_invalid": "The requested journal sequence is invalid.",
        "dag_viewer_sequence_not_in_run": "The requested sequence is not part of this run.",
        "dag_viewer_explanation_subject_kind_invalid": (
            "The requested causal subject kind is not supported."
        ),
        "dag_viewer_explanation_not_found": "The requested causal subject is not projected.",
        "dag_viewer_transition_receipt_not_indexed": (
            "A committed transition receipt is not in the run receipt index."
        ),
        "dag_viewer_head_projection_mismatch": (
            "The journal head disagrees with a mutable projection."
        ),
        "dag_viewer_query_invalid": "The bounded viewer query is invalid.",
        "dag_viewer_query_cursor_invalid": "The viewer query cursor is invalid.",
        "dag_viewer_comparison_invalid": "The exactly-two comparison request is invalid.",
        "dag_viewer_comparison_sides_identical": "The comparison sides must differ.",
        "dag_viewer_attempt_comparison_not_found": (
            "The requested same-node attempts were not found."
        ),
        "dag_viewer_correction_comparison_not_found": (
            "The requested correction lineage was not found."
        ),
        "dag_viewer_comparison_cross_run": "Comparison sides must belong to one run.",
        "dag_viewer_comparison_future_sequence": (
            "Comparison sides cannot exceed the authoritative journal prefix."
        ),
        "dag_viewer_comparison_too_large": "The bounded comparison exceeds its size limit.",
        "dag_viewer_receipt_sequence_missing": (
            "A receipt could not be bound to its committed journal sequence."
        ),
    }
    return messages.get(code, "The DAG viewer could not validate the requested run data.")


def with_headers(response: ViewerHttpResponse, extra: dict[str, str]) -> ViewerHttpResponse:
    return ViewerHttpResponse(
        response.status,
        response.body,
        response.content_type,
        {**response.headers, **extra},
    )
