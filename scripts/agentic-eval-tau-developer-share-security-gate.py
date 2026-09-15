#!/usr/bin/env python3
"""Real-world eval for tau#343 developer-share security gate."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from tau_coding.security_audit_conformance import (
    write_developer_share_security_gate,
    write_issue_343_security_gate_verification,
)

CANDIDATE_PATHS = (
    "src/tau_coding/browser_cdp_proof.py",
    "src/tau_coding/coding_worker_adapters.py",
    "src/tau_coding/dag_runtime/ticket_repair_release.py",
    "src/tau_coding/dag_runtime/triage_error_bridge.py",
    "src/tau_coding/external_workspace.py",
    "src/tau_coding/project_dag.py",
    "src/tau_coding/runtime_handshake.py",
    "src/tau_coding/security_audit_conformance.py",
    "tests/test_security_audit_conformance.py",
    "evals/tau_developer_share_security_gate_agentic_eval.json",
    "scripts/agentic-eval-tau-developer-share-security-gate.py",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-commit")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    work = args.work.expanduser().resolve()
    out = args.out.expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    source_commit = args.source_commit or _candidate_commit(repo)

    gate_path = work / "security-gate.json"
    verification_path = work / "verification.json"
    gate = write_developer_share_security_gate(repo, source_commit=source_commit, output=gate_path)
    verification = write_issue_343_security_gate_verification(
        repo, source_commit=source_commit, output=verification_path
    )
    runtime_grep = _runtime_path_grep(repo, source_commit)
    checks = {
        "exact_commit_gate_ready": gate["status"] == "PASS"
        and gate["share_readiness"] == "READY"
        and gate["counts"]["unresolved_blockers"] == 0,
        "dispositions_reconcile": gate["counts"]["reconciled"] is True
        and gate["counts"]["findings"] == gate["counts"]["dispositions"],
        "runtime_paths_not_required": gate["runtime_python_path_scan"][
            "normal_installed_wheel_required_count"
        ]
        == 0,
        "controlled_seed_blocks": verification["checks"]["controlled_seeded_gate_blocks"] is True,
        "controlled_clean_passes": verification["checks"]["controlled_clean_gate_passes"] is True,
        "verification_passes": verification["status"] == "PASS",
        "runtime_python_grep_reviewed": runtime_grep["exit_code"] in {0, 1},
        "live_unmocked": gate["mocked"] is False
        and gate["live"] is True
        and verification["mocked"] is False
        and verification["live"] is True,
    }
    payload: dict[str, Any] = {
        "schema": "tau.developer_share_security_gate_agentic_eval.v1",
        "ok": all(checks.values()),
        "mocked": False,
        "live": True,
        "source_commit": source_commit,
        "candidate_paths": list(CANDIDATE_PATHS),
        "artifacts": {
            "security_gate": str(gate_path),
            "verification": str(verification_path),
        },
        "gate_counts": gate["counts"],
        "scan_hashes": gate["scan_hashes"],
        "runtime_python_path_scan": gate["runtime_python_path_scan"],
        "runtime_path_grep": runtime_grep,
        "checks": checks,
        "what_was_checked": gate["what_was_checked"],
        "remains_unverified": gate["remains_unverified"],
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


def _candidate_commit(repo: Path) -> str:
    index = repo / ".git" / "tau-343-agentic-eval.index"
    if index.exists():
        index.unlink()
    env = {"GIT_INDEX_FILE": str(index)}
    _git(repo, ["read-tree", "HEAD"], env)
    present = [path for path in CANDIDATE_PATHS if (repo / path).exists()]
    _git(repo, ["add", "--", *present], env)
    tree = _git(repo, ["write-tree"], env).strip()
    commit = _git(repo, ["commit-tree", tree, "-p", "HEAD", "-m", "tau#343 candidate"] , env).strip()
    index.unlink(missing_ok=True)
    return commit


def _runtime_path_grep(repo: Path, source_commit: str) -> dict[str, Any]:
    command = [
        "git",
        "-C",
        str(repo),
        "grep",
        "-nE",
        r"/home/|/Users/|workspace/experiments|Path\.home\(\) / \\\"workspace\\\"|Path\.home\(\) / \\\"\\.pi\\\"",
        source_commit,
        "--",
        "src/**/*.py",
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=60)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip().splitlines(),
        "stderr": completed.stderr.strip(),
    }


def _git(repo: Path, args: list[str], env: dict[str, str] | None = None) -> str:
    full_env = None
    if env is not None:
        import os

        full_env = os.environ.copy()
        full_env.update(env)
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
        env=full_env,
        timeout=60,
    )
    return completed.stdout


if __name__ == "__main__":
    raise SystemExit(main())
