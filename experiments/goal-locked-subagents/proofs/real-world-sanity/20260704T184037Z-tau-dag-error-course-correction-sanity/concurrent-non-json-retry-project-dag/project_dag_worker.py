#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    artifact_dir = Path(os.environ["TAU_HANDOFF_COMMAND_ARTIFACT_DIR"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.role == "coder":
        response = coder_response(payload, artifact_dir, args.scenario)
    elif args.role == "research-auditor":
        response = research_response(payload, artifact_dir, args.scenario)
    elif args.role == "reviewer":
        response = reviewer_response(payload, artifact_dir, args.scenario)
    else:
        raise SystemExit(f"unknown role: {args.role}")
    print(json.dumps(response, sort_keys=True))
    return 0


def coder_response(payload, artifact_dir, scenario):
    if scenario.startswith("concurrent"):
        time.sleep(0.4)
    prior = reviewer_verdicts(payload)
    attempt = 2 if any(item.get("verdict") == "REVISE" for item in prior) else 1
    artifact = artifact_dir / f"creator-artifact-attempt-{attempt}.json"
    artifact_payload = {
        "schema": "tau.creator_artifact.v1",
        "attempt": attempt,
        "scenario": scenario,
        "goal_hash": payload["goal"]["goal_hash"],
        "summary": "Creator artifact for real-world project DAG sanity.",
    }
    artifact.write_text(json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return handoff(
        payload,
        previous_subagent="coder",
        result_status="PASS",
        evidence=[
            {
                "kind": "creator_artifact",
                "path": str(artifact),
                "attempt": attempt,
                "goal_hash": payload["goal"]["goal_hash"],
            }
        ],
        next_agent="reviewer",
        next_executor="local",
        summary=f"Creator produced attempt {attempt} artifact for reviewer.",
    )


def research_response(payload, artifact_dir, scenario):
    if scenario.startswith("concurrent"):
        time.sleep(0.4)
    artifact = artifact_dir / "source-summary.json"
    artifact_payload = {
        "schema": "tau.source_summary.v1",
        "scenario": scenario,
        "goal_hash": payload["goal"]["goal_hash"],
        "summary": "Research branch source summary for real-world project DAG sanity.",
    }
    artifact.write_text(json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return handoff(
        payload,
        previous_subagent="research-auditor",
        result_status="PASS",
        evidence=[
            {
                "kind": "source_summary",
                "path": str(artifact),
                "goal_hash": payload["goal"]["goal_hash"],
            }
        ],
        next_agent="human",
        next_executor="human",
        summary="Research branch produced source summary evidence.",
    )


def reviewer_response(payload, artifact_dir, scenario):
    creator = creator_artifacts(payload)
    attempt = int(creator[-1].get("attempt", 1)) if creator else 0
    active_goal_hash = os.environ["TAU_HANDOFF_ACTIVE_GOAL_HASH"]
    verdict_goal_hash = "sha256:stale-reviewer-goal" if scenario == "complex" else active_goal_hash
    if scenario == "max-steps":
        verdict = "REVISE"
        next_agent = "coder"
        next_executor = "local"
    elif scenario == "medium" and attempt < 2:
        verdict = "REVISE"
        next_agent = "coder"
        next_executor = "local"
    else:
        verdict = "PASS"
        next_agent = "human"
        next_executor = "human"
    artifact = artifact_dir / f"reviewer-verdict-attempt-{max(attempt, 1)}.json"
    artifact_payload = {
        "schema": "tau.reviewer_verdict.v1",
        "scenario": scenario,
        "reviewed_node_id": "coder",
        "creator_artifact_count": len(creator),
        "creator_attempt": attempt,
        "goal_hash": verdict_goal_hash,
        "active_goal_hash": active_goal_hash,
        "goal_matches": verdict_goal_hash == active_goal_hash,
        "verdict": verdict,
    }
    artifact.write_text(json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return handoff(
        payload,
        previous_subagent="reviewer",
        result_status=verdict,
        evidence=[
            {
                "kind": "reviewer_verdict",
                "path": str(artifact),
                "reviewed_node_id": "coder",
                "creator_attempt": attempt,
                "goal_hash": verdict_goal_hash,
                "verdict": verdict,
            }
        ],
        next_agent=next_agent,
        next_executor=next_executor,
        summary=f"Reviewer returned {verdict} for creator attempt {attempt}.",
    )


def handoff(payload, *, previous_subagent, result_status, evidence, next_agent, next_executor, summary):
    return {
        "schema": "tau.agent_handoff.v1",
        "github": payload["github"],
        "goal": payload["goal"],
        "previous_subagent": previous_subagent,
        "context": {
            "summary": summary,
            "artifacts": [item["path"] for item in evidence if isinstance(item, dict) and "path" in item],
        },
        "result": {
            "status": result_status,
            "summary": summary,
            "evidence": evidence,
        },
        "rationale": "The DAG contract controls routing and immutable-goal checks.",
        "next_agent": {
            "name": next_agent,
            "executor": next_executor,
            "reason": "Continue according to the project DAG contract.",
        },
        "required_evidence": ["creator_artifact", "reviewer_verdict"],
        "stop_condition": "Stop at human or a fail-closed DAG invariant.",
    }


def creator_artifacts(payload):
    return evidence_items(payload, "creator_artifact")


def reviewer_verdicts(payload):
    return evidence_items(payload, "reviewer_verdict")


def evidence_items(payload, kind):
    evidence = payload.get("result", {}).get("evidence", [])
    return [item for item in evidence if isinstance(item, dict) and item.get("kind") == kind]


if __name__ == "__main__":
    raise SystemExit(main())
