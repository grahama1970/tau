"""Run-owned Herdr cleanup for Tau proof workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERDR_CLEANUP_RECEIPT_SCHEMA = "tau.herdr_cleanup_receipt.v1"
HERDR_GC_RECEIPT_SCHEMA = "tau.herdr_gc_receipt.v1"
HERDR_WORKSPACE_LEASE_SCHEMA = "tau.herdr_workspace_lease.v1"
DEFAULT_GC_LABEL_PREFIXES = (
    "rw-sanity-generic-provider-",
    "rw-sanity-provider-",
    "tau-live-provider-",
    "tau-provider-dag-",
    "tau-generic-provider-",
    "tau-traycer-",
)


def run_herdr_cleanup(
    *,
    run_dir: Path,
    mode: str = "audit",
    herdr_bin: str = "herdr",
    include_current_workspace: bool = False,
    workspace_lease_path: Path | None = None,
) -> dict[str, Any]:
    """Audit, dry-run, or apply cleanup for Herdr resources recorded by one Tau run."""

    if mode not in {"audit", "dry-run", "apply"}:
        raise RuntimeError("mode must be audit, dry-run, or apply")
    resolved_run_dir = run_dir.expanduser().resolve()
    manifest = _load_runtime_manifest(resolved_run_dir)
    resources = _resources_from_manifest(manifest)
    current_workspace = os.environ.get("HERDR_WORKSPACE_ID") or ""
    candidates = _candidate_actions(
        resources,
        current_workspace=current_workspace,
        include_current_workspace=include_current_workspace,
    )
    lease_payload: dict[str, Any] | None = None
    lease_alerts = _workspace_lease_alerts(
        workspace_lease_path=workspace_lease_path,
        manifest=manifest,
        candidates=candidates,
        mode=mode,
    )
    resolved_lease_path = (
        workspace_lease_path.expanduser().resolve()
        if workspace_lease_path is not None
        else None
    )
    if resolved_lease_path is not None and resolved_lease_path.is_file():
        lease_payload = _read_json_object(resolved_lease_path, label="workspace lease")
    command_results: list[dict[str, Any]] = []
    applied_actions: list[dict[str, Any]] = []
    if mode == "apply" and not lease_alerts:
        for candidate in candidates:
            if candidate["action"] != "workspace_close":
                continue
            workspace_id = candidate["workspace_id"]
            result = subprocess.run(
                [herdr_bin, "workspace", "close", workspace_id],
                cwd=str(resolved_run_dir),
                text=True,
                capture_output=True,
            )
            command_results.append(_command_result_dict(result))
            verify_result = subprocess.run(
                [herdr_bin, "workspace", "get", workspace_id],
                cwd=str(resolved_run_dir),
                text=True,
                capture_output=True,
            )
            command_results.append(_command_result_dict(verify_result))
            verify_error_code = _json_error_code(verify_result)
            post_verified_absent = verify_result.returncode != 0 and verify_error_code == "workspace_not_found"
            applied_actions.append(
                {
                    **candidate,
                    "returncode": result.returncode,
                    "applied": result.returncode == 0,
                    "post_verify_action": "workspace_get",
                    "post_verify_returncode": verify_result.returncode,
                    "post_verify_error_code": verify_error_code,
                    "post_verified_absent": post_verified_absent,
                }
            )
    ok = not lease_alerts and all(_applied_action_ok(action) for action in applied_actions)
    receipt = {
        "schema": HERDR_CLEANUP_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": mode == "apply",
        "mode": mode,
        "run_dir": str(resolved_run_dir),
        "runtime_manifest": str(resolved_run_dir / "runtime-manifest.json"),
        "runtime_manifest_sha256": _file_sha256(resolved_run_dir / "runtime-manifest.json"),
        "resource_count": len(resources),
        "resources": resources,
        "current_workspace": current_workspace or None,
        "include_current_workspace": include_current_workspace,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "workspace_lease": str(resolved_lease_path) if resolved_lease_path else None,
        "workspace_lease_sha256": (
            _file_sha256(resolved_lease_path) if resolved_lease_path else None
        ),
        "workspace_lease_payload": lease_payload,
        "lease_required": mode == "apply",
        "alerts": lease_alerts,
        "applied_actions": applied_actions,
        "command_results": command_results,
        "proof_scope": {
            "proves": [
                "Tau can identify Herdr workspace/session resources recorded by one run",
                "Tau cleanup defaults to audit/dry-run before mutation",
                "Tau refuses to close the current Herdr workspace unless explicitly allowed",
                "Tau apply cleanup requires a Herdr workspace lease before mutation",
                "Tau verifies applied workspace cleanup by requiring Herdr workspace get to report workspace_not_found",
            ],
            "does_not_prove": [
                "global regex cleanup of unrelated Herdr resources",
                "Git worktree deletion",
                "session deletion for runs that did not record a session id",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_run_dir / "herdr-cleanup-receipt.json", receipt)
    return receipt


def run_herdr_gc(
    *,
    run_dir: Path,
    apply: bool = False,
    herdr_bin: str = "herdr",
    include_current_workspace: bool = False,
    label_prefixes: tuple[str, ...] = DEFAULT_GC_LABEL_PREFIXES,
) -> dict[str, Any]:
    """Garbage-collect stale Tau-owned Herdr workspaces by label prefix."""

    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    current_workspace = os.environ.get("HERDR_WORKSPACE_ID") or ""
    list_result = subprocess.run(
        [herdr_bin, "workspace", "list"],
        cwd=str(resolved_run_dir),
        text=True,
        capture_output=True,
    )
    command_results = [_command_result_dict(list_result)]
    workspaces = _workspace_list_from_result(list_result)
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for workspace in workspaces:
        workspace_id = str(workspace.get("workspace_id") or "")
        label = str(workspace.get("label") or "")
        status = str(workspace.get("agent_status") or "unknown")
        focused = bool(workspace.get("focused"))
        if not workspace_id or not label.startswith(label_prefixes):
            continue
        base = {
            "workspace_id": workspace_id,
            "label": label,
            "agent_status": status,
            "focused": focused,
            "pane_count": workspace.get("pane_count"),
            "tab_count": workspace.get("tab_count"),
        }
        if current_workspace and workspace_id == current_workspace and not include_current_workspace:
            skipped.append({**base, "reason": "current_workspace"})
        elif focused:
            skipped.append({**base, "reason": "focused_workspace"})
        elif status not in {"done", "idle"}:
            skipped.append({**base, "reason": "agent_status_not_done_or_idle"})
        else:
            candidates.append(
                {
                    **base,
                    "action": "workspace_close",
                    "reason": "stale Tau/real-world-sanity workspace label",
                }
            )

    applied_actions: list[dict[str, Any]] = []
    if apply:
        for candidate in candidates:
            workspace_id = str(candidate["workspace_id"])
            close_result = subprocess.run(
                [herdr_bin, "workspace", "close", workspace_id],
                cwd=str(resolved_run_dir),
                text=True,
                capture_output=True,
            )
            command_results.append(_command_result_dict(close_result))
            verify_result = subprocess.run(
                [herdr_bin, "workspace", "get", workspace_id],
                cwd=str(resolved_run_dir),
                text=True,
                capture_output=True,
            )
            command_results.append(_command_result_dict(verify_result))
            verify_error_code = _json_error_code(verify_result)
            post_verified_absent = (
                verify_result.returncode != 0 and verify_error_code == "workspace_not_found"
            )
            applied_actions.append(
                {
                    **candidate,
                    "returncode": close_result.returncode,
                    "applied": close_result.returncode == 0,
                    "post_verify_action": "workspace_get",
                    "post_verify_returncode": verify_result.returncode,
                    "post_verify_error_code": verify_error_code,
                    "post_verified_absent": post_verified_absent,
                }
            )
    ok = all(_applied_action_ok(action) for action in applied_actions)
    receipt = {
        "schema": HERDR_GC_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": apply,
        "mode": "apply" if apply else "dry-run",
        "run_dir": str(resolved_run_dir),
        "herdr_bin": herdr_bin,
        "label_prefixes": list(label_prefixes),
        "current_workspace": current_workspace or None,
        "include_current_workspace": include_current_workspace,
        "workspace_count": len(workspaces),
        "candidate_count": len(candidates),
        "skipped_count": len(skipped),
        "applied_action_count": len(applied_actions),
        "post_verified_absent_count": sum(
            1 for action in applied_actions if action.get("post_verified_absent") is True
        ),
        "candidates": candidates,
        "skipped": skipped,
        "applied_actions": applied_actions,
        "command_results": command_results,
        "proof_scope": {
            "proves": [
                "Tau inspected Herdr workspace state through the Herdr API/CLI surface.",
                "Tau selected only stale Tau/real-world-sanity workspace labels for GC.",
                "Tau protected the current, focused, and non-idle/non-done workspaces by default.",
                "Apply mode verifies closed workspaces are no longer addressable through Herdr.",
            ],
            "does_not_prove": [
                "Cleanup of arbitrary non-Tau Herdr workspaces.",
                "Deletion of Git worktrees or local proof artifacts.",
                "Provider/model semantic quality.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_run_dir / "herdr-gc-receipt.json", receipt)
    return receipt


def _load_runtime_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "runtime-manifest.json"
    manifest = _read_json_object(manifest_path, label="runtime manifest")
    manifest["_manifest_dir"] = str(run_dir)
    return manifest


def _resources_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    resources: dict[tuple[str, str], dict[str, Any]] = {}
    manifest_dir = Path(str(manifest.get("_manifest_dir") or manifest.get("run_dir") or ".")).expanduser()

    def add_record(source: str, role: str, record: Any) -> None:
        if not isinstance(record, dict):
            return
        workspace_id = str(record.get("workspace_id") or "")
        if workspace_id:
            key = ("workspace", workspace_id)
            existing = resources.setdefault(
                key,
                {
                    "kind": "workspace",
                    "workspace_id": workspace_id,
                    "roles": [],
                    "sources": [],
                    "pane_ids": [],
                    "terminal_ids": [],
                },
            )
            _append_unique(existing["roles"], role)
            _append_unique(existing["sources"], source)
            _append_unique(existing["pane_ids"], str(record.get("pane_id") or ""))
            _append_unique(existing["terminal_ids"], str(record.get("terminal_id") or ""))
        session = str(record.get("session") or record.get("session_id") or "")
        if session:
            key = ("session", session)
            existing = resources.setdefault(
                key,
                {
                    "kind": "session",
                    "session": session,
                    "roles": [],
                    "sources": [],
                },
            )
            _append_unique(existing["roles"], role)
            _append_unique(existing["sources"], source)

    for source_key in ("provider_sessions", "visible_subagents"):
        records = manifest.get(source_key)
        if isinstance(records, dict):
            for role, record in records.items():
                add_record(source_key, str(role), record)
    for source_key in ("provider_session_states", "readiness_records"):
        for index, record in enumerate(_records_from_path_list(manifest.get(source_key), manifest_dir)):
            role = str(record.get("provider_id") or record.get("role") or f"{source_key}-{index}")
            add_record(source_key, role, record)
    return sorted(resources.values(), key=lambda item: (str(item["kind"]), str(item.get("workspace_id") or item.get("session"))))


def _records_from_path_list(value: Any, manifest_dir: Path) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    records = []
    for item in value:
        if isinstance(item, dict):
            records.append(item)
            continue
        if not isinstance(item, str) or not item:
            continue
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = manifest_dir / path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _candidate_actions(
    resources: list[dict[str, Any]],
    *,
    current_workspace: str,
    include_current_workspace: bool,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for resource in resources:
        if resource.get("kind") == "workspace":
            workspace_id = str(resource["workspace_id"])
            if current_workspace and workspace_id == current_workspace and not include_current_workspace:
                continue
            candidates.append(
                {
                    "action": "workspace_close",
                    "workspace_id": workspace_id,
                    "roles": resource.get("roles", []),
                    "pane_ids": resource.get("pane_ids", []),
                    "reason": "run-owned workspace recorded in runtime manifest",
                }
            )
        elif resource.get("kind") == "session":
            candidates.append(
                {
                    "action": "session_stop",
                    "session": resource["session"],
                    "roles": resource.get("roles", []),
                    "reason": "run-owned session recorded in runtime manifest",
                    "applied": False,
                    "note": "session stop/delete is not applied until Tau records session ownership in run manifests",
                }
            )
    return candidates


def _workspace_lease_alerts(
    *,
    workspace_lease_path: Path | None,
    manifest: dict[str, Any],
    candidates: list[dict[str, Any]],
    mode: str,
) -> list[dict[str, Any]]:
    workspace_candidates = [
        str(candidate.get("workspace_id") or "")
        for candidate in candidates
        if candidate.get("action") == "workspace_close"
    ]
    workspace_candidates = [item for item in workspace_candidates if item]
    if not workspace_candidates:
        return []
    if workspace_lease_path is None:
        if mode == "apply":
            return [
                _alert(
                    "BLOCK",
                    "missing_workspace_lease",
                    "Apply cleanup requires a Herdr workspace lease.",
                    {"workspace_ids": workspace_candidates},
                )
            ]
        return []
    resolved_path = workspace_lease_path.expanduser().resolve()
    try:
        lease = _read_json_object(resolved_path, label="workspace lease")
    except RuntimeError as exc:
        return [
            _alert(
                "BLOCK",
                "workspace_lease_unreadable",
                "Herdr workspace lease could not be read.",
                {"workspace_lease": str(resolved_path), "error": str(exc)},
            )
        ]
    alerts: list[dict[str, Any]] = []
    if lease.get("schema") != HERDR_WORKSPACE_LEASE_SCHEMA:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_workspace_lease_schema",
                "Herdr workspace lease schema is not supported.",
                {"schema": lease.get("schema")},
            )
        )
    if not lease.get("owner"):
        alerts.append(
            _alert(
                "BLOCK",
                "missing_workspace_lease_owner",
                "Herdr workspace lease must include an owner.",
                {"workspace_lease": str(resolved_path)},
            )
        )
    expected_run_id = manifest.get("run_id")
    if expected_run_id and lease.get("run_id") != expected_run_id:
        alerts.append(
            _alert(
                "BLOCK",
                "workspace_lease_run_id_mismatch",
                "Herdr workspace lease run_id does not match the runtime manifest.",
                {"expected": expected_run_id, "observed": lease.get("run_id")},
            )
        )
    cleanup_policy = lease.get("cleanup_policy")
    if cleanup_policy not in {"audit", "dry-run", "apply"}:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_workspace_lease_cleanup_policy",
                "Herdr workspace lease cleanup_policy is not supported.",
                {"cleanup_policy": cleanup_policy},
            )
        )
    elif mode == "apply" and cleanup_policy != "apply":
        alerts.append(
            _alert(
                "BLOCK",
                "workspace_lease_policy_not_apply",
                "Herdr workspace lease does not authorize apply cleanup.",
                {"cleanup_policy": cleanup_policy},
            )
        )
    lease_workspaces = _lease_workspace_ids(lease)
    missing = sorted(set(workspace_candidates) - set(lease_workspaces))
    if missing:
        alerts.append(
            _alert(
                "BLOCK",
                "workspace_lease_missing_workspace",
                "Herdr workspace lease does not cover every cleanup candidate.",
                {"missing_workspace_ids": missing, "lease_workspace_ids": lease_workspaces},
            )
        )
    expires_at = lease.get("expires_at")
    parsed_expiry = _parse_timestamp(expires_at)
    if parsed_expiry is None:
        alerts.append(
            _alert(
                "BLOCK",
                "invalid_workspace_lease_expiry",
                "Herdr workspace lease expires_at must be an ISO-8601 timestamp.",
                {"expires_at": expires_at},
            )
        )
    elif parsed_expiry <= datetime.now(UTC):
        alerts.append(
            _alert(
                "BLOCK",
                "workspace_lease_expired",
                "Herdr workspace lease has expired.",
                {"expires_at": expires_at},
            )
        )
    return alerts


def _lease_workspace_ids(lease: dict[str, Any]) -> list[str]:
    workspace_ids: list[str] = []
    _append_unique(workspace_ids, str(lease.get("workspace_id") or ""))
    value = lease.get("workspace_ids")
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                _append_unique(workspace_ids, item)
    workspaces = lease.get("workspaces")
    if isinstance(workspaces, list):
        for item in workspaces:
            if isinstance(item, str):
                _append_unique(workspace_ids, item)
            elif isinstance(item, dict):
                _append_unique(workspace_ids, str(item.get("workspace_id") or ""))
    return workspace_ids


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _command_result_dict(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "argv": result.args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _workspace_list_from_result(result: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    if result.returncode != 0:
        raise RuntimeError(f"herdr workspace list failed: {result.stderr or result.stdout}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"herdr workspace list returned non-JSON output: {exc}") from exc
    workspaces = payload.get("workspaces")
    if not isinstance(workspaces, list):
        result_payload = payload.get("result")
        if isinstance(result_payload, dict):
            workspaces = result_payload.get("workspaces")
    if not isinstance(workspaces, list):
        raise RuntimeError("herdr workspace list JSON did not include a workspaces list")
    return [item for item in workspaces if isinstance(item, dict)]


def _applied_action_ok(action: dict[str, Any]) -> bool:
    if action.get("action") != "workspace_close":
        return True
    return action.get("applied") is True and action.get("post_verified_absent") is True


def _json_error_code(result: subprocess.CompletedProcess[str]) -> str | None:
    for raw in (result.stdout, result.stderr):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return None


def _alert(severity: str, code: str, message: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "evidence": evidence}


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return payload


def _file_sha256(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
