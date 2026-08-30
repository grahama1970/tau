"""Canonical-scheduler conformance for tau_native_agent_loop nodes (tau#310)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tau_agent import AgentTool, AgentToolResult, AssistantMessage, ToolCall
from tau_ai import FakeProvider, ProviderResponseEndEvent, ProviderResponseStartEvent
from tau_coding.dag_runtime.agent_node_adapter import (
    TAU_NATIVE_ADAPTER_KIND,
    execute_tau_agent_node,
)
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan

GOAL_HASH = canonical_sha256({"goal": "scheduler adapter conformance"})


def _spec(tmp_path: Path) -> dict[str, Any]:
    def agent_node(node_id: str, prompt: str, depends_on: list[str]) -> dict[str, Any]:
        return {
            "node_id": node_id,
            "role": node_id,
            "tau_agent": {"prompt": prompt, "role": node_id, "model": "fake"},
            "depends_on": depends_on,
            "accepted_context_from": depends_on,
            "receipt_path": str(tmp_path / "receipts" / f"{node_id}.json"),
            "timeout_seconds": 30,
            "max_attempts": 1,
        }

    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "agent-adapter-conformance",
        "run_dir": str(tmp_path / "run"),
        "nodes": [
            {
                "node_id": "prep",
                "role": "prep",
                "command": ["true"],
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(tmp_path / "receipts" / "prep.json"),
                "timeout_seconds": 5,
                "max_attempts": 1,
            },
            agent_node("worker", "Write the value with the tool, then summarize.", ["prep"]),
            agent_node("verifier", "Verify the upstream output and give a verdict.", ["worker"]),
        ],
    }


def _tool_stream() -> list[Any]:
    return [
        ProviderResponseStartEvent(model="fake"),
        ProviderResponseEndEvent(
            message=AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="call-1", name="note", arguments={"path": "notes/a.txt"})
                ],
            ),
            finish_reason="tool_calls",
        ),
    ]


def _text_stream(text: str) -> list[Any]:
    return [
        ProviderResponseStartEvent(model="fake"),
        ProviderResponseEndEvent(message=AssistantMessage(content=text), finish_reason="stop"),
    ]


def _note_tool() -> AgentTool:
    async def _executor(arguments: Any, signal: Any = None) -> AgentToolResult:
        return AgentToolResult(
            tool_call_id="", name="note", ok=True, content=f"noted {arguments.get('path')}"
        )

    return AgentTool(
        name="note",
        description="Record a note.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        executor=_executor,
    )


def test_scheduler_runs_tau_native_agent_nodes(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(_spec(tmp_path), source_path=tmp_path / "dag.json")
    kinds = {node.node_id: node.adapter_kind for node in plan.nodes}
    assert kinds["worker"] == TAU_NATIVE_ADAPTER_KIND
    assert kinds["verifier"] == TAU_NATIVE_ADAPTER_KIND
    assert kinds["prep"] == "generic_command"

    providers = {
        "worker": FakeProvider([_tool_stream(), _text_stream("worker wrote the note")]),
        "verifier": FakeProvider([_text_stream("VERDICT: PASS")]),
    }
    seen_context: dict[str, str] = {}

    def execute(
        plan_node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        if plan_node.adapter_kind != TAU_NATIVE_ADAPTER_KIND:
            return {
                "node_id": plan_node.node_id,
                "status": "PASS",
                "verdict": "PASS",
                "accepted_output": {"source_node_id": plan_node.node_id},
            }
        seen_context[plan_node.node_id] = str(accepted_inputs)
        return execute_tau_agent_node(
            plan_node,
            accepted_inputs,
            execution,
            goal_hash=GOAL_HASH,
            provider_factory=lambda node, config: providers[node.node_id],
            tools_factory=lambda node, config: [_note_tool()],
        )

    result = run_dag_plan(plan, execute_node=execute)
    assert result.status == "PASS"
    assert set(result.completed_node_ids) == {"prep", "worker", "verifier"}
    by_id = {item["node_id"]: item for item in result.node_results}
    worker_settlement = by_id["worker"]["accepted_output"]["settlement"]
    assert worker_settlement["schema"] == "tau.agent_node_settlement.v1"
    assert worker_settlement["state"] == "completed"
    assert worker_settlement["tool_effect_receipt_sha256s"]
    assert "worker" in seen_context["verifier"]


def test_generic_tau_agent_node_accepts_explicit_herdr_runtime_requirement(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    spec["nodes"][1]["runtime_requirement"] = {
        "schema": "tau.runtime_requirement.v1",
        "backend": "herdr",
        "interaction_mode": "interactive",
        "required_capabilities": [
            "interactive",
            "stable_endpoint_id",
            "human_attach",
            "native_agent_state",
            "foreground_process_state",
            "supports_working_directory",
            "supports_owned_inventory",
            "supports_terminate",
        ],
        "session_scope": "node_attempt",
        "observation_requirements": ["PROCESS"],
    }

    plan = compile_generic_dag_plan(spec, source_path=tmp_path / "dag.json")
    runtime = {node.node_id: node.runtime_requirement.to_value() for node in plan.nodes}

    assert runtime["worker"]["backend"] == "herdr"
    assert runtime["worker"]["interaction_mode"] == "interactive"
    assert "human_attach" in runtime["worker"]["required_capabilities"]
    assert runtime["verifier"]["backend"] == "local"


def test_scheduler_agent_node_failure_is_fail_closed(tmp_path: Path) -> None:
    plan = compile_generic_dag_plan(_spec(tmp_path), source_path=tmp_path / "dag.json")
    providers = {
        "worker": FakeProvider([_text_stream("")]),  # empty terminal output
        "verifier": FakeProvider([_text_stream("VERDICT: PASS")]),
    }

    def execute(
        plan_node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        if plan_node.adapter_kind != TAU_NATIVE_ADAPTER_KIND:
            return {
                "node_id": plan_node.node_id,
                "status": "PASS",
                "verdict": "PASS",
                "accepted_output": {"source_node_id": plan_node.node_id},
            }
        return execute_tau_agent_node(
            plan_node,
            accepted_inputs,
            execution,
            goal_hash=GOAL_HASH,
            provider_factory=lambda node, config: providers[node.node_id],
            tools_factory=lambda node, config: [],
        )

    result = run_dag_plan(plan, execute_node=execute)
    assert result.status != "PASS"
    assert "verifier" not in result.completed_node_ids  # downstream must not succeed
