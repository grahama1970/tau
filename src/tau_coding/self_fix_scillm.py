"""Streaming Scillm caller for Tau self-fix coder/reviewer loops."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

SCILLM_CALL_SCHEMA = "tau.self_fix_scillm_call_receipt.v1"


def call_scillm_streaming(
    *,
    role: str,
    model: str,
    scillm_base_url: str,
    timeout_s: float,
    api_key: str | None,
    api_key_source: str | None,
    payload: dict[str, Any],
    events_path: Path,
) -> dict[str, Any]:
    """Call Scillm through streaming chat completions and write SSE events."""

    started = time.monotonic()
    request = {
        "model": model,
        "stream": True,
        "stream_heartbeat_s": 15,
        "stream_progress_events": True,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are Tau's bounded {role} subagent. Return concise review notes. "
                    "Do not claim proof unless deterministic artifacts are supplied."
                ),
            },
            {"role": "user", "content": json.dumps(payload, sort_keys=True)},
        ],
        "temperature": 0,
        "scillm_metadata": {
            "caller": "tau",
            "proof": "self-fix-coder-reviewer-loop",
            "role": role,
        },
    }
    receipt: dict[str, Any] = {
        "schema": SCILLM_CALL_SCHEMA,
        "role": role,
        "model": model,
        "scillm_base_url": scillm_base_url,
        "api_key_source": api_key_source,
        "request": {**request, "messages": "<redacted-request-messages>"},
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
        "stream": True,
        "events_path": str(events_path),
    }
    if not api_key:
        receipt["error"] = "scillm_api_key_unavailable"
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt

    try:
        with httpx.Client(base_url=scillm_base_url.rstrip("/"), timeout=timeout_s) as client:
            with client.stream(
                "POST",
                "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Caller-Skill": "tau",
                    "Accept": "text/event-stream",
                },
                json=request,
            ) as response:
                receipt["http_status"] = response.status_code
                if response.status_code != 200:
                    body = response.read()
                    receipt["error"] = f"http_{response.status_code}"
                    receipt["response_excerpt"] = body.decode("utf-8", errors="replace")[:1000]
                    receipt["duration_seconds"] = round(time.monotonic() - started, 6)
                    return receipt
                stream_result = _collect_sse(response.iter_lines(), events_path)
    except httpx.HTTPError as exc:
        receipt["error"] = str(exc)
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        return receipt

    receipt["duration_seconds"] = round(time.monotonic() - started, 6)
    receipt["stream_event_count"] = stream_result["event_count"]
    receipt["stream_heartbeat_count"] = stream_result["heartbeat_count"]
    receipt["stream_done_seen"] = stream_result["done_seen"]
    receipt["stream_last_event_type"] = stream_result["last_event_type"]
    receipt["response"] = _redact_scillm_response(stream_result["last_payload"])
    content = stream_result["content"]
    if not content:
        receipt["error"] = "missing_message_content"
        return receipt
    receipt["status"] = "PASS"
    receipt["content_excerpt"] = content[:1200]
    return receipt


def _collect_sse(lines: Any, events_path: Path) -> dict[str, Any]:
    content_parts: list[str] = []
    event_count = 0
    heartbeat_count = 0
    done_seen = False
    current_event = "message"
    last_event_type = ""
    last_payload: dict[str, Any] = {}
    events_path.parent.mkdir(parents=True, exist_ok=True)
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        if not line:
            continue
        if line.startswith(":"):
            heartbeat_count += 1
            last_event_type = "heartbeat"
            _append_jsonl(events_path, {"type": "heartbeat", "created_at": _now_iso(), "raw": line[:1000]})
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
        _append_content_parts(payload, content_parts)
        current_event = "message"
    return {
        "content": "".join(content_parts),
        "event_count": event_count,
        "heartbeat_count": heartbeat_count,
        "done_seen": done_seen,
        "last_event_type": last_event_type,
        "last_payload": last_payload,
    }


def _append_content_parts(payload: object, content_parts: list[str]) -> None:
    if not isinstance(payload, dict):
        return
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


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _redact_scillm_response(body: object) -> object:
    if isinstance(body, dict):
        redacted = {}
        for key, value in body.items():
            if "key" in key.lower() or "token" in key.lower() or "authorization" in key.lower():
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_scillm_response(value)
        return redacted
    if isinstance(body, list):
        return [_redact_scillm_response(item) for item in body]
    return body
