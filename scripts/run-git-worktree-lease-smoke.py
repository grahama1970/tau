#!/usr/bin/env python3
"""Exercise Tau's worktree lease lifecycle against a real repository."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from tau_coding.runtime_backends import (
    GitWorktreeLeaseManager,
    worktree_discard_authorization,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tau-worktree-lease-smoke-") as temporary:
        manager = GitWorktreeLeaseManager(Path(temporary) / "state", owner="tau-smoke")
        lease = manager.allocate(
            repository=args.repository,
            run_id="worktree-lease-smoke",
            plan_revision="smoke-v1",
            node_id="bounded-writer",
            attempt_id="attempt-1",
            base_commit="HEAD",
            allowed_paths=("tau-worktree-smoke",),
        )
        restarted = GitWorktreeLeaseManager(Path(temporary) / "state", owner="tau-smoke")
        rediscovered = restarted.rediscover(run_id=lease.run_id)
        with restarted.writer_guard(lease) as worktree:
            marker = worktree / "tau-worktree-smoke" / "marker.txt"
            marker.parent.mkdir(parents=True)
            marker.write_text("real Git worktree lease smoke\n", encoding="utf-8")
        inspection = restarted.inspect(lease)
        blocked_cleanup = restarted.cleanup(lease)
        authorization = worktree_discard_authorization(lease, inspection)
        cleanup = restarted.cleanup(lease, discard_authorization=authorization)

        checks = {
            "real_worktree_distinct": Path(lease.worktree_path).resolve()
            != args.repository.resolve(),
            "restart_rediscovered_exact_lease": rediscovered == (lease,),
            "allowed_change_inspection_passed": inspection["status"] == "PASS",
            "dirty_cleanup_blocked": blocked_cleanup["status"] == "BLOCKED",
            "exact_discard_cleanup_passed": cleanup["status"] == "PASS",
            "worktree_post_verified_absent": cleanup["post_verified_absent"] is True,
        }
        status = "PASS" if all(checks.values()) else "BLOCKED"
        receipt = {
            "schema": "tau.git_worktree_lease_smoke_receipt.v1",
            "status": status,
            "ok": status == "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "repository": str(args.repository.resolve()),
            "lease": lease.to_payload(),
            "inspection": inspection,
            "blocked_cleanup": blocked_cleanup,
            "cleanup": cleanup,
            "checks": checks,
            "proof_scope": {
                "proves": [
                    "Tau created and rediscovered a real per-attempt Git worktree lease.",
                    (
                        "Tau refused dirty unadmitted cleanup and accepted exact "
                        "discard authorization."
                    ),
                    "Tau post-verified that the worktree was absent after cleanup.",
                ],
                "does_not_prove": [
                    "Scheduler integration or automatic worktree allocation for every DAG node.",
                    "Durable runtime-event reconciliation owned by later runtime work packages.",
                    "Agent semantic correctness or provider execution.",
                ],
            },
        }
        args.out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(receipt, sort_keys=True))
        return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
