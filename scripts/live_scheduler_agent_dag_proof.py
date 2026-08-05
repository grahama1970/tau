"""Live canonical-scheduler proof for tau#310.

Compiles a ``tau.generic_dag_spec.v1`` containing ``tau_agent`` nodes and
executes it through the canonical ``run_dag_plan`` scheduler, with each
Tau-native agent node carried over live scillm#28 transports on two
heterogeneous profiles. This is the DAG-contract entry path: spec → compile →
scheduler → Tau-native loop → SciLLM transport → settlement receipts.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from tau_agent.tools import AgentTool, AgentToolResult  # noqa: E402
from tau_ai.scillm_transport import ScillmTransportProvider  # noqa: E402
from tau_coding.dag_runtime.agent_node_adapter import (  # noqa: E402
    TAU_NATIVE_ADAPTER_KIND,
    execute_tau_agent_node,
)
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.model import canonical_sha256  # noqa: E402
from tau_coding.dag_runtime.scheduler import run_dag_plan  # noqa: E402

BASE_URL = os.environ.get("SCILLM_BASE_URL", "http://localhost:4001")
API_KEY = os.environ.get("SCILLM_MASTER_KEY", "sk-dev-proxy-123")
GOAL_HASH = canonical_sha256({"goal": "scheduler-native live proof", "ticket": "tau#310"})
PROFILE_BY_NODE = {"worker": "claude-model-turn", "verifier": "codex-model-turn"}
CONTENT = "scheduler-native live proof for tau#310"


def _tools(workspace: Path, write: bool) -> list[AgentTool]:
    async def _executor(arguments: Any, signal: Any = None) -> AgentToolResult:
        name = "write_file" if write else "read_file"
        rel = str(arguments.get("path", ""))
        target = (workspace / rel).resolve()
        if not str(target).startswith(str(workspace.resolve())):
            return AgentToolResult(
                tool_call_id="", name=name, ok=False, content="escape", error="escape"
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

    name = "write_file" if write else "read_file"
    properties: dict[str, Any] = {"path": {"type": "string"}}
    required = ["path"]
    if write:
        properties["content"] = {"type": "string"}
        required = ["path", "content"]
    return [
        AgentTool(
            name=name,
            description=f"{name} under the node workspace",
            input_schema={"type": "object", "properties": properties, "required": required},
            executor=_executor,
        )
    ]


def main() -> int:
    run_id = f"sched-live-{int(time.time())}"
    out_dir = REPO / "artifacts" / "agent_native_live_proof" / run_id
    workspace = out_dir / "workspace"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": run_id,
        "run_dir": str(out_dir / "run"),
        "nodes": [
            {
                "node_id": "worker",
                "role": "backend",
                "tau_agent": {
                    "prompt": (
                        "Call write_file exactly once with path='proof.txt' and "
                        f"content='{CONTENT}'. Then state what you wrote."
                    ),
                    "role": "backend",
                    "model": "profile:claude-model-turn",
                    "allowed_paths": ["proof.txt"],
                    "required_evidence": ["tool_effect_receipt"],
                },
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(out_dir / "receipts" / "worker.json"),
                "timeout_seconds": 300,
                "max_attempts": 1,
            },
            {
                "node_id": "verifier",
                "role": "review",
                "tau_agent": {
                    "prompt": (
                        "Call read_file with path='proof.txt'. Answer exactly "
                        f"'VERDICT: PASS' if its content is '{CONTENT}', else "
                        "'VERDICT: FAIL' plus the reason."
                    ),
                    "role": "review",
                    "model": "profile:codex-model-turn",
                    "allowed_paths": ["proof.txt"],
                    "required_evidence": ["tool_effect_receipt"],
                },
                "depends_on": ["worker"],
                "accepted_context_from": ["worker"],
                "receipt_path": str(out_dir / "receipts" / "verifier.json"),
                "timeout_seconds": 300,
                "max_attempts": 1,
            },
        ],
    }
    (out_dir / "dag-spec.json").write_text(json.dumps(spec, indent=2))
    plan = compile_generic_dag_plan(spec, source_path=out_dir / "dag-spec.json")

    def provider_factory(node: Any, config: Any) -> ScillmTransportProvider:
        return ScillmTransportProvider(
            base_url=BASE_URL,
            api_key=API_KEY,
            profile_id=PROFILE_BY_NODE[node.node_id],
            correlation={
                "tau_run_id": run_id,
                "node_id": node.node_id,
                "attempt": 1,
                "goal_hash": GOAL_HASH,
            },
            required_capabilities=["tool_calling", "structured_events"],
            timeout_seconds=240,
        )

    def execute(plan_node: Any, accepted_inputs: Any, execution: Any) -> dict[str, Any]:
        assert plan_node.adapter_kind == TAU_NATIVE_ADAPTER_KIND
        return execute_tau_agent_node(
            plan_node,
            accepted_inputs,
            execution,
            goal_hash=GOAL_HASH,
            provider_factory=provider_factory,
            tools_factory=lambda node, config: _tools(
                workspace, write=node.node_id == "worker"
            ),
        )

    result = run_dag_plan(plan, execute_node=execute)
    by_id = {item["node_id"]: item for item in result.node_results}
    artifact = workspace / "proof.txt"
    artifact_ok = artifact.exists() and artifact.read_text() == CONTENT
    verdict = str(
        (by_id.get("verifier", {}).get("accepted_output") or {}).get("final_text", "")
    )
    summary = {
        "schema": "tau.scheduler_live_proof_summary.v1",
        "run_id": run_id,
        "goal_hash": GOAL_HASH,
        "scheduler_status": result.status,
        "scheduler_verdict": result.verdict,
        "completed_node_ids": list(result.completed_node_ids),
        "profiles": PROFILE_BY_NODE,
        "worker_settlement": (by_id.get("worker", {}).get("accepted_output") or {}).get(
            "settlement", {}
        ),
        "verifier_settlement": (by_id.get("verifier", {}).get("accepted_output") or {}).get(
            "settlement", {}
        ),
        "artifact_readback_ok": artifact_ok,
        "review_verdict_pass": "VERDICT: PASS" in verdict,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: summary[k] for k in (
        "run_id", "scheduler_status", "completed_node_ids",
        "artifact_readback_ok", "review_verdict_pass",
    )}, indent=2))
    ok = (
        result.status == "PASS"
        and artifact_ok
        and summary["review_verdict_pass"]
        and summary["worker_settlement"].get("state") == "completed"
    )
    print(f"SCHEDULER LIVE PROOF {'PASS' if ok else 'FAIL'}: {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--live" not in sys.argv:
        print("refusing to run: live provider calls; pass --live")
        raise SystemExit(2)
    raise SystemExit(main())
