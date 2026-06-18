"""Events emitted by Tau's portable agent layer."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from tau_agent.messages import AgentMessage
from tau_agent.tools import AgentToolResult, ToolCall
from tau_agent.types import JSONValue


class AgentStartEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["agent_start"] = "agent_start"


class AgentEndEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["agent_end"] = "agent_end"


class TurnStartEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["turn_start"] = "turn_start"
    turn: int


class TurnEndEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["turn_end"] = "turn_end"
    turn: int


class RetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["retry"] = "retry"
    attempt: int
    max_attempts: int
    delay_seconds: float
    message: str
    data: dict[str, JSONValue] | None = None


class MessageStartEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["message_start"] = "message_start"
    message_role: Literal["user", "assistant", "tool"] = "assistant"


class MessageDeltaEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["message_delta"] = "message_delta"
    delta: str


class MessageEndEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["message_end"] = "message_end"
    message: AgentMessage


class ToolExecutionStartEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call: ToolCall


class ToolExecutionUpdateEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str
    message: str
    data: dict[str, JSONValue] | None = None


class ToolExecutionEndEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_execution_end"] = "tool_execution_end"
    result: AgentToolResult


class ErrorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    message: str
    recoverable: bool = False
    data: dict[str, JSONValue] | None = None


type AgentEvent = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | RetryEvent
    | MessageStartEvent
    | MessageDeltaEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
    | ErrorEvent
)
