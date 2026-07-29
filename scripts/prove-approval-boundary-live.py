#!/usr/bin/env python3
"""Live proof for exact approval-boundary and rollback semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tau_coding.workflows.runner import (
    approve_approved_release_bundle,
    resume_approved_release_bundle,
    run_approved_release_bundle_workflow,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _tree_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _local_signature(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("signature", None)
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"local-signature-sha256:{digest}"


def _signed_packet(
    *,
    path: Path,
    target: dict[str, Any],
    reason: str,
    approved: bool = True,
    expires_at: str | None = None,
) -> Path:
    payload: dict[str, Any] = {
        "schema": "tau.human_approval_packet.v1",
        "approved": approved,
        "actor": {"id": "human:approval-boundary-proof", "auth_method": "local-signature"},
        "action": "generic_dag_transaction_continue",
        "target": {str(key): str(value) for key, value in target.items()},
        "reason": reason,
        "evidence": [str(path.parent / "approval-gate-receipt.json")],
        "nonce": hashlib.sha256(json.dumps(target, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    payload["signature"] = _local_signature(payload)
    _write_json(path, payload)
    return path


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("# Release fixture\n", encoding="utf-8")
    (path / "src").mkdir()
    (path / "src" / "release.py").write_text("print('release fixture')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md", "src/release.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Tau Approval Proof",
            "-c",
            "user.email=tau-approval@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return path


def _run_until_approval(base: Path, *, simulate_failure: bool = False) -> dict[str, Any]:
    repo = _git_repo(base / "repo")
    run_dir = base / "run"
    publish_path = base / "published"
    first = run_approved_release_bundle_workflow(
        repo_path=repo,
        human_goal="Publish one exact approval-bound release bundle.",
        publish_path=publish_path,
        run_dir=run_dir,
        open_viewer=False,
        browser_open=False,
        viewer_hold_seconds=None,
        simulate_publish_verification_failure=simulate_failure,
        step_delay_seconds=0.01,
    )
    if first.get("status") != "BLOCKED":
        raise RuntimeError("workflow_did_not_block_for_approval")
    return {"repo": repo, "run_dir": run_dir, "publish_path": publish_path, "first": first}


def _approval_target(run_dir: Path) -> dict[str, Any]:
    gate = _read_json(
        run_dir / "transactions" / "publish-approved-release" / "approval-gate-receipt.json"
    )
    target = gate.get("expected_target")
    if not isinstance(target, dict):
        raise RuntimeError("approval_gate_expected_target_missing")
    return target


def _capture_attempt(base: Path, name: str, payload: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    proof_path = base / "attempts" / f"{name}.json"
    gate_copy = base / "attempts" / f"{name}.approval-gate-receipt.json"
    workflow_copy = base / "attempts" / f"{name}.workflow-approval.json"
    _write_json(proof_path, payload)
    gate_path = run_dir / "transactions" / "publish-approved-release" / "approval-gate-receipt.json"
    workflow_path = run_dir / "receipts" / "workflow-approval.json"
    if gate_path.is_file():
        shutil.copy2(gate_path, gate_copy)
    if workflow_path.is_file():
        shutil.copy2(workflow_path, workflow_copy)
    return {
        "name": name,
        "result_path": str(proof_path),
        "approval_gate_receipt": str(gate_copy) if gate_copy.is_file() else None,
        "workflow_approval_receipt": str(workflow_copy) if workflow_copy.is_file() else None,
        "status": payload.get("status"),
        "errors": payload.get("errors"),
    }


def _normal_flow(root: Path) -> dict[str, Any]:
    base = root / "normal"
    context = _run_until_approval(base)
    run_dir = context["run_dir"]
    publish_path = context["publish_path"]
    target = _approval_target(run_dir)
    before_hashes = _tree_hashes(publish_path)
    attempts = []

    denied = approve_approved_release_bundle(run_dir=run_dir)
    attempts.append(_capture_attempt(base, "denied-missing-packet", denied, run_dir))
    malformed_packet = base / "malformed-approval.json"
    malformed_packet.write_text("{not-json", encoding="utf-8")
    malformed = approve_approved_release_bundle(run_dir=run_dir, approval_packet=malformed_packet)
    attempts.append(_capture_attempt(base, "malformed-packet", malformed, run_dir))

    mismatch_target = {**target, "node_id": "wrong-node"}
    mismatch_packet = _signed_packet(
        path=base / "mismatched-approval.json",
        target=mismatch_target,
        reason="mismatched approval must fail closed",
    )
    mismatch = approve_approved_release_bundle(run_dir=run_dir, approval_packet=mismatch_packet)
    attempts.append(_capture_attempt(base, "mismatched-target", mismatch, run_dir))

    stale_packet = _signed_packet(
        path=base / "expired-approval.json",
        target=target,
        reason="expired approval must fail closed",
        expires_at="2000-01-01T00:00:00Z",
    )
    stale = approve_approved_release_bundle(run_dir=run_dir, approval_packet=stale_packet)
    attempts.append(_capture_attempt(base, "expired-approval", stale, run_dir))

    no_side_effect_after_blockers = not publish_path.exists()
    approval_packet = _signed_packet(
        path=base / "exact-approval.json",
        target=target,
        reason="approve exact rollback-protected publication side effect",
    )
    approved = approve_approved_release_bundle(run_dir=run_dir, approval_packet=approval_packet)
    attempts.append(_capture_attempt(base, "exact-approval", approved, run_dir))
    final = resume_approved_release_bundle(run_dir=run_dir)
    repeated_resume = resume_approved_release_bundle(run_dir=run_dir)
    after_hashes = _tree_hashes(publish_path)
    ledger_path = publish_path / "publication-ledger.json"
    ledger = _read_json(ledger_path)
    final_gate = _read_json(
        run_dir / "transactions" / "publish-approved-release" / "approval-gate-receipt.json"
    )
    final_workflow_approval = _read_json(run_dir / "receipts" / "workflow-approval.json")
    return {
        "run_dir": str(run_dir),
        "publish_path": str(publish_path),
        "target": target,
        "approval_packet": str(approval_packet),
        "approval_packet_sha256": _sha256(approval_packet),
        "approval_gate_receipt": str(
            run_dir / "transactions" / "publish-approved-release" / "approval-gate-receipt.json"
        ),
        "workflow_approval_receipt": str(run_dir / "receipts" / "workflow-approval.json"),
        "run_receipt": str(run_dir / "run-receipt.json"),
        "attempts": attempts,
        "before_hashes": before_hashes,
        "after_hashes": after_hashes,
        "side_effect_ledger": str(ledger_path),
        "side_effect_ledger_payload": ledger,
        "final": final,
        "repeated_resume": repeated_resume,
        "final_gate_summary": {
            "status": final_gate.get("status"),
            "live": final_gate.get("live"),
            "approved": final_gate.get("approved"),
            "packet_summary": final_gate.get("packet_summary"),
            "expected_target": final_gate.get("expected_target"),
        },
        "workflow_approval_summary": final_workflow_approval,
        "checks": {
            "initial_blocked_for_approval": context["first"].get("status") == "BLOCKED",
            "target_binds_goal_run_node_action": all(
                key in target for key in ("goal_hash", "run_id", "node_id", "action")
            ),
            "target_binds_side_effect_and_rollback": all(
                key in target
                for key in (
                    "expected_side_effect_path",
                    "side_effect_ledger_path",
                    "rollback_artifact_path",
                )
            ),
            "denied_malformed_mismatch_expired_blocked": all(
                item["status"] == "BLOCKED" for item in attempts[:4]
            ),
            "no_side_effect_before_exact_approval": before_hashes == {}
            and no_side_effect_after_blockers,
            "exact_approval_passed": approved.get("status") == "PASS",
            "approved_execution_passed": final.get("status") == "PASS",
            "repeated_resume_did_not_duplicate_effect": repeated_resume.get("status") == "PASS"
            and ledger.get("effect_count") == 1,
            "side_effect_ledger_committed": ledger.get("status") == "COMMITTED"
            and ledger.get("effect_count") == 1,
            "approval_record_preserves_boundary": final_gate.get("live") is True
            and final_gate.get("approved") is True
            and final_gate.get("expected_target") == target,
            "post_hashes_present": all(
                name in after_hashes
                for name in (
                    "approved-release-bundle.json",
                    "approved-release-bundle.md",
                    "publication-ledger.json",
                )
            ),
        },
    }


def _rollback_flow(root: Path) -> dict[str, Any]:
    base = root / "rollback"
    context = _run_until_approval(base, simulate_failure=True)
    run_dir = context["run_dir"]
    publish_path = context["publish_path"]
    target = _approval_target(run_dir)
    approval_packet = _signed_packet(
        path=base / "exact-approval-for-rollback.json",
        target=target,
        reason="approve exact side effect to prove rollback on failed verification",
    )
    approval = approve_approved_release_bundle(run_dir=run_dir, approval_packet=approval_packet)
    resumed = resume_approved_release_bundle(run_dir=run_dir)
    rollback_path = run_dir / "receipts" / "publication-rollback.json"
    rollback = _read_json(rollback_path)
    return {
        "run_dir": str(run_dir),
        "publish_path": str(publish_path),
        "target": target,
        "approval_packet": str(approval_packet),
        "approval": approval,
        "resumed": resumed,
        "rollback_receipt": str(rollback_path),
        "rollback_payload": rollback,
        "post_rollback_hashes": _tree_hashes(publish_path),
        "checks": {
            "approval_passed_before_forced_failure": approval.get("status") == "PASS",
            "resume_blocked_on_post_write_verification": resumed.get("status") == "BLOCKED",
            "rollback_artifact_exists": rollback_path.is_file(),
            "rollback_recorded_verification_failure": rollback.get("status") == "ROLLED_BACK"
            and rollback.get("reason") == "post_write_verification_failed",
            "rollback_removed_side_effect": not publish_path.exists()
            and not (run_dir / "results").exists(),
            "approval_target_named_rollback_artifact": target.get("rollback_artifact_path")
            == str(rollback_path),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    root = args.run_root.expanduser().resolve()
    if root.exists():
        raise RuntimeError(f"proof run root already exists: {root}")
    root.mkdir(parents=True)
    normal = _normal_flow(root)
    rollback = _rollback_flow(root)
    checks = {
        **{f"normal_{key}": value for key, value in normal["checks"].items()},
        **{f"rollback_{key}": value for key, value in rollback["checks"].items()},
    }
    receipt = {
        "schema": "tau.approval_boundary_live_proof.v1",
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "ok": all(checks.values()),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "normal_flow": normal,
        "rollback_flow": rollback,
        "checks": checks,
        "errors": [key for key, value in checks.items() if not value],
    }
    _write_json(args.receipt.expanduser().resolve(), receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
