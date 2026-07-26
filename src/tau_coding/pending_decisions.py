"""Detect Tau runs waiting for explicit human decisions.

Inputs are the local Tau run registry and durable workflow approval-gate
receipts. Outputs are small inbox records for TUI and CLI presentation. Missing
or malformed run artifacts fail closed by omitting operational claims rather
than inferring that a run is healthy.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.paths import TauPaths


@dataclass(frozen=True, slots=True)
class PendingDecision:
    """A human action needed before a Tau run can continue."""

    run_id: str
    workflow_id: str
    run_dir: Path
    node_id: str
    transaction_id: str
    target_id: str
    requested_action: str
    required_action: str
    command: str
    approval_packet: Path
    receipt_path: Path

    @property
    def key(self) -> str:
        """Return a stable identity for notification dedupe."""
        return f"{self.run_dir}:{self.node_id}:{self.transaction_id}:{self.requested_action}"


def collect_pending_decisions(*, limit: int | None = None) -> tuple[PendingDecision, ...]:
    """Return current approval waits from the newest registered Tau runs."""
    decisions: list[PendingDecision] = []
    for entry in _load_registered_runs():
        run_dir_value = entry.get("run_dir")
        if not isinstance(run_dir_value, str) or not run_dir_value:
            continue
        run_dir = Path(run_dir_value).expanduser().resolve()
        if not (run_dir / "dag-run.sqlite3").exists():
            continue
        workflow_id = _string(entry.get("workflow_id"), fallback="UNKNOWN")
        run_id = _string(entry.get("run_id"), fallback=run_dir.name)
        decisions.extend(
            _pending_decisions_for_run(
                run_dir=run_dir,
                run_id=run_id,
                workflow_id=workflow_id,
            )
        )
        if limit is not None and len(decisions) >= limit:
            return tuple(decisions[:limit])
    return tuple(decisions)


def pending_decision_lines(decisions: tuple[PendingDecision, ...]) -> tuple[str, ...]:
    """Render compact inbox lines suitable for the prompt-region TUI widget."""
    if not decisions:
        return ()
    lines = ["Pending human decisions"]
    for decision in decisions:
        lines.append(
            f"- {decision.workflow_id}/{decision.node_id}: {decision.required_action}"
        )
        lines.append(f"  target: {decision.target_id}")
        lines.append(f"  command: {decision.command}")
    return tuple(lines)


def pending_decision_report(decisions: tuple[PendingDecision, ...]) -> str:
    """Render a transcript-friendly inbox report."""
    if not decisions:
        return "No pending human decisions."
    return "\n".join(pending_decision_lines(decisions))


def _pending_decisions_for_run(
    *,
    run_dir: Path,
    run_id: str,
    workflow_id: str,
) -> list[PendingDecision]:
    workflow_approval = _load_json(run_dir / "receipts" / "workflow-approval.json")
    if workflow_approval.get("ok") is True:
        return []
    decisions: list[PendingDecision] = []
    for receipt_path in sorted((run_dir / "transactions").glob("*/transaction-receipt.json")):
        receipt = _load_json(receipt_path)
        state = receipt.get("transaction_state", receipt.get("state"))
        if state != "APPROVAL_REQUIRED":
            continue
        gate_path = receipt_path.parent / "approval-gate-receipt.json"
        gate = _load_json(gate_path)
        if gate.get("schema") != "tau.approval_gate_receipt.v1":
            continue
        if gate.get("status") == "PASS" or gate.get("approved") is True:
            continue
        requested_action = _string(gate.get("requested_action"), fallback="human_approval")
        target = gate.get("expected_target")
        target_id = "UNKNOWN"
        transaction_id = _string(receipt.get("transaction_id"), fallback=receipt_path.parent.name)
        if isinstance(target, dict):
            target_id = _string(target.get("id"), fallback=target_id)
            transaction_id = _string(target.get("transaction_id"), fallback=transaction_id)
        node_id = _string(receipt.get("node_id"), fallback=receipt_path.parent.name)
        approval_packet = Path(
            _string(gate.get("approval_packet"), fallback=str(run_dir / "input" / "approval.json"))
        )
        decisions.append(
            PendingDecision(
                run_id=run_id,
                workflow_id=workflow_id,
                run_dir=run_dir,
                node_id=node_id,
                transaction_id=transaction_id,
                target_id=target_id,
                requested_action=requested_action,
                required_action=f"Provide human approval for {requested_action}",
                command=(
                    f"tau workflows approve {run_dir} "
                    "--approval-packet <approval.json>"
                ),
                approval_packet=approval_packet,
                receipt_path=gate_path,
            )
        )
    return decisions


def _run_registry_path() -> Path:
    override = os.environ.get("TAU_RUN_REGISTRY")
    if override:
        return Path(override).expanduser().resolve()
    return (TauPaths().home / "runs.json").expanduser().resolve()


def _load_registered_runs() -> list[dict[str, Any]]:
    path = _run_registry_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"run registry is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"run registry must contain a JSON object: {path}")
    runs = payload.get("runs", [])
    if not isinstance(runs, list):
        raise RuntimeError("run registry runs must be a list")
    return [dict(item) for item in runs if isinstance(item, dict)]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"pending decision artifact is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"pending decision artifact must contain a JSON object: {path}")
    return payload


def _string(value: object, *, fallback: str) -> str:
    if isinstance(value, str) and value:
        return value
    return fallback
