"""Adversarial checks for Tau/agent-skills composition boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.code_runner_skill_adapter import write_code_runner_skill_adapter_receipt
from tau_coding.debugger_skill_adapter import write_debugger_skill_adapter_receipt
from tau_coding.evidence_case_skill_adapter import write_evidence_case_skill_adapter_receipt
from tau_coding.research_skill_adapter import write_research_skill_adapter_receipt
from tau_coding.review_code_skill_adapter import write_review_code_skill_adapter_receipt
from tau_coding.skill_invocation import (
    SKILL_INVOCATION_REQUEST_SCHEMA,
    write_skill_invocation_receipt,
)

SKILL_COMPOSITION_REDTEAM_RECEIPT_SCHEMA = "tau.skill_composition_redteam_receipt.v1"
GOAL_HASH = "sha256:skill-composition-goal"
REQUIRED_UNPROVEN_CLAIMS = [
    "Live skill execution.",
    "Provider/model semantic quality.",
    "Exhaustive skill attack coverage.",
    "Future route correctness.",
    "Skill output correctness without Tau adapter validation.",
]


def run_skill_composition_redteam(*, run_dir: Path) -> dict[str, Any]:
    """Run deterministic malicious skill-artifact fixtures.

    The suite proves only that Tau does not blindly accept malformed or
    policy-incompatible skill artifacts. It does not invoke external skills.
    """

    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    attempts = [
        _attempt_debugger_missing_goal_hash(resolved_run_dir),
        _attempt_review_pass_with_blocking_finding(resolved_run_dir),
        _attempt_code_runner_patch_outside_allowlist(resolved_run_dir),
        _attempt_research_without_query_safety(resolved_run_dir),
        _attempt_evidence_case_boundary_mismatch(resolved_run_dir),
        _attempt_skill_invocation_artifact_outside_repo(resolved_run_dir),
        _attempt_skill_invocation_mocked_high_stakes(resolved_run_dir),
    ]
    ok = all(attempt["status"] == "PASS" for attempt in attempts)
    receipt = {
        "schema": SKILL_COMPOSITION_REDTEAM_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "attempt_count": len(attempts),
        "passed_attempt_count": sum(1 for attempt in attempts if attempt["status"] == "PASS"),
        "attempts": attempts,
        "receipt_path": str(resolved_run_dir / "skill-composition-redteam-receipt.json"),
        "proof_scope": {
            "proves": [
                "Tau exercised deterministic malicious skill-artifact fixtures.",
                "Each attempt passed only if Tau produced the expected fail-closed result.",
                "No external skill, provider, Memory, research, GitHub, or browser call was made.",
            ],
            "does_not_prove": [
                *REQUIRED_UNPROVEN_CLAIMS,
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_run_dir / "skill-composition-redteam-receipt.json", receipt)
    return receipt


def _attempt_debugger_missing_goal_hash(run_dir: Path) -> dict[str, Any]:
    proof_path = run_dir / "debugger-proof-missing-goal.json"
    _write_json(
        proof_path,
        {
            "schema": "debugger.proof.v1",
            "target_command": "python3 example.py",
            "adapter_label": "python",
            "breakpoints": [{"file": "example.py", "line": 1}],
            "stopped_frame": {"file": "example.py", "line": 1},
            "variables": [],
        },
    )
    receipt = write_debugger_skill_adapter_receipt(
        proof_path=proof_path,
        output_path=run_dir / "debugger-adapter-receipt.json",
        debug_session_output_path=run_dir / "debug-session-receipt.json",
        repo_root=run_dir,
        expected_goal_hash=GOAL_HASH,
        zero_trust=True,
    )
    return _attempt_summary(
        "debugger_proof_missing_goal_hash",
        receipt,
        expected_error="goal_hash is required in zero-trust mode",
        course_correction_required=True,
    )


def _attempt_review_pass_with_blocking_finding(run_dir: Path) -> dict[str, Any]:
    review_path = run_dir / "review-pass-with-blocker.json"
    _write_json(
        review_path,
        {
            "schema": "review_code.result.v1",
            "goal_hash": GOAL_HASH,
            "verdict": "PASS",
            "reviewer": "review-code",
            "findings": [
                {
                    "severity": "P0",
                    "file": "src/example.py",
                    "claim": "PASS hides a blocking issue.",
                    "evidence": [],
                    "required_action": "block",
                }
            ],
        },
    )
    receipt = write_review_code_skill_adapter_receipt(
        review_path=review_path,
        output_path=run_dir / "review-code-adapter-receipt.json",
        repo_root=run_dir,
        expected_goal_hash=GOAL_HASH,
    )
    return _attempt_summary(
        "review_code_pass_with_blocking_finding",
        receipt,
        expected_error="review findings receipt blocked",
        course_correction_required=True,
    )


def _attempt_code_runner_patch_outside_allowlist(run_dir: Path) -> dict[str, Any]:
    patch_path = run_dir / "code-patch.json"
    dod_path = run_dir / "dod.json"
    log_path = run_dir / "test.log"
    _write_json(
        patch_path,
        {
            "schema": "tau.code_patch.v1",
            "goal_hash": GOAL_HASH,
            "target_file": "forbidden.py",
            "edits": [],
        },
    )
    _write_json(dod_path, {"schema": "code_runner.dod.v1", "status": "PASS"})
    log_path.write_text("tests passed claim\n", encoding="utf-8")
    result_path = run_dir / "code-runner-result.json"
    _write_json(
        result_path,
        {
            "schema": "code_runner.result.v1",
            "status": "PASS",
            "goal_hash": GOAL_HASH,
            "allowed_paths": ["src/*.py"],
            "patch_artifact": str(patch_path),
            "dod_artifact": str(dod_path),
            "test_log_artifact": str(log_path),
        },
    )
    receipt = write_code_runner_skill_adapter_receipt(
        result_path=result_path,
        output_path=run_dir / "code-runner-adapter-receipt.json",
        repo_root=run_dir,
        expected_goal_hash=GOAL_HASH,
    )
    return _attempt_summary(
        "code_runner_patch_outside_allowlist",
        receipt,
        expected_error="patch target_file is outside allowed_paths",
        course_correction_required=True,
    )


def _attempt_research_without_query_safety(run_dir: Path) -> dict[str, Any]:
    report_path = run_dir / "dogpile-report.json"
    _write_json(
        report_path,
        {
            "schema": "dogpile.report.v1",
            "query": "adaptive DAGs",
            "sources": [
                {
                    "title": "Example",
                    "url": "https://example.com",
                    "claims_supported": ["example claim"],
                }
            ],
        },
    )
    receipt = write_research_skill_adapter_receipt(
        report_path=report_path,
        query_safety_receipt_path=run_dir / "missing-query-safety.json",
        output_path=run_dir / "research-adapter-receipt.json",
        repo_root=run_dir,
    )
    return _attempt_summary(
        "dogpile_report_without_query_safety_receipt",
        receipt,
        expected_error="query safety receipt is unreadable",
        course_correction_required=True,
    )


def _attempt_evidence_case_boundary_mismatch(run_dir: Path) -> dict[str, Any]:
    support_path = run_dir / "support.json"
    _write_json(support_path, {"schema": "support.v1", "status": "PASS"})
    case_path = run_dir / "evidence-case-result.json"
    _write_json(
        case_path,
        {
            "schema": "create_evidence_case.result.v1",
            "goal_hash": GOAL_HASH,
            "question": "Does this evidence support the goal?",
            "claim": "Claim is supported.",
            "support_artifacts": [{"path": str(support_path), "schema": "support.v1"}],
            "data_boundary": {"boundary_id": "public"},
        },
    )
    receipt = write_evidence_case_skill_adapter_receipt(
        case_path=case_path,
        output_path=run_dir / "evidence-case-adapter-receipt.json",
        repo_root=run_dir,
        expected_goal_hash=GOAL_HASH,
        data_boundary={"boundary_id": "controlled"},
    )
    return _attempt_summary(
        "create_evidence_case_boundary_mismatch",
        receipt,
        expected_error="data_boundary mismatches create-evidence-case artifact",
        course_correction_required=True,
    )


def _attempt_skill_invocation_artifact_outside_repo(run_dir: Path) -> dict[str, Any]:
    outside = run_dir.parent / "outside-skill-artifact.txt"
    outside.write_text("outside\n", encoding="utf-8")
    request_path = run_dir / "skill-invocation-outside-artifact.json"
    _write_json(
        request_path,
        _skill_invocation_request(
            mode="ingest_existing",
            artifacts=[{"path": str(outside), "schema": "debugger.proof.v1"}],
        ),
    )
    receipt = write_skill_invocation_receipt(
        request_path=request_path,
        output_path=run_dir / "skill-invocation-outside-artifact-receipt.json",
        repo_root=run_dir,
    )
    return _attempt_summary(
        "skill_invocation_artifact_outside_repo",
        receipt,
        expected_error="escapes repo root",
        course_correction_required=False,
    )


def _attempt_skill_invocation_mocked_high_stakes(run_dir: Path) -> dict[str, Any]:
    request_path = run_dir / "skill-invocation-mocked-high-stakes.json"
    _write_json(
        request_path,
        _skill_invocation_request(
            mode="dry_run",
            command=["echo", "mocked"],
            zero_trust=True,
            live_required=True,
            mocked=True,
            live=False,
        ),
    )
    receipt = write_skill_invocation_receipt(
        request_path=request_path,
        output_path=run_dir / "skill-invocation-mocked-high-stakes-receipt.json",
        repo_root=run_dir,
    )
    return _attempt_summary(
        "skill_invocation_mocked_but_high_stakes_requires_live",
        receipt,
        expected_error="live execution is required when live_required is true",
        course_correction_required=False,
    )


def _skill_invocation_request(**updates: Any) -> dict[str, Any]:
    payload = {
        "schema": SKILL_INVOCATION_REQUEST_SCHEMA,
        "skill": "debugger",
        "capability": "debug_runtime_state",
        "mode": "dry_run",
        "run_id": "skill-redteam",
        "dag_id": "skill-redteam-dag",
        "node_id": "skill-redteam-node",
        "goal_hash": GOAL_HASH,
        "work_order_sha256": "sha256:work-order",
        "command": ["echo", "debug"],
        "artifacts": [],
        "mocked": False,
        "live": False,
        "provider_live": False,
    }
    payload.update(updates)
    return payload


def _attempt_summary(
    name: str,
    receipt: dict[str, Any],
    *,
    expected_error: str,
    course_correction_required: bool,
) -> dict[str, Any]:
    errors = [str(error) for error in receipt.get("errors", [])]
    expected_error_seen = any(expected_error in error for error in errors)
    course_correction_present = isinstance(receipt.get("course_correction"), dict)
    passed = (
        receipt.get("status") == "BLOCKED"
        and receipt.get("ok") is False
        and expected_error_seen
        and (course_correction_present or not course_correction_required)
    )
    return {
        "name": name,
        "status": "PASS" if passed else "BLOCKED",
        "expected_fail_closed": True,
        "observed_receipt_schema": receipt.get("schema"),
        "observed_status": receipt.get("status"),
        "observed_ok": receipt.get("ok"),
        "expected_error": expected_error,
        "expected_error_seen": expected_error_seen,
        "course_correction_required": course_correction_required,
        "course_correction_present": course_correction_present,
        "errors": errors,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
