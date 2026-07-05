"""Provider lifecycle state normalization for Tau orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROVIDER_SESSION_STATE_SCHEMA = "tau.provider_session_state.v1"
PROVIDER_SESSION_STATES = {
    "starting",
    "ready",
    "running",
    "waiting_on_input",
    "waiting_on_approval",
    "auth_required",
    "interstitial",
    "blocked",
    "exited",
    "crashed",
    "unknown",
}


def build_provider_session_state(
    readiness: dict[str, Any],
    *,
    visible_text: str = "",
    provider_api_available: bool = False,
) -> dict[str, Any]:
    """Normalize a provider readiness record into Tau's session-state contract."""

    evidence = _dict(readiness.get("evidence"))
    diagnostics = _dict(readiness.get("diagnostics"))
    process_alive = bool(evidence.get("process_alive") is True)
    interstitial_present = bool(diagnostics.get("interstitial_visible") is True)
    state = _normalized_state(
        readiness=readiness,
        diagnostics=diagnostics,
        visible_text=visible_text,
        process_alive=process_alive,
        interstitial_present=interstitial_present,
    )
    ready = state == "ready"
    foreground_command = str(evidence.get("foreground_command") or "")
    return {
        "schema": PROVIDER_SESSION_STATE_SCHEMA,
        "run_id": readiness.get("run_id"),
        "provider_id": readiness.get("provider_id"),
        "workspace_id": readiness.get("workspace_id"),
        "pane_id": readiness.get("pane_id"),
        "terminal_id": readiness.get("terminal_id"),
        "provider_session_id": readiness.get("provider_session_id"),
        "process": {
            "pid": evidence.get("foreground_pid"),
            "alive": process_alive,
            "foreground": bool(foreground_command),
            "command": foreground_command,
            "argv": evidence.get("foreground_argv") if isinstance(evidence.get("foreground_argv"), list) else [],
            "cwd": evidence.get("foreground_cwd"),
        },
        "state": state,
        "ready": ready,
        "auth": {
            "status": "auth_required" if state == "auth_required" else "unknown",
            "method": "unknown",
        },
        "interstitial": {
            "present": interstitial_present,
            "kind": _interstitial_kind(readiness, visible_text) if interstitial_present else None,
            "safe_actions": diagnostics.get("readiness_actions")
            if isinstance(diagnostics.get("readiness_actions"), list)
            else [],
        },
        "provider_api": {
            "available": provider_api_available,
            "endpoint": "none",
            "last_event_type": None,
        },
        "source": readiness.get("source") or "unknown",
        "observed_at": readiness.get("observed_at") or _utc_stamp(),
        "evidence": {
            "provider_readiness_path": evidence.get("provider_readiness_path"),
            "visible_log_path": evidence.get("visible_log_path"),
            "provider_event_log_path": evidence.get("provider_event_log_path"),
        },
        "diagnostics": {
            "visible_prompt_observed": diagnostics.get("visible_prompt_observed") is True,
            "visible_prompt_is_gate": diagnostics.get("visible_prompt_is_gate") is True,
            "pane_agent_status": evidence.get("pane_agent_status"),
            "pane_label": evidence.get("pane_label"),
            "readiness_state": readiness.get("state"),
        },
    }


def compact_provider_session_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return the lifecycle fields most useful in run receipts and inspect output."""

    process = _dict(state.get("process"))
    evidence = _dict(state.get("evidence"))
    auth = _dict(state.get("auth"))
    interstitial = _dict(state.get("interstitial"))
    provider_api = _dict(state.get("provider_api"))
    return {
        "schema": state.get("schema"),
        "provider_id": state.get("provider_id"),
        "workspace_id": state.get("workspace_id"),
        "pane_id": state.get("pane_id"),
        "terminal_id": state.get("terminal_id"),
        "state": state.get("state"),
        "ready": state.get("ready"),
        "source": state.get("source"),
        "observed_at": state.get("observed_at"),
        "process_alive": process.get("alive"),
        "foreground_command": process.get("command"),
        "auth_status": auth.get("status"),
        "interstitial_present": interstitial.get("present"),
        "interstitial_kind": interstitial.get("kind"),
        "provider_api_available": provider_api.get("available"),
        "visible_log_path": evidence.get("visible_log_path"),
        "provider_readiness_path": evidence.get("provider_readiness_path"),
        "provider_event_log_path": evidence.get("provider_event_log_path"),
    }


def load_provider_session_states(paths: list[Any]) -> list[dict[str, Any]]:
    """Load provider session state objects from receipt paths."""

    states = []
    for path_text in paths:
        if not isinstance(path_text, str) or not path_text:
            continue
        path = Path(path_text).expanduser()
        try:
            import json

            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("schema") == PROVIDER_SESSION_STATE_SCHEMA:
            states.append(payload)
    return states


def _normalized_state(
    *,
    readiness: dict[str, Any],
    diagnostics: dict[str, Any],
    visible_text: str,
    process_alive: bool,
    interstitial_present: bool,
) -> str:
    raw_state = str(readiness.get("state") or "unknown").lower()
    if _auth_required_visible(visible_text):
        return "auth_required"
    if interstitial_present:
        return "interstitial"
    if not process_alive:
        return "crashed"
    if raw_state in PROVIDER_SESSION_STATES:
        if raw_state == "ready" and readiness.get("ready") is not True:
            return "blocked"
        return raw_state
    if diagnostics.get("visible_prompt_observed") is True:
        return "ready"
    return "unknown"


def _auth_required_visible(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("sign in", "login required", "not authenticated"))


def _interstitial_kind(readiness: dict[str, Any], visible_text: str) -> str:
    provider_id = str(readiness.get("provider_id") or "")
    if provider_id == "codex" and "Hooks need review" in visible_text:
        return "hook_trust"
    if provider_id == "codex" and "Update available" in visible_text:
        return "update_prompt"
    return "unknown"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
