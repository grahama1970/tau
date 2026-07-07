#!/usr/bin/env python3
"""Run Tau coding capability sanity checks and write one receipt."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "tau.coding_capability_sanity_receipt.v1"
CHECK_SCHEMA = "tau.coding_capability_sanity_check.v1"


@dataclass(frozen=True)
class Check:
    check_id: str
    command: list[str]
    purpose: str
    expected_exit_code: int = 0
    timeout_seconds: int = 120
    output_artifact: Path | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, default=Path("/tmp/tau-coding-capability-sanity"))
    parser.add_argument("--uv-bin", default="uv")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    checks = build_checks(repo=repo, run_dir=run_dir, uv_bin=args.uv_bin)
    records = [run_check(check, repo=repo) for check in checks]
    receipt = build_receipt(repo=repo, run_dir=run_dir, records=records)
    receipt_path = run_dir / "coding-capability-sanity-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["ok"] is True else 1


def build_checks(*, repo: Path, run_dir: Path, uv_bin: str) -> list[Check]:
    examples = repo / "examples"
    return [
        Check(
            check_id="zero_trust_basic_example_syntax",
            command=["bash", "-n", str(examples / "zero-trust-basic" / "run.sh")],
            purpose="Check zero-trust basic example shell syntax.",
            timeout_seconds=30,
        ),
        Check(
            check_id="zero_trust_basic_example_run",
            command=[
                str(examples / "zero-trust-basic" / "run.sh"),
                str(run_dir / "zero-trust-basic"),
            ],
            purpose="Run policy/data-boundary preflight example.",
            output_artifact=run_dir / "zero-trust-basic" / "zero-trust-preflight-receipt.json",
        ),
        Check(
            check_id="coding_reliability_example_syntax",
            command=["bash", "-n", str(examples / "coding-reliability-basic" / "run.sh")],
            purpose="Check coding reliability example shell syntax.",
            timeout_seconds=30,
        ),
        Check(
            check_id="memory_evidence_case_example_syntax",
            command=["bash", "-n", str(examples / "memory-evidence-case" / "run.sh")],
            purpose="Check memory/evidence-case example shell syntax.",
            timeout_seconds=30,
        ),
        Check(
            check_id="memory_evidence_case_example_run",
            command=[
                str(examples / "memory-evidence-case" / "run.sh"),
                str(run_dir / "memory-evidence-case"),
            ],
            purpose="Run memory intent and evidence-case gate example.",
            output_artifact=run_dir / "memory-evidence-case" / "demo-receipt.json",
        ),
        Check(
            check_id="coding_reliability_example_run",
            command=[
                str(examples / "coding-reliability-basic" / "run.sh"),
                str(run_dir / "coding-reliability-basic"),
            ],
            purpose="Run hash-bound patch, diagnostics, review, commit-plan, and reliability demo.",
            output_artifact=run_dir / "coding-reliability-basic" / "demo-receipt.json",
        ),
        Check(
            check_id="coding_zero_trust_init",
            command=[
                uv_bin,
                "run",
                "tau",
                "init",
                "--profile",
                "coding-zero-trust",
                "--out",
                str(run_dir / "coding-zero-trust-init"),
            ],
            purpose="Create the coding zero-trust starter profile.",
            output_artifact=run_dir / "coding-zero-trust-init" / ".tau" / "dag-template.json",
        ),
        Check(
            check_id="skill_composition_basic_example_syntax",
            command=["bash", "-n", str(examples / "skill-composition-basic" / "run.sh")],
            purpose="Check skill composition example shell syntax.",
            timeout_seconds=30,
        ),
        Check(
            check_id="skill_composition_basic_example_run",
            command=[
                str(examples / "skill-composition-basic" / "run.sh"),
                str(run_dir / "skill-composition-basic"),
            ],
            purpose="Run skill capability registry generation and validation example.",
            output_artifact=run_dir / "skill-composition-basic" / "demo-receipt.json",
        ),
        Check(
            check_id="omp_worker_example_syntax",
            command=["bash", "-n", str(examples / "omp-worker" / "run.sh")],
            purpose="Check OMP worker example shell syntax.",
            timeout_seconds=30,
        ),
        Check(
            check_id="omp_worker_example_run",
            command=[str(examples / "omp-worker" / "run.sh"), str(run_dir / "omp-worker")],
            purpose="Run OMP worker launch-request and result-validation demo.",
            output_artifact=run_dir / "omp-worker" / "demo-receipt.json",
        ),
        Check(
            check_id="scillm_worker_example_syntax",
            command=["bash", "-n", str(examples / "scillm-worker" / "run.sh")],
            purpose="Check SciLLM worker example shell syntax.",
            timeout_seconds=30,
        ),
        Check(
            check_id="scillm_worker_example_run",
            command=[str(examples / "scillm-worker" / "run.sh"), str(run_dir / "scillm-worker")],
            purpose="Run SciLLM worker launch-request and result-validation demo.",
            output_artifact=run_dir / "scillm-worker" / "demo-receipt.json",
        ),
        Check(
            check_id="itar_grade_containment_example_syntax",
            command=["bash", "-n", str(examples / "itar-grade-containment" / "run.sh")],
            purpose="Check ITAR-grade containment example shell syntax.",
            timeout_seconds=30,
        ),
        Check(
            check_id="itar_grade_containment_example_run",
            command=[
                str(examples / "itar-grade-containment" / "run.sh"),
                str(run_dir / "itar-grade-containment"),
            ],
            purpose=(
                "Run controlled-boundary containment, package validation, and red-team demo."
            ),
            output_artifact=run_dir / "itar-grade-containment" / "demo-receipt.json",
        ),
        Check(
            check_id="coding_receipt_lint",
            command=[
                uv_bin,
                "run",
                "ruff",
                "check",
                "--select",
                "I,F,E501",
                "src/tau_coding/code_patch.py",
                "src/tau_coding/review_findings.py",
                "src/tau_coding/course_correction.py",
                "src/tau_coding/lsp_receipts.py",
                "src/tau_coding/test_run_receipt.py",
                "src/tau_coding/commit_plan.py",
                "src/tau_coding/debug_session_receipt.py",
                "src/tau_coding/github_read_schemes.py",
                "src/tau_coding/coding_worker_adapters.py",
                "src/tau_coding/memory_evidence_gate.py",
                "src/tau_coding/memory_acquisition.py",
                "src/tau_coding/compliance_package.py",
                "src/tau_coding/run_status.py",
                "src/tau_coding/run_report.py",
                "src/tau_coding/server.py",
                "src/tau_coding/provenance.py",
                "src/tau_coding/receipt_signing.py",
                "src/tau_coding/zero_trust_redteam.py",
                "src/tau_coding/herdr_observation_gate.py",
                "src/tau_coding/orchestration_reliability.py",
                "src/tau_coding/sandbox_run.py",
                "src/tau_coding/skill_capability_registry.py",
                "src/tau_coding/cli.py",
                "tests/test_code_patch.py",
                "tests/test_review_findings.py",
                "tests/test_course_correction.py",
                "tests/test_lsp_receipts.py",
                "tests/test_test_run_receipt.py",
                "tests/test_commit_plan.py",
                "tests/test_debug_session_receipt.py",
                "tests/test_github_read_schemes.py",
                "tests/test_coding_worker_adapters.py",
                "tests/test_memory_evidence_gate.py",
                "tests/test_memory_acquisition.py",
                "tests/test_compliance_package.py",
                "tests/test_run_status.py",
                "tests/test_run_report.py",
                "tests/test_server.py",
                "tests/test_provenance.py",
                "tests/test_receipt_signing.py",
                "tests/test_zero_trust_redteam.py",
                "tests/test_herdr_observation_gate.py",
                "tests/test_orchestration_reliability.py",
                "tests/test_sandbox_policy.py",
                "tests/test_skill_capability_registry.py",
            ],
            purpose="Run focused import/style checks for coding capability modules.",
        ),
        Check(
            check_id="coding_receipt_tests",
            command=[
                uv_bin,
                "run",
                "pytest",
                "tests/test_code_patch.py",
                "tests/test_review_findings.py",
                "tests/test_course_correction.py",
                "tests/test_lsp_receipts.py",
                "tests/test_test_run_receipt.py",
                "tests/test_commit_plan.py",
                "tests/test_debug_session_receipt.py",
                "tests/test_github_read_schemes.py",
                "tests/test_coding_worker_adapters.py",
                "tests/test_memory_evidence_gate.py",
                "tests/test_memory_acquisition.py",
                "tests/test_compliance_package.py",
                "tests/test_run_status.py",
                "tests/test_run_report.py",
                "tests/test_server.py",
                "tests/test_provenance.py",
                "tests/test_receipt_signing.py",
                "tests/test_zero_trust_redteam.py",
                "tests/test_herdr_observation_gate.py",
                "tests/test_orchestration_reliability.py",
                "tests/test_sandbox_policy.py",
                "tests/test_skill_capability_registry.py",
                "-q",
            ],
            purpose=(
                "Run focused tests for coding receipts, worker gates, and reliability receipts."
            ),
        ),
    ]


def run_check(check: Check, *, repo: Path) -> dict[str, Any]:
    started = datetime.now(UTC)
    completed = started
    stdout = ""
    stderr = ""
    timed_out = False
    try:
        result = subprocess.run(
            check.command,
            cwd=repo,
            text=True,
            capture_output=True,
            timeout=check.timeout_seconds,
            check=False,
        )
        exit_code = result.returncode
        stdout = result.stdout[-8000:]
        stderr = result.stderr[-8000:]
        completed = datetime.now(UTC)
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        timed_out = True
        stdout = _tail(exc.stdout)
        stderr = _tail(exc.stderr)
        completed = datetime.now(UTC)

    artifact_payload = _read_json_artifact(check.output_artifact)
    ok = exit_code == check.expected_exit_code and not timed_out
    if check.output_artifact is not None:
        ok = ok and artifact_payload.get("read_ok") is True

    return {
        "schema": CHECK_SCHEMA,
        "check_id": check.check_id,
        "purpose": check.purpose,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "command": check.command,
        "expected_exit_code": check.expected_exit_code,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
        "duration_seconds": round((completed - started).total_seconds(), 3),
        "stdout_tail": stdout,
        "stderr_tail": stderr,
        "output_artifact": str(check.output_artifact) if check.output_artifact else None,
        "output_artifact_payload": artifact_payload,
    }


def build_receipt(*, repo: Path, run_dir: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [record for record in records if record.get("ok") is not True]
    return {
        "schema": RECEIPT_SCHEMA,
        "ok": not failed,
        "status": "PASS" if not failed else "BLOCKED",
        "mocked": "mixed",
        "live": "mixed",
        "provider_live": False,
        "repo": str(repo),
        "run_dir": str(run_dir),
        "check_count": len(records),
        "failed_check_count": len(failed),
        "checks": records,
        "coverage": [
            "hash-bound code patch receipts",
            "coding zero-trust init starter profile",
            "zero-trust policy/data-boundary preflight receipts",
            "coding course-correction receipts",
            "structured review findings",
            "LSP diagnostics, symbols, and rename planning receipts",
            "focused test-run receipts",
            "commit-plan receipts",
            "debug-session receipts",
            "GitHub read receipts",
            "OMP/SciLLM worker validation receipts",
            "OMP/SciLLM dry-run and bounded apply launch receipts",
            "memory intent and evidence-case gate receipts",
            "Graph Memory intent and create-evidence-case acquisition receipts",
            "skill capability registry receipts",
            "compliance evidence package receipts",
            "run report generation",
            "local API preflight surfaces",
            "actor/environment provenance and signed receipt envelopes",
            "zero-trust adversarial red-team receipts",
            "ITAR-grade containment example receipts",
            "Herdr observation gate receipts",
            "sandbox-run policy receipts",
            "orchestration reliability receipts",
        ],
        "proof_scope": {
            "proves": [
                "Tau's focused coding receipt tests pass in this checkout.",
                "Tau's copyable zero-trust example produces a parseable preflight receipt.",
                "Tau's copyable memory/evidence, coding, and worker examples produce "
                "parseable receipts.",
                "Tau can initialize a coding zero-trust starter with explicit "
                "coding evidence requirements.",
                "Tau records worker launch requests without trusting worker execution.",
                "Tau validates a read-only skill capability registry before treating "
                "skill outputs as admissible Tau evidence.",
                "Tau exercises memory-first gates, package/report/API surfaces, "
                "provenance/signing, and adversarial containment tests.",
                "Tau's ITAR-grade containment example emits local fail-closed and "
                "review-package receipts.",
                "Tau exercises Herdr observation and sandbox-run policy receipt tests.",
            ],
            "does_not_prove": [
                "ITAR compliance.",
                "Live OMP or SciLLM semantic worker execution.",
                "Provider/model semantic quality.",
                "Semantic code correctness.",
                "GitHub mutation.",
                "Human acceptance.",
                "Legal compliance.",
                "Full sandbox isolation on every host.",
            ],
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _read_json_artifact(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"read_ok": False, "error": str(exc)}
    return {
        "read_ok": True,
        "schema": payload.get("schema"),
        "status": payload.get("status"),
        "ok": payload.get("ok"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
    }


def _tail(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-8000:]
    return value[-8000:]


if __name__ == "__main__":
    raise SystemExit(main())
