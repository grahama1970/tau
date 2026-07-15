"""Small HTTP response and query helpers for the local DAG viewer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs


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
    }
    return messages.get(code, "The DAG viewer could not validate the requested run data.")


def with_headers(response: ViewerHttpResponse, extra: dict[str, str]) -> ViewerHttpResponse:
    return ViewerHttpResponse(
        response.status,
        response.body,
        response.content_type,
        {**response.headers, **extra},
    )
