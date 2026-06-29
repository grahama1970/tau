"""Streaming Scillm caller used by the Battle Tau handoff proof."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

SCILLM_CALL_SCHEMA = "tau.battle_scillm_call_receipt.v1"


async def call_scillm_async(
    *,
    handoff: dict[str, Any],
    team: str,
    persona: str,
    worker_context: dict[str, Any] | None,
    model: str,
    scillm_base_url: str,
    timeout_s: float,
    api_key: str | None,
    api_key_source: str,
    events_path: Path,
) -> dict[str, Any]:
    """Call Scillm with streaming enabled and write every SSE event to JSONL."""

    started = time.monotonic()
    payload = {
        "model": model,
        "stream": True,
        "stream_heartbeat_s": 15,
        "stream_progress_events": True,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a bounded Battle subagent. Return one concise action "
                    "summary for the supplied Tau handoff. Do not claim Docker proof."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(handoff, sort_keys=True),
            },
        ],
        "temperature": 0,
        "scillm_metadata": {
            "caller": "tau",
            "proof": "battle-live-handoff",
            "team": team,
            "persona": persona,
            "worker_id": _worker_field(worker_context, "worker_id"),
            "combination_id": _worker_field(worker_context, "combination_id"),
        },
    }
    receipt: dict[str, Any] = {
        "schema": SCILLM_CALL_SCHEMA,
        "team": team,
        "persona": persona,
        "worker_id": _worker_field(worker_context, "worker_id"),
        "combination_id": _worker_field(worker_context, "combination_id"),
        "model": model,
        "scillm_base_url": scillm_base_url,
        "api_key_source": api_key_source,
        "request": {**payload, "messages": "<redacted-request-messages>"},
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
        "stream": True,
        "events_path": str(events_path),
        "started_at_seconds": 0.0,
    }
    if not api_key:
        receipt["error"] = "scillm_api_key_unavailable"
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt

    try:
        async with httpx.AsyncClient(
            base_url=scillm_base_url.rstrip("/"),
            timeout=timeout_s,
        ) as client:
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "X-Caller-Skill": "tau",
                    "Accept": "text/event-stream",
                },
                json=payload,
            ) as response:
                receipt["http_status"] = response.status_code
                if response.status_code != 200:
                    body = await response.aread()
                    receipt["error"] = f"scillm_http_status_{response.status_code}"
                    receipt["response_text"] = body.decode("utf-8", errors="replace")[:1000]
                    receipt["duration_seconds"] = round(time.monotonic() - started, 6)
                    return receipt
                stream_result = await _collect_scillm_sse_async(response.aiter_lines(), events_path)
    except httpx.HTTPError as exc:
        receipt["error"] = f"scillm_http_error: {exc}"
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt

    receipt["duration_seconds"] = round(time.monotonic() - started, 6)
    receipt["stream_event_count"] = stream_result["event_count"]
    receipt["stream_heartbeat_count"] = stream_result["heartbeat_count"]
    receipt["stream_done_seen"] = stream_result["done_seen"]
    receipt["stream_last_event_type"] = stream_result["last_event_type"]
    receipt["response"] = _redact_response(stream_result["last_payload"])
    content = stream_result["content"]
    receipt["response_content"] = content
    receipt["status"] = "PASS" if content.strip() else "BLOCKED"
    if not content.strip():
        receipt["error"] = "scillm_empty_response_content"
    return receipt


async def _collect_scillm_sse_async(lines: Any, events_path: Path) -> dict[str, Any]:
    content_parts: list[str] = []
    event_count = 0
    heartbeat_count = 0
    done_seen = False
    current_event = "message"
    last_event_type = ""
    last_payload: dict[str, Any] = {}
    events_path.parent.mkdir(parents=True, exist_ok=True)
    async for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        if not line:
            continue
        if line.startswith(":"):
            heartbeat_count += 1
            last_event_type = "heartbeat"
            _append_jsonl(
                events_path,
                {"type": "heartbeat", "created_at": _now_iso(), "raw": line[:1000]},
            )
            continue
        if line.startswith("event:"):
            current_event = line.removeprefix("event:").strip() or "message"
            continue
        if not line.startswith("data:"):
            continue
        data_text = line.removeprefix("data:").strip()
        if data_text == "[DONE]":
            done_seen = True
            last_event_type = "done"
            _append_jsonl(events_path, {"type": "done", "created_at": _now_iso()})
            continue
        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError:
            payload = {"raw": data_text}
        event_count += 1
        last_payload = payload if isinstance(payload, dict) else {"payload": payload}
        event_type = current_event
        if isinstance(payload, dict) and isinstance(payload.get("type"), str):
            event_type = str(payload["type"])
        elif isinstance(payload, dict) and isinstance(payload.get("choices"), list):
            event_type = "chunk"
        last_event_type = event_type
        _append_jsonl(events_path, {"type": event_type, "created_at": _now_iso(), "data": payload})
        if isinstance(payload, dict):
            choices = payload.get("choices")
            for choice in choices if isinstance(choices, list) else []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    content_parts.append(delta["content"])
                message = choice.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    content_parts.append(message["content"])
        current_event = "message"
    return {
        "content": "".join(content_parts),
        "event_count": event_count,
        "heartbeat_count": heartbeat_count,
        "done_seen": done_seen,
        "last_event_type": last_event_type,
        "last_payload": last_payload,
    }


def _worker_field(worker_context: dict[str, Any] | None, field: str) -> Any:
    if not worker_context:
        return None
    return worker_context.get(field)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _redact_response(data: Any) -> Any:
    if isinstance(data, dict):
        redacted = {}
        for key, value in data.items():
            if "key" in key.lower() or "token" in key.lower() or "authorization" in key.lower():
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_response(value)
        return redacted
    if isinstance(data, list):
        return [_redact_response(item) for item in data]
    return data
