"""Fail-closed validation for Scillm-backed subagent loop receipts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCILLM_SUBAGENT_GATE_SCHEMA = "tau.scillm_subagent_gate.v1"
BLOCKED_SUBSTRATE_KINDS = {"blocked_substrate"}
BLOCKED_SUBSTRATE_REASONS = {
    "scillm_opencode_timeout",
    "timeout",
    "command_timeout",
    "delegate_timeout",
}


@dataclass(frozen=True, slots=True)
class ScillmSubagentGateResult:
    """Validation result for one Scillm subagent loop summary."""

    ok: bool
    summary: str
    checked_receipts: tuple[str, ...]
    errors: tuple[str, ...]
    blocked_substrate_receipts: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCILLM_SUBAGENT_GATE_SCHEMA,
            "ok": self.ok,
            "summary": self.summary,
            "checked_receipts": list(self.checked_receipts),
            "blocked_substrate_receipts": list(self.blocked_substrate_receipts),
            "errors": list(self.errors),
        }


def validate_scillm_subagent_loop_summary(summary_path: Path) -> ScillmSubagentGateResult:
    """Reject summaries that advance from timed-out Scillm/OpenCode delegate output."""

    resolved = summary_path.expanduser().resolve()
    summary = _load_json_object(resolved, label="summary")
    out_dir = _summary_out_dir(summary, resolved)
    checked: list[str] = []
    errors: list[str] = []
    blocked_receipts: list[str] = []
    completed_accepting_review = False

    events = summary.get("events")
    if not isinstance(events, list):
        errors.append("summary.events must be a list")
        events = []

    summary_attempts: dict[int, dict[str, Any]] = {}
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            errors.append(f"summary.events[{index}] must be an object")
            continue
        attempt = event.get("attempt")
        if not isinstance(attempt, int) or attempt < 1:
            errors.append(f"summary.events[{index}].attempt must be a positive integer")
            continue
        summary_attempts[attempt] = event
    receipt_attempts = _receipt_attempts(out_dir)
    for attempt in sorted(set(summary_attempts) | receipt_attempts):
        event = summary_attempts.get(attempt)
        for role in ("coder", "reviewer"):
            receipt_path = out_dir / f"attempt_{attempt:03d}" / f"{role}_tau_subagent_receipt.json"
            if not receipt_path.exists():
                continue
            receipt = _load_json_object(receipt_path, label=f"{role} receipt")
            checked.append(str(receipt_path))
            gate = _receipt_gate(receipt)
            if gate["blocked"]:
                blocked_receipts.append(str(receipt_path))
            if role == "reviewer" and gate["completed"] and _parsed_reviewer_accepts(gate["parsed"]):
                completed_accepting_review = True
        if event is not None and event.get("accepted") is True:
            reviewer_receipt = out_dir / f"attempt_{attempt:03d}" / "reviewer_tau_subagent_receipt.json"
            if not reviewer_receipt.exists():
                errors.append(
                    f"summary attempt {attempt} accepted=true but reviewer receipt is missing"
                )
            else:
                reviewer = _load_json_object(reviewer_receipt, label="reviewer receipt")
                reviewer_gate = _receipt_gate(reviewer)
                if not (
                    reviewer_gate["completed"]
                    and _parsed_reviewer_accepts(reviewer_gate["parsed"])
                ):
                    errors.append(
                        "summary accepted=true requires completed reviewer substrate plus "
                        f"accepted parsed JSON: {reviewer_receipt}"
                    )

    final_status = summary.get("final_status")
    if final_status in {"reviewer_passed", "PASS", "pass", "accepted"}:
        if not completed_accepting_review:
            errors.append(
                f"summary.final_status {final_status!r} requires a completed reviewer receipt "
                "with parsed accepted=true"
            )

    return ScillmSubagentGateResult(
        ok=not errors,
        summary=str(resolved),
        checked_receipts=tuple(checked),
        errors=tuple(errors),
        blocked_substrate_receipts=tuple(blocked_receipts),
    )


def _receipt_attempts(out_dir: Path) -> set[int]:
    attempts: set[int] = set()
    if not out_dir.exists() or not out_dir.is_dir():
        return attempts
    for path in out_dir.glob("attempt_*/**/*_tau_subagent_receipt.json"):
        try:
            attempts.add(int(path.parent.name.removeprefix("attempt_")))
        except ValueError:
            continue
    return attempts


def _summary_out_dir(summary: dict[str, Any], summary_path: Path) -> Path:
    out_dir = summary.get("out_dir")
    if isinstance(out_dir, str) and out_dir:
        return Path(out_dir).expanduser().resolve()
    return summary_path.parent


def _receipt_gate(receipt: dict[str, Any]) -> dict[str, Any]:
    result = receipt.get("result")
    if not isinstance(result, dict):
        return {
            "completed": False,
            "blocked": True,
            "parsed": {},
        }
    status = str(result.get("status") or "")
    kind = str(result.get("kind") or "")
    reason = str(result.get("reason") or "")
    parsed = result.get("parsed")
    blocked = (
        status.upper() != "COMPLETED"
        or kind in BLOCKED_SUBSTRATE_KINDS
        or reason in BLOCKED_SUBSTRATE_REASONS
    )
    return {
        "completed": status.upper() == "COMPLETED" and not blocked,
        "blocked": blocked,
        "parsed": parsed if isinstance(parsed, dict) else {},
    }


def _parsed_claims_success(parsed: dict[str, Any]) -> bool:
    return (
        parsed.get("accepted") is True
        or parsed.get("verified") is True
        or str(parsed.get("verdict") or "").lower() == "pass"
        or str(parsed.get("status") or "").lower() in {"ok", "pass", "accepted"}
    )


def _parsed_reviewer_accepts(parsed: Any) -> bool:
    if not isinstance(parsed, dict):
        return False
    return (
        parsed.get("accepted") is True
        and parsed.get("verified") is True
        and str(parsed.get("verdict") or "").lower() == "pass"
    )


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return payload
