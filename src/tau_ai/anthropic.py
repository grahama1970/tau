"""Anthropic Messages API provider."""

from collections.abc import AsyncIterator, Mapping
from json import loads
from typing import Any

import httpx

from tau_agent.messages import AgentMessage, AssistantMessage, ToolResultMessage, Usage, UserMessage
from tau_agent.tools import AgentTool, ToolCall
from tau_agent.types import JSONValue
from tau_ai.env import AnthropicConfig
from tau_ai.events import (
    ProviderErrorEvent,
    ProviderEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderTextDeltaEvent,
    ProviderThinkingDeltaEvent,
    ProviderToolCallEvent,
)
from tau_ai.provider import CancellationToken
from tau_ai.retry import provider_retry_event, retry_delay_seconds, wait_for_retry

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider:
    """Provider adapter for Anthropic's streaming Messages API."""

    def __init__(
        self,
        config: AnthropicConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the underlying HTTP client if this provider created it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Stream one Anthropic response as provider-neutral events."""

        async def iterator() -> AsyncIterator[ProviderEvent]:
            client = self._get_client()
            payload = _build_messages_payload(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                thinking_budget_tokens=self._config.thinking_budget_tokens,
                thinking_mode=self._config.thinking_mode,
                thinking_effort=self._config.thinking_effort,
            )
            payload = await _apply_before_provider_request(self._config.hooks, payload)
            headers = {
                **(dict(self._config.headers or {})),
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            }
            if self._config.use_bearer_auth:
                headers["Authorization"] = f"Bearer {self._config.api_key}"
            else:
                headers["x-api-key"] = self._config.api_key
            headers = await _apply_before_provider_headers(self._config.hooks, headers)
            url = f"{self._config.base_url.rstrip('/')}/messages"

            attempt = 0
            while True:
                emitted_content = False
                try:
                    async with client.stream(
                        "POST", url, json=payload, headers=headers
                    ) as response:
                        await _emit_after_provider_response(self._config.hooks, response)
                        if response.status_code >= 400:
                            body = await response.aread()
                            if self._should_retry(attempt, status_code=response.status_code):
                                delay = retry_delay_seconds(
                                    attempt,
                                    max_delay_seconds=self._config.max_retry_delay_seconds,
                                )
                                yield provider_retry_event(
                                    attempt=attempt,
                                    max_retries=self._config.max_retries,
                                    delay_seconds=delay,
                                    reason=f"HTTP {response.status_code}",
                                    data={
                                        "status_code": response.status_code,
                                        "body": body.decode(errors="replace"),
                                    },
                                )
                                attempt += 1
                                if not await wait_for_retry(delay, signal=signal):
                                    return
                                continue
                            yield ProviderErrorEvent(
                                message=(
                                    "Provider request failed with status "
                                    f"{response.status_code}"
                                ),
                                data={
                                    "body": body.decode(errors="replace"),
                                    "attempts": attempt + 1,
                                },
                            )
                            return

                        yield ProviderResponseStartEvent(model=model)
                        content_parts: list[str] = []
                        tool_builders: dict[int, _AnthropicToolBuilder] = {}
                        finish_reason: str | None = None
                        usage: Usage | None = None

                        async for line in response.aiter_lines():
                            if signal is not None and signal.is_cancelled():
                                return

                            event = _parse_sse_line(line)
                            if event is None:
                                continue
                            chunk = _loads_object(event)
                            if chunk is None:
                                yield ProviderErrorEvent(
                                    message="Provider returned invalid JSON chunk"
                                )
                                return

                            event_type = chunk.get("type")
                            if event_type == "message_start":
                                message = chunk.get("message")
                                if isinstance(message, Mapping):
                                    usage = _usage_from_message_start(message.get("usage"))
                            elif event_type == "content_block_start":
                                block = chunk.get("content_block")
                                if isinstance(block, Mapping) and block.get("type") == "tool_use":
                                    index = int(chunk.get("index", 0))
                                    builder = tool_builders.setdefault(
                                        index, _AnthropicToolBuilder()
                                    )
                                    builder.id = _string_or_empty(block.get("id"))
                                    builder.name = _string_or_empty(block.get("name"))
                                    emitted_content = True
                            elif event_type == "content_block_delta":
                                delta = chunk.get("delta")
                                if not isinstance(delta, Mapping):
                                    continue
                                delta_type = delta.get("type")
                                if delta_type == "text_delta":
                                    text = _string_or_empty(delta.get("text"))
                                    if text:
                                        emitted_content = True
                                        content_parts.append(text)
                                        yield ProviderTextDeltaEvent(delta=text)
                                elif delta_type == "thinking_delta":
                                    thinking = _string_or_empty(delta.get("thinking"))
                                    if thinking:
                                        emitted_content = True
                                        yield ProviderThinkingDeltaEvent(delta=thinking)
                                elif delta_type == "input_json_delta":
                                    index = int(chunk.get("index", 0))
                                    builder = tool_builders.setdefault(
                                        index, _AnthropicToolBuilder()
                                    )
                                    builder.arguments_parts.append(
                                        _string_or_empty(delta.get("partial_json"))
                                    )
                                    emitted_content = True
                            elif event_type == "message_delta":
                                delta = chunk.get("delta")
                                if isinstance(delta, Mapping):
                                    finish_reason = (
                                        _string_or_empty(delta.get("stop_reason"))
                                        or finish_reason
                                    )
                                usage = _apply_message_delta_usage(usage, chunk.get("usage"))
                            elif event_type == "error":
                                error = chunk.get("error")
                                message = "Provider returned an error"
                                if isinstance(error, Mapping):
                                    message = _string_or_empty(error.get("message")) or message
                                yield ProviderErrorEvent(message=message, data=chunk)
                                return

                        tool_calls = [
                            builder.build(index)
                            for index, builder in sorted(tool_builders.items())
                        ]
                        for tool_call in tool_calls:
                            yield ProviderToolCallEvent(tool_call=tool_call)

                        yield ProviderResponseEndEvent(
                            message=AssistantMessage(
                                content="".join(content_parts),
                                tool_calls=tool_calls,
                                usage=usage or Usage(),
                            ),
                            finish_reason=finish_reason,
                        )
                        return
                except httpx.HTTPError as exc:
                    if not emitted_content and self._should_retry(attempt):
                        delay = retry_delay_seconds(
                            attempt,
                            max_delay_seconds=self._config.max_retry_delay_seconds,
                        )
                        yield provider_retry_event(
                            attempt=attempt,
                            max_retries=self._config.max_retries,
                            delay_seconds=delay,
                            reason="network error",
                            data={
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            },
                        )
                        attempt += 1
                        if not await wait_for_retry(delay, signal=signal):
                            return
                        continue
                    yield ProviderErrorEvent(
                        message=str(exc),
                        data={"attempts": attempt + 1},
                    )
                    return

        return iterator()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
        return self._client

    def _should_retry(self, attempt: int, *, status_code: int | None = None) -> bool:
        if attempt >= self._config.max_retries:
            return False
        return status_code is None or status_code in {408, 409, 429, 500, 502, 503, 504}


class _AnthropicToolBuilder:
    def __init__(self) -> None:
        self.id = ""
        self.name = ""
        self.arguments_parts: list[str] = []

    def build(self, index: int) -> ToolCall:
        arguments_text = "".join(self.arguments_parts)
        arguments = _loads_object(arguments_text) if arguments_text else {}
        if arguments is None:
            arguments = {"_raw_arguments": arguments_text}
        return ToolCall(
            id=self.id or f"tool-call-{index}",
            name=self.name,
            arguments=arguments,
        )


def _build_messages_payload(
    *,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    thinking_budget_tokens: int | None = None,
    thinking_mode: str = "budget",
    thinking_effort: str | None = None,
) -> dict[str, JSONValue]:
    max_tokens = DEFAULT_MAX_TOKENS
    if thinking_budget_tokens is not None:
        max_tokens = max(max_tokens, thinking_budget_tokens + 1024)
    payload: dict[str, JSONValue] = {
        "model": model,
        "max_tokens": max_tokens,
        "stream": True,
        "system": system,
        "messages": [_anthropic_message(message) for message in messages],
    }
    if thinking_mode == "disabled":
        payload["thinking"] = {"type": "disabled"}
    elif thinking_mode == "adaptive" and thinking_effort is not None:
        payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        payload["output_config"] = {"effort": thinking_effort}
    elif thinking_budget_tokens is not None:
        payload["thinking"] = {
            "type": "enabled",
            "budget_tokens": thinking_budget_tokens,
        }
    if tools:
        payload["tools"] = [_anthropic_tool(tool) for tool in tools]
    return payload


def _anthropic_message(message: AgentMessage) -> dict[str, JSONValue]:
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, AssistantMessage):
        content: list[JSONValue] = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        for tool_call in message.tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "input": tool_call.arguments,
                }
            )
        return {"role": "assistant", "content": content}
    if isinstance(message, ToolResultMessage):
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                    "is_error": not message.ok,
                }
            ],
        }
    raise TypeError(f"Unsupported message type: {type(message).__name__}")


def _anthropic_tool(tool: AgentTool) -> dict[str, JSONValue]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.input_schema),
    }


def _parse_sse_line(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    return line.removeprefix("data:").strip()


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        value = loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _usage_from_message_start(raw: object) -> Usage:
    data = raw if isinstance(raw, Mapping) else {}
    cache_creation = data.get("cache_creation")
    cache_write_1h = (
        _int_or_none(cache_creation.get("ephemeral_1h_input_tokens"))
        if isinstance(cache_creation, Mapping)
        else None
    )
    usage = Usage(
        input=_int_or_none(data.get("input_tokens")) or 0,
        output=_int_or_none(data.get("output_tokens")) or 0,
        cache_read=_int_or_none(data.get("cache_read_input_tokens")) or 0,
        cache_write=_int_or_none(data.get("cache_creation_input_tokens")) or 0,
        cache_write_1h=cache_write_1h,
    )
    usage.total_tokens = usage.input + usage.output + usage.cache_read + usage.cache_write
    return usage


def _apply_message_delta_usage(usage: Usage | None, raw: object) -> Usage | None:
    if not isinstance(raw, Mapping):
        return usage
    usage = usage or Usage()
    if (value := _int_or_none(raw.get("input_tokens"))) is not None:
        usage.input = value
    if (value := _int_or_none(raw.get("output_tokens"))) is not None:
        usage.output = value
    if (value := _int_or_none(raw.get("cache_read_input_tokens"))) is not None:
        usage.cache_read = value
    if (value := _int_or_none(raw.get("cache_creation_input_tokens"))) is not None:
        usage.cache_write = value
    details = raw.get("output_tokens_details")
    if isinstance(details, Mapping):
        thinking = _int_or_none(details.get("thinking_tokens"))
        if thinking is not None:
            usage.reasoning = thinking
    usage.total_tokens = usage.input + usage.output + usage.cache_read + usage.cache_write
    return usage


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def _apply_before_provider_request(hooks: object, payload: object) -> object:
    before_request = getattr(hooks, "before_request", None)
    if not callable(before_request):
        return payload
    result = before_request(payload)
    if hasattr(result, "__await__"):
        result = await result
    return payload if result is None else result


async def _apply_before_provider_headers(
    hooks: object,
    headers: dict[str, str],
) -> dict[str, str]:
    before_headers = getattr(hooks, "before_headers", None)
    if not callable(before_headers):
        return headers
    result = before_headers(headers)
    if hasattr(result, "__await__"):
        result = await result
    if isinstance(result, Mapping):
        headers = {str(key): str(value) for key, value in result.items() if value is not None}
    else:
        headers = {key: value for key, value in headers.items() if value is not None}
    return headers


async def _emit_after_provider_response(hooks: object, response: httpx.Response) -> None:
    after_response = getattr(hooks, "after_response", None)
    if not callable(after_response):
        return
    result = after_response(response.status_code, dict(response.headers))
    if hasattr(result, "__await__"):
        await result
