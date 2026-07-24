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


class AssistantMessage(BaseModel):
    """A message authored by the assistant, optionally requesting tool calls."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None

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
