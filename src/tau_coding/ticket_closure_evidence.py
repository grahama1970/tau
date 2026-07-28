"""Strict closure-evidence validation for Tau code-ticket subagents."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TICKET_CLOSURE_EVIDENCE_SCHEMA = "agent_skills.ticket_closure_evidence.v1"
TICKET_SUBAGENT_CLOSURE_PROOF_SCHEMA = "tau.ticket_subagent_closure_proof.v1"
BLOCKED_E2E_REQUIRED = "BLOCKED_E2E_REQUIRED"
CODE_RELATED_TASK_MARKERS = frozenset(
    {
        "bug",
        "bugfix",
        "code",
        "coding",
        "implementation",
        "refactor",
        "repair",
    }
)
PASSING_SUBAGENT_STATUSES = frozenset({"PASS", "COMPLETED"})


@dataclass(frozen=True, slots=True)
class TicketClosureValidationResult:
    ok: bool
    status: str
    blocker: str | None
    errors: tuple[str, ...]
    artifact_payload: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "blocker": self.blocker,
            "errors": list(self.errors),
            "artifact_payload": self.artifact_payload,
        }


def validate_code_ticket_closure_evidence(payload: dict[str, Any]) -> TicketClosureValidationResult:
    """Validate closure evidence required before accepting a code-ticket subagent result."""

    errors: list[str] = []
    if payload.get("schema") != TICKET_CLOSURE_EVIDENCE_SCHEMA:
        errors.append(
            f"schema must be {TICKET_CLOSURE_EVIDENCE_SCHEMA}; got {payload.get('schema')!r}"
        )

    unit = payload.get("unit")
    if not isinstance(unit, dict):
        errors.append("unit must be an object")
        unit = {}
    if unit.get("exit_code") != 0:
        errors.append("unit.exit_code must be 0")
    if not isinstance(unit.get("command"), str) or not unit.get("command", "").strip():
        errors.append("unit.command must be a non-empty string")

    e2e = payload.get("e2e")
    if not isinstance(e2e, dict):
        errors.append("e2e must be an object")
        return _blocked(errors)

    command = str(e2e.get("command") or "")
    if not command.strip():
        errors.append("e2e.command must be a non-empty string")
    if _is_deterministic_runner(command):
        errors.append("e2e.command must not be a deterministic test runner")
    if e2e.get("exit_code") != 0:
        errors.append("e2e.exit_code must be 0")
    if e2e.get("mocked") is not False:
        errors.append("e2e.mocked must be false")
    if e2e.get("live") is not True:
        errors.append("e2e.live must be true")

    artifact_value = e2e.get("artifact")
    artifact_payload: dict[str, Any] | None = None
    if not isinstance(artifact_value, str) or not artifact_value.strip():
        errors.append("e2e.artifact must be a non-empty path")
    else:
        artifact_path = Path(artifact_value).expanduser()
        if not artifact_path.exists() or artifact_path.stat().st_size == 0:
            errors.append(f"e2e.artifact must exist and be non-empty: {artifact_path}")
        else:
            try:
                loaded = json.loads(artifact_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"e2e.artifact must be readable JSON: {artifact_path}: {exc}")
            else:
                if not isinstance(loaded, dict):
                    errors.append(f"e2e.artifact must contain a JSON object: {artifact_path}")
                else:
                    artifact_payload = loaded
                    if loaded.get("mocked") is not False:
                        errors.append("e2e.artifact.mocked must be false")
                    if loaded.get("live") is not True:
                        errors.append("e2e.artifact.live must be true")

    if errors:
        return _blocked(errors, artifact_payload=artifact_payload)
    return TicketClosureValidationResult(
        ok=True,
        status="ACCEPTED",
        blocker=None,
        errors=(),
        artifact_payload=artifact_payload,
    )


def validate_subagent_code_ticket_closure(payload: Mapping[str, Any]) -> list[str]:
    """Return fail-closed errors for passing code-related subagent receipts."""

    if not _requires_code_ticket_closure(payload):
        return []

    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        return [
            f"{BLOCKED_E2E_REQUIRED}: code-related PASS receipts require evidence list "
            f"with {TICKET_CLOSURE_EVIDENCE_SCHEMA}"
        ]

    closure_results: list[TicketClosureValidationResult] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        if item.get("schema") != TICKET_CLOSURE_EVIDENCE_SCHEMA:
            continue
        closure_results.append(validate_code_ticket_closure_evidence(item))

    if any(result.ok for result in closure_results):
        return []
    if closure_results:
        joined = "; ".join(error for result in closure_results for error in result.errors)
        return [f"{BLOCKED_E2E_REQUIRED}: {joined}"]
    return [
        f"{BLOCKED_E2E_REQUIRED}: code-related PASS receipts require "
        f"{TICKET_CLOSURE_EVIDENCE_SCHEMA}"
    ]


def write_ticket_subagent_closure_proof(output: Path, *, allow_live_filesystem: bool) -> dict[str, Any]:
    """Write a live local proof for the code-ticket closure-evidence gate."""

    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required for live closure proof")

    resolved = output.expanduser().resolve()
    proof_dir = resolved.parent
    proof_dir.mkdir(parents=True, exist_ok=True)
    live_artifact = proof_dir / "live-e2e-artifact.json"
    run_id = f"ticket-closure-proof-{uuid.uuid4().hex}"
    live_artifact.write_text(
        json.dumps(
            {
                "schema": "tau.ticket_subagent_live_e2e_artifact.v1",
                "mocked": False,
                "live": True,
                "provider_live": False,
                "run_id": run_id,
                "observed_at_unix_ns": time.time_ns(),
                "filesystem_readback_required": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    unit_only = {
        "schema": TICKET_CLOSURE_EVIDENCE_SCHEMA,
        "issue": 183,
        "unit": {
            "command": "uv run pytest -q tests/test_ticket_closure_evidence.py",
            "exit_code": 0,
            "passed": 3,
        },
    }
    live_e2e = {
        "schema": TICKET_CLOSURE_EVIDENCE_SCHEMA,
        "issue": 183,
        "unit": {
            "command": "uv run pytest -q tests/test_ticket_closure_evidence.py",
            "exit_code": 0,
            "passed": 3,
        },
        "e2e": {
            "command": "tau ticket-subagent-closure-proof --allow-live-filesystem",
            "exit_code": 0,
            "mocked": False,
            "live": True,
            "artifact": str(live_artifact),
        },
    }

    rejected = validate_code_ticket_closure_evidence(unit_only)
    accepted = validate_code_ticket_closure_evidence(live_e2e)
    code_receipt_without_e2e_errors = validate_subagent_code_ticket_closure(
        {
            "context": {"code_related": True},
            "result": {"status": "PASS"},
            "evidence": [],
        }
    )
    code_receipt_with_e2e_errors = validate_subagent_code_ticket_closure(
        {
            "context": {"task_type": "code"},
            "result": {"status": "PASS"},
            "evidence": [live_e2e],
        }
    )
    non_code_receipt_without_e2e_errors = validate_subagent_code_ticket_closure(
        {
            "context": {"task_type": "research"},
            "result": {"status": "PASS"},
            "evidence": [],
        }
    )
    rejected_code_subagent_without_e2e = any(
        error.startswith(BLOCKED_E2E_REQUIRED)
        for error in code_receipt_without_e2e_errors
    )
    accepted_code_subagent_live_e2e = not code_receipt_with_e2e_errors
    accepted_non_code_without_e2e = not non_code_receipt_without_e2e_errors
    proof = {
        "schema": TICKET_SUBAGENT_CLOSURE_PROOF_SCHEMA,
        "status": (
            "PASS"
            if (
                not rejected.ok
                and accepted.ok
                and rejected_code_subagent_without_e2e
                and accepted_code_subagent_live_e2e
                and accepted_non_code_without_e2e
            )
            else "FAIL"
        ),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_id": run_id,
        "unit_only_result": rejected.as_dict(),
        "live_e2e_result": accepted.as_dict(),
        "rejected_unit_only": rejected.ok is False and rejected.blocker == BLOCKED_E2E_REQUIRED,
        "accepted_live_e2e": accepted.ok is True,
        "rejected_code_subagent_without_e2e": rejected_code_subagent_without_e2e,
        "accepted_code_subagent_live_e2e": accepted_code_subagent_live_e2e,
        "accepted_non_code_without_e2e": accepted_non_code_without_e2e,
        "code_receipt_without_e2e_errors": code_receipt_without_e2e_errors,
        "code_receipt_with_e2e_errors": code_receipt_with_e2e_errors,
        "non_code_receipt_without_e2e_errors": non_code_receipt_without_e2e_errors,
        "live_artifact": str(live_artifact),
        "proof_boundary": {
            "proves": [
                "Code-ticket closure evidence without an e2e section is rejected.",
                "Code-ticket closure evidence with a live non-mocked artifact read-back is accepted.",
                "Passing code-related subagent receipts are blocked without the closure evidence.",
                "Passing non-code subagent receipts are not forced through the code-ticket gate.",
            ],
            "does_not_prove": [
                "Provider semantic correctness.",
                "That historical receipts written before this gate carry code-ticket closure evidence.",
            ],
        },
    }
    resolved.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return proof


def _blocked(
    errors: list[str],
    *,
    artifact_payload: dict[str, Any] | None = None,
) -> TicketClosureValidationResult:
    return TicketClosureValidationResult(
        ok=False,
        status="BLOCKED",
        blocker=BLOCKED_E2E_REQUIRED,
        errors=tuple(errors),
        artifact_payload=artifact_payload,
    )


def _requires_code_ticket_closure(payload: Mapping[str, Any]) -> bool:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        return False
    status = str(result.get("status") or "").upper()
    if status not in PASSING_SUBAGENT_STATUSES:
        return False

    context = payload.get("context")
    if not isinstance(context, Mapping):
        context = {}
    if context.get("code_related") is True or result.get("code_related") is True:
        return True
    for value in (
        context.get("task_type"),
        context.get("ticket_type"),
        context.get("work_type"),
        result.get("task_type"),
        result.get("ticket_type"),
        result.get("work_type"),
    ):
        if isinstance(value, str) and value.strip().lower() in CODE_RELATED_TASK_MARKERS:
            return True
    return False


def _is_deterministic_runner(command: str) -> bool:
    lowered = command.lower()
    deterministic_markers = (
        "pytest",
        "unittest",
        "ruff check",
        "mypy",
        "py_compile",
        "npm run test",
        "vitest",
    )
    return any(marker in lowered for marker in deterministic_markers)
