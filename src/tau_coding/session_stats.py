"""Lifetime activity and usage totals for an active session branch."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tau_agent.messages import AssistantMessage, UserMessage
from tau_agent.session import MessageEntry
from tau_agent.session.entries import SessionEntry


@dataclass(frozen=True, slots=True)
class SessionStats:
    """Cumulative activity and provider-reported usage for one active branch."""

    turn_count: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated_cost: float | None = None
    latest_cache_hit_rate: float | None = None


def calculate_session_stats(entries: Sequence[SessionEntry]) -> SessionStats:
    """Aggregate original branch messages, including messages replaced by compaction."""
    turn_count = 0
    tool_call_count = 0
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    estimated_cost = 0.0
    has_cost = False
    latest_cache_hit_rate: float | None = None

    for entry in entries:
        if not isinstance(entry, MessageEntry):
            continue
        message = entry.message
        if isinstance(message, UserMessage):
            turn_count += 1
            continue
        if not isinstance(message, AssistantMessage):
            continue

        tool_call_count += len(message.tool_calls)
        usage = message.usage
        prompt_tokens = usage.input + usage.cache_read + usage.cache_write
        input_tokens += usage.input
        output_tokens += usage.output
        cache_read_tokens += usage.cache_read
        cache_write_tokens += usage.cache_write
        if prompt_tokens > 0:
            latest_cache_hit_rate = (usage.cache_read / prompt_tokens) * 100
        if usage.cost.total > 0:
            estimated_cost += usage.cost.total
            has_cost = True

    return SessionStats(
        turn_count=turn_count,
        tool_call_count=tool_call_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        estimated_cost=estimated_cost if has_cost else None,
        latest_cache_hit_rate=latest_cache_hit_rate,
    )
