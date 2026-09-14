#!/usr/bin/env python3
"""Clean-archive proof for tau#359 full-suite compatibility."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any


OVERLAY_PATHS = (
    "src/tau_coding/dag_runtime/attempt_result.py",
    "src/tau_coding/dag_runtime/replay.py",
    "src/tau_coding/dag_runtime/run_store.py",
    "src/tau_coding/dag_runtime/scheduler.py",
    "src/tau_coding/dag_template_registry.py",
    "src/tau_coding/dag_viewer/projection.py",
    "src/tau_coding/source_feature_inventory.py",
    "tests/test_dag_node_dispatch_envelope.py",
    "scripts/agentic-eval-tau-full-suite-compatibility.py",
    "evals/tau_full_suite_compatibility_agentic_eval.json",
    "evals/tau_developer_surface_inventory_agentic_eval.json",
)
DELETE_PATHS = ("tests/test_battle_adaptive_parent_reflection.py",)


def _run(
    cmd: list[str], *, cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
) -> dict[str, Any]:
    completed = subprocess.run(
        cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout, env=env
    )
    return {
        "command": cmd,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _copy_overlay(repo: Path, archive: Path) -> list[str]:
    copied: list[str] = []
    for rel in OVERLAY_PATHS:
        src = repo / rel
        if not src.exists():
            continue
        dst = archive / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel)
    for rel in DELETE_PATHS:
        target = archive / rel
        if target.exists():
            target.unlink()
    return copied


def _pytest_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.endswith("_API_KEY") or key.endswith("_ACCESS_TOKEN"):
            env.pop(key, None)
    return env


def _extract_summary(stdout: str, stderr: str) -> dict[str, Any]:
    text = "\n".join((stdout, stderr))
    summary: dict[str, Any] = {}
    for pattern, key in (
        (r"(\d+) passed", "passed"),
        (r"(\d+) failed", "failed"),
        (r"(\d+) errors?", "errors"),
        (r"(\d+) skipped", "skipped"),
    ):
        matches = re.findall(pattern, text)
        if matches:
            summary[key] = int(matches[-1])
    summary.setdefault("failed", 0)
    summary.setdefault("errors", 0)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work")
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    work = Path(args.work).expanduser().resolve() if args.work else Path(tempfile.mkdtemp())
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    archive = work / "tau-clean-origin-overlay"
    archive.mkdir()

    archive_proc = subprocess.run(
        ["git", "archive", "--format=tar", "origin/main"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    tar_path = work / "origin-main.tar"
    tar_path.write_bytes(archive_proc.stdout)
    with tarfile.open(tar_path) as tar:
        tar.extractall(archive, filter="data")

    copied = _copy_overlay(repo, archive)
    test = _run(
        ["uv", "run", "pytest", "--tau-suite=all", "-q"],
        cwd=archive,
        timeout=args.timeout_seconds,
        env=_pytest_env(),
    )
    summary = _extract_summary(test["stdout"], test["stderr"])
    ok = test["exit_code"] == 0 and summary.get("failed", 0) == 0 and summary.get("errors", 0) == 0
    proof = {
        "schema": "tau.full_suite_compatibility_proof.v1",
        "status": "PASS" if ok else "FAIL",
        "ok": ok,
        "mocked": False,
        "live": True,
        "archive": str(archive),
        "base": "origin/main",
        "overlaid_paths": copied,
        "deleted_paths": list(DELETE_PATHS),
        "pytest": {
            "exit_code": test["exit_code"],
            "summary": summary,
            "stdout_tail": test["stdout"][-12000:],
            "stderr_tail": test["stderr"][-12000:],
        },
    }
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
