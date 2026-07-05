"""Adapters that let generic Tau DAG nodes call provider-backed Tau runs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.generic_dag import GENERIC_DAG_NODE_RECEIPT_SCHEMA
from tau_coding.provider_dag_poc import run_provider_dag_poc

PROVIDER_DAG_WORK_ORDER_SCHEMA = "tau.provider_dag_work_order.v1"


def run_generic_provider_dag_node(
    *,
    node_id: str,
    receipt_path: Path,
    provider_run_root: Path,
    repo: Path,
    label: str = "tau-generic-provider-dag-node",
    max_attempts: int = 1,
    receipt_timeout_seconds: float = 120.0,
    herdr_workstation: Path | None = None,
    herdr_bin: str = "herdr",
    session: str | None = None,
    install_integrations: bool = False,
    cleanup_mode: str = "dry-run",
    work_order_path: Path | None = None,
) -> dict[str, Any]:
    """Run Tau's provider DAG as one generic DAG node and write its node receipt."""

    if not node_id.strip():
        raise RuntimeError("node_id must be a non-empty string")
    resolved_receipt = receipt_path.expanduser().resolve()
    resolved_provider_root = provider_run_root.expanduser().resolve()
    provider_receipt = run_provider_dag_poc(
        repo=repo,
        run_root=resolved_provider_root,
        label=label,
        max_attempts=max_attempts,
        receipt_timeout_seconds=receipt_timeout_seconds,
        herdr_workstation=herdr_workstation,
        herdr_bin=herdr_bin,
        session=session,
        install_integrations=install_integrations,
        cleanup_mode=cleanup_mode,
    )
    generic_receipt = build_generic_provider_node_receipt(
        node_id=node_id,
        provider_receipt=provider_receipt,
        work_order_path=work_order_path,
    )
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    resolved_receipt.write_text(
        json.dumps(generic_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return generic_receipt


def build_generic_provider_node_receipt(
    *,
    node_id: str,
    provider_receipt: dict[str, Any],
    work_order_path: Path | None = None,
) -> dict[str, Any]:
    """Translate a provider-DAG receipt into the generic DAG node contract."""

    provider_status = str(provider_receipt.get("status") or "UNKNOWN").upper()
    provider_verdict = str(provider_receipt.get("verdict") or "UNKNOWN").upper()
    provider_passed = provider_receipt.get("ok") is True and provider_status == "PASS"
    provider_binding = _provider_binding(
        node_id=node_id,
        provider_receipt=provider_receipt,
        work_order_path=work_order_path,
    )
    binding_passed = provider_binding["status"] != "BLOCKED"
    passed = provider_passed and binding_passed
    receipt = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": node_id,
        "status": "PASS" if passed else "BLOCKED",
        "verdict": "PASS" if passed else "BLOCKED",
        "provider_status": provider_status,
        "provider_verdict": provider_verdict,
        "mocked": False,
        "live": provider_receipt.get("live"),
        "provider_live": provider_receipt.get("live") is True,
        "artifacts": _provider_artifacts(provider_receipt),
        "commands_run": [
            "tau provider-dag-poc via tau generic-provider-dag-node",
        ],
        "handoff_summary": _handoff_summary(provider_status, provider_verdict),
        "provider_binding": provider_binding,
        "errors": _provider_errors(
            provider_receipt,
            provider_passed=provider_passed,
            binding_errors=provider_binding["errors"],
        ),
        "policy_exceptions": [],
        "timestamp": _utc_stamp(),
    }
    work_order_sha256 = _work_order_sha256(work_order_path)
    if work_order_sha256 is not None:
        receipt["work_order_path"] = str(work_order_path.expanduser().resolve())
        receipt["work_order_sha256"] = work_order_sha256
    if provider_binding["status"] == "PASS":
        for key in (
            "dag_id",
            "goal_hash",
            "attempt",
            "workspace_id",
            "pane_id",
            "terminal_id",
            "visible_log_path",
            "visible_log_sha256",
        ):
            receipt[key] = provider_binding[key]
    return receipt


def _provider_artifacts(provider_receipt: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for key in (
        "run_dir",
        "runtime_manifest",
        "events_jsonl",
        "dag_spec",
        "provider_readiness_receipt",
        "herdr_cleanup_receipt",
        "orchestration_evidence_receipt",
    ):
        value = provider_receipt.get(key)
        if isinstance(value, str) and value:
            artifacts.append({"kind": key, "path": value})
    return artifacts


def _provider_errors(
    provider_receipt: dict[str, Any],
    *,
    provider_passed: bool,
    binding_errors: list[str],
) -> list[str]:
    if provider_passed and not binding_errors:
        return []
    errors = provider_receipt.get("errors")
    result = [str(error) for error in errors] if isinstance(errors, list) else []
    provider_verdict = str(provider_receipt.get("verdict") or "UNKNOWN").upper()
    if not provider_passed and provider_verdict and provider_verdict != "PASS":
        result.append(f"provider DAG verdict: {provider_verdict}")
    result.extend(binding_errors)
    return result or ["provider DAG did not pass"]


def _provider_binding(
    *,
    node_id: str,
    provider_receipt: dict[str, Any],
    work_order_path: Path | None,
) -> dict[str, Any]:
    """Bind a canonical provider work order to visible Herdr/provider evidence."""

    base = {
        "schema": "tau.provider_dag_node_binding.v1",
        "status": "UNBOUND",
        "work_order_schema": None,
        "work_order_path": None,
        "work_order_sha256": None,
        "dag_id": None,
        "goal_hash": None,
        "node_id": node_id,
        "agent": None,
        "attempt": None,
        "max_attempts": None,
        "target_repo": None,
        "scratch_worktree": None,
        "receipt_path": None,
        "workspace_id": None,
        "pane_id": None,
        "terminal_id": None,
        "visible_log_path": None,
        "visible_log_sha256": None,
        "errors": [],
    }
    if work_order_path is None:
        return base

    resolved = work_order_path.expanduser().resolve()
    base["work_order_path"] = str(resolved)
    base["work_order_sha256"] = _work_order_sha256(work_order_path)
    try:
        work_order = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        base["status"] = "BLOCKED"
        base["errors"] = [f"work_order_unreadable: {exc}"]
        return base

    if not isinstance(work_order, dict):
        base["status"] = "BLOCKED"
        base["errors"] = ["work_order_not_object"]
        return base

    schema = work_order.get("schema")
    base["work_order_schema"] = schema
    if schema != PROVIDER_DAG_WORK_ORDER_SCHEMA:
        base["status"] = "LEGACY_UNBOUND"
        return base

    errors: list[str] = []
    dag_id = _string(work_order.get("dag_id"))
    goal_hash = _nested_string(work_order, "goal", "goal_hash")
    work_order_node_id = _nested_string(work_order, "node", "node_id")
    agent = _nested_string(work_order, "node", "agent")
    attempt = _nested_int(work_order, "node", "attempt")
    max_attempts = _nested_int(work_order, "node", "max_attempts")
    target_repo = _nested_string(work_order, "target", "repo")
    scratch_worktree = _nested_string(work_order, "target", "scratch_worktree")
    workspace_id = _nested_string(work_order, "herdr", "workspace_id")
    pane_id = _nested_string(work_order, "herdr", "pane_id")
    terminal_id = _nested_string(work_order, "herdr", "terminal_id")
    receipt_path = _string(work_order.get("receipt_path"))

    base.update(
        {
            "dag_id": dag_id,
            "goal_hash": goal_hash,
            "node_id": work_order_node_id or node_id,
            "agent": agent,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "target_repo": target_repo,
            "scratch_worktree": scratch_worktree,
            "receipt_path": receipt_path,
            "workspace_id": workspace_id,
            "pane_id": pane_id,
            "terminal_id": terminal_id,
        }
    )
    if not dag_id:
        errors.append("work_order_missing_dag_id")
    if not goal_hash:
        errors.append("work_order_missing_goal_hash")
    if work_order_node_id != node_id:
        errors.append(
            f"work_order_node_id_mismatch: expected {node_id!r}, got {work_order_node_id!r}"
        )
    if attempt is None:
        errors.append("work_order_missing_attempt")
    if max_attempts is None:
        errors.append("work_order_missing_max_attempts")
    elif max_attempts < 1:
        errors.append("work_order_invalid_max_attempts")
    if not agent:
        errors.append("work_order_missing_agent")
    if not target_repo:
        errors.append("work_order_missing_target_repo")
    if not scratch_worktree:
        errors.append("work_order_missing_scratch_worktree")
    if not receipt_path:
        errors.append("work_order_missing_receipt_path")
    for field_name, value in (
        ("allowed_paths", _target_allowed_paths(work_order)),
        ("required_evidence", work_order.get("required_evidence")),
        ("forbidden_actions", work_order.get("forbidden_actions")),
    ):
        if not isinstance(value, list):
            errors.append(f"work_order_missing_{field_name}")
    declared_work_order_sha256 = _string(work_order.get("work_order_sha256"))
    if not declared_work_order_sha256:
        errors.append("work_order_missing_work_order_sha256")
    elif declared_work_order_sha256 != _canonical_work_order_payload_sha256(work_order):
        errors.append("work_order_sha256_mismatch")
    for field_name, value in (
        ("workspace_id", workspace_id),
        ("pane_id", pane_id),
        ("terminal_id", terminal_id),
    ):
        if not value:
            errors.append(f"work_order_missing_{field_name}")

    matching_record = _matching_herdr_record(
        provider_receipt=provider_receipt,
        workspace_id=workspace_id,
        pane_id=pane_id,
        terminal_id=terminal_id,
    )
    if matching_record is None:
        errors.append("provider_receipt_missing_matching_herdr_record")
    else:
        visible_log_path = _string(matching_record.get("visible_log_path"))
        if not visible_log_path:
            errors.append("provider_receipt_missing_visible_log_path")
        else:
            visible_log_sha256 = _file_sha256(Path(visible_log_path))
            if visible_log_sha256 is None:
                errors.append("provider_receipt_visible_log_unreadable")
            base["visible_log_path"] = visible_log_path
            base["visible_log_sha256"] = visible_log_sha256

    base["status"] = "BLOCKED" if errors else "PASS"
    base["errors"] = errors
    return base


def _matching_herdr_record(
    *,
    provider_receipt: dict[str, Any],
    workspace_id: str | None,
    pane_id: str | None,
    terminal_id: str | None,
) -> dict[str, Any] | None:
    for collection_key in ("provider_sessions", "visible_subagents"):
        collection = provider_receipt.get(collection_key)
        if not isinstance(collection, dict):
            continue
        for record in collection.values():
            if not isinstance(record, dict):
                continue
            if (
                record.get("workspace_id") == workspace_id
                and record.get("pane_id") == pane_id
                and record.get("terminal_id") == terminal_id
            ):
                return record
    return None


def _target_allowed_paths(work_order: dict[str, Any]) -> Any:
    target = work_order.get("target")
    if not isinstance(target, dict):
        return None
    return target.get("allowed_paths")


def _nested_string(data: dict[str, Any], parent: str, child: str) -> str | None:
    parent_value = data.get(parent)
    if not isinstance(parent_value, dict):
        return None
    return _string(parent_value.get(child))


def _nested_int(data: dict[str, Any], parent: str, child: str) -> int | None:
    parent_value = data.get(parent)
    if not isinstance(parent_value, dict):
        return None
    value = parent_value.get(child)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _handoff_summary(provider_status: str, provider_verdict: str) -> str:
    if provider_status == "PASS" and provider_verdict == "PASS":
        return "Provider DAG subrun passed and was translated into a generic DAG node receipt."
    return (
        "Provider DAG subrun did not pass; generic DAG node is blocked "
        f"with provider status {provider_status} and verdict {provider_verdict}."
    )


def _work_order_sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        data = path.expanduser().resolve().read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str | None:
    try:
        data = path.expanduser().resolve().read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _canonical_work_order_payload_sha256(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("work_order_sha256", None)
    data = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
