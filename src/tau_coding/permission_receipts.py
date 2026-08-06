"""Durable permission request and reply receipts for Tau operator gates."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tau_coding.approval_gate import ALLOWED_ACTIONS

PERMISSION_REQUEST_RECEIPT_SCHEMA = "tau.permission_request_receipt.v1"
PERMISSION_REPLY_RECEIPT_SCHEMA = "tau.permission_reply_receipt.v1"
PERMISSION_GATE_RECEIPT_SCHEMA = "tau.permission_gate_receipt.v1"
ALLOWED_PERMISSION_REPLIES = ("once", "always", "reject")
PERMISSION_USE_PREFIX = "permission-use-"


def write_permission_request_receipt(
    *,
    action: str,
    resources: list[str],
    source_node: str,
    run_dir: Path,
    output: Path | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    mode: str | None = None,
    proposed_save_rule: str | None = None,
    denied: bool = False,
    reason: str | None = None,
    goal_hash: str | None = None,
    active_goal: str | None = None,
    requested_scope: str | None = None,
    nonce: str | None = None,
    expires_at: str | None = None,
    turn_id: str | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """Record a pending or denied permission request without executing a mutation."""

    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    normalized_resources = [item.strip() for item in resources if item.strip()]
    errors = _permission_request_errors(
        action=action,
        resources=normalized_resources,
        source_node=source_node,
        request_id=request_id,
        proposed_save_rule=proposed_save_rule,
        nonce=nonce,
        expires_at=expires_at,
    )
    resolved_request_id = request_id or _default_request_id(
        action=action,
        resources=normalized_resources,
        source_node=source_node,
        session_id=session_id,
    )
    resolved_nonce = nonce or f"nonce-{uuid4().hex}"
    status = "BLOCKED" if denied or errors else "PENDING"
    ok = not denied and not errors
    output_path = (
        output.expanduser().resolve()
        if output is not None
        else resolved_run_dir / f"permission-request-{resolved_request_id}.json"
    )
    receipt = {
        "schema": PERMISSION_REQUEST_RECEIPT_SCHEMA,
        "ok": ok,
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "request_id": resolved_request_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "attempt_id": attempt_id,
        "mode": mode,
        "action": action,
        "resources": normalized_resources,
        "resource": normalized_resources[0] if normalized_resources else None,
        "source_node": source_node,
        "active_goal": active_goal,
        "goal_hash": goal_hash,
        "requested_scope": requested_scope or proposed_save_rule or "once",
        "nonce": resolved_nonce,
        "expires_at": expires_at,
        "allowed_replies": list(ALLOWED_PERMISSION_REPLIES) if status == "PENDING" else [],
        "proposed_save_rule": proposed_save_rule,
        "decision": "DENIED" if denied or errors else "ASK",
        "reason": reason or (
            "permission denied before mutation"
            if denied
            else "permission request is pending human reply"
        ),
        "run_dir": str(resolved_run_dir),
        "receipt_path": str(output_path),
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau records a durable permission request before a gated mutation",
                "Tau records denied permission attempts as fail-closed receipts",
                (
                    "Tau exposes the exact request id, action, resources, source node, "
                    "and reply choices"
                ),
            ],
            "does_not_prove": [
                "the requested mutation was executed",
                "a human approved the request",
                "permission queue UI rendering",
                "provider or model behavior",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(output_path, receipt)
    return receipt


def write_permission_reply_receipt(
    *,
    request_receipt: Path,
    reply: str,
    output: Path | None = None,
    actor_id: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Record a durable human reply to a pending permission request receipt."""

    resolved_request = request_receipt.expanduser().resolve()
    request_payload, load_errors = _load_json_object(resolved_request, label="request receipt")
    normalized_reply = reply.strip().lower()
    request_id = (
        str(request_payload.get("request_id"))
        if isinstance(request_payload.get("request_id"), str)
        else "unknown-request"
    )
    default_output_path = resolved_request.parent / f"permission-reply-{request_id}.json"
    output_path = output.expanduser().resolve() if output is not None else default_output_path
    errors = load_errors + _permission_reply_errors(
        request_payload=request_payload,
        reply=normalized_reply,
        output_path=output_path,
    )
    accepted = normalized_reply in {"once", "always"} and not errors
    receipt = {
        "schema": PERMISSION_REPLY_RECEIPT_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "request_id": request_id,
        "reply": normalized_reply,
        "accepted": accepted,
        "actor_id": actor_id,
        "scope": scope,
        "decision": "APPROVED" if accepted else "DENIED",
        "request_receipt": str(resolved_request),
        "request_receipt_sha256": _file_sha256(resolved_request),
        "action": request_payload.get("action") if request_payload else None,
        "resources": request_payload.get("resources") if request_payload else [],
        "resource": request_payload.get("resource") if request_payload else None,
        "session_id": request_payload.get("session_id") if request_payload else None,
        "turn_id": request_payload.get("turn_id") if request_payload else None,
        "attempt_id": request_payload.get("attempt_id") if request_payload else None,
        "goal_hash": request_payload.get("goal_hash") if request_payload else None,
        "active_goal": request_payload.get("active_goal") if request_payload else None,
        "requested_scope": request_payload.get("requested_scope") if request_payload else None,
        "nonce": request_payload.get("nonce") if request_payload else None,
        "expires_at": request_payload.get("expires_at") if request_payload else None,
        "source_node": request_payload.get("source_node") if request_payload else None,
        "save_rule": _reply_save_rule(
            request_payload=request_payload,
            reply=normalized_reply,
            scope=scope,
        ),
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau records a durable permission reply linked to a request receipt",
                "Tau distinguishes once, always, and reject replies",
                "Tau preserves request receipt provenance with a SHA-256 digest",
            ],
            "does_not_prove": [
                "the requested mutation was executed",
                "permission state was applied to a running process",
                "permission queue UI rendering",
                "provider or model behavior",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(output_path, receipt)
    return receipt


def evaluate_permission_gate(
    *,
    request_receipt: Path,
    reply_receipt: Path,
    requested_action: str,
    resources: list[str],
    run_dir: Path,
    output: Path | None = None,
    session_id: str | None = None,
    goal_hash: str | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Check and consume a permission reply before a governed operation."""

    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    resolved_request = request_receipt.expanduser().resolve()
    resolved_reply = reply_receipt.expanduser().resolve()
    request_payload, request_errors = _load_json_object(resolved_request, label="request receipt")
    reply_payload, reply_errors = _load_json_object(resolved_reply, label="reply receipt")
    request_id = (
        str(request_payload.get("request_id"))
        if isinstance(request_payload.get("request_id"), str)
        else "unknown-request"
    )
    output_path = (
        output.expanduser().resolve()
        if output is not None
        else resolved_run_dir / f"permission-gate-{request_id}.json"
    )
    use_path = resolved_run_dir / f"{PERMISSION_USE_PREFIX}{request_id}.json"
    errors = request_errors + reply_errors + _permission_gate_errors(
        request_payload=request_payload,
        reply_payload=reply_payload,
        requested_action=requested_action,
        resources=[item.strip() for item in resources if item.strip()],
        session_id=session_id,
        goal_hash=goal_hash,
        nonce=nonce,
        use_path=use_path,
    )
    admitted = not errors
    receipt = {
        "schema": PERMISSION_GATE_RECEIPT_SCHEMA,
        "ok": admitted,
        "status": "PASS" if admitted else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "request_id": request_id,
        "requested_action": requested_action,
        "requested_resources": [item.strip() for item in resources if item.strip()],
        "request_receipt": str(resolved_request),
        "request_receipt_sha256": _file_sha256(resolved_request),
        "reply_receipt": str(resolved_reply),
        "reply_receipt_sha256": _file_sha256(resolved_reply),
        "session_id": session_id,
        "goal_hash": goal_hash,
        "nonce": nonce,
        "admitted": admitted,
        "consumed_once": admitted and reply_payload.get("reply") == "once",
        "use_receipt": str(use_path) if admitted and reply_payload.get("reply") == "once" else None,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau checks a permission reply against the exact request id, action, "
                "resource list, session, goal hash, nonce, and expiry before admitting use",
                "A once reply is consumed with a durable use receipt to reject replay",
            ],
            "does_not_prove": [
                "The governed mutation body was executed",
                "Provider or model behavior",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    if admitted and reply_payload.get("reply") == "once":
        _write_json(
            use_path,
            {
                "schema": "tau.permission_use_receipt.v1",
                "request_id": request_id,
                "reply_receipt": str(resolved_reply),
                "used_at": receipt["timestamp"],
                "requested_action": requested_action,
                "requested_resources": receipt["requested_resources"],
            },
        )
    _write_json(output_path, receipt)
    return receipt


def collect_permission_request_views(root: Path) -> list[dict[str, Any]]:
    """Return bounded operator-facing permission request summaries under root."""
    roots = _permission_search_roots(root.expanduser().resolve())
    paths: list[Path] = []
    for base in roots:
        if not base.exists() or not base.is_dir():
            continue
        paths.extend(sorted(base.glob("**/permission-request-*.json"))[:200])
    views = [_permission_request_view(path) for path in dict.fromkeys(paths)]
    return sorted(views, key=lambda item: str(item.get("timestamp") or ""), reverse=True)


def _permission_request_errors(
    *,
    action: str,
    resources: list[str],
    source_node: str,
    request_id: str | None,
    proposed_save_rule: str | None,
    nonce: str | None,
    expires_at: str | None,
) -> list[str]:
    errors: list[str] = []
    if action not in ALLOWED_ACTIONS:
        errors.append(f"action must be one of {sorted(ALLOWED_ACTIONS)}")
    if not resources:
        errors.append("at least one --resource is required")
    if not source_node.strip():
        errors.append("--source-node must be a non-empty string")
    if request_id is not None and not request_id.strip():
        errors.append("--request-id must be a non-empty string when present")
    if proposed_save_rule is not None and not proposed_save_rule.strip():
        errors.append("--save-rule must be a non-empty string when present")
    if nonce is not None and not nonce.strip():
        errors.append("--nonce must be a non-empty string when present")
    if expires_at is not None and _parse_timestamp(expires_at) is None:
        errors.append("--expires-at must be an ISO-8601 timestamp when present")
    return errors


def _permission_reply_errors(
    *,
    request_payload: dict[str, Any],
    reply: str,
    output_path: Path,
) -> list[str]:
    errors: list[str] = []
    if reply not in ALLOWED_PERMISSION_REPLIES:
        errors.append(f"reply must be one of {list(ALLOWED_PERMISSION_REPLIES)}")
    if not request_payload:
        return errors
    if request_payload.get("schema") != PERMISSION_REQUEST_RECEIPT_SCHEMA:
        errors.append(f"request receipt schema must be {PERMISSION_REQUEST_RECEIPT_SCHEMA}")
    if request_payload.get("status") != "PENDING":
        errors.append("request receipt status must be PENDING")
    allowed = request_payload.get("allowed_replies")
    if not isinstance(allowed, list) or reply not in allowed:
        errors.append("reply must be allowed by request receipt allowed_replies")
    if not isinstance(request_payload.get("request_id"), str) or not request_payload[
        "request_id"
    ].strip():
        errors.append("request receipt request_id must be a non-empty string")
    if output_path.exists():
        errors.append(f"permission reply already exists: {output_path}")
    expires_at = request_payload.get("expires_at")
    if isinstance(expires_at, str):
        expires_at_value = _parse_timestamp(expires_at)
        if expires_at_value is None:
            errors.append("request receipt expires_at must be an ISO-8601 timestamp")
        elif expires_at_value <= datetime.now(UTC):
            errors.append("request receipt expired")
    return errors


def _permission_gate_errors(
    *,
    request_payload: dict[str, Any],
    reply_payload: dict[str, Any],
    requested_action: str,
    resources: list[str],
    session_id: str | None,
    goal_hash: str | None,
    nonce: str | None,
    use_path: Path,
) -> list[str]:
    errors: list[str] = []
    if not request_payload or not reply_payload:
        return errors
    if request_payload.get("schema") != PERMISSION_REQUEST_RECEIPT_SCHEMA:
        errors.append(f"request receipt schema must be {PERMISSION_REQUEST_RECEIPT_SCHEMA}")
    if reply_payload.get("schema") != PERMISSION_REPLY_RECEIPT_SCHEMA:
        errors.append(f"reply receipt schema must be {PERMISSION_REPLY_RECEIPT_SCHEMA}")
    if request_payload.get("request_id") != reply_payload.get("request_id"):
        errors.append("reply request_id must match request receipt")
    if reply_payload.get("accepted") is not True:
        errors.append("reply must be accepted")
    if reply_payload.get("action") != requested_action:
        errors.append("reply action must match requested_action")
    if request_payload.get("action") != requested_action:
        errors.append("request action must match requested_action")
    if list(request_payload.get("resources") or []) != resources:
        errors.append("requested resources must match request receipt")
    if list(reply_payload.get("resources") or []) != resources:
        errors.append("requested resources must match reply receipt")
    if session_id is not None and request_payload.get("session_id") != session_id:
        errors.append("session_id must match request receipt")
    if goal_hash is not None and request_payload.get("goal_hash") != goal_hash:
        errors.append("goal_hash must match request receipt")
    if nonce is not None and request_payload.get("nonce") != nonce:
        errors.append("nonce must match request receipt")
    expires_at = request_payload.get("expires_at")
    if isinstance(expires_at, str):
        expires_at_value = _parse_timestamp(expires_at)
        if expires_at_value is None:
            errors.append("request receipt expires_at must be an ISO-8601 timestamp")
        elif expires_at_value <= datetime.now(UTC):
            errors.append("request receipt expired")
    if reply_payload.get("reply") == "once" and use_path.exists():
        errors.append(f"permission reply already consumed: {use_path}")
    return errors


def _reply_save_rule(
    *,
    request_payload: dict[str, Any],
    reply: str,
    scope: str | None,
) -> dict[str, Any] | None:
    if reply != "always" or not request_payload:
        return None
    return {
        "scope": scope or request_payload.get("proposed_save_rule") or "session",
        "action": request_payload.get("action"),
        "resources": request_payload.get("resources", []),
        "source_node": request_payload.get("source_node"),
    }


def _default_request_id(
    *,
    action: str,
    resources: list[str],
    source_node: str,
    session_id: str | None,
) -> str:
    payload = {
        "action": action,
        "resources": resources,
        "session_id": session_id,
        "source_node": source_node,
        "timestamp": _utc_stamp(),
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"perm-{hashlib.sha256(data).hexdigest()[:16]}"


def _load_json_object(path: Path, *, label: str) -> tuple[dict[str, Any], list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [f"{label} not found: {path}"]
    except json.JSONDecodeError as exc:
        return {}, [f"{label} is not valid JSON: {exc}"]
    if not isinstance(payload, dict):
        return {}, [f"{label} must be a JSON object"]
    return payload, []


def _permission_search_roots(root: Path) -> tuple[Path, ...]:
    return (
        root / "experiments" / "goal-locked-subagents" / "proofs" / "permissions",
        root / ".tau" / "permissions",
        root,
    )


def _permission_request_view(path: Path) -> dict[str, Any]:
    payload, load_errors = _load_json_object(path, label="permission request")
    reply_path = _reply_path_for_request(path, payload)
    reply_payload, reply_errors = (
        _load_json_object(reply_path, label="permission reply") if reply_path.exists() else ({}, [])
    )
    state, reason = _permission_view_state(
        request_payload=payload,
        request_errors=load_errors,
        reply_payload=reply_payload,
        reply_errors=reply_errors,
    )
    resources = payload.get("resources") if isinstance(payload.get("resources"), list) else []
    return {
        "path": str(path),
        "reply_path": str(reply_path),
        "request_id": payload.get("request_id") if payload else path.stem,
        "action": payload.get("action") if payload else None,
        "resources": resources,
        "resource": payload.get("resource") or (resources[0] if resources else None),
        "reason": payload.get("reason") if payload else "; ".join(load_errors),
        "session_id": payload.get("session_id") if payload else None,
        "turn_id": payload.get("turn_id") if payload else None,
        "attempt_id": payload.get("attempt_id") if payload else None,
        "active_goal": payload.get("active_goal") if payload else None,
        "goal_hash": payload.get("goal_hash") if payload else None,
        "requested_scope": payload.get("requested_scope") if payload else None,
        "expires_at": payload.get("expires_at") if payload else None,
        "nonce": payload.get("nonce") if payload else None,
        "status": payload.get("status") if payload else None,
        "state": state,
        "state_reason": reason,
        "actionable": state == "PENDING",
        "timestamp": payload.get("timestamp") if payload else None,
        "reply": reply_payload.get("reply") if reply_payload else None,
        "reply_status": reply_payload.get("status") if reply_payload else None,
        "reply_errors": reply_payload.get("errors") if reply_payload else reply_errors,
    }


def _reply_path_for_request(path: Path, payload: dict[str, Any]) -> Path:
    request_id = (
        str(payload.get("request_id"))
        if isinstance(payload.get("request_id"), str)
        else path.stem.removeprefix("permission-request-")
    )
    return path.parent / f"permission-reply-{request_id}.json"


def _permission_view_state(
    *,
    request_payload: dict[str, Any],
    request_errors: list[str],
    reply_payload: dict[str, Any],
    reply_errors: list[str],
) -> tuple[str, str]:
    if request_errors:
        return "MALFORMED", "; ".join(request_errors)
    if request_payload.get("schema") != PERMISSION_REQUEST_RECEIPT_SCHEMA:
        return "MALFORMED", f"schema must be {PERMISSION_REQUEST_RECEIPT_SCHEMA}"
    if request_payload.get("status") != "PENDING":
        return "NON_ACTIONABLE", f"request status is {request_payload.get('status')}"
    expires_at = request_payload.get("expires_at")
    if isinstance(expires_at, str):
        expires_at_value = _parse_timestamp(expires_at)
        if expires_at_value is None:
            return "MALFORMED", "expires_at is not an ISO-8601 timestamp"
        if expires_at_value <= datetime.now(UTC):
            return "EXPIRED", "request expired"
    if reply_payload:
        if reply_errors:
            return "MALFORMED_REPLY", "; ".join(reply_errors)
        if reply_payload.get("schema") != PERMISSION_REPLY_RECEIPT_SCHEMA:
            return "MALFORMED_REPLY", f"reply schema must be {PERMISSION_REPLY_RECEIPT_SCHEMA}"
        if reply_payload.get("accepted") is True:
            return "APPROVED", "reply receipt accepted"
        return "DENIED", "reply receipt denied"
    return "PENDING", "awaiting operator decision"


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _file_sha256(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
