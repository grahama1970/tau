"""Read-only command workers for the repository-readiness workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

NODE_RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    _common(inspect_parser)
    inspect_parser.add_argument("--output", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    _common(validate_parser)
    validate_parser.add_argument("--output", type=Path, required=True)
    publish_parser = subparsers.add_parser("publish")
    _common(publish_parser)
    publish_parser.add_argument("--json-output", type=Path, required=True)
    publish_parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    request = _read_json(args.request, label="repository readiness request")
    delay = float(args.step_delay_seconds)
    if delay < 0:
        raise RuntimeError("step_delay_seconds must be non-negative")
    if delay:
        time.sleep(delay)
    if args.command == "inspect":
        _inspect(request, output=args.output, receipt=args.receipt)
    elif args.command == "validate":
        _validate(request, output=args.output, receipt=args.receipt)
    else:
        _publish(
            request,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            receipt=args.receipt,
        )
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--step-delay-seconds", type=float, default=0.0)


def _inspect(request: dict[str, Any], *, output: Path, receipt: Path) -> None:
    repo = Path(_required_string(request, "repo_path")).resolve()
    git_root = _git(repo, "rev-parse", "--show-toplevel").strip()
    head_sha = _git(repo, "rev-parse", "HEAD").strip()
    branch = _git(repo, "branch", "--show-current").strip()
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    diff_check = subprocess.run(
        ["git", "-C", str(repo), "diff", "--check"],
        check=False,
        capture_output=True,
        text=True,
    )
    status_entries = status.splitlines()
    inspection = {
        "schema": "tau.repository_inspection.v1",
        "repo_path": str(repo),
        "git_root": git_root,
        "head_sha": head_sha,
        "branch": branch,
        "dirty": bool(status_entries),
        "status_entries": status_entries[:200],
        "status_entry_count": len(status_entries),
        "status_entries_truncated": len(status_entries) > 200,
        "diff_check_passed": diff_check.returncode == 0,
    }
    _write_json(output, inspection)
    artifact = _artifact("repository_inspection", output)
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="inspect-repository",
            status="PASS",
            artifacts=[artifact],
            accepted_output={
                "schema": inspection["schema"],
                "summary": "Repository inspection completed.",
                "dirty": inspection["dirty"],
                "head_sha": head_sha,
                "branch": branch,
                "repo_path": str(repo),
                "diff_check_passed": inspection["diff_check_passed"],
                "artifacts": [artifact],
            },
            commands_run=["git rev-parse", "git branch", "git status", "git diff --check"],
            errors=[],
            handoff="Repository inspection is available for readiness validation.",
        ),
    )


def _validate(request: dict[str, Any], *, output: Path, receipt: Path) -> None:
    inspection = _accepted_input("tau.repository_inspection.v1")
    dirty = inspection.get("dirty") is True
    if request.get("require_clean") is True and dirty:
        _write_json(
            receipt,
            _receipt(
                request,
                node_id="validate-readiness",
                status="BLOCKED",
                artifacts=[],
                accepted_output=None,
                commands_run=["deterministic readiness validation"],
                errors=["dirty_repository"],
                handoff=(
                    "Repository readiness is blocked because a clean worktree was required."
                ),
            ),
        )
        return
    validation = {
        "schema": "tau.repository_readiness_validation.v1",
        "status": "PASS",
        "require_clean": request.get("require_clean") is True,
        "dirty": dirty,
        "goal_hash": _goal_hash(request),
        "summary": "Repository readiness policy passed.",
    }
    _write_json(output, validation)
    artifact = _artifact("repository_readiness_validation", output)
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="validate-readiness",
            status="PASS",
            artifacts=[artifact],
            accepted_output={**validation, "artifacts": [artifact]},
            commands_run=["deterministic readiness validation"],
            errors=[],
            handoff="Repository readiness validation passed and may be published.",
        ),
    )


def _publish(
    request: dict[str, Any],
    *,
    json_output: Path,
    markdown_output: Path,
    receipt: Path,
) -> None:
    inspection = _accepted_input("tau.repository_inspection.v1")
    validation = _accepted_input("tau.repository_readiness_validation.v1")
    if validation.get("status") != "PASS":
        raise RuntimeError("accepted readiness validation did not pass")
    goal = request.get("goal")
    if not isinstance(goal, dict):
        raise RuntimeError("repository readiness request goal is missing")
    report = {
        "schema": "tau.repository_readiness_report.v1",
        "status": "READY",
        "goal": {
            "goal_id": goal["goal_id"],
            "goal_version": goal["goal_version"],
            "goal_hash": goal["goal_hash"],
            "summary": goal["summary"],
        },
        "repository": {
            "path": inspection["repo_path"],
            "head_sha": inspection["head_sha"],
            "branch": inspection["branch"],
            "dirty": inspection["dirty"],
        },
        "policy": {
            "require_clean": request.get("require_clean") is True,
            "validation": "PASS",
        },
        "summary": "Repository is ready for focused work.",
        "proof_scope": {
            "proves": [
                "The named repository was inspected with fixed read-only Git commands.",
                "The requested clean-worktree policy passed.",
            ],
            "does_not_prove": [
                "The repository test suite passes.",
                "The implementation goal is semantically correct.",
                "Provider or model quality.",
                "Production deployment readiness.",
            ],
        },
    }
    _write_json(json_output, report)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(_markdown_report(report), encoding="utf-8")
    artifacts = [
        _artifact("repository_readiness_json", json_output),
        _artifact("repository_readiness_markdown", markdown_output),
    ]
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="publish-readiness",
            status="PASS",
            artifacts=artifacts,
            accepted_output={
                "schema": report["schema"],
                "summary": report["summary"],
                "status": report["status"],
                "artifacts": artifacts,
            },
            commands_run=["deterministic readiness publication"],
            errors=[],
            handoff="The accepted repository-readiness report is available.",
        ),
    )


def _accepted_input(schema: str) -> dict[str, Any]:
    context_value = os.environ.get("TAU_GENERIC_DAG_CONTEXT")
    if not context_value:
        raise RuntimeError("TAU_GENERIC_DAG_CONTEXT is required")
    context = _read_json(Path(context_value), label="generic DAG context")
    inputs = context.get("accepted_inputs")
    if not isinstance(inputs, list):
        raise RuntimeError("generic DAG accepted_inputs is missing")
    for item in inputs:
        if not isinstance(item, dict):
            continue
        if item.get("schema") == schema:
            return item
        accepted = item.get("accepted_output")
        if isinstance(accepted, dict) and accepted.get("schema") == schema:
            return accepted
    raise RuntimeError(f"accepted input missing schema {schema}")


def _receipt(
    request: dict[str, Any],
    *,
    node_id: str,
    status: str,
    artifacts: list[dict[str, str]],
    accepted_output: dict[str, Any] | None,
    commands_run: list[str],
    errors: list[str],
    handoff: str,
) -> dict[str, Any]:
    return {
        "schema": NODE_RECEIPT_SCHEMA,
        "node_id": node_id,
        "status": status,
        "verdict": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": _goal_hash(request),
        "artifacts": artifacts,
        "accepted_output": accepted_output,
        "commands_run": commands_run,
        "errors": errors,
        "policy_exceptions": [],
        "handoff_summary": handoff,
    }


def _goal_hash(request: dict[str, Any]) -> str:
    goal = request.get("goal")
    if not isinstance(goal, dict) or not isinstance(goal.get("goal_hash"), str):
        raise RuntimeError("repository readiness request goal_hash is missing")
    return str(goal["goal_hash"])


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _artifact(kind: str, path: Path) -> dict[str, str]:
    return {"kind": kind, "path": str(path.resolve()), "sha256": _file_sha256(path)}


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"repository readiness request missing {key}")
    return value


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unavailable: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be an object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _markdown_report(report: dict[str, Any]) -> str:
    repository = report["repository"]
    goal = report["goal"]
    return (
        "# Repository Readiness\n\n"
        f"**Status:** {report['status']}\n\n"
        f"**Goal:** {goal['summary']}\n\n"
        f"**Repository:** `{repository['path']}`\n\n"
        f"**HEAD:** `{repository['head_sha']}`\n\n"
        f"**Branch:** `{repository['branch']}`\n\n"
        f"{report['summary']}\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
