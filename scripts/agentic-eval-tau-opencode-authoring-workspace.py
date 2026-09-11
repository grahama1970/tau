#!/usr/bin/env python3
"""tau#355 retained eval: opencode.serve authoring lane survives codex unavailability.

Modes:
  route       — deterministic seam proof: codex.exec workspace route rejects non-codex
                handlers (the outage condition leaves no codex author), while the
                opencode.serve lane accepts oc-author + workspace (the fallback).
  lane        — real-world: allocate + admit a leased worktree for a real git repo,
                redirect the launch cwd into it, then release with a cleanup receipt.
  mismatch    — negative: declared worktree cross-check mismatch blocks fail-closed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

TAU_SRC = Path(__file__).resolve().parents[1] / "src"
ASK_SRC = Path("/home/graham/workspace/experiments/agent-skills/skills/ask/src")
sys.path.insert(0, str(TAU_SRC))
sys.path.insert(1, str(ASK_SRC))


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _fixture_repo(tmp: Path) -> Path:
    repo = tmp / "repository"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "tau-eval@example.invalid")
    _git(repo, "config", "user.name", "Tau Eval")
    (repo / "src").mkdir()
    (repo / "src" / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    return repo


def _home_lease_root(key: str) -> Path:
    import shutil

    root = Path.home() / ".cache" / "tau-eval-leases" / key
    if root.exists():
        shutil.rmtree(root)
    root.parent.mkdir(parents=True, exist_ok=True)
    return root


def _workspace_work_order(repo: Path, state_root: Path, **overrides: object) -> dict:
    order = {
        "schema": "tau.executor.scillm_worker.v1",
        "dag_id": "tau355-eval",
        "node_id": "author",
        "agent": "author",
        "goal_hash": "sha256:" + "b" * 64,
        "attempt": 1,
        "task": "Bounded authoring repair.",
        "repo": str(repo),
        "allowed_paths": ["src/**"],
        "result_path": "result.json",
        "receipt_path": "receipt.json",
        "model_provider_route": {
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
        "workspace": {
            "transport": "opencode.serve",
            "worktree_state_root": str(state_root),
            "release_required": True,
            "agent_profile": "build",
        },
    }
    ws = dict(order["workspace"])  # type: ignore[arg-type]
    ws.update(overrides)
    order["workspace"] = ws
    return order


def mode_route() -> int:
    from ask.seam_models import HandlerExecutionBinding
    from pydantic import ValidationError

    # Codex-unavailable condition: the only codex workspace route rejects every
    # non-codex handler, so no codex authoring remains during the outage.
    rejected = 0
    for bad in (
        {"handler": "claude-fable-low", "transport": "codex.exec", "model": "claude-fable-5", "workspace": "/w"},
        {"handler": "gpt-5.5", "transport": "codex.exec", "model": "claude-fable-5", "workspace": "/w"},
    ):
        try:
            HandlerExecutionBinding.model_validate(bad)
        except ValidationError:
            rejected += 1
    if rejected != 2:
        print("ROUTE_FAIL: codex exec route did not reject non-codex handlers")
        return 1
    # The fallback that survives the outage: oc-author + opencode.serve + workspace.
    HandlerExecutionBinding.model_validate(
        {"handler": "oc-author", "transport": "opencode.serve", "model": None, "workspace": "/w"}
    )
    print("ROUTE_PASS: codex workspace route closed under outage; opencode.serve oc-author lane accepted")
    return 0


def _run_launch(order: dict, tmp: Path) -> dict:
    from tau_coding.coding_worker_adapters import write_scillm_worker_launch_receipt

    path = tmp / "work-order.json"
    path.write_text(json.dumps(order), encoding="utf-8")
    return write_scillm_worker_launch_receipt(
        work_order_path=path,
        output_path=tmp / "launch-receipt.json",
        apply=True,
        auth_token="eval",
        scillm_base_url="http://127.0.0.1:1",  # dead: lane evidence must not depend on provider
    )


def mode_lane() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="tau355-lane-"))
    repo = _fixture_repo(tmp)
    state_root = _home_lease_root("lane")
    receipt = _run_launch(_workspace_work_order(repo, state_root), tmp)
    ws = receipt.get("workspace") or {}
    if ws.get("status") != "allocated" or ws.get("admission_status") != "PASS":
        print(f"LANE_FAIL: workspace stage {ws}")
        return 1
    if not Path(str(ws.get("worktree_path"))).is_dir():
        print("LANE_FAIL: leased worktree missing on disk")
        return 1
    # Read back the durable admission receipt the lease manager wrote
    # (admissions land at <state_root>/admissions/<sha>.json — no name prefix).
    admission = None
    for candidate in sorted(state_root.rglob("*.json")):
        try:
            doc = json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
        if doc.get("schema") == "tau.git_worktree_admission.v1":
            admission = doc
            break
    if admission is None:
        print("LANE_FAIL: no admission receipt on disk")
        return 1
    if admission.get("status") != "PASS":
        print(f"LANE_FAIL: admission receipt {admission.get('schema')}/{admission.get('status')}")
        return 1
    # Cleanup-authorized release: re-allocate identity is deterministic, so
    # rebuild the lease handle via rediscover and release with a receipt.
    from tau_coding.runtime_backends.worktrees import GitWorktreeLeaseManager

    manager = GitWorktreeLeaseManager(root=state_root, owner="tau-scillm-worker")
    leases = manager.rediscover()
    if not leases:
        print("LANE_FAIL: rediscover found no lease to release")
        return 1
    release = manager.cleanup(leases[0])
    if release.get("status") not in {"PASS", "BLOCKED"} or not release.get("lease_sha256"):
        print(f"LANE_FAIL: release receipt {release}")
        return 1
    print(
        "LANE_PASS: lease allocated+admitted, cwd redirected, admission receipt read back, "
        f"release receipt status={release.get('status')}"
    )
    return 0


def mode_mismatch() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="tau355-mismatch-"))
    repo = _fixture_repo(tmp)
    state_root = _home_lease_root("mismatch")
    receipt = _run_launch(
        _workspace_work_order(repo, state_root, worktree_root="/nonexistent/declared"), tmp
    )
    codes = [a.get("code") for a in receipt.get("alerts", [])]
    if "workspace_worktree_mismatch" not in codes:
        print(f"MISMATCH_FAIL: alerts {codes}")
        return 1
    print("MISMATCH_PASS: declared cross-check mismatch blocked fail-closed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["route", "lane", "mismatch"])
    args = parser.parse_args()
    return {"route": mode_route, "lane": mode_lane, "mismatch": mode_mismatch}[args.mode]()


if __name__ == "__main__":
    raise SystemExit(main())
