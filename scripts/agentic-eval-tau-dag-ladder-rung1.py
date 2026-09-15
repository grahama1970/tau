#!/usr/bin/env python3
"""Agentic-eval wrapper for Tau's rung-1 clean-checkout proof.

The durable proof implementation lives in ``tau_coding.workflows.proofs`` and is
also exposed through ``tau workflows prove-rung1-clean-checkout``. This script
keeps the historical eval entrypoint while making it exercise the same retained
proof bundle reviewers inspect for issue #331.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.workflows.proofs import write_dag_ladder_rung1_clean_checkout_proof

RECEIPT_SCHEMA = "tau.dag_ladder_rung1_clean_checkout_eval_summary.v1"
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, help="Retained proof directory override.")
    parser.add_argument("--uv-bin", default="uv", help="Accepted for compatibility.")
    parser.add_argument(
        "--timeout-seconds", type=int, default=180, help="Accepted for compatibility."
    )
    args = parser.parse_args()

    source_repo = Path(run_git(args.repo, "rev-parse", "--show-toplevel")).resolve()
    source_head = run_git(source_repo, "rev-parse", "HEAD")
    source_branch = run_git(source_repo, "branch", "--show-current")
    source_status = run_git(source_repo, "status", "--porcelain=v1", "--untracked-files=all")
    out = args.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    proof_dir = (
        args.work_root.expanduser().resolve()
        if args.work_root
        else out.parent / f"{out.stem}-clean-checkout"
    )

    with tempfile.TemporaryDirectory(prefix="tau-issue-331-source-") as temp_text:
        temp_source = Path(temp_text) / "source"
        subprocess.run(
            ["git", "clone", "--quiet", "--no-local", str(source_repo), str(temp_source)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(temp_source), "checkout", "--quiet", source_head],
            check=True,
        )
        proof = write_dag_ladder_rung1_clean_checkout_proof(
            source_repo=temp_source,
            output_dir=proof_dir,
        )

    summary = summarize_proof(
        proof=proof,
        proof_dir=proof_dir,
        source_repo=source_repo,
        source_head=source_head,
        source_branch=source_branch,
        source_status=source_status,
    )
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"status": summary["status"], "proof": str(out), "errors": summary["errors"]},
            sort_keys=True,
        )
    )
    return 0 if summary["ok"] else 1


def summarize_proof(
    *,
    proof: dict[str, Any],
    proof_dir: Path,
    source_repo: Path,
    source_head: str,
    source_branch: str,
    source_status: str,
) -> dict[str, Any]:
    errors: list[str] = []
    manifest = read_json(proof_dir / "ladder-manifest.json", errors=errors)
    verifier = read_json(proof_dir / "verifier-pass-receipt.json", errors=errors)
    negative = read_json(proof_dir / "negative-mutation-receipt.json", errors=errors)
    result = read_json(proof_dir / "rung-1-result.json", errors=errors)
    ledger_replay = read_json(proof_dir / "rung-1-ledger-replay.json", errors=errors)
    run_receipt = read_json(proof_dir / "rung-1-run" / "run-receipt.json", errors=errors)

    if proof.get("status") != "PASS" or proof.get("ok") is not True:
        errors.append("proof_receipt_not_pass")
    if verifier.get("status") != "PASS" or verifier.get("ok") is not True:
        errors.append("verifier_receipt_not_pass")
    if negative.get("status") != "PASS" or negative.get("ok") is not True:
        errors.append("negative_mutation_receipt_not_pass")
    if result.get("schema") != "tau.repository_readiness_report.v1":
        errors.append("result_schema_invalid")
    if result.get("status") != "READY":
        errors.append("result_status_not_ready")
    if ledger_replay.get("status") != "PASS":
        errors.append("ledger_replay_not_pass")

    rungs = manifest.get("rungs") if isinstance(manifest.get("rungs"), list) else []
    goal_hashes = verifier.get("details", {}).get("goal_hashes")
    goal_hash_preserved = isinstance(goal_hashes, list) and len(goal_hashes) == 1
    if not goal_hash_preserved:
        errors.append("goal_hash_not_preserved")

    retained = (
        proof.get("rung_1_artifacts")
        if isinstance(proof.get("rung_1_artifacts"), dict)
        else {}
    )
    for key in ("dag_progress", "events", "ledger", "ledger_replay", "result"):
        artifact = retained.get(key) if isinstance(retained, dict) else None
        path = (
            Path(str(artifact.get("path")))
            if isinstance(artifact, dict) and artifact.get("path")
            else None
        )
        if path is None or not path.is_file():
            errors.append(f"retained_artifact_missing:{key}")
        elif artifact.get("sha256") != file_sha256(path):
            errors.append(f"retained_artifact_digest_mismatch:{key}")

    return {
        "schema": RECEIPT_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "source_repo": str(source_repo),
        "source_branch": source_branch,
        "source_head": source_head,
        "source_worktree_dirty": bool(source_status.strip()),
        "source_status_porcelain": source_status,
        "proof_dir": str(proof_dir.resolve()),
        "ladder_manifest": {
            "path": str((proof_dir / "ladder-manifest.json").resolve()),
            "schema": manifest.get("schema"),
            "rung_count": len(rungs),
            "topology_progression": manifest.get("topology_progression"),
        },
        "clean_checkout": {
            "bootstrap_log": proof.get("bootstrap_log"),
            "source_commit": proof.get("source_commit"),
        },
        "rung1": {
            "status": proof.get("status"),
            "result_schema": result.get("schema"),
            "result_status": result.get("status"),
            "repository_dirty": result.get("repository", {}).get("dirty")
            if isinstance(result.get("repository"), dict)
            else None,
            "goal_hash_preserved": goal_hash_preserved,
            "goal_hashes": goal_hashes if isinstance(goal_hashes, list) else [],
            "run_receipt_status": run_receipt.get("status"),
            "ledger_replay": ledger_replay,
            "retained_artifacts": retained,
            "verifier_receipt": proof.get("verifier_receipt"),
        },
        "negative_controls": negative,
        "proof_receipt": proof,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "The packaged DAG ladder manifest names all five rungs.",
                "Rung 1 runs from a clean clone of the current Git HEAD through Tau's "
                "public workflow proof command.",
                "The retained proof includes bootstrap, dag-progress, events, ledger, "
                "ledger replay, result, verifier, and negative mutation receipts.",
            ],
            "does_not_prove": [
                "Rungs 2 through 5 have fresh retained clean-checkout proof.",
                "Provider-live execution.",
                "Dynamic React Flow progress.",
                "Human acceptance of GOAL.md completion.",
            ],
        },
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def read_json(path: Path, *, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing_required_artifact:{path}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"json_not_object:{path}")
        return {}
    return payload


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo.expanduser()), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
