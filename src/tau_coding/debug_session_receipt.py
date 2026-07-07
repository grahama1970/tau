"""Debugger/DAP evidence receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA

DEBUG_SESSION_PACKET_SCHEMA = "tau.debug_session_packet.v1"
DEBUG_SESSION_RECEIPT_SCHEMA = "tau.debug_session_receipt.v1"
SUPPORTED_ADAPTERS = {"debugpy", "lldb-dap", "dlv", "node"}


def write_debug_session_receipt(
    *,
    session_path: Path,
    output_path: Path,
    required: bool = False,
    expected_goal_hash: str | None = None,
    zero_trust: bool = False,
    policy_profile: dict[str, Any] | None = None,
    data_boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_session = session_path.expanduser().resolve()
    alerts: list[dict[str, Any]] = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
    )
    packet = _read_json_object(resolved_session, alerts)
    if packet.get("schema") != DEBUG_SESSION_PACKET_SCHEMA:
        alerts.append(
            _alert("invalid_debug_session_schema", f"schema must be {DEBUG_SESSION_PACKET_SCHEMA}")
        )
    adapter = _string(packet.get("adapter"))
    adapter_available = bool(packet.get("adapter_available", bool(adapter)))
    if adapter not in SUPPORTED_ADAPTERS:
        alerts.append(_alert("unsupported_debug_adapter", "adapter is not supported"))
    if required and not adapter_available:
        alerts.append(_alert("debug_adapter_unavailable", "required debug adapter is unavailable"))
    target = _string(packet.get("target"))
    if target is None:
        alerts.append(_alert("missing_debug_target", "debug session target is required"))
    goal_hash = _string(packet.get("goal_hash"))
    if zero_trust and goal_hash is None:
        alerts.append(_alert("missing_goal_hash", "zero-trust debug receipt requires goal_hash"))
    if expected_goal_hash and goal_hash != expected_goal_hash:
        alerts.append(_alert("goal_hash_mismatch", "debug session goal_hash mismatches expected"))

    breakpoints = _optional_list(packet.get("breakpoints"), "breakpoints", alerts)
    stopped_frame = _optional_mapping(packet.get("stopped_frame"), "stopped_frame", alerts)
    variables = _optional_list(packet.get("variables"), "variables", alerts)
    commands = _optional_list(packet.get("commands"), "commands", alerts)

    stdout_path = _optional_debug_log_path(
        packet.get("stdout_path"),
        resolved_session.parent,
        "stdout_path",
        alerts,
    )
    stderr_path = _optional_debug_log_path(
        packet.get("stderr_path"),
        resolved_session.parent,
        "stderr_path",
        alerts,
    )

    ok = not alerts
    payload = {
        "schema": DEBUG_SESSION_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "session_path": str(resolved_session),
        "session_sha256": _sha256_uri(resolved_session),
        "session_bytes": _artifact_size(resolved_session),
        "session_artifact": _artifact_descriptor("debug_session_packet", resolved_session),
        "goal_hash": goal_hash,
        "expected_goal_hash": expected_goal_hash,
        "target": target,
        "adapter": adapter,
        "adapter_available": adapter_available,
        "breakpoints": breakpoints,
        "stopped_frame": stopped_frame,
        "variables": variables,
        "commands": commands,
        "stdout_path": str(stdout_path) if stdout_path is not None else None,
        "stdout_sha256": _sha256_uri(stdout_path),
        "stdout_bytes": stdout_path.stat().st_size if stdout_path is not None else None,
        "stderr_path": str(stderr_path) if stderr_path is not None else None,
        "stderr_sha256": _sha256_uri(stderr_path),
        "stderr_bytes": stderr_path.stat().st_size if stderr_path is not None else None,
        "log_artifacts": _log_artifacts(stdout_path=stdout_path, stderr_path=stderr_path),
        "conclusion": packet.get("conclusion"),
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau captured debugger evidence from a structured local session packet.",
                "Tau recorded adapter, target, breakpoints, stopped frame, variables, and logs.",
            ],
            "does_not_prove": [
                "The bug is fixed.",
                "The debug conclusion is semantically complete.",
                "The code is correct.",
                "Provider/model semantic quality.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(output_path.expanduser().resolve())
    _write_json(output_path, payload)
    return payload


def _read_json_object(path: Path, alerts: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        alerts.append(_alert("debug_session_missing", "debug session packet is missing"))
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        alerts.append(_alert("debug_session_unreadable", f"debug session packet unreadable: {exc}"))
        return {}
    if not isinstance(payload, dict):
        alerts.append(_alert("debug_session_not_object", "debug session packet must be an object"))
        return {}
    return payload


def _optional_debug_log_path(
    value: object,
    base_dir: Path,
    field: str,
    alerts: list[dict[str, Any]],
) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    resolved = candidate.resolve()
    resolved_base = base_dir.resolve()
    try:
        resolved.relative_to(resolved_base)
    except ValueError:
        alerts.append(
            _alert(
                f"{field}_outside_session_dir",
                f"{field} must stay under the debug session directory",
            )
        )
        return None
    if not resolved.exists():
        alerts.append(_alert(f"{field}_missing", f"{field} does not exist"))
        return None
    return resolved


def _log_artifacts(*, stdout_path: Path | None, stderr_path: Path | None) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for label, path in (("stdout", stdout_path), ("stderr", stderr_path)):
        if path is None:
            continue
        artifacts.append(
            {
                "label": label,
                "path": str(path),
                "exists": True,
                "sha256": _sha256_uri(path),
                "bytes": path.stat().st_size,
            }
        )
    return artifacts


def _artifact_descriptor(label: str, path: Path | None) -> dict[str, Any]:
    return {
        "label": label,
        "path": str(path) if path is not None else None,
        "exists": bool(path is not None and path.exists()),
        "sha256": _sha256_uri(path),
        "bytes": _artifact_size(path),
    }


def _sha256_uri(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    except OSError:
        return None


def _artifact_size(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_list(
    value: object,
    field: str,
    alerts: list[dict[str, Any]],
) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    alerts.append(_alert(f"invalid_{field}", f"{field} must be a list"))
    return []


def _optional_mapping(
    value: object,
    field: str,
    alerts: list[dict[str, Any]],
) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    alerts.append(_alert(f"invalid_{field}", f"{field} must be an object"))
    return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _coding_policy_alerts(
    *,
    zero_trust: bool,
    policy_profile: dict[str, Any] | None,
    data_boundary: dict[str, Any] | None,
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    if zero_trust and policy_profile is None:
        alerts.append(
            _alert("missing_policy_profile", "zero-trust debug receipt requires policy_profile")
        )
    if zero_trust and data_boundary is None:
        alerts.append(
            _alert("missing_data_boundary", "zero-trust debug receipt requires data_boundary")
        )
    if policy_profile is not None and policy_profile.get("schema") != POLICY_PROFILE_SCHEMA:
        alerts.append(_alert("invalid_policy_profile_schema", "policy_profile schema is invalid"))
    if data_boundary is not None and data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA:
        alerts.append(_alert("invalid_data_boundary_schema", "data_boundary schema is invalid"))
    return alerts


def _alert(code: str, message: str) -> dict[str, str]:
    return {"severity": "BLOCK", "code": code, "message": message}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
