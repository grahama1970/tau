import json
import os
import sys
from pathlib import Path

payload = json.load(sys.stdin)
active_goal_hash = os.environ["TAU_HANDOFF_ACTIVE_GOAL_HASH"]
evidence = payload.get("result", {}).get("evidence", [])
creator_evidence = [item for item in evidence if isinstance(item, dict) and item.get("kind") == "creator_artifact"]
goal_matches = payload.get("goal", {}).get("goal_hash") == active_goal_hash
evidence_matches = bool(creator_evidence) and creator_evidence[0].get("goal_hash") == active_goal_hash
verdict = "PASS" if goal_matches and evidence_matches else "BLOCK"
artifact_dir = Path(os.environ.get("TAU_HANDOFF_COMMAND_ARTIFACT_DIR", "."))
artifact_dir.mkdir(parents=True, exist_ok=True)
verdict_path = artifact_dir / "reviewer-verdict.json"
verdict_payload = {
    "schema": "tau.reviewer_verdict.v1",
    "reviewed_node_id": "coder",
    "goal_hash": active_goal_hash,
    "creator_goal_hash": payload.get("goal", {}).get("goal_hash"),
    "creator_evidence_count": len(creator_evidence),
    "goal_matches": goal_matches,
    "evidence_matches": evidence_matches,
    "verdict": verdict,
}
verdict_path.write_text(json.dumps(verdict_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
response = {
    "schema": "tau.agent_handoff.v1",
    "github": payload["github"],
    "goal": payload["goal"],
    "previous_subagent": "reviewer",
    "context": {
        "summary": "Reviewer compared creator evidence against immutable goal.",
        "artifacts": [str(verdict_path)],
    },
    "result": {
        "status": verdict,
        "summary": "Reviewer verdict compares creator goal/evidence to active immutable goal.",
        "evidence": [{
            "kind": "reviewer_verdict",
            "path": str(verdict_path),
            "reviewed_node_id": "coder",
            "goal_hash": active_goal_hash,
            "verdict": verdict,
        }],
    },
    "rationale": "The reviewer verdict is the final local gate for this bounded DAG.",
    "next_agent": {"name": "human", "executor": "human", "reason": "Human receives reviewer-gated DAG receipt."},
    "required_evidence": ["creator_artifact", "reviewer_verdict"],
    "stop_condition": "Stop at human.",
}
print(json.dumps(response, sort_keys=True))
