"""Experimental Tau loop receipt writer for Loop2 alignment.

This module intentionally lives outside `src/`. It proves one small behavior:
Tau's existing prompt loop can be observed and written as Loop2-shaped evidence
artifacts without changing the production loop.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from tau_agent.events import AgentEvent, ErrorEvent
from tau_agent.messages import AssistantMessage
from tau_agent.session import JsonlSessionStorage
from tau_ai import (
    FakeProvider,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderTextDeltaEvent,
)
from tau_ai.provider import ModelProvider
from tau_coding.session import CodingSession, CodingSessionConfig

CHUTES_BASE_URL = "https://llm.chutes.ai/v1"
CHUTES_DEFAULT_MODEL = "Qwen/Qwen3-32B-TEE"


@dataclass(frozen=True, slots=True)
class TauLoopReceiptResult:
    """Paths written by one experimental receipt run."""

    run_dir: Path
    events_path: Path
    current_state_path: Path
    final_receipt_path: Path
    node_result_path: Path
    session_path: Path


async def run_fake_tau_loop_receipt(
    *,
    run_dir: Path,
    prompt: str = "prove the tau loop",
    response: str = "Tau loop receipt works.",
    node_id: str = "tau-fake-provider-loop",
) -> TauLoopReceiptResult:
    """Run one Tau prompt with `FakeProvider` and write Loop2-shaped artifacts."""

    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model="fake"),
                ProviderTextDeltaEvent(delta=response),
                ProviderResponseEndEvent(
                    message=AssistantMessage(content=response),
                    finish_reason="stop",
                ),
            ]
        ]
    )
    return await _run_tau_loop_receipt(
        run_dir=run_dir,
        prompt=prompt,
        node_id=node_id,
        provider=provider,
        provider_name="fake",
        model="fake",
        mocked=True,
        live=False,
        close_provider=False,
    )


async def run_chutes_tau_loop_receipt(
    *,
    run_dir: Path,
    prompt: str = "Reply with exactly: TAU_CHUTES_OK",
    model: str = CHUTES_DEFAULT_MODEL,
    node_id: str = "tau-chutes-provider-loop",
) -> TauLoopReceiptResult:
    """Run one Tau prompt through Chutes using Tau's OpenAI-compatible provider."""

    token = os.environ.get("CHUTES_API_TOKEN") or os.environ.get("CHUTES_API_KEY")
    if not token:
        raise RuntimeError("CHUTES_API_TOKEN or CHUTES_API_KEY is required")

    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            api_key=token,
            base_url=CHUTES_BASE_URL,
            timeout_seconds=60.0,
            max_retries=1,
            max_retry_delay_seconds=1.0,
        )
    )
    return await _run_tau_loop_receipt(
        run_dir=run_dir,
        prompt=prompt,
        node_id=node_id,
        provider=provider,
        provider_name="chutes",
        model=model,
        mocked=False,
        live=True,
        close_provider=True,
    )


async def _run_tau_loop_receipt(
    *,
    run_dir: Path,
    prompt: str,
    node_id: str,
    provider: ModelProvider,
    provider_name: str,
    model: str,
    mocked: bool,
    live: bool,
    close_provider: bool,
) -> TauLoopReceiptResult:
    """Run one Tau prompt and write receipt artifacts."""

    run_dir.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"
    current_state_path = run_dir / "current-state.json"
    final_receipt_path = run_dir / "final-receipt.json"
    node_result_path = run_dir / "node-result.json"
    session_path = run_dir / "session.jsonl"

    storage = JsonlSessionStorage(session_path)
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model=model,
            storage=storage,
            cwd=run_dir,
            provider_name=provider_name,
        )
    )

    started_at = _now()
    started_monotonic = monotonic()
    status = "PASS"
    event_count = 0
    last_event_type = ""

    try:
        with events_path.open("w", encoding="utf-8") as events_file:
            async for event in session.prompt(prompt):
                event_count += 1
                last_event_type = event.type
                if isinstance(event, ErrorEvent) and not event.recoverable:
                    status = "FAILED"
                events_file.write(
                    json.dumps(
                        {
                            "sequence": event_count,
                            "timestamp": _now(),
                            "event": _event_json(event),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
    finally:
        await session.aclose()
        if close_provider:
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()

    elapsed_s = round(monotonic() - started_monotonic, 6)
    finished_at = _now()
    session_entries = await storage.read_all()

    current_state = {
        "schema": "tau.loop_receipt.current_state.v1",
        "node_id": node_id,
        "status": status,
        "last_event_type": last_event_type,
        "event_count": event_count,
        "mocked": mocked,
        "live": live,
        "updated_at": finished_at,
    }
    does_not_prove = [
        "Scillm/OpenCode repair behavior",
        "semantic repair quality",
        "TransportRoom DAG rendering",
    ]
    if mocked:
        does_not_prove.insert(0, "live provider behavior")

    final_receipt = {
        "schema": "tau.loop_receipt.final.v1",
        "node_id": node_id,
        "status": status,
        "mocked": mocked,
        "live": live,
        "provider": provider_name,
        "model": model,
        "prompt": prompt,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_s": elapsed_s,
        "event_count": event_count,
        "session_path": str(session_path),
        "session_entry_count": len(session_entries),
        "claims": {
            "proves": [
                f"Tau {provider_name} prompt loop emitted agent events",
                "Tau coding session persisted prompt and assistant messages",
                "experimental receipt artifacts were written",
            ],
            "does_not_prove": does_not_prove,
        },
    }
    node_result = {
        "schema": "loop2.node_result.v1",
        "node_id": node_id,
        "status": status,
        "mocked": mocked,
        "live": live,
        "events": str(events_path),
        "final_receipt": str(final_receipt_path),
        "checks": [],
        "changed_files": [
            str(events_path),
            str(current_state_path),
            str(final_receipt_path),
            str(node_result_path),
            str(session_path),
        ],
    }

    _write_json(current_state_path, current_state)
    _write_json(final_receipt_path, final_receipt)
    _write_json(node_result_path, node_result)

    return TauLoopReceiptResult(
        run_dir=run_dir,
        events_path=events_path,
        current_state_path=current_state_path,
        final_receipt_path=final_receipt_path,
        node_result_path=node_result_path,
        session_path=session_path,
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _event_json(event: AgentEvent) -> dict[str, object]:
    return event.model_dump(mode="json")


def _now() -> str:
    return datetime.now(UTC).isoformat()
