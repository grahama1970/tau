#!/usr/bin/env python3
"""Prove canonical DAG useful outputs and fail-closed paths.

This is a live corpus proof for Tau issue #252. It launches every canonical DAG
through the documented CLI surface, then independently reads back the emitted
JSON artifacts and receipts. It also runs one deterministic negative control per
canonical DAG and checks that each negative path blocks with a precise verdict.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "tau.canonical_dag_corpus_proof.v1"


@dataclass(frozen=True, slots=True)
class CorpusCase:
    dag_id: str
    expected_schemas: tuple[str, ...]
    expected_final_verdict: str
    min_node_count: int
    min_edge_count: int
    min_coder_attempts: int
    negative_check_id: str
    negative_expected_verdict: str


CORPUS_CASES: tuple[CorpusCase, ...] = (
    CorpusCase(
        dag_id="simple-linear",
        expected_schemas=("tau.creator_artifact.v1", "tau.reviewer_verdict.v1"),
        expected_final_verdict="PASS",
        min_node_count=2,
        min_edge_count=3,
        min_coder_attempts=1,
        negative_check_id="advanced.project_dag_non_json_fail_closed",
        negative_expected_verdict="INVALID_COMMAND_JSON",
    ),
    CorpusCase(
        dag_id="multi-step-sequential",
        expected_schemas=("tau.creator_artifact.v1", "tau.reviewer_verdict.v1"),
        expected_final_verdict="PASS",
        min_node_count=2,
        min_edge_count=3,
        min_coder_attempts=2,
        negative_check_id="advanced.project_dag_max_steps_fail_closed",
        negative_expected_verdict="MAX_STEPS_EXHAUSTED",
    ),
    CorpusCase(
        dag_id="fanout-fanin",
        expected_schemas=(
            "tau.source_summary.v1",
            "tau.creator_artifact.v1",
            "tau.reviewer_verdict.v1",
        ),
        expected_final_verdict="PASS",
        min_node_count=4,
        min_edge_count=5,
        min_coder_attempts=1,
        negative_check_id="advanced.project_dag_ready_queue_max_retries_fail_closed",
        negative_expected_verdict="INVALID_COMMAND_JSON",
    ),
    CorpusCase(
        dag_id="mixed-retry-approval",
        expected_schemas=(
            "tau.source_summary.v1",
            "tau.creator_artifact.v1",
            "tau.reviewer_verdict.v1",
        ),
        expected_final_verdict="PASS",
        min_node_count=4,
        min_edge_count=5,
        min_coder_attempts=2,
        negative_check_id="advanced.project_dag_ready_queue_mutating_branch_fail_closed",
        negative_expected_verdict="MUTATING_NODE_NOT_ALLOWED",
    ),
    CorpusCase(
        dag_id="durable-recovery",
        expected_schemas=(
            "tau.source_summary.v1",
            "tau.creator_artifact.v1",
            "tau.reviewer_verdict.v1",
        ),
        expected_final_verdict="PASS",
        min_node_count=4,
        min_edge_count=5,
        min_coder_attempts=2,
        negative_check_id="advanced.project_dag_ready_queue_provider_policy_fail_closed",
        negative_expected_verdict="NON_LOCAL_READY_QUEUE_NODE_NOT_ALLOWED",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("/tmp/tau-canonical-dag-corpus-proof"),
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
        else run_root / "canonical-dag-corpus-proof-receipt.json"
    )
    logs_dir = run_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    positives: list[dict[str, Any]] = []
    negatives: list[dict[str, Any]] = []
    errors: list[str] = []

    for case in CORPUS_CASES:
        positive = run_positive_case(
            case,
            repo=repo,
            run_root=run_root,
            logs_dir=logs_dir,
            uv_bin=args.uv_bin,
            timeout_seconds=args.timeout_seconds,
        )
        positives.append(positive)
        errors.extend(f"{case.dag_id}:positive:{error}" for error in positive["errors"])

        negative = run_negative_case(
            case,
            repo=repo,
            run_root=run_root,
            logs_dir=logs_dir,
            uv_bin=args.uv_bin,
            timeout_seconds=args.timeout_seconds,
        )
        negatives.append(negative)
        errors.extend(f"{case.dag_id}:negative:{error}" for error in negative["errors"])

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "repo": str(repo),
        "run_root": str(run_root),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "positive_cases": positives,
        "negative_cases": negatives,
        "errors": errors,
        "read_back_assertions": {
            "useful_output_content": all(not case["errors"] for case in positives),
            "fail_closed_negative_paths": all(not case["errors"] for case in negatives),
            "goal_hash_present_on_artifacts": all(
                not case["errors"] and case["goal_hash_preserved"] for case in positives
            ),
        },
        "proof_scope": {
            "proves": [
                "Each canonical DAG launches through the documented Tau CLI entrypoint.",
                (
                    "Each positive run produces non-mocked, live filesystem "
                    "artifacts with expected schemas."
                ),
                (
                    "Each positive run preserves one human goal hash across "
                    "accepted artifacts and reviewer verdicts."
                ),
                (
                    "Each canonical DAG has a deterministic negative control "
                    "that blocks with a precise verdict."
                ),
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "Browser-rendered dynamic React Flow progress.",
                (
                    "Crash-safe resume or targeted repair beyond the recovery "
                    "evidence present in these DAG receipts."
                ),
                "Human acceptance of the full immutable Tau goal.",
            ],
        },
        "timestamp": utc_stamp(),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["ok"] else 1


def run_positive_case(
    case: CorpusCase,
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
    errors = positive_launch_errors(case, launch=launch, exit_code=record["exit_code"])
    output_receipt = read_optional_json_path(
        launch.get("output_receipt_path") if isinstance(launch, dict) else None
    )
    artifact_checks = inspect_positive_artifacts(case, receipt=output_receipt)
    errors.extend(artifact_checks["errors"])
    return {
        "dag_id": case.dag_id,
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "command": command,
        "exit_code": record["exit_code"],
        "stdout_path": record["stdout_path"],
        "stderr_path": record["stderr_path"],
        "launch_receipt": minimal_launch_receipt(launch),
        "output_receipt_path": (
            launch.get("output_receipt_path") if isinstance(launch, dict) else None
        ),
        "run_dir": launch.get("run_dir") if isinstance(launch, dict) else None,
        "viewer_url": launch.get("viewer_url") if isinstance(launch, dict) else None,
        "useful_outputs": artifact_checks["useful_outputs"],
        "goal_hash": artifact_checks["goal_hash"],
        "goal_hash_preserved": artifact_checks["goal_hash_preserved"],
        "errors": errors,
    }


def run_negative_case(
    case: CorpusCase,
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
        str(run_root / "negative-runs"),
        "--label",
        f"canonical-negative-{case.dag_id}",
        "--levels",
        "advanced",
        "--checks",
        case.negative_check_id,
        "--receipt-timeout-seconds",
        str(timeout_seconds),
    ]
    record = run_command(
        command,
        cwd=repo,
        timeout=timeout_seconds + 60,
        stdout_path=logs_dir / f"{case.dag_id}.negative.stdout.json",
        stderr_path=logs_dir / f"{case.dag_id}.negative.stderr.txt",
    )
    suite = parse_json_payload(record["stdout"])
    check = first_check(suite)
    errors = negative_check_errors(case, suite=suite, check=check, exit_code=record["exit_code"])
    summary = check.get("receipt_summary") if isinstance(check, dict) else None
    dag_error = summary.get("dag_error") if isinstance(summary, dict) else None
    return {
        "dag_id": case.dag_id,
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "command": command,
        "exit_code": record["exit_code"],
        "stdout_path": record["stdout_path"],
        "stderr_path": record["stderr_path"],
        "check_id": case.negative_check_id,
        "expected_verdict": case.negative_expected_verdict,
        "observed_suite_status": suite.get("status") if isinstance(suite, dict) else None,
        "observed_check_status": check.get("status") if isinstance(check, dict) else None,
        "observed_blocked_status": summary.get("status") if isinstance(summary, dict) else None,
        "observed_blocked_verdict": summary.get("verdict") if isinstance(summary, dict) else None,
        "failure_code": dag_error.get("failure_code") if isinstance(dag_error, dict) else None,
        "blocker_receipt_path": (
            dag_error.get("receipt_path") if isinstance(dag_error, dict) else None
        ),
        "errors": errors,
    }


def positive_launch_errors(
    case: CorpusCase,
    *,
    launch: dict[str, Any] | None,
    exit_code: int,
) -> list[str]:
    errors: list[str] = []
    if exit_code != 0:
        errors.append(f"launch_exit_code:{exit_code}")
    if not isinstance(launch, dict):
        return [*errors, "missing_launch_json"]
    if launch.get("schema") != "tau.canonical_dag_launch_receipt.v1":
        errors.append(f"launch_schema:{launch.get('schema')!r}")
    selected = launch.get("canonical_dag")
    if not isinstance(selected, dict) or selected.get("dag_id") != case.dag_id:
        errors.append("canonical_dag_mismatch")
    if launch.get("status") != "PASS" or launch.get("ok") is not True:
        errors.append(f"launch_status:{launch.get('status')!r}")
    if launch.get("mocked") is not False:
        errors.append(f"launch_mocked:{launch.get('mocked')!r}")
    if launch.get("live") is not True:
        errors.append(f"launch_live:{launch.get('live')!r}")
    if launch.get("dag_execution_status") != "PASS":
        errors.append(f"dag_execution_status:{launch.get('dag_execution_status')!r}")
    for key in ("run_dir", "output_receipt_path", "viewer_url"):
        if not isinstance(launch.get(key), str) or not launch[key]:
            errors.append(f"missing_{key}")
    return errors


def negative_check_errors(
    case: CorpusCase,
    *,
    suite: dict[str, Any] | None,
    check: dict[str, Any] | None,
    exit_code: int,
) -> list[str]:
    errors: list[str] = []
    if exit_code != 0:
        errors.append(f"suite_exit_code:{exit_code}")
    if not isinstance(suite, dict):
        return [*errors, "missing_suite_json"]
    if suite.get("status") != "PASS" or suite.get("ok") is not True:
        errors.append(f"suite_status:{suite.get('status')!r}")
    if suite.get("mocked") is not False:
        errors.append(f"suite_mocked:{suite.get('mocked')!r}")
    if not isinstance(check, dict):
        return [*errors, "missing_check_record"]
    if check.get("check_id") != case.negative_check_id:
        errors.append(f"check_id:{check.get('check_id')!r}")
    if check.get("status") != "PASS" or check.get("ok") is not True:
        errors.append(f"check_status:{check.get('status')!r}")
    summary = check.get("receipt_summary")
    if not isinstance(summary, dict):
        return [*errors, "missing_receipt_summary"]
    if summary.get("mocked") is not False:
        errors.append(f"blocked_mocked:{summary.get('mocked')!r}")
    if summary.get("live") is not True:
        errors.append(f"blocked_live:{summary.get('live')!r}")
    if summary.get("status") != "BLOCKED":
        errors.append(f"blocked_status:{summary.get('status')!r}")
    if summary.get("verdict") != case.negative_expected_verdict:
        errors.append(f"blocked_verdict:{summary.get('verdict')!r}")
    dag_error = summary.get("dag_error")
    if not isinstance(dag_error, dict):
        errors.append("missing_dag_error")
    elif not isinstance(dag_error.get("failure_code"), str) or not dag_error["failure_code"]:
        errors.append("missing_failure_code")
    elif not isinstance(dag_error.get("receipt_path"), str) or not Path(
        dag_error["receipt_path"]
    ).exists():
        errors.append("missing_blocker_receipt_path")
    return errors


def inspect_positive_artifacts(
    case: CorpusCase,
    *,
    receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    errors: list[str] = []
    useful_outputs: list[dict[str, Any]] = []
    goal_hash = None
    if not isinstance(receipt, dict):
        return {
            "errors": ["missing_output_receipt_json"],
            "useful_outputs": useful_outputs,
            "goal_hash": goal_hash,
            "goal_hash_preserved": False,
        }
    if receipt.get("schema") != "tau.dag_receipt.v1":
        errors.append(f"receipt_schema:{receipt.get('schema')!r}")
    if receipt.get("status") != "PASS" or receipt.get("verdict") != case.expected_final_verdict:
        errors.append(f"receipt_status:{receipt.get('status')!r}/{receipt.get('verdict')!r}")
    if receipt.get("mocked") is not False or receipt.get("live") is not True:
        errors.append(f"receipt_mocked_live:{receipt.get('mocked')!r}/{receipt.get('live')!r}")
    if int(receipt.get("node_count") or 0) < case.min_node_count:
        errors.append(f"node_count:{receipt.get('node_count')!r}")
    if int(receipt.get("edge_count") or 0) < case.min_edge_count:
        errors.append(f"edge_count:{receipt.get('edge_count')!r}")
    node_attempts = receipt.get("node_attempts")
    coder_attempts = node_attempts.get("coder") if isinstance(node_attempts, dict) else None
    if int(coder_attempts or 0) < case.min_coder_attempts:
        errors.append(f"coder_attempts:{coder_attempts!r}")
    goal_hash = receipt.get("active_goal_hash")
    if not isinstance(goal_hash, str) or not goal_hash.startswith("sha256:"):
        errors.append(f"active_goal_hash:{goal_hash!r}")
        goal_hash = None

    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    artifact_payloads = []
    for path_text in artifacts:
        payload = read_optional_json_path(path_text)
        if isinstance(payload, dict):
            artifact_payloads.append((str(path_text), payload))
    for schema in case.expected_schemas:
        matching = [
            (path, payload)
            for path, payload in artifact_payloads
            if payload.get("schema") == schema
        ]
        if not matching:
            errors.append(f"missing_useful_artifact_schema:{schema}")
            continue
        for path, payload in matching:
            artifact_errors = useful_artifact_errors(payload, path=path, goal_hash=goal_hash)
            if not artifact_errors:
                useful_outputs.append(
                    {
                        "path": path,
                        "schema": payload.get("schema"),
                        "scenario": payload.get("scenario"),
                        "goal_hash": payload.get("goal_hash"),
                        "summary": payload.get("summary"),
                        "verdict": payload.get("verdict"),
                    }
                )
        if not any(
            payload.get("schema") == schema
            and not useful_artifact_errors(payload, path=path, goal_hash=goal_hash)
            for path, payload in matching
        ):
            errors.append(f"invalid_useful_artifact_schema:{schema}")

    reviewer_verdicts = receipt.get("reviewer_verdicts")
    final_verdict = (
        reviewer_verdicts[-1]
        if isinstance(reviewer_verdicts, list) and reviewer_verdicts
        else None
    )
    if not isinstance(final_verdict, dict):
        errors.append("missing_final_reviewer_verdict")
    elif final_verdict.get("verdict") != case.expected_final_verdict:
        errors.append(f"final_reviewer_verdict:{final_verdict.get('verdict')!r}")
    elif goal_hash and final_verdict.get("goal_hash") != goal_hash:
        errors.append("final_reviewer_goal_hash_mismatch")

    goal_hash_preserved = bool(goal_hash) and all(
        output.get("goal_hash") == goal_hash for output in useful_outputs
    )
    if not goal_hash_preserved:
        errors.append("useful_output_goal_hash_not_preserved")
    return {
        "errors": sorted(set(errors)),
        "useful_outputs": useful_outputs,
        "goal_hash": goal_hash,
        "goal_hash_preserved": goal_hash_preserved,
    }


def useful_artifact_errors(
    payload: dict[str, Any],
    *,
    path: str,
    goal_hash: str | None,
) -> list[str]:
    errors: list[str] = []
    if not Path(path).exists():
        errors.append("artifact_path_missing")
    if not isinstance(payload.get("schema"), str):
        errors.append("artifact_schema_missing")
    if goal_hash and payload.get("goal_hash") != goal_hash:
        errors.append("artifact_goal_hash_mismatch")
    if payload.get("schema") in {"tau.creator_artifact.v1", "tau.source_summary.v1"}:
        summary = payload.get("summary")
        if not isinstance(summary, str) or "real-world project DAG sanity" not in summary:
            errors.append("artifact_summary_missing_useful_content")
    if payload.get("schema") == "tau.reviewer_verdict.v1":
        if payload.get("verdict") not in {"PASS", "REVISE"}:
            errors.append("reviewer_verdict_invalid")
        if payload.get("goal_matches") is not True:
            errors.append("reviewer_goal_matches_not_true")
    return errors


def minimal_launch_receipt(launch: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(launch, dict):
        return None
    return {
        "schema": launch.get("schema"),
        "ok": launch.get("ok"),
        "status": launch.get("status"),
        "mocked": launch.get("mocked"),
        "live": launch.get("live"),
        "provider_live": launch.get("provider_live"),
        "dag_execution_status": launch.get("dag_execution_status"),
        "run_id": launch.get("run_id"),
        "run_dir": launch.get("run_dir"),
        "output_receipt_path": launch.get("output_receipt_path"),
        "viewer_url": launch.get("viewer_url"),
    }


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


def read_optional_json_path(path_text: object) -> dict[str, Any] | None:
    if not isinstance(path_text, str) or not path_text:
        return None
    path = Path(path_text).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
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
