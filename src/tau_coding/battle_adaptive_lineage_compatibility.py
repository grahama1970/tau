"""Verify the current Tau/Battle adaptive-lineage contract pair."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

GATE = "ADAPTIVE_LINEAGE_GATE_V2"


def verify_pair(*, tau_root: Path, agent_skills_root: Path) -> dict[str, Any]:
    tau_root = tau_root.expanduser().resolve()
    agent_skills_root = agent_skills_root.expanduser().resolve()
    tau_source = tau_root / "src" / "tau_coding" / "battle_live_handoff.py"
    battle_source = (
        agent_skills_root
        / "skills"
        / "battle"
        / "src"
        / "battle_skill"
        / "arena_live_battle_proof.py"
    )
    tau_text = tau_source.read_text(encoding="utf-8")
    battle_text = battle_source.read_text(encoding="utf-8")
    checks = {
        "tau_gate_marker": GATE in tau_text,
        "battle_gate_marker": GATE in battle_text,
        "parent_spawn_decision_cli": '"--parent-spawn-decision"' in tau_text,
        "pressure_receipt_cli": '"--pressure-receipt"' in tau_text,
        "spawn_decision_receipt_cli": '"--spawn-decision-receipt"' in tau_text,
        "battle_invokes_parent_decision": '"--parent-spawn-decision"' in battle_text,
        "battle_forwards_pressure": '"--pressure-receipt"' in battle_text,
        "battle_forwards_spawn_decision": '"--spawn-decision-receipt"' in battle_text,
    }
    errors = [name for name, passed in checks.items() if not passed]
    return {
        "schema": "tau.battle_adaptive_lineage_compatibility.v1",
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "gate": GATE,
        "compatible_pair": {
            "tau": _git_sha(tau_root),
            "agent_skills": _git_sha(agent_skills_root),
        },
        "checks": checks,
        "errors": errors,
        "mocked": False,
        "live": False,
        "proof_scope": {
            "proves": [
                "The named current worktrees expose the same versioned adaptive-lineage contract."
            ],
            "does_not_prove": [
                "The live Scillm calls or Docker Battle arena pass.",
                "The named commits are remote canonical refs unless separately verified.",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tau-root", type=Path, required=True)
    parser.add_argument("--agent-skills-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = verify_pair(tau_root=args.tau_root, agent_skills_root=args.agent_skills_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


def _git_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"cannot resolve Git SHA for {root}: {result.stderr.strip()}")
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
