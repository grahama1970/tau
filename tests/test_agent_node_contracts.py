"""Fixture state-machine and tool-policy tests for tau#310 agent-node contracts."""

from __future__ import annotations

from typing import Any

import pytest

from tau_agent import AgentTool, AgentToolResult, AssistantMessage, ToolCall
from tau_ai import FakeProvider, ProviderResponseEndEvent, ProviderResponseStartEvent
from tau_coding.dag_runtime.agent_node import (
    AgentNodeError,
    AgentNodeRun,
    ToolPolicy,
    reconstruct_context_from_journal,
    validate_agent_node_work_order,
)

GOAL = "g" * 64


def _work_order(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": "tau.agent_node.v1",
        "run_id": "run-1",
        "node_id": "node-a",
        "attempt_id": "attempt-1",
        "attempt": 1,
        "goal_hash": GOAL,
        "plan_sha256": "p" * 64,
        "model": "fake",
        "harness": "tau_native_agent_loop",
        "required_evidence": [],
    }
    base.update(overrides)
    return base


def _policy(**overrides: Any) -> ToolPolicy:
    base: dict[str, Any] = {
        "goal_hash": GOAL,
        "allowed_tools": ("write_file",),
        "allowed_paths": ("apps/web/**",),
        "max_tool_calls": 4,
    }
    base.update(overrides)
    return ToolPolicy(**base)


def _text_stream(text: str, finish_reason: str = "stop") -> list[Any]:
    return [
        ProviderResponseStartEvent(model="fake"),
        ProviderResponseEndEvent(
            message=AssistantMessage(content=text), finish_reason=finish_reason
        ),
    ]


def _tool_stream(tool_name: str, arguments: dict[str, Any], call_id: str = "call-1") -> list[Any]:
    return [
        ProviderResponseStartEvent(model="fake"),
        ProviderResponseEndEvent(
            message=AssistantMessage(
                content="",
                tool_calls=[ToolCall(id=call_id, name=tool_name, arguments=arguments)],
            ),
            finish_reason="tool_use",
        ),
    ]


def _write_tool() -> AgentTool:
    async def _executor(arguments: Any, signal: Any = None) -> AgentToolResult:
        return AgentToolResult(
            tool_call_id="",
            name="write_file",
            ok=True,
            content=f"wrote {arguments.get('path')}",
        )

    return AgentTool(
        name="write_file",
        description="Write a file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        executor=_executor,
    )


async def _run(
    streams: list[list[Any]],
    *,
    work_order: dict[str, Any] | None = None,
    policy: ToolPolicy | None = None,
    tools: list[AgentTool] | None = None,
) -> AgentNodeRun:
    run = AgentNodeRun(
        work_order=work_order or _work_order(),
        policy=policy or _policy(),
        provider=FakeProvider(streams),
        tools=tools if tools is not None else [_write_tool()],
    )
    await run.run("Do the work.")
    return run


def test_work_order_validation_fails_closed() -> None:
    for key in ("run_id", "goal_hash", "model"):
        broken = _work_order()
        broken[key] = ""
        with pytest.raises(AgentNodeError):
            validate_agent_node_work_order(broken)
    with pytest.raises(AgentNodeError) as excinfo:
        validate_agent_node_work_order(_work_order(harness="scillm_agent_loop"))
    assert excinfo.value.code == "agent_node_harness_invalid"


@pytest.mark.anyio
async def test_model_only_node_completes() -> None:
    run = await _run([_text_stream("Result: done analysis.")])
    settlement = run.settle()
    assert settlement["state"] == "completed"
    assert settlement["turns"] == 1
    run.journal.verify_chain()


@pytest.mark.anyio
async def test_tool_calling_multi_turn_records_effect_receipts() -> None:
    streams = [
        _tool_stream("write_file", {"path": "apps/web/a.ts", "content": "x"}),
        _text_stream("Wrote the file."),
    ]
    run = await _run(streams)
    settlement = run.settle()
    assert settlement["state"] == "completed"
    assert settlement["turns"] == 2
    assert len(run.tool_effect_receipts) == 1
    receipt = run.tool_effect_receipts[0]
    assert receipt["ok"] is True
    assert receipt["tool_request"]["tool_name"] == "write_file"


@pytest.mark.anyio
async def test_provider_completed_without_required_evidence_does_not_settle() -> None:
    run = await _run(
        [_text_stream("All done!", finish_reason="stop")],
        work_order=_work_order(required_evidence=["test_run_receipt"]),
    )
    settlement = run.settle()
    assert settlement["state"] == "failed"
    assert any(b.startswith("required_evidence_missing") for b in settlement["blockers"])


@pytest.mark.anyio
async def test_evidence_receipt_enables_settlement() -> None:
    run = await _run(
        [_text_stream("Done with proof.")],
        work_order=_work_order(required_evidence=["test_run_receipt"]),
    )
    run.add_evidence("test_run_receipt", {"command": "pytest -q", "exit_code": 0})
    settlement = run.settle()
    assert settlement["state"] == "completed"
    assert "test_run_receipt" in settlement["evidence"]


@pytest.mark.anyio
async def test_model_done_claim_with_empty_output_fails() -> None:
    run = await _run([_text_stream("")])
    settlement = run.settle()
    assert settlement["state"] == "failed"
    assert "empty_terminal_output" in settlement["blockers"]


@pytest.mark.anyio
async def test_cancelled_node_settles_cancelled() -> None:
    run = AgentNodeRun(
        work_order=_work_order(),
        policy=_policy(),
        provider=FakeProvider([_text_stream("partial")]),
        tools=[],
    )
    run.cancel("operator requested cancel")
    await run.run("Do the work.")
    settlement = run.settle()
    assert settlement["state"] == "cancelled"
    events = [entry["event_type"] for entry in run.journal.entries]
    assert "agent_cancelled" in events


@pytest.mark.anyio
async def test_approval_missing_blocks_node() -> None:
    streams = [
        _tool_stream("write_file", {"path": "apps/web/a.ts", "content": "x"}),
        _text_stream("Tried to write."),
    ]
    policy = _policy(approval_required_tools=("write_file",))
    run = await _run(streams, policy=policy)
    settlement = run.settle()
    assert settlement["state"] == "blocked"
    assert "approval_required" in settlement["blockers"]
    rejected = run.rejected_tool_requests[0]
    assert "TOOL_APPROVAL_MISSING" in rejected["rejection_codes"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("arguments", "policy_kwargs", "code"),
    [
        ({"path": "secrets/key.pem", "content": "x"}, {}, "TOOL_PATH_FORBIDDEN"),
        ({"path": "../etc/passwd", "content": "x"}, {}, "TOOL_PATH_FORBIDDEN"),
        ({"path": "apps/web/a.ts", "content": "x"}, {"max_tool_calls": 0}, "TOOL_BUDGET_EXHAUSTED"),
        ({"path": "apps/web/a.ts"}, {}, "TOOL_ARGS_MALFORMED"),
        ({"path": 5, "content": "x"}, {}, "TOOL_ARGS_MALFORMED"),
        (
            {"path": "apps/web/a.ts", "content": "x"},
            {"allowed_tools": ("read_file",)},
            "TOOL_NOT_ALLOWED",
        ),
    ],
)
async def test_tool_policy_rejections(
    arguments: dict[str, Any], policy_kwargs: dict[str, Any], code: str
) -> None:
    streams = [
        _tool_stream("write_file", arguments),
        _text_stream("Attempted."),
    ]
    run = await _run(streams, policy=_policy(**policy_kwargs))
    assert run.rejected_tool_requests, "expected the tool request to be rejected"
    assert code in run.rejected_tool_requests[0]["rejection_codes"]
    assert not run.tool_effect_receipts


@pytest.mark.anyio
async def test_stale_goal_hash_rejects_tool_effect() -> None:
    policy = ToolPolicy(
        goal_hash="h" * 64,
        allowed_tools=("write_file",),
        allowed_paths=("apps/web/**",),
    )
    with pytest.raises(AgentNodeError) as excinfo:
        AgentNodeRun(
            work_order=_work_order(),
            policy=policy,
            provider=FakeProvider([]),
            tools=[],
        )
    assert excinfo.value.code == "agent_node_goal_policy_mismatch"


@pytest.mark.anyio
async def test_repair_attempt_reconstructs_context_from_journal() -> None:
    first = await _run([_text_stream("Attempt one output.")])
    first.settle()
    context = reconstruct_context_from_journal(first.journal, first.turn_receipts)
    assert context == [{"turn": 1, "assistant_text": "Attempt one output."}]
    forged = dict(first.turn_receipts[0])
    forged["assistant_text"] = "forged provider transcript"
    forged["sha256"] = "f" * 64
    with pytest.raises(AgentNodeError) as excinfo:
        reconstruct_context_from_journal(first.journal, [forged])
    assert excinfo.value.code == "context_turn_not_in_journal"


@pytest.mark.anyio
async def test_journal_tamper_detected() -> None:
    run = await _run([_text_stream("ok output")])
    run.journal.entries[0]["payload"]["prompt_sha256"] = "0" * 64
    with pytest.raises(AgentNodeError):
        run.journal.verify_chain()


@pytest.mark.anyio
async def test_steering_message_reaches_next_turn() -> None:
    streams = [
        _text_stream("First answer."),
        _text_stream("Steered answer."),
    ]
    run = AgentNodeRun(
        work_order=_work_order(),
        policy=_policy(),
        provider=FakeProvider(streams),
        tools=[],
    )
    run.steer("Also cover the edge case.")
    await run.run("Do the work.")
    settlement = run.settle()
    assert settlement["turns"] == 2
    provider_calls = run.provider.calls
    sent_second = provider_calls[1][2]
    assert any(
        getattr(message, "content", "") == "Also cover the edge case." for message in sent_second
    )
    events = [entry["event_type"] for entry in run.journal.entries]
    assert "steering_queued" in events
