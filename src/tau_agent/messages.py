"""Provider-neutral transcript message models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tau_agent.tools import ToolCall
from tau_agent.types import JSONValue


class UserMessage(BaseModel):
    """A message authored by the user."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user"] = "user"
    content: str

    @field_validator("content", mode="before")
    @classmethod
    def _normalize_content(cls, value: object) -> object:
        return "" if value is None else value


class UsageCost(BaseModel):
    """Provider-reported response cost in USD."""

    model_config = ConfigDict(extra="forbid")

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


class Usage(BaseModel):
    """Provider-reported token usage for one assistant response."""

    model_config = ConfigDict(extra="forbid")

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_1h: int | None = None
    reasoning: int | None = None
    total_tokens: int = 0
    cost: UsageCost = Field(default_factory=UsageCost)


class AssistantMessage(BaseModel):
    """A message authored by the assistant, optionally requesting tool calls."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)

    @field_validator("content", mode="before")
    @classmethod
    def _normalize_content(cls, value: object) -> object:
        return "" if value is None else value


class ToolResultMessage(BaseModel):
    """A transcript message containing the result of a previous tool call."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    content: str
    ok: bool = True
    data: dict[str, JSONValue] | None = None
    details: dict[str, JSONValue] | None = None
    error: str | None = None

    @field_validator("content", mode="before")
    @classmethod
    def _normalize_content(cls, value: object) -> object:
        return "" if value is None else value


type AgentMessage = UserMessage | AssistantMessage | ToolResultMessage
