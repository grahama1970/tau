"""Command-loop adapter for Scillm subagent substrate gate checks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from tau_coding.scillm_subagent_gate import validate_scillm_subagent_loop_summary


def main(argv: list[str] | None = None) -> int:
    """Run the Scillm subagent gate and emit one tau.agent_handoff.v1 response."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args(argv)

    start_payload = _load_start_payload()
    selected_agent = os.environ.get("TAU_HANDOFF_SELECTED_AGENT") or "reviewer"
    artifact_dir = os.environ.get("TAU_HANDOFF_COMMAND_ARTIFACT_DIR")

    gate = validate_scillm_subagent_loop_summary(args.summary)
    artifact_refs = [str(args.summary.expanduser().resolve())]
    if artifact_dir:
        artifact_path = Path(artifact_dir).expanduser().resolve() / "scillm-subagent-gate.receipt.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(gate.as_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        artifact_refs.append(str(artifact_path))

    response = _handoff_response(
        start_payload=start_payload,
        selected_agent=selected_agent,
        gate_payload=gate.as_dict(),
        artifact_refs=artifact_refs,
    )
    sys.stdout.write(json.dumps(response, sort_keys=True) + "\n")
    return 0


def _load_start_payload() -> dict[str, Any]:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"stdin must contain one Tau handoff JSON object: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("stdin must contain one Tau handoff JSON object")
    return payload


def _handoff_response(
    *,
    start_payload: dict[str, Any],
    selected_agent: str,
    gate_payload: dict[str, Any],
    artifact_refs: list[str],
) -> dict[str, Any]:
    goal = start_payload.get("goal") if isinstance(start_payload.get("goal"), dict) else {}
    github = start_payload.get("github") if isinstance(start_payload.get("github"), dict) else {}
    status = "COMPLETED" if gate_payload.get("ok") is True else "BLOCKED"
    blocked_receipts = gate_payload.get("blocked_substrate_receipts")
    blocked_count = len(blocked_receipts) if isinstance(blocked_receipts, list) else 0
    errors = gate_payload.get("errors")
    error_count = len(errors) if isinstance(errors, list) else 0
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": str(github.get("repo") or "grahama1970/tau"),
            "target": str(github.get("target") or "issue#26"),
        },
        "goal": {
            "goal_id": str(goal.get("goal_id") or "goal-tau-scillm-subagent-gate"),
            "goal_version": int(goal.get("goal_version") or 1),
            "goal_hash": str(
                goal.get("goal_hash")
                or "sha256:0000000000000000000000000000000000000000000000000000000000000000"
            ),
        },
        "previous_subagent": selected_agent,
        "context": {
            "summary": "Tau ran the Scillm subagent gate against a delegate-loop summary.",
            "artifacts": artifact_refs,
        },
        "result": {
            "status": status,
            "summary": (
                "Scillm subagent substrate gate accepted the loop summary."
                if status == "COMPLETED"
                else (
                    "Scillm subagent substrate gate blocked the loop summary: "
                    f"blocked_receipts={blocked_count} errors={error_count}."
                )
            ),
            "evidence": artifact_refs,
            "gate": gate_payload,
        },
        "rationale": (
            "Tau must not advance from reviewer pass claims unless the underlying "
            "Scillm/OpenCode substrate receipt is completed and no prompt/runtime stall "
            "phase remains."
        ),
        "next_agent": {
            "name": "human",
            "executor": "human",
            "reason": "A substrate blocker requires human-visible proof before retry or escalation.",
        },
        "required_evidence": [
            "A subsequent Scillm/OpenCode run must produce completed substrate receipts before Tau accepts reviewer pass."
        ],
        "stop_condition": "Human chooses retry, escalation, or accepts this blocked substrate diagnosis.",
    }


if __name__ == "__main__":
    raise SystemExit(main())
