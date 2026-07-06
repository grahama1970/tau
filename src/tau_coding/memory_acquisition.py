"""Graph Memory acquisition receipts for Tau DAG dispatch inputs."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

MEMORY_INTENT_ACQUISITION_RECEIPT_SCHEMA = "tau.memory_intent_acquisition_receipt.v1"
EVIDENCE_CASE_ACQUISITION_RECEIPT_SCHEMA = "tau.evidence_case_acquisition_receipt.v1"
DEFAULT_MEMORY_URL = "http://127.0.0.1:8601"


def write_memory_intent_acquisition_receipt(
    *,
    query: str,
    receipt_path: Path,
    memory_url: str | None = None,
    scope: str = "tau",
    app: str = "tau",
    fast: bool = True,
    goal_hash: str | None = None,
    target: dict[str, Any] | None = None,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Call Graph Memory /intent and bind request/response artifacts to a receipt."""

    if not query.strip():
        raise RuntimeError("--query must be non-empty")
    resolved_receipt = receipt_path.expanduser().resolve()
    response_path = resolved_receipt.with_name(f"{resolved_receipt.stem}-response.json")
    request_payload: dict[str, Any] = {
        "q": query,
        "scope": scope,
        "app": app,
        "fast": fast,
    }
    if goal_hash:
        request_payload["goal_hash"] = goal_hash
    if target:
        request_payload["target"] = target
    base_url = _memory_url(memory_url)
    response_payload, call = _post_json(
        memory_url=base_url,
        path="/intent",
        payload=request_payload,
        timeout_seconds=timeout_seconds,
    )
    alerts = _intent_alerts(response_payload, call)
    receipt = _receipt(
        schema=MEMORY_INTENT_ACQUISITION_RECEIPT_SCHEMA,
        receipt_path=resolved_receipt,
        memory_url=base_url,
        endpoint="/intent",
        request_payload=request_payload,
        response_payload=response_payload,
        response_path=response_path,
        call=call,
        alerts=alerts,
        goal_hash=goal_hash,
        target=target,
        proves=[
            "Tau called Graph Memory /intent and captured the observable response.",
            "Tau hashed the Memory request and response artifacts.",
            "Tau checked that the response is an observable memory intent artifact.",
        ],
        does_not_prove=[
            "Memory fact truth.",
            "Dispatch should proceed without the memory/evidence gate.",
            "Provider/model semantic quality.",
            "The evidence case exists.",
        ],
    )
    _write_json(response_path, response_payload)
    _write_json(resolved_receipt, receipt)
    return receipt


def write_evidence_case_acquisition_receipt(
    *,
    intent_path: Path,
    receipt_path: Path,
    memory_url: str | None = None,
    question: str | None = None,
    scope: str = "tau",
    app: str = "tau",
    goal_hash: str | None = None,
    target: dict[str, Any] | None = None,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Call Graph Memory /create-evidence-case for a previously captured intent."""

    resolved_intent = intent_path.expanduser().resolve()
    intent_payload = _read_json_object(resolved_intent, label="memory intent")
    resolved_receipt = receipt_path.expanduser().resolve()
    response_path = resolved_receipt.with_name(f"{resolved_receipt.stem}-response.json")
    request_payload: dict[str, Any] = {
        "intent": intent_payload,
        "scope": scope,
        "app": app,
    }
    if question:
        request_payload["question"] = question
    if goal_hash:
        request_payload["goal_hash"] = goal_hash
    if target:
        request_payload["target"] = target
    base_url = _memory_url(memory_url)
    response_payload, call = _post_json(
        memory_url=base_url,
        path="/create-evidence-case",
        payload=request_payload,
        timeout_seconds=timeout_seconds,
    )
    alerts = _evidence_case_alerts(response_payload, call)
    receipt = _receipt(
        schema=EVIDENCE_CASE_ACQUISITION_RECEIPT_SCHEMA,
        receipt_path=resolved_receipt,
        memory_url=base_url,
        endpoint="/create-evidence-case",
        request_payload=request_payload,
        response_payload=response_payload,
        response_path=response_path,
        call=call,
        alerts=alerts,
        goal_hash=goal_hash,
        target=target,
        extra={
            "intent_path": str(resolved_intent),
            "intent_sha256": f"sha256:{_sha256(resolved_intent)}",
        },
        proves=[
            "Tau called Graph Memory /create-evidence-case and captured the response.",
            "Tau hashed the source intent, request, and response artifacts.",
            "Tau checked that the response is an observable evidence-case artifact.",
        ],
        does_not_prove=[
            "Evidence-case semantic completeness.",
            "Memory fact truth.",
            "Dispatch should proceed without the memory/evidence gate.",
            "Provider/model semantic quality.",
        ],
    )
    _write_json(response_path, response_payload)
    _write_json(resolved_receipt, receipt)
    return receipt


def _receipt(
    *,
    schema: str,
    receipt_path: Path,
    memory_url: str,
    endpoint: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    response_path: Path,
    call: dict[str, Any],
    alerts: list[dict[str, Any]],
    goal_hash: str | None,
    target: dict[str, Any] | None,
    proves: list[str],
    does_not_prove: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = "PASS" if not alerts else "BLOCKED"
    request_sha256 = _payload_sha256(request_payload)
    response_sha256 = _payload_sha256(response_payload)
    receipt: dict[str, Any] = {
        "schema": schema,
        "ok": status == "PASS",
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "memory_url": memory_url,
        "endpoint": endpoint,
        "goal_hash": goal_hash,
        "target": target,
        "receipt_path": str(receipt_path),
        "request_sha256": f"sha256:{request_sha256}",
        "response_path": str(response_path),
        "response_sha256": f"sha256:{response_sha256}",
        "response_schema": response_payload.get("schema"),
        "call": call,
        "alert_codes": [alert["code"] for alert in alerts],
        "alerts": alerts,
        "proof_scope": {
            "proves": proves,
            "does_not_prove": does_not_prove,
        },
        "timestamp": _utc_stamp(),
    }
    if extra:
        receipt.update(extra)
    return receipt


def _post_json(
    *,
    memory_url: str,
    path: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = datetime.now(UTC)
    try:
        with httpx.Client(
            base_url=memory_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0)),
        ) as client:
            response = client.post(path, json=payload)
    except httpx.HTTPError as exc:
        return {}, _call(path=path, ok=False, started=started, error=str(exc))
    call = _call(path=path, ok=response.status_code < 400, started=started)
    call["status_code"] = response.status_code
    try:
        body = response.json()
    except json.JSONDecodeError:
        call["ok"] = False
        call["error"] = "response was not JSON"
        return {"raw": response.text}, call
    if not isinstance(body, dict):
        call["ok"] = False
        call["error"] = "response JSON was not an object"
        return {"value": body}, call
    return body, call


def _call(
    *,
    path: str,
    ok: bool,
    started: datetime,
    error: str | None = None,
) -> dict[str, Any]:
    duration_seconds = (datetime.now(UTC) - started).total_seconds()
    call: dict[str, Any] = {
        "ok": ok,
        "path": path,
        "duration_seconds": duration_seconds,
    }
    if error:
        call["error"] = error
    return call


def _intent_alerts(payload: dict[str, Any], call: dict[str, Any]) -> list[dict[str, Any]]:
    alerts = _call_alerts(call)
    schema = payload.get("schema")
    if not alerts and (not isinstance(schema, str) or not schema.startswith("memory.")):
        alerts.append(
            _alert(
                "invalid_memory_intent_schema",
                "Memory /intent response must include a memory.* schema.",
                {"observed_schema": schema},
            )
        )
    if not alerts and payload.get("memory_first") is not True:
        alerts.append(
            _alert(
                "memory_first_required",
                "Memory /intent response must set memory_first true.",
            )
        )
    return alerts


def _evidence_case_alerts(payload: dict[str, Any], call: dict[str, Any]) -> list[dict[str, Any]]:
    alerts = _call_alerts(call)
    schema = payload.get("schema")
    if not alerts and (not isinstance(schema, str) or "evidence" not in schema):
        alerts.append(
            _alert(
                "invalid_evidence_case_schema",
                "Memory /create-evidence-case response must include an evidence-case schema.",
                {"observed_schema": schema},
            )
        )
    return alerts


def _call_alerts(call: dict[str, Any]) -> list[dict[str, Any]]:
    if call.get("ok") is True:
        return []
    code = "memory_http_error"
    if call.get("error") == "response was not JSON":
        code = "memory_non_json_response"
    elif call.get("error") == "response JSON was not an object":
        code = "memory_non_object_response"
    return [
        _alert(
            code,
            "Graph Memory acquisition call failed.",
            {"call": call},
        )
    ]


def _alert(code: str, message: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    alert: dict[str, Any] = {"severity": "BLOCK", "code": code, "message": message}
    if evidence:
        alert["evidence"] = evidence
    return alert


def _memory_url(memory_url: str | None) -> str:
    value = memory_url or os.environ.get("MEMORY_DAEMON_URL") or DEFAULT_MEMORY_URL
    return value.rstrip("/")


def _payload_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
