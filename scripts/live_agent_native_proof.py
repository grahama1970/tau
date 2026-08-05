"""Live non-mocked proof for tau#308/#309/#310.

Runs a heterogeneous two-node Tau DAG through the real SciLLM boundary:

1. Live scillm#27 discovery readback (profiles + live readiness probes).
2. tau#308 deterministic selection with frozen receipts for two roles
   (backend -> claude-model-turn, review -> codex-model-turn).
3. tau#310 Tau-native agent loop over scillm#28 normalized transports:
   node A performs a real tool effect (writes an artifact file under a
   policy-allowed path); node B independently verifies the artifact.
4. tau#309: a live operator action (add_next_turn_instruction) applied to
   node A before its turn, receipt bound to the journal transition; run
   projections validated for monotonic coherence at each phase.

Every artifact is written under artifacts/agent_native_live_proof/<run_id>/.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from tau_agent.tools import AgentTool, AgentToolResult  # noqa: E402
from tau_ai.scillm_transport import ScillmTransportProvider  # noqa: E402
from tau_coding.dag_runtime.agent_node import AgentNodeRun, ToolPolicy  # noqa: E402
from tau_coding.dag_runtime.agent_projection import (  # noqa: E402
    apply_operator_action,
    project_agent_node,
    project_run,
    validate_projection_readback,
)
from tau_coding.dag_runtime.agent_requirement import (  # noqa: E402
    select_transport_profile,
    validate_selection_receipt,
)
from tau_coding.dag_runtime.model import canonical_sha256  # noqa: E402

BASE_URL = os.environ.get("SCILLM_BASE_URL", "http://localhost:4001")
API_KEY = os.environ.get("SCILLM_MASTER_KEY", "sk-dev-proxy-123")
GOAL = {
    "goal": "Prove the Tau-native agent harness over live SciLLM transports",
    "owner": "human",
    "tickets": ["tau#308", "tau#309", "tau#310"],
}
GOAL_HASH = canonical_sha256(GOAL)
PLAN_SHA = canonical_sha256({"dag": "agent-native-live-proof", "nodes": ["backend", "review"]})
ARTIFACT_CONTENT = "tau-native live proof artifact for tickets 308/309/310"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}", "X-Caller-Skill": "tau-310-live-proof"}


def discover_live() -> dict[str, Any]:
    with httpx.Client(timeout=180) as client:
        profiles = client.get(f"{BASE_URL}/v1/scillm/profiles", headers=_headers()).json()
        readiness = client.get(
            f"{BASE_URL}/v1/scillm/profiles/readiness",
            headers=_headers(),
            params={"live": "true"},
        ).json()
    states = {item["profile"]: item["state"] for item in readiness["readiness"]}
    return {
        "profiles": profiles["profiles"],
        "readiness": states,
        "readiness_evidence": readiness["readiness"],
    }


def _requirement(role: str, preferences: list[str], capabilities: list[str]) -> dict[str, Any]:
    return {
        "schema": "tau.agent_requirement.v1",
        "role": role,
        "harness": "tau_native_agent_loop",
        "profile_preferences": preferences,
        "required_transport_capabilities": capabilities,
        "domain_capabilities": [],
        "workspace": {"mode": "isolated_worktree", "allowed_paths": ["artifacts/**"]},
        "required_evidence": ["tool_effect_receipt"],
        "fallback_policy": {"allowed": True, "prohibit_capability_downgrade": True},
    }


def _file_tool(name: str, description: str, root: Path, write: bool) -> AgentTool:
    async def _executor(arguments: Any, signal: Any = None) -> AgentToolResult:
        rel = str(arguments.get("path", ""))
        target = (root / rel).resolve()
        if not str(target).startswith(str(root.resolve())):
            return AgentToolResult(
                tool_call_id="", name=name, ok=False, content="path escape", error="path escape"
            )
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(arguments.get("content", "")))
            return AgentToolResult(tool_call_id="", name=name, ok=True, content=f"wrote {rel}")
        if not target.exists():
            return AgentToolResult(
                tool_call_id="", name=name, ok=False, content="missing", error="missing"
            )
        return AgentToolResult(tool_call_id="", name=name, ok=True, content=target.read_text())

    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}}
        if write
        else {"path": {"type": "string"}},
        "required": ["path", "content"] if write else ["path"],
    }
    return AgentTool(name=name, description=description, input_schema=schema, executor=_executor)


async def run_node(
    *,
    node_id: str,
    role: str,
    selection_receipt: dict[str, Any],
    prompt: str,
    tool: AgentTool,
    run_id: str,
    steer_instruction: str | None,
    out_dir: Path,
) -> tuple[AgentNodeRun, dict[str, Any], dict[str, Any] | None]:
    profile = selection_receipt["selected_profile"]
    provider = ScillmTransportProvider(
        base_url=BASE_URL,
        api_key=API_KEY,
        profile_id=profile["profile_id"],
        correlation={
            "tau_run_id": run_id,
            "node_id": node_id,
            "attempt": 1,
            "goal_hash": GOAL_HASH,
        },
        required_capabilities=["tool_calling", "structured_events"],
        timeout_seconds=240,
    )
    work_order = {
        "schema": "tau.agent_node.v1",
        "run_id": run_id,
        "node_id": node_id,
        "attempt_id": f"{node_id}-attempt-1",
        "attempt": 1,
        "goal_hash": GOAL_HASH,
        "plan_sha256": PLAN_SHA,
        "model": profile["model"],
        "harness": "tau_native_agent_loop",
        "role": role,
        "required_evidence": ["tool_effect_receipt"],
        "transport_profile_selection": selection_receipt,
    }
    run = AgentNodeRun(
        work_order=work_order,
        policy=ToolPolicy(
            goal_hash=GOAL_HASH,
            allowed_tools=(tool.name,),
            allowed_paths=("live-proof/**",),
            max_tool_calls=4,
        ),
        provider=provider,
        tools=[tool],
        system=(
            "You are a Tau agent node. Use the provided tool exactly as instructed, "
            "then summarize what you did in plain text."
        ),
        max_turns=4,
    )
    action_receipt: dict[str, Any] | None = None
    if steer_instruction is not None:
        action_receipt = apply_operator_action(
            run=run,
            request={
                "schema": "tau.operator_action_request.v1",
                "action": "add_next_turn_instruction",
                "actor": "human_operator",
                "run_id": run_id,
                "node_id": node_id,
                "goal_hash": GOAL_HASH,
                "observed_journal_seq": 0,
                "instruction": steer_instruction,
            },
        )
    await run.run(prompt)
    if run.tool_effect_receipts and all(r["ok"] for r in run.tool_effect_receipts):
        run.add_evidence(
            "tool_effect_receipt",
            {"receipts": [r["sha256"] for r in run.tool_effect_receipts]},
        )
    settlement = run.settle()
    (out_dir / f"{node_id}-journal.json").write_text(json.dumps(run.journal.entries, indent=2))
    (out_dir / f"{node_id}-settlement.json").write_text(json.dumps(settlement, indent=2))
    (out_dir / f"{node_id}-turn-receipts.json").write_text(
        json.dumps(run.turn_receipts, indent=2)
    )
    (out_dir / f"{node_id}-transport-results.json").write_text(
        json.dumps(provider.turn_results, indent=2)
    )
    if action_receipt is not None:
        (out_dir / f"{node_id}-operator-action-receipt.json").write_text(
            json.dumps(action_receipt, indent=2)
        )
    return run, settlement, action_receipt


async def main() -> int:
    run_id = f"live-proof-{int(time.time())}"
    out_dir = REPO / "artifacts" / "agent_native_live_proof" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace = out_dir / "workspace"

    discovery = discover_live()
    (out_dir / "scillm-discovery.json").write_text(json.dumps(discovery, indent=2))
    not_live = [p for p, s in discovery["readiness"].items() if s != "transport_live_ready"]
    print(f"discovery: {len(discovery['profiles'])} profiles; not live-ready: {not_live}")

    selections: dict[str, dict[str, Any]] = {}
    for role, preferences in (
        ("backend", ["claude-model-turn"]),
        ("review", ["codex-model-turn"]),
    ):
        selection = select_transport_profile(
            requirement=_requirement(
                role, preferences, ["tool_calling", "streaming", "cancellation"]
            ),
            discovery={"profiles": discovery["profiles"], "readiness": discovery["readiness"]},
        )
        receipt = selection.receipt_payload(
            run_id=run_id,
            node_id=f"node-{role}",
            attempt_id=f"node-{role}-attempt-1",
            attempt=1,
            plan_sha256=PLAN_SHA,
            goal_hash=GOAL_HASH,
            policy_hash=canonical_sha256({"allowed_paths": ["live-proof/**"]}),
            data_boundary_hash=canonical_sha256({"trust_zone": "local"}),
        )
        validate_selection_receipt(receipt)
        selections[role] = receipt
        (out_dir / f"selection-{role}.json").write_text(json.dumps(receipt, indent=2))
        selected = receipt["selected_profile"]
        print(
            f"selected[{role}]: {selected['profile_id']} provider={selected['provider']} "
            f"model={selected['model']} mode={selected['mode']}"
        )

    backend_run, backend_settlement, action_receipt = await run_node(
        node_id="node-backend",
        role="backend",
        selection_receipt=selections["backend"],
        prompt=(
            "Call the write_file tool exactly once with path='live-proof/proof.txt' and "
            f"content={ARTIFACT_CONTENT!r}. Then state what you wrote."
        ),
        tool=_file_tool("write_file", "Write a file under the workspace.", workspace, True),
        run_id=run_id,
        steer_instruction=(
            "In your final summary, include the token TAU-STEERED so the operator "
            "action is visible in the transcript."
        ),
        out_dir=out_dir,
    )
    print(
        f"backend settlement: {backend_settlement['state']} turns={backend_settlement['turns']} "
        f"tool_effects={len(backend_run.tool_effect_receipts)}"
    )
    backend_projection = project_agent_node(backend_run, settlement=backend_settlement)
    validate_projection_readback(backend_projection, run=backend_run)

    artifact_path = workspace / "live-proof" / "proof.txt"
    artifact_ok = artifact_path.exists() and artifact_path.read_text() == ARTIFACT_CONTENT
    print(f"artifact readback ok: {artifact_ok} ({artifact_path})")

    review_run, review_settlement, _ = await run_node(
        node_id="node-review",
        role="review",
        selection_receipt=selections["review"],
        prompt=(
            "Independently verify the artifact: call the read_file tool with "
            "path='live-proof/proof.txt', then answer exactly 'VERDICT: PASS' if its "
            f"content is {ARTIFACT_CONTENT!r}, else 'VERDICT: FAIL' with the reason."
        ),
        tool=_file_tool("read_file", "Read a file under the workspace.", workspace, False),
        run_id=run_id,
        steer_instruction=None,
        out_dir=out_dir,
    )
    print(
        f"review settlement: {review_settlement['state']} turns={review_settlement['turns']}"
    )
    review_projection = project_agent_node(review_run, settlement=review_settlement)
    validate_projection_readback(review_projection, run=review_run)

    run_projection = project_run(
        run_id=run_id,
        dag_id="agent-native-live-proof",
        goal_hash=GOAL_HASH,
        node_projections=[backend_projection, review_projection],
    )
    (out_dir / "run-projection.json").write_text(json.dumps(run_projection, indent=2))

    verdict_text = (
        review_run.turn_receipts[-1]["assistant_text"] if review_run.turn_receipts else ""
    )
    steer_visible = any("TAU-STEERED" in r["assistant_text"] for r in backend_run.turn_receipts)
    summary = {
        "schema": "tau.live_proof_summary.v1",
        "run_id": run_id,
        "goal_hash": GOAL_HASH,
        "tickets": ["tau#308", "tau#309", "tau#310"],
        "heterogeneous_profiles": [
            selections["backend"]["selected_profile"],
            selections["review"]["selected_profile"],
        ],
        "backend_settlement_state": backend_settlement["state"],
        "review_settlement_state": review_settlement["state"],
        "artifact_readback_ok": artifact_ok,
        "review_verdict_pass": "VERDICT: PASS" in verdict_text,
        "operator_action": {
            "outcome": action_receipt["outcome"] if action_receipt else None,
            "journal_changed": (
                action_receipt["journal_transition"]["journal_changed"]
                if action_receipt
                else False
            ),
            "instruction_visible_in_output": steer_visible,
        },
        "transport_ids": {
            "backend": backend_run.provider.transport_id,
            "review": review_run.provider.transport_id,
        },
        "projection_sha256": run_projection["sha256"],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    ok = (
        backend_settlement["state"] == "completed"
        and review_settlement["state"] == "completed"
        and artifact_ok
        and summary["review_verdict_pass"]
        and summary["operator_action"]["journal_changed"]
    )
    print(f"LIVE PROOF {'PASS' if ok else 'FAIL'}: {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
