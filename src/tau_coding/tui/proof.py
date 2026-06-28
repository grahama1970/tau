"""Repeatable proof helpers for Tau's Textual TUI renderer."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

from tau_agent import (
    AgentEvent,
    AssistantMessage,
    ToolExecutionUpdateEvent,
    UserMessage,
)
from tau_coding.commands import CommandResult
from tau_coding.session import CodingSession, ModelChoice
from tau_coding.skills import Skill
from tau_coding.system_prompt import ProjectContextFile
from tau_coding.tools import create_coding_tools
from tau_coding.tui.app import TauTuiApp
from tau_coding.tui.state import LoopMonitorStatus
from tau_coding.tui.widgets import TranscriptView

DEFAULT_TUI_PROOF_PROMPT = "How does Tau handle a CWE-287 SPARTA evidence case?"
DEFAULT_TUI_PROOF_RUN_ID = "loop2-tau-stress-math_add-1782507220-da39d0"


class FixtureTuiProofSession:
    """Small session object for deterministic TauTuiApp render proofs."""

    def __init__(
        self,
        *,
        prompt: str,
        run_id: str,
        route: str,
        next_agent: str,
    ) -> None:
        self.cwd = Path("/workspace/project")
        self.provider_name = "openai"
        self.model = "fixture-model"
        self.available_models = ("fixture-model",)
        self.available_model_choices = (ModelChoice(provider_name="openai", model="fixture-model"),)
        self.scoped_model_choices: tuple[ModelChoice, ...] = ()
        self.available_providers = ("openai",)
        self.tools = tuple(create_coding_tools(cwd=self.cwd))
        self.skills = (Skill(name="review", path=self.cwd / "review.md", content="Review code"),)
        self.prompt_templates: tuple[object, ...] = ()
        self.context_files = (
            ProjectContextFile(path=str(self.cwd / "AGENTS.md"), content="Follow rules."),
        )
        self.context_token_estimate = 12034
        self.auto_compact_token_threshold = 200000
        self.context_window_tokens = 216384
        self.thinking_level = "medium"
        self.available_thinking_levels = ("off", "minimal", "low", "medium", "high")
        self.resource_diagnostics: tuple[object, ...] = ()
        self.session_manager = None
        self.state = _FixtureTuiProofSessionState()
        self.messages = (
            UserMessage(content=prompt),
            AssistantMessage(
                content=(
                    "tau.agent_handoff.v1 -> "
                    f"next_agent={next_agent}; route={route}; run_id={run_id}"
                )
            ),
        )

    def handle_command(self, text: str) -> CommandResult:
        del text
        return CommandResult(handled=False)

    async def prompt(
        self,
        text: str,
        *,
        streaming_behavior: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        del text, streaming_behavior
        if False:
            yield ToolExecutionUpdateEvent(tool_call_id="never", message="never")


class _FixtureTuiProofSessionState:
    thinking_level = "medium"
    loop_monitor_status: LoopMonitorStatus | None = None


def render_textual_tui_memory_stage_proof(
    *,
    output_dir: Path,
    prompt: str = DEFAULT_TUI_PROOF_PROMPT,
    run_id: str = DEFAULT_TUI_PROOF_RUN_ID,
    route: str = "COMPLIANCE",
    next_agent: str = "reviewer",
) -> dict[str, object]:
    """Render TauTuiApp with fixture Memory events and write proof artifacts."""

    import anyio

    return anyio.run(
        _render_textual_tui_memory_stage_proof,
        output_dir,
        prompt,
        run_id,
        route,
        next_agent,
    )


async def _render_textual_tui_memory_stage_proof(
    output_dir: Path,
    prompt: str,
    run_id: str,
    route: str,
    next_agent: str,
) -> dict[str, object]:
    resolved_output = output_dir.expanduser().resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    screenshot_svg = resolved_output / "tau-textual-tui-memory-stage.svg"
    receipt_path = resolved_output / "proof.json"
    session = FixtureTuiProofSession(
        prompt=prompt,
        run_id=run_id,
        route=route,
        next_agent=next_agent,
    )
    app = TauTuiApp(cast(CodingSession, session))
    async with app.run_test(size=(130, 24)) as pilot:
        app.state.add_thinking_delta("internal memory routing hidden from transcript")
        app._refresh()
        await pilot.pause()
        for event in _memory_stage_events(run_id=run_id):
            app.adapter.apply(event)
            await app._apply_streaming_transcript_event(event)
            app._refresh()
            await pilot.pause()
        transcript = app.query_one("#transcript", TranscriptView)
        transcript.scroll_end(animate=False, immediate=True)
        await pilot.pause()
        app.save_screenshot(str(screenshot_svg))
        text = "\n".join(line.text for line in transcript.lines)

    assertions = {
        "prompt": prompt in text,
        "accessing_memory": "Accessing Memory..." in text,
        "handoff_schema": "tau.agent_handoff.v1" in text,
        "next_agent": f"next_agent={next_agent}" in text,
        "run_id": run_id in text,
        "hidden_reasoning_absent": "internal memory routing hidden from transcript" not in text,
    }
    receipt: dict[str, object] = {
        "schema": "tau.textual_tui_render_proof.v1",
        "ok": all(assertions.values()),
        "mocked": True,
        "live": False,
        "exercised": (
            "real TauTuiApp Textual rendering with fixture session "
            "and structured Tau Memory events"
        ),
        "prompt": prompt,
        "run_id": run_id,
        "route": route,
        "next_agent": next_agent,
        "screenshot_svg": str(screenshot_svg),
        "visible_assertions": assertions,
        "does_not_prove": [
            "live provider call",
            "live Memory backend call from the TUI process",
            "interactive PTY embedded in UX Lab #tau",
            "production Sparta Chat readiness",
        ],
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def _memory_stage_events(*, run_id: str) -> tuple[ToolExecutionUpdateEvent, ...]:
    del run_id
    return (
        ToolExecutionUpdateEvent(
            tool_call_id="memory-1",
            message="intent classified",
            data={"memory_stage": "intent"},
        ),
        ToolExecutionUpdateEvent(
            tool_call_id="memory-1",
            message="entities extracted",
            data={"pipeline_stage": "extract_entities"},
        ),
        ToolExecutionUpdateEvent(
            tool_call_id="memory-1",
            message="memory recall started",
            data={"memory_stage": "recall"},
        ),
    )
