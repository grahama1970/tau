"""Deterministic tests for tau#311 grounding/response_format transport options."""

from __future__ import annotations

from typing import Any

import pytest

from tau_agent import AssistantMessage
from tau_ai import FakeProvider, ProviderResponseEndEvent, ProviderResponseStartEvent
from tau_ai.scillm_transport import ScillmTransportProvider
from tau_coding.dag_runtime.agent_node import AgentNodeRun, ToolPolicy

GOAL = "g" * 64


def _provider(**overrides: Any) -> ScillmTransportProvider:
    base: dict[str, Any] = {
        "base_url": "http://localhost:4001",
        "api_key": "sk-test",
        "profile_id": "claude-model-turn",
        "correlation": {"tau_run_id": "run-1", "node_id": "node-a"},
    }
    base.update(overrides)
    return ScillmTransportProvider(**base)


def test_create_body_omits_options_by_default() -> None:
    body = _provider()._create_body([{"role": "user", "content": "hi"}], [])
    for key in ("source", "grounding_threshold", "grounding_retries", "response_format", "metadata"):
        assert key not in body


def test_create_body_carries_grounding_and_response_format() -> None:
    provider = _provider(
        source="SOURCE TEXT",
        grounding_threshold=0.8,
        grounding_retries=1,
        response_format={"type": "json_object"},
        metadata={"caller_skill": "ask.argue"},
    )
    body = provider._create_body([{"role": "user", "content": "hi"}], [])
    assert body["source"] == "SOURCE TEXT"
    assert body["grounding_threshold"] == 0.8
    assert body["grounding_retries"] == 1
    assert body["response_format"] == {"type": "json_object"}
    assert body["metadata"] == {"caller_skill": "ask.argue"}


def test_grounding_thresholds_require_source() -> None:
    provider = _provider(grounding_threshold=0.9)
    body = provider._create_body([{"role": "user", "content": "hi"}], [])
    assert "grounding_threshold" not in body
    assert "source" not in body


@pytest.mark.anyio
async def test_settlement_surfaces_provider_grounding() -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(
                    message=AssistantMessage(content="grounded answer"), finish_reason="stop"
                ),
            ]
        ]
    )
    provider.last_grounding = {"verified": True, "score": 0.93, "attempts": 1}
    run = AgentNodeRun(
        work_order={
            "schema": "tau.agent_node.v1",
            "run_id": "run-1",
            "node_id": "node-a",
            "attempt_id": "attempt-1",
            "attempt": 1,
            "goal_hash": GOAL,
            "plan_sha256": "p" * 64,
            "model": "fake",
        },
        policy=ToolPolicy(goal_hash=GOAL, allowed_tools=()),
        provider=provider,
        tools=[],
    )
    await run.run("Answer from the source only.")
    settlement = run.settle()
    assert settlement["grounding"] == {"verified": True, "score": 0.93, "attempts": 1}


@pytest.mark.anyio
async def test_settlement_grounding_none_without_provider_support() -> None:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(
                    message=AssistantMessage(content="answer"), finish_reason="stop"
                ),
            ]
        ]
    )
    run = AgentNodeRun(
        work_order={
            "schema": "tau.agent_node.v1",
            "run_id": "run-1",
            "node_id": "node-a",
            "attempt_id": "attempt-1",
            "attempt": 1,
            "goal_hash": GOAL,
            "plan_sha256": "p" * 64,
            "model": "fake",
        },
        policy=ToolPolicy(goal_hash=GOAL, allowed_tools=()),
        provider=provider,
        tools=[],
    )
    await run.run("Answer.")
    assert run.settle()["grounding"] is None
