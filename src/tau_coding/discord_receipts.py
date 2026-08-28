"""Typed Discord collaboration receipts for Tau DAG human decisions.

Discord is a collaboration surface, not proof that a repair is complete. These
helpers make that boundary explicit: status messages are read-only; only a
matching answer receipt can unblock a human decision, and it must bind to the
same question, run, node, and immutable goal hash.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DISCORD_HUMAN_QUESTION_SCHEMA = "tau.discord_human_question.v1"
DISCORD_HUMAN_ANSWER_SCHEMA = "tau.discord_human_answer.v1"
DISCORD_STATUS_RESPONSE_SCHEMA = "tau.discord_status_response_receipt.v1"
DISCORD_ANSWER_VALIDATION_SCHEMA = "tau.discord_human_answer_validation.v1"


def write_human_question_receipt(
    *,
    output_path: Path,
    question_id: str,
    run_id: str,
    node_id: str,
    goal_hash: str,
    question: str,
    allowed_answers: list[str],
) -> dict[str, Any]:
    """Write a typed question receipt that can later validate one answer."""

    _require_nonempty("question_id", question_id)
    _require_nonempty("run_id", run_id)
    _require_nonempty("node_id", node_id)
    _require_nonempty("goal_hash", goal_hash)
    _require_nonempty("question", question)
    normalized_answers = [item.strip() for item in allowed_answers if item.strip()]
    if not normalized_answers:
        raise RuntimeError("allowed_answers must contain at least one non-empty answer")
    payload = {
        "schema": DISCORD_HUMAN_QUESTION_SCHEMA,
        "ok": True,
        "status": "QUESTION_PREPARED",
        "mocked": False,
        "live": False,
        "question_id": question_id,
        "run_id": run_id,
        "node_id": node_id,
        "goal_hash": goal_hash,
        "question": question,
        "allowed_answers": normalized_answers,
        "answer_receipt_schema": DISCORD_HUMAN_ANSWER_SCHEMA,
        "status_receipt_schema": DISCORD_STATUS_RESPONSE_SCHEMA,
        "proof_boundary": "Prepared Discord collaboration only; not repair proof.",
        "timestamp": _utc_stamp(),
    }
    return _write_json(output_path, payload)


def write_status_response_receipt(
    *,
    output_path: Path,
    question_id: str,
    run_id: str,
    node_id: str,
    goal_hash: str,
    message: str,
) -> dict[str, Any]:
    """Write a read-only Discord status receipt that cannot unblock a decision."""

    payload = {
        "schema": DISCORD_STATUS_RESPONSE_SCHEMA,
        "ok": True,
        "status": "SENT_READ_ONLY_STATUS",
        "mocked": False,
        "live": False,
        "question_id": question_id,
        "run_id": run_id,
        "node_id": node_id,
        "goal_hash": goal_hash,
        "message": message,
        "unblocks_decision": False,
        "proof_boundary": "Status delivery informs humans; it cannot satisfy repair proof.",
        "timestamp": _utc_stamp(),
    }
    return _write_json(output_path, payload)


def write_human_answer_receipt(
    *,
    output_path: Path,
    question_id: str,
    run_id: str,
    node_id: str,
    goal_hash: str,
    answer: str,
    answered_by: str,
) -> dict[str, Any]:
    """Write a candidate human answer receipt for later validation."""

    payload = {
        "schema": DISCORD_HUMAN_ANSWER_SCHEMA,
        "ok": True,
        "status": "ANSWER_RECORDED",
        "mocked": False,
        "live": False,
        "question_id": question_id,
        "run_id": run_id,
        "node_id": node_id,
        "goal_hash": goal_hash,
        "answer": answer,
        "answered_by": answered_by,
        "proof_boundary": "Human answer can unblock a decision only after typed validation.",
        "timestamp": _utc_stamp(),
    }
    return _write_json(output_path, payload)


def validate_human_answer_receipts(
    *,
    question_receipt_path: Path,
    answer_receipt_path: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Validate that a Discord answer matches its exact Tau DAG question."""

    question = _read_json(question_receipt_path)
    answer = _read_json(answer_receipt_path)
    errors: list[str] = []
    if question.get("schema") != DISCORD_HUMAN_QUESTION_SCHEMA:
        errors.append("question_schema_invalid")
    if answer.get("schema") != DISCORD_HUMAN_ANSWER_SCHEMA:
        errors.append("answer_schema_invalid")
    for key in ("question_id", "run_id", "node_id", "goal_hash"):
        if question.get(key) != answer.get(key):
            errors.append(f"{key}_mismatch")
    allowed = question.get("allowed_answers")
    allowed_answers = [str(item) for item in allowed] if isinstance(allowed, list) else []
    if str(answer.get("answer") or "") not in allowed_answers:
        errors.append("answer_not_allowed")
    payload = {
        "schema": DISCORD_ANSWER_VALIDATION_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": False,
        "question_receipt": str(question_receipt_path),
        "answer_receipt": str(answer_receipt_path),
        "question_id": question.get("question_id"),
        "run_id": question.get("run_id"),
        "node_id": question.get("node_id"),
        "goal_hash": question.get("goal_hash"),
        "answer": answer.get("answer"),
        "unblocks_decision": not errors,
        "errors": errors,
        "proof_boundary": (
            "Validates typed human intent only; repair still needs normal evidence gates."
        ),
        "timestamp": _utc_stamp(),
    }
    if output_path is not None:
        return _write_json(output_path, payload)
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"receipt_unreadable:{path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"receipt_root_not_object:{path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["receipt_path"] = str(resolved)
    payload["receipt_sha256"] = _payload_sha256(payload)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _payload_sha256(payload: dict[str, Any]) -> str:
    basis = {key: value for key, value in payload.items() if key != "receipt_sha256"}
    data = json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _require_nonempty(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{label} must be a non-empty string")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
