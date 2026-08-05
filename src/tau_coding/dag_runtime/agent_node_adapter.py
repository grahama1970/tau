"""Scheduler dispatch for ``tau_native_agent_loop`` DAG nodes (tau#310).

Turns a compiled ``DagPlanNode`` with adapter kind ``tau_native_agent_loop``
into an ``AgentNodeRun`` executed under the canonical scheduler's injected
``execute_node`` boundary. The provider is supplied by the caller (SciLLM
transport for live runs, ``FakeProvider`` for deterministic fixtures) — the
adapter itself never talks to a provider SDK.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tau_agent.tools import AgentTool
from tau_coding.dag_runtime.agent_node import AgentNodeError, AgentNodeRun, ToolPolicy
from tau_coding.dag_runtime.model import DagPlanNode

TAU_NATIVE_ADAPTER_KIND = "tau_native_agent_loop"

ProviderFactory = Callable[[DagPlanNode, Mapping[str, Any]], Any]
ToolsFactory = Callable[[DagPlanNode, Mapping[str, Any]], Sequence[AgentTool]]


def execute_tau_agent_node(
    plan_node: DagPlanNode,
    accepted_inputs: tuple[dict[str, Any], ...],
    execution: Any,
    *,
    goal_hash: str,
    provider_factory: ProviderFactory,
    tools_factory: ToolsFactory,
) -> dict[str, Any]:
    """Execute one Tau-native agent node and map its settlement to the scheduler."""
    config = dict(plan_node.adapter_config.to_value() or {})
    prompt = str(config.get("prompt", ""))
    if not prompt:
        return _result(plan_node, "FAIL", "AGENT_NODE_PROMPT_MISSING", errors=["prompt missing"])
    context_lines = [
        f"[accepted upstream {item.get('source_node_id', '?')}]: "
        + str(item.get("accepted_output"))
        for item in accepted_inputs
    ]
    if context_lines:
        prompt = "\n".join(["Accepted upstream context:", *context_lines, "", prompt])
    work_order = {
        "schema": "tau.agent_node.v1",
        "run_id": execution.run_id,
        "node_id": plan_node.node_id,
        "attempt_id": execution.attempt_id,
        "attempt": execution.attempt,
        "goal_hash": goal_hash,
        "plan_sha256": getattr(plan_node, "plan_sha256", None) or "0" * 64,
        "model": str(config.get("model", "profile-owned")),
        "harness": str(config.get("harness", "tau_native_agent_loop")),
        "role": config.get("role"),
        "required_evidence": list(config.get("required_evidence", [])),
        "transport_profile_selection": config.get("transport_profile_selection"),
    }
    tools = list(tools_factory(plan_node, config))
    policy = ToolPolicy(
        goal_hash=goal_hash,
        allowed_tools=tuple(config.get("allowed_tools", [tool.name for tool in tools])),
        allowed_paths=tuple(config.get("allowed_paths", ["**"])),
        max_tool_calls=int(config.get("max_tool_calls", 16)),
    )
    try:
        run = AgentNodeRun(
            work_order=work_order,
            policy=policy,
            provider=provider_factory(plan_node, config),
            tools=tools,
            max_turns=int(config.get("max_turns", 8)),
        )
        asyncio.run(run.run(prompt))
        if run.tool_effect_receipts and all(r["ok"] for r in run.tool_effect_receipts):
            run.add_evidence(
                "tool_effect_receipt",
                {"receipts": [r["sha256"] for r in run.tool_effect_receipts]},
            )
        settlement = run.settle()
    except AgentNodeError as error:
        return _result(plan_node, "FAIL", error.code.upper(), errors=[str(error)])
    status_by_state = {
        "completed": "PASS",
        "failed": "FAIL",
        "cancelled": "FAIL",
        "blocked": "BLOCKED",
    }
    return _result(
        plan_node,
        status_by_state[settlement["state"]],
        settlement["state"].upper() if settlement["state"] != "completed" else "PASS",
        accepted_output={
            "settlement": settlement,
            "final_text": (
                run.turn_receipts[-1]["assistant_text"] if run.turn_receipts else ""
            ),
        },
        errors=list(settlement["blockers"]),
    )


def _result(
    plan_node: DagPlanNode,
    status: str,
    verdict: str,
    *,
    accepted_output: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": plan_node.node_id,
        "status": status,
        "verdict": verdict,
        "accepted_output": accepted_output,
        "errors": errors or [],
    }
