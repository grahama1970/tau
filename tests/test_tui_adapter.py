from pathlib import Path

from tau_agent import (
    AgentEndEvent,
    AgentStartEvent,
    AgentToolResult,
    AssistantMessage,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    QueueUpdateEvent,
    RetryEvent,
    ThinkingDeltaEvent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    UserMessage,
)
from tau_coding.extensions import ExtensionToolRenderers
from tau_coding.skills import Skill, format_skill_invocation
from tau_coding.tui import TuiEventAdapter, TuiState
from tau_coding.tui.state import format_tool_call_block, format_tool_result_block


def test_tui_adapter_tracks_running_state() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(AgentStartEvent())
    assert state.running is True

    adapter.apply(AgentEndEvent())
    assert state.running is False


def test_tui_adapter_builds_assistant_items_from_streamed_messages() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(MessageStartEvent())
    adapter.apply(MessageDeltaEvent(delta="Hel"))
    adapter.apply(MessageDeltaEvent(delta="lo"))
    assert state.assistant_buffer == "Hello"
    assert state.items == []

    adapter.apply(MessageEndEvent(message=AssistantMessage(content="Hello")))

    assert state.assistant_buffer == ""
    assert [(item.role, item.text) for item in state.items] == [("assistant", "Hello")]


def test_tui_adapter_builds_user_items_from_streamed_messages() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(MessageStartEvent(message_role="user"))
    adapter.apply(MessageEndEvent(message=UserMessage(content="Hello Tau")))

    assert state.assistant_buffer == ""
    assert [(item.role, item.text) for item in state.items] == [("user", "Hello Tau")]


def test_tui_adapter_compacts_streamed_skill_invocations() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)
    skill = Skill(
        name="review",
        path=Path("/workspace/.tau/skills/review.md"),
        content="# Review\nFull noisy instructions.",
        description="Review code",
    )

    adapter.apply(
        MessageEndEvent(
            message=UserMessage(content=format_skill_invocation(skill, "check the auth flow"))
        )
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        (
            "skill",
            "Using skill: review (Ctrl+O to expand)",
            "**review**\n\n"
            "References are relative to /workspace/.tau/skills.\n\n"
            "# Review\nFull noisy instructions.",
        ),
        ("user", "check the auth flow", None),
    ]


def test_tui_adapter_uses_extension_tool_renderers() -> None:
    state = TuiState()

    def render_call(arguments: dict[str, object], _theme: object, context: object) -> str:
        assert isinstance(context, dict)
        return f"Analyze {arguments['path']} as {context['toolName']}"

    def render_result(
        result: AgentToolResult,
        options: dict[str, object],
        _theme: object,
        context: object,
    ) -> str:
        assert isinstance(context, dict)
        return (
            f"Analysis {result.content} expanded={options['expanded']} "
            f"error={context['isError']}"
        )

    adapter = TuiEventAdapter(
        state,
        extension_tool_sources={"analyze_fixture": "quality-lab"},
        extension_tool_renderers={
            "analyze_fixture": ExtensionToolRenderers(
                call=render_call,
                result=render_result,
            )
        },
    )

    adapter.apply(
        ToolExecutionStartEvent(
            tool_call=ToolCall(
                id="call-1",
                name="analyze_fixture",
                arguments={"path": "fixture.json"},
            )
        )
    )
    adapter.apply(
        ToolExecutionEndEvent(
            result=AgentToolResult(
                tool_call_id="call-1",
                name="analyze_fixture",
                ok=True,
                content="complete",
            )
        )
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        (
            "tool",
            "Analyze fixture.json as analyze_fixture [extension:quality-lab]",
            "Analysis complete expanded=False error=False",
        )
    ]


def test_tui_adapter_groups_thinking_deltas_separately() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(ThinkingDeltaEvent(delta="hidden "))
    adapter.apply(ThinkingDeltaEvent(delta="reasoning"))

    assert [(item.role, item.text) for item in state.items] == [("thinking", "hidden reasoning")]
    assert state.show_thinking is False


def test_tui_adapter_updates_hidden_thinking_status_from_pipeline_stage() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(
        ToolExecutionUpdateEvent(
            tool_call_id="memory-1",
            message="intent classified",
            data={"memory_stage": "intent"},
        )
    )
    assert state.thinking_placeholder_text == "Getting Intent..."

    adapter.apply(
        ToolExecutionUpdateEvent(
            tool_call_id="memory-1",
            message="entities extracted",
            data={"pipeline_stage": "extract_entities"},
        )
    )
    assert state.thinking_placeholder_text == "Extracting Entities..."

    adapter.apply(
        ToolExecutionUpdateEvent(
            tool_call_id="memory-1",
            message="plain update",
        )
    )
    assert state.thinking_placeholder_text == "Extracting Entities..."

    adapter.apply(
        ToolExecutionUpdateEvent(
            tool_call_id="memory-1",
            message="memory recall started",
            data={"memory_stage": "recall"},
        )
    )
    assert state.thinking_placeholder_text == "Accessing Memory..."

    adapter.apply(
        ToolExecutionUpdateEvent(
            tool_call_id="figure-1",
            message="figure started",
            data={"stage": "figure"},
        )
    )
    assert state.thinking_placeholder_text == "Creating Figure..."

    adapter.apply(
        ToolExecutionUpdateEvent(
            tool_call_id="voice-1",
            message="voice metadata attached",
            data={"stage": "personaplex"},
        )
    )
    assert state.thinking_placeholder_text == "Preparing Persona Voice..."


def test_tui_adapter_updates_loop_monitor_status_from_tool_update() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(
        ToolExecutionUpdateEvent(
            tool_call_id="loop2-monitor",
            message="loop event stream updated",
            data={
                "loop2_monitor": {
                    "label": "STREAM READY",
                    "run_id": "loop2-tau-stress-math_add-1782507220-da39d0",
                    "event_count": 25,
                    "last_event_type": "receipt_written",
                    "receipt_status": "PASS",
                    "proof_scope": "loop2_tau_harness_stream",
                    "mocked": False,
                    "live": True,
                    "does_not_prove": ["provider semantic correctness"],
                    "source": "http://127.0.0.1:8876",
                }
            },
        )
    )

    assert state.loop_monitor_status is not None
    assert state.loop_monitor_status.label == "STREAM READY"
    assert state.loop_monitor_status.run_id == "loop2-tau-stress-math_add-1782507220-da39d0"
    assert state.loop_monitor_status.event_count == 25
    assert state.loop_monitor_status.last_event_type == "receipt_written"
    assert state.loop_monitor_status.receipt_status == "PASS"
    assert state.loop_monitor_status.proof_scope == "loop2_tau_harness_stream"
    assert state.loop_monitor_status.mocked is False
    assert state.loop_monitor_status.live is True
    assert state.loop_monitor_status.does_not_prove == ("provider semantic correctness",)


def test_tui_adapter_flushes_assistant_buffer_before_tool_events() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(MessageDeltaEvent(delta="Before tool"))
    adapter.apply(
        ToolExecutionStartEvent(
            tool_call=ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
        )
    )

    assert state.assistant_buffer == ""
    assert state.items[0].role == "assistant"
    assert state.items[0].text == "Before tool"
    assert state.items[1].role == "tool"
    assert "→ read" in state.items[1].text


def test_tui_adapter_renders_skill_file_reads_with_skill_style() -> None:
    skill = Skill(
        name="review",
        path=Path("/workspace/.tau/skills/review.md"),
        content="# Review",
        description="Review code",
    )
    state = TuiState(skills=(skill,))
    adapter = TuiEventAdapter(state)

    adapter.apply(
        ToolExecutionStartEvent(
            tool_call=ToolCall(
                id="call-1",
                name="read",
                arguments={"path": "/workspace/.tau/skills/review.md"},
            )
        )
    )
    adapter.apply(
        ToolExecutionEndEvent(
            result=AgentToolResult(
                tool_call_id="call-1",
                name="read",
                ok=True,
                content="# Review\nFull instructions.",
            )
        )
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        ("skill", "Loading skill: review", "✓ read\n# Review\nFull instructions.")
    ]


def test_tui_adapter_leaves_ordinary_reads_as_tool_items() -> None:
    skill = Skill(
        name="review",
        path=Path("/workspace/.tau/skills/review.md"),
        content="# Review",
        description="Review code",
    )
    state = TuiState(skills=(skill,))
    adapter = TuiEventAdapter(state)

    adapter.apply(
        ToolExecutionStartEvent(
            tool_call=ToolCall(
                id="call-1",
                name="read",
                arguments={"path": "/workspace/README.md"},
            )
        )
    )

    assert [(item.role, item.text) for item in state.items] == [
        ("tool", "→ read /workspace/README.md")
    ]


def test_read_tool_call_blocks_compact_tau_docs_and_resources() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert (
        format_tool_call_block(
            ToolCall(id="call-1", name="read", arguments={"path": str(repo_root / "README.md")})
        )
        == "→ read docs README.md (Ctrl+O to expand)"
    )
    assert (
        format_tool_call_block(
            ToolCall(
                id="call-2",
                name="read",
                arguments={"path": str(repo_root / "docs" / "configuration.md")},
            )
        )
        == "→ read docs docs/configuration.md (Ctrl+O to expand)"
    )
    assert (
        format_tool_call_block(
            ToolCall(
                id="call-3",
                name="read",
                arguments={"path": str(repo_root / "examples" / "README.md"), "offset": 2},
            )
        )
        == "→ read docs examples/README.md:2- (Ctrl+O to expand)"
    )
    assert (
        format_tool_call_block(
            ToolCall(id="call-4", name="read", arguments={"path": str(repo_root / "AGENTS.md")})
        )
        == "→ read resource AGENTS.md (Ctrl+O to expand)"
    )


def test_tool_call_blocks_use_human_readable_invocations() -> None:
    assert (
        format_tool_call_block(
            ToolCall(
                id="call-1",
                name="read",
                arguments={"path": "tests/test_tui_app.py", "offset": 1, "limit": 80},
            )
        )
        == "→ read tests/test_tui_app.py:1-80"
    )
    assert (
        format_tool_call_block(
            ToolCall(id="call-2", name="edit", arguments={"path": "src/tau_coding/tui/app.py"})
        )
        == "→ edit src/tau_coding/tui/app.py"
    )
    assert (
        format_tool_call_block(
            ToolCall(
                id="call-3",
                name="bash",
                arguments={
                    "command": "git log --oneline --decorate --graph --max-count=8",
                    "timeout": 30,
                },
            )
        )
        == "$ git log --oneline --decorate --graph --max-count=8 (timeout 30s)"
    )
    assert (
        format_tool_call_block(
            ToolCall(
                id="call-4",
                name="analyze_fixture",
                arguments={"path": "fixture.json", "strict": True},
            )
        )
        == '→ analyze_fixture {"path": "fixture.json", "strict": true}'
    )


def test_tui_adapter_records_tool_updates_and_results() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(ToolExecutionUpdateEvent(tool_call_id="call-1", message="reading"))
    adapter.apply(
        ToolExecutionEndEvent(
            result=AgentToolResult(tool_call_id="call-1", name="read", ok=True, content="done")
        )
    )
    adapter.apply(
        ToolExecutionEndEvent(
            result=AgentToolResult(
                tool_call_id="call-2",
                name="bash",
                ok=False,
                content="failed",
            )
        )
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        ("tool", "… reading", None),
        ("tool", "✓ read", "✓ read\ndone"),
        ("tool", "✗ bash", "✗ bash\nfailed"),
    ]


def test_tui_adapter_records_retry_status() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(
        RetryEvent(
            attempt=2,
            max_attempts=3,
            delay_seconds=0,
            message="Retrying provider request 2/3 after HTTP 503.",
        )
    )

    assert [(item.role, item.text) for item in state.items] == [
        ("status", "Retrying (2/3) in 0s... (Escape to cancel)")
    ]


def test_tui_adapter_records_queue_updates() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(QueueUpdateEvent(steering=("adjust",), follow_up=("after",)))

    assert state.queued_steering == ("adjust",)
    assert state.queued_follow_up == ("after",)
    assert state.queued_message_count == 2


def test_tool_result_blocks_preview_long_content() -> None:
    content = "\n".join(f"line {index}" for index in range(1, 12))

    block = format_tool_result_block(name="read", ok=True, content=content)

    assert "line 1" in block
    assert "line 8" in block
    assert "line 9" not in block
    assert "3 more lines" in block


def test_tool_result_formats_permission_receipt_data_for_operator_readability() -> None:
    block = format_tool_result_block(
        name="permission-request",
        ok=True,
        content="",
        data={
            "schema": "tau.permission_request_receipt.v1",
            "status": "PENDING",
            "mocked": False,
            "live": True,
            "request_id": "perm-123",
            "action": "working_tree_mutation",
            "decision": "ASK",
            "resources": ["src/tau_coding/tui/state.py"],
            "receipt_path": "/tmp/tau/permission-request.json",
            "errors": [],
            "proof_scope": {
                "does_not_prove": ["the requested mutation was executed"],
            },
        },
    )

    assert block == (
        "✓ permission-request · PENDING · tau.permission_request_receipt.v1\n"
        "Action: working_tree_mutation\n"
        "Decision: ASK\n"
        "Request: perm-123\n"
        "Resources: src/tau_coding/tui/state.py\n"
        "Evidence: mocked=false live=true\n"
        "Receipt: /tmp/tau/permission-request.json\n"
        "Does not prove: the requested mutation was executed"
    )


def test_tool_result_formats_approval_receipt_json_output_for_operator_readability() -> None:
    content = (
        '{"schema":"tau.approval_gate_receipt.v1","status":"BLOCKED",'
        '"mocked":false,"live":false,"requested_action":"github_apply",'
        '"approved":false,"approval_packet":"/tmp/tau/approval.json",'
        '"errors":["approved must be true"],'
        '"proof_scope":{"does_not_prove":["the gated mutation was executed"]}}'
    )

    block = format_tool_result_block(name="bash", ok=False, content=content)

    assert block == (
        "✗ bash · BLOCKED · tau.approval_gate_receipt.v1\n"
        "Action: github_apply\n"
        "Approved: false\n"
        "Evidence: mocked=false live=false\n"
        "Receipt: /tmp/tau/approval.json\n"
        "Errors:\n"
        "- approved must be true\n"
        "Does not prove: the gated mutation was executed"
    )


def test_tui_adapter_renders_live_edit_patch() -> None:
    state = TuiState()
    adapter = TuiEventAdapter(state)

    adapter.apply(
        ToolExecutionEndEvent(
            result=AgentToolResult(
                tool_call_id="call-1",
                name="edit",
                ok=True,
                content="Successfully replaced 1 block.",
                data={"patch": "--- a.py\n+++ a.py\n@@\n-old\n+new"},
            )
        )
    )

    assert [(item.role, item.text, item.tool_result_text) for item in state.items] == [
        (
            "tool",
            "✓ edit",
            "✓ edit\nSuccessfully replaced 1 block.\n\nPatch:\n--- a.py\n+++ a.py\n@@\n-old\n+new",
        )
    ]


def test_tui_adapter_records_errors_and_stops_on_non_recoverable_error() -> None:
    state = TuiState(running=True, assistant_buffer="partial")
    adapter = TuiEventAdapter(state)

    adapter.apply(ErrorEvent(message="provider failed", recoverable=False))

    assert state.running is False
    assert state.error == "provider failed"
    assert [(item.role, item.text) for item in state.items] == [
        ("assistant", "partial"),
        ("error", "Error: provider failed"),
    ]


def test_tui_adapter_renders_cancellation_as_status() -> None:
    state = TuiState(running=True, assistant_buffer="partial")
    adapter = TuiEventAdapter(state)

    adapter.apply(ErrorEvent(message="Agent run cancelled", recoverable=True))

    assert state.running is True
    assert state.error is None
    assert [(item.role, item.text) for item in state.items] == [
        ("assistant", "partial"),
        ("status", "Agent run cancelled."),
    ]
