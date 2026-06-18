from collections.abc import AsyncIterator, Mapping

import pytest

from tau_agent import (
    AgentEvent,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    ErrorEvent,
    RetryEvent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.loop import run_agent_loop
from tau_agent.types import JSONValue
from tau_ai import (
    FakeProvider,
    ProviderErrorEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderRetryEvent,
    ProviderTextDeltaEvent,
)


async def _collect(stream: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    return [event async for event in stream]


@pytest.mark.anyio
async def test_agent_loop_streams_text_and_appends_assistant_message() -> None:
    messages = [UserMessage(content="Say hello")]
    assistant = AssistantMessage(content="Hello")
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta="Hel"),
                ProviderTextDeltaEvent(delta="lo"),
                ProviderResponseEndEvent(message=assistant, finish_reason="stop"),
            ]
        ]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_delta",
        "message_delta",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert messages == [UserMessage(content="Say hello"), assistant]


@pytest.mark.anyio
async def test_agent_loop_executes_tools_and_continues_until_no_tool_calls() -> None:
    async def executor(arguments: Mapping[str, JSONValue]) -> AgentToolResult:
        return AgentToolResult(
            tool_call_id="call-1",
            name="read",
            ok=True,
            content=f"contents of {arguments['path']}",
            data={"path": arguments["path"]},
            details={"source": "fake"},
        )

    tool = AgentTool(
        name="read",
        description="Read a file.",
        input_schema={"type": "object"},
        executor=executor,
    )
    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    first_assistant = AssistantMessage(content="I'll read it.", tool_calls=[tool_call])
    final_assistant = AssistantMessage(content="README.md contains project documentation.")
    messages = [UserMessage(content="Read README.md")]
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=first_assistant, finish_reason="tool_calls"),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta=final_assistant.content),
                ProviderResponseEndEvent(message=final_assistant, finish_reason="stop"),
            ],
        ]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[tool],
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
        "turn_end",
        "turn_start",
        "message_start",
        "message_delta",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert messages == [
        UserMessage(content="Read README.md"),
        first_assistant,
        ToolResultMessage(
            tool_call_id="call-1",
            name="read",
            content="contents of README.md",
            ok=True,
            data={"path": "README.md"},
            details={"source": "fake"},
        ),
        final_assistant,
    ]
    assert len(provider.calls) == 2
    assert provider.calls[1][2] == messages[:3]


@pytest.mark.anyio
async def test_agent_loop_records_unknown_tool_as_failed_tool_result() -> None:
    tool_call = ToolCall(id="call-1", name="missing", arguments={})
    assistant = AssistantMessage(content="I'll use a tool.", tool_calls=[tool_call])
    messages = [UserMessage(content="Use a missing tool")]
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=assistant, finish_reason="tool_calls"),
            ]
        ]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            max_turns=1,
        )
    )

    tool_end_events = [event for event in events if isinstance(event, ToolExecutionEndEvent)]

    assert tool_end_events[0].result.ok is False
    assert tool_end_events[0].result.error == "Unknown tool: missing"
    assert messages[-1] == ToolResultMessage(
        tool_call_id="call-1",
        name="missing",
        content="Unknown tool: missing",
        ok=False,
        error="Unknown tool: missing",
    )


@pytest.mark.anyio
async def test_agent_loop_converts_provider_error_to_agent_error() -> None:
    messages = [UserMessage(content="hello")]
    provider = FakeProvider([[ProviderErrorEvent(message="provider failed")]])

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    errors = [event for event in events if isinstance(event, ErrorEvent)]

    assert errors == [ErrorEvent(message="provider failed", recoverable=False)]
    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "error",
        "turn_end",
        "agent_end",
    ]
    assert messages == [UserMessage(content="hello")]


@pytest.mark.anyio
async def test_agent_loop_forwards_provider_retry_events() -> None:
    messages = [UserMessage(content="hello")]
    assistant = AssistantMessage(content="ok")
    provider = FakeProvider(
        [
            [
                ProviderRetryEvent(
                    attempt=2,
                    max_attempts=3,
                    delay_seconds=0,
                    message="Retrying provider request 2/3 after HTTP 503.",
                    data={"status_code": 503},
                ),
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=assistant),
            ]
        ]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    retries = [event for event in events if isinstance(event, RetryEvent)]

    assert retries == [
        RetryEvent(
            attempt=2,
            max_attempts=3,
            delay_seconds=0,
            message="Retrying provider request 2/3 after HTTP 503.",
            data={"status_code": 503},
        )
    ]
    assert messages == [UserMessage(content="hello"), assistant]


@pytest.mark.anyio
async def test_agent_loop_has_no_default_max_turn_limit() -> None:
    tool_call = ToolCall(id="call-1", name="missing", arguments={})
    looping_assistant = AssistantMessage(content="Again.", tool_calls=[tool_call])
    final_assistant = AssistantMessage(content="Done.")
    messages = [UserMessage(content="loop for a while")]
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=looping_assistant, finish_reason="tool_calls"),
            ]
            for _ in range(9)
        ]
        + [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=final_assistant),
            ]
        ]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    assert not [event for event in events if isinstance(event, ErrorEvent)]
    assert len(provider.calls) == 10
    assert messages[-1] == final_assistant


@pytest.mark.anyio
async def test_agent_loop_stops_after_configured_max_turns() -> None:
    tool_call = ToolCall(id="call-1", name="missing", arguments={})
    assistant = AssistantMessage(content="Again.", tool_calls=[tool_call])
    messages = [UserMessage(content="loop forever")]
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=assistant, finish_reason="tool_calls"),
            ],
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderResponseEndEvent(message=assistant, finish_reason="tool_calls"),
            ],
        ]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            max_turns=1,
        )
    )

    errors = [event for event in events if isinstance(event, ErrorEvent)]

    assert errors == [
        ErrorEvent(message="Agent loop stopped after reaching max_turns=1", recoverable=True)
    ]
    assert len(provider.calls) == 1
