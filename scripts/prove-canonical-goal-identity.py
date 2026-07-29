#!/usr/bin/env python3
"""Prove canonical DAG goal identity preservation and drift rejection."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "tau.canonical_goal_identity_proof.v1"


@dataclass(frozen=True, slots=True)
class GoalIdentityCase:
    dag_id: str
    drift_check_id: str


CASES: tuple[GoalIdentityCase, ...] = (
    GoalIdentityCase(
        dag_id="simple-linear",
        drift_check_id="advanced.project_dag_simple_evidence_goal_drift_fail_closed",
    ),
    GoalIdentityCase(
        dag_id="multi-step-sequential",
        drift_check_id="advanced.project_dag_medium_evidence_goal_drift_fail_closed",
    ),
    GoalIdentityCase(
        dag_id="fanout-fanin",
        drift_check_id="advanced.project_dag_ready_queue_evidence_goal_drift_fail_closed",
    ),
    GoalIdentityCase(
        dag_id="mixed-retry-approval",
        drift_check_id="advanced.project_dag_ready_queue_timeout_retry_goal_drift_fail_closed",
    ),
    GoalIdentityCase(
        dag_id="durable-recovery",
        drift_check_id="advanced.project_dag_ready_queue_non_json_retry_goal_drift_fail_closed",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("/tmp/tau-canonical-goal-identity-proof"),
    )
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    run_root = args.run_root.expanduser().resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    receipt_path = (
        args.receipt.expanduser().resolve()
        if args.receipt
        else run_root / "canonical-goal-identity-proof-receipt.json"
    )
    logs_dir = run_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    positives = [
        run_positive_case(
            case,
            repo=repo,
            run_root=run_root,
            logs_dir=logs_dir,
            uv_bin=args.uv_bin,
            timeout_seconds=args.timeout_seconds,
        )
        for case in CASES
    ]
    drifts = [
        run_drift_case(
            case,
            repo=repo,
            run_root=run_root,
            logs_dir=logs_dir,
            uv_bin=args.uv_bin,
            timeout_seconds=args.timeout_seconds,
        )
        for case in CASES
    ]
    errors = [
        f"{case['dag_id']}:positive:{error}"
        for case in positives
        for error in case["errors"]
    ]
    errors.extend(
        f"{case['dag_id']}:drift:{error}" for case in drifts for error in case["errors"]
    )

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "repo": str(repo),
        "run_root": str(run_root),
        "positive_count": len(positives),
        "drift_count": len(drifts),
        "positive_cases": positives,
        "drift_cases": drifts,
        "errors": errors,
        "read_back_assertions": {
            "goal_identity_present": all(case["goal_identity_present"] for case in positives),
            "positive_artifacts_preserve_goal": all(
                case["artifact_goal_hashes_match"] for case in positives
            ),
            "drift_attempts_rejected": all(
                case["observed_blocked_verdict"] == "EVIDENCE_GOAL_HASH_MISMATCH"
                for case in drifts
            ),
        },
        "proof_scope": {
            "proves": [
                "Each canonical DAG run records a structured goal identity.",
                "Accepted useful evidence in each positive run preserves the active goal hash.",
                "Each canonical topology has a live adversarial drift control.",
                "Drifted node evidence is rejected before it can count as accepted progress.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "Human acceptance of the full immutable Tau goal.",
                "Browser-rendered dynamic progress.",
            ],
        },
        "timestamp": utc_stamp(),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["ok"] else 1


def run_positive_case(
    case: GoalIdentityCase,
    *,
    repo: Path,
    run_root: Path,
    logs_dir: Path,
    uv_bin: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = [
        uv_bin,
        "run",
        "--project",
        str(repo),
        "tau",
        "canonical-dag-launch",
        case.dag_id,
        "--repo",
        str(repo),
        "--run-root",
        str(run_root / "positive-runs"),
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    record = run_command(
        command,
        cwd=repo,
        timeout=timeout_seconds + 60,
        stdout_path=logs_dir / f"{case.dag_id}.positive.stdout.json",
        stderr_path=logs_dir / f"{case.dag_id}.positive.stderr.txt",
    )
    launch = parse_json_payload(record["stdout"])
    errors: list[str] = []
    if record["exit_code"] != 0:
        errors.append(f"exit_code:{record['exit_code']}")
    if not isinstance(launch, dict):
        errors.append("missing_launch_json")
        return positive_payload(case, record, None, None, errors)
    if launch.get("status") != "PASS" or launch.get("ok") is not True:
        errors.append(f"launch_status:{launch.get('status')!r}")
    receipt = read_json_path(launch.get("output_receipt_path"))
    if not isinstance(receipt, dict):
        errors.append("missing_output_receipt")
        return positive_payload(case, record, launch, None, errors)
    errors.extend(goal_identity_receipt_errors(receipt))
    progress = read_json_path(receipt.get("progress_path"))
    if isinstance(progress, dict):
        errors.extend(
            f"progress:{error}" for error in goal_identity_receipt_errors(progress)
        )
    else:
        errors.append("missing_progress_receipt")
    artifact_summary = artifact_goal_summary(receipt)
    errors.extend(artifact_summary["errors"])
    return {
        **positive_payload(case, record, launch, receipt, errors),
        "progress_path": receipt.get("progress_path"),
        "artifact_goal_hashes": artifact_summary["goal_hashes"],
        "artifact_goal_versions": artifact_summary["goal_versions"],
        "artifact_goal_hashes_match": not artifact_summary["errors"],
    }


def run_drift_case(
    case: GoalIdentityCase,
    *,
    repo: Path,
    run_root: Path,
    logs_dir: Path,
    uv_bin: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = [
        uv_bin,
        "run",
        "--project",
        str(repo),
        "python",
        str(repo / "scripts" / "run-real-world-sanity.py"),
        "--repo",
        str(repo),
        "--run-root",
        str(run_root / "drift-runs"),
        "--label",
        f"canonical-goal-drift-{case.dag_id}",
        "--levels",
        "advanced",
        "--checks",
        case.drift_check_id,
        "--receipt-timeout-seconds",
        str(timeout_seconds),
    ]
    record = run_command(
        command,
        cwd=repo,
        timeout=timeout_seconds + 60,
        stdout_path=logs_dir / f"{case.dag_id}.drift.stdout.json",
        stderr_path=logs_dir / f"{case.dag_id}.drift.stderr.txt",
    )
    suite = parse_json_payload(record["stdout"])
    check = first_check(suite)
    summary = check.get("receipt_summary") if isinstance(check, dict) else None
    errors: list[str] = []
    if record["exit_code"] != 0:
        errors.append(f"exit_code:{record['exit_code']}")
    if not isinstance(suite, dict) or suite.get("ok") is not True:
        errors.append("suite_not_pass")
    if not isinstance(check, dict) or check.get("ok") is not True:
        errors.append("check_not_pass")
    if not isinstance(summary, dict):
        errors.append("missing_blocked_summary")
        summary = {}
    if summary.get("status") != "BLOCKED":
        errors.append(f"blocked_status:{summary.get('status')!r}")
    if summary.get("verdict") != "EVIDENCE_GOAL_HASH_MISMATCH":
        errors.append(f"blocked_verdict:{summary.get('verdict')!r}")
    dag_error = summary.get("dag_error")
    failure_code = dag_error.get("failure_code") if isinstance(dag_error, dict) else None
    if failure_code != "evidence_goal_hash_mismatch":
        errors.append(f"failure_code:{failure_code!r}")
    rejected_artifacts = rejected_goal_drift_artifacts(summary)
    if not rejected_artifacts:
        errors.append("missing_rejected_attempt_artifact")
    return {
        "dag_id": case.dag_id,
        "check_id": case.drift_check_id,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "command": command,
        "exit_code": record["exit_code"],
        "stdout_path": record["stdout_path"],
        "stderr_path": record["stderr_path"],
        "observed_blocked_status": summary.get("status"),
        "observed_blocked_verdict": summary.get("verdict"),
        "failure_code": failure_code,
        "blocker_receipt_path": (
            dag_error.get("receipt_path") if isinstance(dag_error, dict) else None
        ),
        "rejected_attempt_artifacts": rejected_artifacts,
        "errors": errors,
    }


def positive_payload(
    case: GoalIdentityCase,
    record: dict[str, Any],
    launch: dict[str, Any] | None,
    receipt: dict[str, Any] | None,
    errors: list[str],
) -> dict[str, Any]:
    goal_identity = receipt.get("goal_identity") if isinstance(receipt, dict) else None
    return {
        "dag_id": case.dag_id,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "command": record["command"],
        "exit_code": record["exit_code"],
        "stdout_path": record["stdout_path"],
        "stderr_path": record["stderr_path"],
        "run_dir": launch.get("run_dir") if isinstance(launch, dict) else None,
        "output_receipt_path": (
            launch.get("output_receipt_path") if isinstance(launch, dict) else None
        ),
        "goal_identity": goal_identity,
        "goal_identity_present": isinstance(goal_identity, dict),
        "errors": errors,
    }


def goal_identity_receipt_errors(receipt: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    identity = receipt.get("goal_identity")
    if not isinstance(identity, dict):
        return ["missing_goal_identity"]
    if identity.get("goal_hash") != receipt.get("active_goal_hash"):
        errors.append("goal_identity_hash_mismatch")
    if identity.get("goal_version") != receipt.get("active_goal_version"):
        errors.append("goal_identity_version_mismatch")
    if not isinstance(identity.get("goal_id"), str) or not identity["goal_id"]:
        errors.append("goal_identity_missing_goal_id")
    if not isinstance(identity.get("goal_hash"), str) or not identity["goal_hash"]:
        errors.append("goal_identity_missing_goal_hash")
    if identity.get("goal_version") is None:
        errors.append("goal_identity_missing_goal_version")
    return errors


def artifact_goal_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    goal_hash = receipt.get("active_goal_hash")
    goal_version = receipt.get("active_goal_version")
    errors: list[str] = []
    goal_hashes: dict[str, str] = {}
    goal_versions: dict[str, object] = {}
    for artifact in receipt.get("artifacts", []):
        payload = read_json_path(artifact)
        if not isinstance(payload, dict):
            continue
        schema = payload.get("schema")
        if schema not in {
            "tau.creator_artifact.v1",
            "tau.source_summary.v1",
            "tau.reviewer_verdict.v1",
        }:
            continue
        goal_hashes[str(artifact)] = str(payload.get("goal_hash"))
        goal_versions[str(artifact)] = payload.get("goal_version")
        if payload.get("goal_hash") != goal_hash:
            errors.append(f"artifact_goal_hash_mismatch:{artifact}")
        if payload.get("goal_version") != goal_version:
            errors.append(f"artifact_goal_version_mismatch:{artifact}")
    if not goal_hashes:
        errors.append("no_goal_bound_useful_artifacts")
    return {
        "errors": errors,
        "goal_hashes": goal_hashes,
        "goal_versions": goal_versions,
    }


def rejected_goal_drift_artifacts(summary: dict[str, Any]) -> list[dict[str, Any]]:
    receipt_path = None
    dag_error = summary.get("dag_error")
    if isinstance(dag_error, dict):
        receipt_path = dag_error.get("receipt_path")
    receipt = read_json_path(receipt_path)
    if not isinstance(receipt, dict):
        return []
    expected = receipt.get("active_goal_hash")
    artifacts: list[dict[str, Any]] = []
    for artifact in receipt.get("artifacts", []):
        payload = read_json_path(artifact)
        if not isinstance(payload, dict):
            continue
        if payload.get("schema") not in {
            "tau.creator_artifact.v1",
            "tau.source_summary.v1",
            "tau.reviewer_verdict.v1",
        }:
            continue
        if payload.get("goal_hash") != expected:
            artifacts.append(
                {
                    "path": str(artifact),
                    "schema": payload.get("schema"),
                    "expected_goal_hash": expected,
                    "observed_goal_hash": payload.get("goal_hash"),
                    "goal_version": payload.get("goal_version"),
                }
            )
    return artifacts


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def first_check(suite: dict[str, Any] | None) -> dict[str, Any] | None:
    checks = suite.get("checks") if isinstance(suite, dict) else None
    if not isinstance(checks, list) or not checks:
        return None
    check = checks[0]
    return check if isinstance(check, dict) else None


def read_json_path(path_text: object) -> dict[str, Any] | None:
    if not isinstance(path_text, str) or not path_text:
        return None
    try:
        payload = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def parse_json_payload(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    stripped = text.strip()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            payload, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if stripped[index + end :].strip():
            continue
        return payload if isinstance(payload, dict) else None
    return None


def utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
