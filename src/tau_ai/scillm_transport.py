"""SciLLM normalized model-turn transport provider (scillm#28, tau#310).

Implements ``ModelProvider`` over ``POST /v1/scillm/transports`` and its
``/turns``/``/events``/``/result``/``/cancel`` sub-resources. One
``stream_response`` call carries exactly one Tau-controlled model turn;
SciLLM never executes tools — tool-call turns stop at
``awaiting_tool_result`` until Tau posts results back through the next turn.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx

from tau_agent.messages import AgentMessage, AssistantMessage, ToolResultMessage, UserMessage
from tau_agent.tools import AgentTool, ToolCall
from tau_ai.events import (
    ProviderErrorEvent,
    ProviderEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
)
from tau_ai.provider import CancellationToken

TRANSPORT_REQUEST_SCHEMA = "scillm.transport_request.v1"


class ScillmTransportError(RuntimeError):
    """Fail-closed transport error with the upstream detail preserved."""


class ScillmTransportProvider:
    """One SciLLM transport session; each stream_response is one model turn."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        profile_id: str,
        correlation: Mapping[str, Any],
        required_capabilities: Sequence[str] = (),
        timeout_seconds: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._profile_id = profile_id
        self._correlation = dict(correlation)
        self._required_capabilities = list(required_capabilities)
        self._timeout = timeout_seconds
        self._transport_id: str | None = None
        self._sent_messages = 0
        self.turn_results: list[dict[str, Any]] = []

    @property
    def transport_id(self) -> str | None:
        return self._transport_id

    async def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[AgentMessage],
        tools: Sequence[AgentTool] = (),
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        del model  # the SciLLM profile owns provider/model resolution
        chat_messages = [{"role": "system", "content": system}] + [
            _to_chat_message(message) for message in messages
        ]
        async with httpx.AsyncClient(timeout=self._timeout + 30) as client:
            try:
                if self._transport_id is None:
                    await self._create(client, chat_messages, tools)
                else:
                    await self._next_turn(client, chat_messages)
                self._sent_messages = len(chat_messages)
                result = await self._wait_result(client, signal)
            except ScillmTransportError as error:
                yield ProviderErrorEvent(message=str(error))
                return
            self.turn_results.append(result)
            yield ProviderResponseStartEvent(model=str(result.get("model") or self._profile_id))
            state = result.get("state")
            if state == "awaiting_tool_result":
                assistant = await self._assistant_from_events(client, "tool_call_request")
                yield ProviderResponseEndEvent(message=assistant, finish_reason="tool_calls")
            elif state == "turn_completed":
                assistant = await self._assistant_from_events(client, "assistant_message")
                yield ProviderResponseEndEvent(message=assistant, finish_reason="stop")
            else:
                yield ProviderErrorEvent(
                    message=f"transport state {state!r}: {result.get('state_reason')}"
                )

    async def cancel(self) -> dict[str, Any]:
        if self._transport_id is None:
            raise ScillmTransportError("no transport session to cancel")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self._base_url}/v1/scillm/transports/{self._transport_id}/cancel",
                headers=self._headers(),
            )
        return response.json()

    async def _create(
        self,
        client: httpx.AsyncClient,
        chat_messages: list[dict[str, Any]],
        tools: Sequence[AgentTool],
    ) -> None:
        body = {
            "schema": TRANSPORT_REQUEST_SCHEMA,
            "profile": self._profile_id,
            "correlation": self._correlation,
            "messages": chat_messages,
            "required_capabilities": self._required_capabilities,
            "limits": {"timeout_sec": int(self._timeout)},
        }
        if tools:
            body["tools"] = [_to_chat_tool(tool) for tool in tools]
        response = await client.post(
            f"{self._base_url}/v1/scillm/transports", headers=self._headers(), json=body
        )
        payload = response.json()
        if response.status_code == 409 and payload.get("state") == "fork_required":
            raise ScillmTransportError(f"fork_required:{payload['error']['message']}")
        if response.status_code != 201:
            raise ScillmTransportError(f"transport create failed: {response.text[:300]}")
        self._transport_id = payload["transport_id"]

    async def _next_turn(
        self, client: httpx.AsyncClient, chat_messages: list[dict[str, Any]]
    ) -> None:
        new_messages = chat_messages[self._sent_messages :]
        tool_results = [
            {"tool_call_id": item["tool_call_id"], "content": item["content"]}
            for item in new_messages
            if item.get("role") == "tool"
        ]
        other = [
            item
            for item in new_messages
            if item.get("role") not in ("tool", "assistant")
        ]
        response = await client.post(
            f"{self._base_url}/v1/scillm/transports/{self._transport_id}/turns",
            headers=self._headers(),
            json={"tool_results": tool_results, "messages": other},
        )
        if response.status_code != 202:
            raise ScillmTransportError(f"transport turn failed: {response.text[:300]}")

    async def _wait_result(
        self, client: httpx.AsyncClient, signal: CancellationToken | None
    ) -> dict[str, Any]:
        remaining = self._timeout
        while remaining > 0:
            if signal is not None and signal.is_cancelled():
                await self.cancel()
                raise ScillmTransportError("cancelled by Tau")
            window = min(remaining, 10.0)
            response = await client.get(
                f"{self._base_url}/v1/scillm/transports/{self._transport_id}/result",
                headers=self._headers(),
                params={"wait_sec": window},
            )
            payload = response.json()
            if response.status_code == 200 and payload.get("ok") is not None:
                return payload
            remaining -= window
        raise ScillmTransportError("transport result timed out")

    async def _assistant_from_events(
        self, client: httpx.AsyncClient, event_type: str
    ) -> AssistantMessage:
        response = await client.get(
            f"{self._base_url}/v1/scillm/transports/{self._transport_id}/events",
            headers=self._headers(),
        )
        events = response.json().get("events", [])
        matches = [event for event in events if event.get("type") == event_type]
        if not matches:
            raise ScillmTransportError(f"no {event_type} event in transport journal")
        data = matches[-1].get("data", {})
        if event_type == "assistant_message":
            return AssistantMessage(content=str(data.get("content", "")))
        tool_calls = [
            ToolCall(
                id=str(item.get("id", f"call-{index}")),
                name=str(item.get("function", {}).get("name", "")),
                arguments=_parse_arguments(item.get("function", {}).get("arguments")),
            )
            for index, item in enumerate(data.get("tool_calls", []), start=1)
        ]
        return AssistantMessage(content=str(data.get("content", "")), tool_calls=tool_calls)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "X-Caller-Skill": "tau-native-agent-loop",
        }


def _to_chat_message(message: AgentMessage) -> dict[str, Any]:
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, AssistantMessage):
        entry: dict[str, Any] = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ]
        return entry
    if isinstance(message, ToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    raise ScillmTransportError(f"unsupported message type {type(message).__name__}")


def _to_chat_tool(tool: AgentTool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        },
    }


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    return {}
