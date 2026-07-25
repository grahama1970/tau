from tau_agent import AssistantMessage, MessageEntry, ToolCall, Usage, UsageCost, UserMessage
from tau_agent.session import CompactionEntry
from tau_coding.session_stats import calculate_session_stats


def test_calculate_session_stats_keeps_compacted_branch_usage() -> None:
    user = MessageEntry(message=UserMessage(content="Fix it"))
    assistant = MessageEntry(
        parent_id=user.id,
        message=AssistantMessage(
            content="Working",
            tool_calls=[
                ToolCall(id="call-1", name="read", arguments={}),
                ToolCall(id="call-2", name="edit", arguments={}),
            ],
            usage=Usage(
                input=1_000_000,
                output=100_000,
                cache_read=500_000,
                cache_write=50_000,
                cost=UsageCost(total=3.05),
            ),
        ),
    )
    compaction = CompactionEntry(
        parent_id=assistant.id,
        summary="Earlier work",
        replaces_entry_ids=[user.id, assistant.id],
    )

    stats = calculate_session_stats([user, assistant, compaction])

    assert stats.turn_count == 1
    assert stats.tool_call_count == 2
    assert stats.input_tokens == 1_000_000
    assert stats.output_tokens == 100_000
    assert stats.cache_read_tokens == 500_000
    assert stats.cache_write_tokens == 50_000
    assert stats.latest_cache_hit_rate == 500_000 / 1_550_000 * 100
    assert stats.estimated_cost == 3.05
