"""Provider-neutral tool definitions and tool execution results."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from tau_agent.types import JSONValue


class ToolCancellationToken(Protocol):
    """Minimal cancellation interface accepted by tools."""

    def is_cancelled(self) -> bool:
        """Return whether tool execution should stop."""
        ...


type ToolUpdateCallback = Callable[[str, Mapping[str, JSONValue] | None], None]


class ToolExecutor(Protocol):
    """Async callable used to execute a tool."""

    def __call__(
        self,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> Awaitable[AgentToolResult]:
        """Execute the tool with optional cancellation support."""
        ...


class ToolCall(BaseModel):
    """A request from the assistant to execute a named tool."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, JSONValue] = Field(default_factory=dict)


class AgentToolResult(BaseModel):
    """Structured result returned by a tool execution."""

    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    name: str
    ok: bool
    content: str
    data: dict[str, JSONValue] | None = None
    details: dict[str, JSONValue] | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentTool:
    """A tool that can be exposed to an agent loop."""

    name: str
    description: str
    input_schema: Mapping[str, JSONValue]
    executor: ToolExecutor
    prompt_snippet: str | None = None
    prompt_guidelines: tuple[str, ...] = ()

    async def execute(
        self,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        """Execute the tool with provider-neutral JSON-like arguments."""
        if on_update is None or not _accepts_on_update(self.executor):
            return await self.executor(arguments, signal=signal)
        return await self.executor(arguments, signal=signal, on_update=on_update)


def _accepts_on_update(executor: ToolExecutor) -> bool:
    try:
        signature = inspect.signature(executor)
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == "on_update"
        for parameter in signature.parameters.values()
    )
