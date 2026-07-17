"""Deterministic artifact worker used by Tau's canonical DAG examples."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_blocked_receipt(
    path: Path, *, node_id: str, verdict: str, error: str, goal_hash: str
) -> None:
    _write_json(
        path,
        {
            "schema": "tau.generic_dag_node_receipt.v1",
            "node_id": node_id,
            "status": "BLOCKED",
            "verdict": verdict,
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": goal_hash,
            "artifacts": [],
            "commands_run": [f"canonical-worker:{node_id}"],
            "handoff_summary": error,
            "errors": [error],
            "policy_exceptions": [],
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--goal", type=Path, required=True)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--fail-once-marker", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--repair-authorization", type=Path)
    parser.add_argument("--input", type=Path, action="append", default=[])
    parser.add_argument("--rollback", type=Path)
    parser.add_argument("--blocked-reason")
    args = parser.parse_args()

    context_path = Path(os.environ["TAU_GENERIC_DAG_CONTEXT"])
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if context.get("node_id") != args.node_id:
        raise RuntimeError("dag_context_node_mismatch")
    goal_sha256 = _sha256(args.goal)
    if args.blocked_reason:
        _write_blocked_receipt(
            args.receipt,
            node_id=args.node_id,
            verdict="BLOCKED",
            error=args.blocked_reason,
            goal_hash=goal_sha256,
        )
        return 0
    if args.fail_once_marker and not args.fail_once_marker.exists():
        args.fail_once_marker.parent.mkdir(parents=True, exist_ok=True)
        args.fail_once_marker.write_text("retry required\n", encoding="utf-8")
        _write_blocked_receipt(
            args.receipt,
            node_id=args.node_id,
            verdict="REVISE",
            error="intentional_first_attempt_revise",
            goal_hash=goal_sha256,
        )
        return 0
    if args.approval and not args.approval.is_file():
        _write_blocked_receipt(
            args.receipt,
            node_id=args.node_id,
            verdict="BLOCKED",
            error=f"human_approval_required:{args.approval}",
            goal_hash=goal_sha256,
        )
        return 0
    if args.repair_authorization and not args.repair_authorization.is_file():
        _write_blocked_receipt(
            args.receipt,
            node_id=args.node_id,
            verdict="BLOCKED",
            error=f"targeted_repair_required:{args.repair_authorization}",
            goal_hash=goal_sha256,
        )
        return 0

    inputs = []
    for path in args.input:
        if not path.is_file():
            raise RuntimeError(f"accepted_dependency_artifact_missing:{path}")
        inputs.append({"path": str(path), "sha256": _sha256(path)})
    if args.rollback:
        _write_json(
            args.rollback,
            {
                "schema": "tau.canonical_dag_rollback.v1",
                "target": str(args.output),
                "target_existed_before": args.output.exists(),
                "prior_sha256": _sha256(args.output) if args.output.exists() else None,
                "rollback_action": (
                    "restore prior bytes" if args.output.exists() else "delete target"
                ),
            },
        )

    time.sleep(max(0.0, args.delay))
    output = {
        "schema": "tau.canonical_dag_artifact.v1",
        "node_id": args.node_id,
        "role": args.role,
        "goal_sha256": goal_sha256,
        "dependency_ids": sorted(context.get("accepted_inputs", []), key=str),
        "accepted_input_artifacts": inputs,
        "approval_path": str(args.approval) if args.approval else None,
        "repair_authorization_path": (
            str(args.repair_authorization) if args.repair_authorization else None
        ),
    }
    _write_json(args.output, output)
    output_artifact = {
        "kind": args.role,
        "path": str(args.output),
        "sha256": _sha256(args.output),
        "bytes": args.output.stat().st_size,
    }
    _write_json(
        args.receipt,
        {
            "schema": "tau.generic_dag_node_receipt.v1",
            "node_id": args.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": goal_sha256,
            "artifacts": [
                output_artifact,
                *(
                    [
                        {
                            "kind": "rollback-manifest",
                            "path": str(args.rollback),
                            "sha256": _sha256(args.rollback),
                            "bytes": args.rollback.stat().st_size,
                        }
                    ]
                    if args.rollback
                    else []
                ),
            ],
            "accepted_output": {
                "schema": "tau.canonical_dag_result.v1",
                "summary": f"{args.node_id} produced its accepted {args.role} artifact.",
                "status": "ACCEPTED",
                "artifacts": [output_artifact],
            },
            "commands_run": [f"canonical-worker:{args.node_id}"],
            "handoff_summary": f"{args.node_id} produced its bounded artifact.",
            "errors": [],
            "policy_exceptions": [],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
