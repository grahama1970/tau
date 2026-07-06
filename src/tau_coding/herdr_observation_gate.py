"""Tau gate over Herdr observation snapshots."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.course_correction import build_course_correction_receipt

HERDR_OBSERVATION_GATE_SCHEMA = "tau.herdr_observation_gate_receipt.v1"


def write_herdr_observation_gate_receipt(
    output_path: Path,
    *,
    snapshot_path: Path,
    expected_receipt_path: Path | None = None,
    expected_workspace_id: str | None = None,
    expected_pane_id: str | None = None,
    expected_terminal_id: str | None = None,
    run_id: str | None = None,
    dag_id: str | None = None,
    goal_hash: str | None = None,
    node_id: str | None = None,
    agent: str | None = None,
    attempt: int | None = None,
    receipt_overdue: bool = False,
    receipt_timeout_seconds: float | None = None,
    mocked: bool = False,
    live: bool = True,
    provider_live: bool = False,
) -> dict[str, Any]:
    snapshot = _read_json_object(snapshot_path, label="Herdr observation snapshot")
    observed = _normalize_herdr_snapshot(snapshot)
    expected_receipt = expected_receipt_path.expanduser().resolve() if expected_receipt_path else None
    binding_errors = _binding_errors(
        observed,
        expected_workspace_id=expected_workspace_id,
        expected_pane_id=expected_pane_id,
        expected_terminal_id=expected_terminal_id,
    )
    receipt_missing = bool(expected_receipt and not expected_receipt.exists())
    trigger = _trigger_for_observation(
        observed,
        binding_errors=binding_errors,
        receipt_missing=receipt_missing,
        receipt_overdue=receipt_overdue,
    )
    course_correction = None
    status = "PASS"
    ok = True
    recommended_action = "continue"
    if trigger:
        ok = False
        status = "BLOCKED"
        course_correction = build_course_correction_receipt(
            trigger=trigger,
            run_id=run_id,
            dag_id=dag_id,
            goal_hash=goal_hash,
            node_id=node_id,
            agent=agent or observed.get("agent_name"),
            attempt=attempt,
            observed_state={
                **observed,
                "binding_errors": binding_errors,
                "expected_receipt_path": str(expected_receipt) if expected_receipt else None,
                "receipt_missing": receipt_missing,
                "receipt_overdue": receipt_overdue,
                "receipt_timeout_seconds": receipt_timeout_seconds,
            },
            errors=binding_errors
            or (
                [f"expected receipt is missing: {expected_receipt}"]
                if receipt_missing and receipt_overdue
                else []
            ),
            mocked=mocked,
            live=live,
            provider_live=provider_live,
        )
        recommended_action = str(course_correction["required_next_action"])
    payload = {
        "schema": HERDR_OBSERVATION_GATE_SCHEMA,
        "ok": ok,
        "status": status,
        "mocked": mocked,
        "live": live,
        "provider_live": provider_live,
        "run_id": run_id,
        "dag_id": dag_id,
        "goal_hash": goal_hash,
        "node_id": node_id,
        "agent": agent or observed.get("agent_name"),
        "attempt": attempt,
        "snapshot_path": str(snapshot_path.expanduser().resolve()),
        "expected_receipt_path": str(expected_receipt) if expected_receipt else None,
        "expected_workspace_id": expected_workspace_id,
        "expected_pane_id": expected_pane_id,
        "expected_terminal_id": expected_terminal_id,
        "observed": observed,
        "binding_errors": binding_errors,
        "receipt_missing": receipt_missing,
        "receipt_overdue": receipt_overdue,
        "receipt_timeout_seconds": receipt_timeout_seconds,
        "recommended_action": recommended_action,
        "course_correction": course_correction,
        "proof_scope": {
            "proves": [
                "Tau consumed a Herdr observation snapshot as runtime evidence.",
                "Tau compared Herdr identity fields with expected DAG/work-order binding.",
                "Tau emitted an admissibility decision without parsing pane chat as truth.",
            ],
            "does_not_prove": [
                "Herdr pane output is truthful.",
                "Provider/model semantic quality.",
                "The required course-correction action has been executed.",
                "Production route correctness.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    resolved = output_path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _trigger_for_observation(
    observed: dict[str, Any],
    *,
    binding_errors: list[str],
    receipt_missing: bool,
    receipt_overdue: bool,
) -> str | None:
    if binding_errors:
        return "herdr_binding_mismatch"
    state = str(observed.get("state") or "").lower()
    if state == "auth_required":
        return "provider_auth_required"
    if state == "interstitial":
        return "provider_interstitial"
    if state in {"crashed", "exited"}:
        return "provider_crashed"
    if state == "stale":
        return "herdr_stale"
    if receipt_missing and receipt_overdue:
        return "receipt_timeout"
    return None


def _binding_errors(
    observed: dict[str, Any],
    *,
    expected_workspace_id: str | None,
    expected_pane_id: str | None,
    expected_terminal_id: str | None,
) -> list[str]:
    errors: list[str] = []
    for key, expected in (
        ("workspace_id", expected_workspace_id),
        ("pane_id", expected_pane_id),
        ("terminal_id", expected_terminal_id),
    ):
        if not expected:
            continue
        actual = str(observed.get(key) or "")
        if actual != expected:
            errors.append(f"{key} mismatch: expected {expected}, observed {actual or '<missing>'}")
    return errors


def _normalize_herdr_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    source = snapshot
    result = snapshot.get("result")
    if isinstance(result, dict):
        agent = result.get("agent")
        pane = result.get("pane")
        if isinstance(agent, dict):
            source = {**result, **agent}
        elif isinstance(pane, dict):
            source = {**result, **pane}
    state = _first_str(
        source,
        "state",
        "agent_status",
        "status",
        "custom_status",
        default="unknown",
    )
    return {
        "schema": snapshot.get("schema") or "herdr.monitor_snapshot.v1",
        "state": state,
        "workspace_id": _first_str(source, "workspace_id", "workspace"),
        "pane_id": _first_str(source, "pane_id", "pane"),
        "terminal_id": _first_str(source, "terminal_id", "terminal"),
        "agent_name": _first_str(source, "agent_name", "name", "agent"),
        "process_alive": _first_bool(source, "process_alive", "alive"),
        "visible_log_path": _first_str(source, "visible_log_path"),
        "last_visible_output_at": _first_str(source, "last_visible_output_at"),
        "raw_state": state,
    }


def _first_str(source: dict[str, Any], *keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return default


def _first_bool(source: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, bool):
            return value
    return None


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {resolved}")
    return payload


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
