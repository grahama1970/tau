import json
import os
import sys
from pathlib import Path

payload = json.load(sys.stdin)
artifact_dir = Path(os.environ.get("TAU_HANDOFF_COMMAND_ARTIFACT_DIR", "."))
artifact_dir.mkdir(parents=True, exist_ok=True)
creator_artifact = artifact_dir / "creator-artifact.json"
creator_payload = {
    "schema": "tau.creator_artifact.v1",
    "goal_hash": payload["goal"]["goal_hash"],
    "summary": "Creator produced bounded artifact for immutable-goal review.",
}
creator_artifact.write_text(json.dumps(creator_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
response = {
    "schema": "tau.agent_handoff.v1",
    "github": payload["github"],
    "goal": payload["goal"],
    "previous_subagent": "coder",
    "context": {
        "summary": "Creator produced an artifact for reviewer comparison.",
        "artifacts": [str(creator_artifact)],
    },
    "result": {
        "status": "PASS",
        "summary": "Creator artifact is ready for reviewer comparison.",
        "evidence": [{"kind": "creator_artifact", "path": str(creator_artifact), "goal_hash": payload["goal"]["goal_hash"]}],
    },
    "rationale": "Reviewer must compare creator evidence against the immutable goal.",
    "next_agent": {"name": "reviewer", "executor": "local", "reason": "Review creator artifact against immutable goal."},
    "required_evidence": ["creator_artifact", "reviewer_verdict"],
    "stop_condition": "Stop at human after reviewer verdict.",
}
print(json.dumps(response, sort_keys=True))
