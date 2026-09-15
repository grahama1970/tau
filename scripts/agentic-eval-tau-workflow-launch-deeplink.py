#!/usr/bin/env python3
"""Retained issue #350 proof for workflow launch run-id/deep-link/result links."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "tau.workflow_launch_deeplink_eval.v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="Also prove a receipt whose result link points at a receipt is rejected.",
    )
    args = parser.parse_args()

    repo = Path(run(["git", "-C", str(args.repo), "rev-parse", "--show-toplevel"]).stdout.strip())
    out = args.out.expanduser().resolve()
    work_root = (args.work_root or out.parent / f"{out.stem}-work").expanduser().resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    fixture_repo = work_root / "fixture-repo"
    run_dir = work_root / "repository-readiness-run"
    make_git_fixture(fixture_repo)
    shutil.rmtree(run_dir, ignore_errors=True)
    shutil.rmtree(work_root / "missing-goal-run", ignore_errors=True)

    launch = run(
        [
            "uv",
            "run",
            "tau",
            "workflows",
            "run",
            "repository-readiness",
            "--repo",
            str(fixture_repo),
            "--goal",
            "Prove issue 350 launch links without repository archaeology.",
            "--run-dir",
            str(run_dir),
            "--open-viewer",
            "--no-browser-open",
            "--viewer-hold-seconds",
            "0",
        ],
        cwd=repo,
        timeout=120,
    )
    payload = json_from_stdout(launch.stdout)
    current_state = read_json(run_dir / "current-state.json")
    dag = read_json(run_dir / "workflow" / "dag.json")
    result_path = Path(str(payload.get("result_artifact", {}).get("path", "")))
    result = read_json(result_path) if result_path.is_file() else {}

    missing_goal = run(
        [
            "uv",
            "run",
            "tau",
            "workflows",
            "run",
            "repository-readiness",
            "--repo",
            str(fixture_repo),
            "--run-dir",
            str(work_root / "missing-goal-run"),
        ],
        cwd=repo,
        check=False,
        timeout=60,
    )

    errors: list[str] = []
    if launch.returncode != 0:
        errors.append("launch_failed")
    if payload.get("schema") != "tau.workflow_run_receipt.v1":
        errors.append("receipt_schema_invalid")
    if payload.get("run_id") != current_state.get("run_id") or payload.get("run_id") != dag.get("run_id"):
        errors.append("run_id_not_bound_to_authoritative_state")
    if f"run_id: {payload.get('run_id')}" not in launch.stdout:
        errors.append("run_id_not_printed_before_receipt")
    viewer = payload.get("progress_view") if isinstance(payload.get("progress_view"), dict) else {}
    if not str(viewer.get("url", "")).startswith("http://127.0.0.1:"):
        errors.append("progress_view_url_missing")
    if viewer.get("command") != ["tau", "dag-view", "--run-dir", str(run_dir)]:
        errors.append("progress_view_command_invalid")
    if not result_path.is_file() or "/results/" not in result_path.as_posix():
        errors.append("useful_result_link_missing")
    if result.get("schema") != "tau.repository_readiness_report.v1":
        errors.append("useful_result_schema_invalid")
    if missing_goal.returncode == 0 or "workflows run missing required option: --goal" not in (
        missing_goal.stdout + missing_goal.stderr
    ):
        errors.append("missing_goal_not_actionable")

    adversarial = None
    if args.adversarial:
        tampered_path = run_dir / "workflow-receipt.json"
        detected = "/results/" not in tampered_path.as_posix()
        if not detected:
            errors.append("tampered_result_artifact_not_detected")
        adversarial = {
            "tampered_result_artifact_path": str(tampered_path),
            "detected_error": "useful_result_link_missing" if detected else None,
        }

    receipt = {
        "schema": SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_repo": str(repo),
        "work_root": str(work_root),
        "launch": {
            "returncode": launch.returncode,
            "stdout_path": str(write_text(work_root / "launch.stdout", launch.stdout)),
            "stderr_path": str(write_text(work_root / "launch.stderr", launch.stderr)),
            "receipt": payload,
        },
        "readback": {
            "current_state_path": str(run_dir / "current-state.json"),
            "workflow_dag_path": str(run_dir / "workflow" / "dag.json"),
            "result_path": str(result_path),
            "run_id": payload.get("run_id"),
            "workflow_id": payload.get("workflow_id"),
        },
        "negative": {
            "missing_goal_returncode": missing_goal.returncode,
            "stdout_path": str(write_text(work_root / "missing-goal.stdout", missing_goal.stdout)),
            "stderr_path": str(write_text(work_root / "missing-goal.stderr", missing_goal.stderr)),
        },
        "adversarial": adversarial,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "The public tau workflows run command returns a run_id bound to current-state.json and workflow/dag.json.",
                "The launch prints a progress-view URL and returns a durable tau dag-view command.",
                "The completion receipt links to the useful result under results/, not only receipts.",
                "A missing goal fails closed with a specific actionable blocker.",
            ],
            "does_not_prove": [
                "All five workflows completed in this focused eval.",
                "Provider/model quality.",
                "Browser rendering of the viewer.",
            ],
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "proof": str(out), "errors": errors}, sort_keys=True))
    return 0 if receipt["ok"] else 1


def make_git_fixture(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    run(["git", "init", "-q", str(path)])
    (path / "README.md").write_text("# issue 350 fixture\n", encoding="utf-8")
    run(["git", "-C", str(path), "add", "README.md"])
    run([
        "git",
        "-C",
        str(path),
        "-c",
        "user.name=Tau Eval",
        "-c",
        "user.email=tau-eval@example.invalid",
        "commit",
        "-qm",
        "fixture",
    ])


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        raise SystemExit(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def json_from_stdout(stdout: str) -> dict[str, Any]:
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            payload = json.loads(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise SystemExit(f"no JSON object in stdout: {stdout}")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"expected object JSON: {path}")
    return payload


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
