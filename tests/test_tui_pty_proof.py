from pathlib import Path
from collections.abc import AsyncIterator

import pytest
from textual.widgets import Static

from tau_agent import AgentEvent, AgentMessage, QueueUpdateEvent
from tau_agent.tools import AgentTool
from tau_coding.commands import CommandResult
from tau_coding.session import CodingSession, ModelChoice
from tau_coding.system_prompt import ProjectContextFile
from tau_coding.tui.app import TauTuiApp
from tau_coding.tui.pty_proof import (
    pty_input_received_line,
    pty_ready_line,
)
from typing import cast


class _ProofSessionState:
    thinking_level = "medium"
    loop_monitor_status = None


class _ProofSession:
    def __init__(self) -> None:
        self.cwd = Path("/workspace/project")
        self.provider_name = "pty-proof"
        self.model = "real-tui-app"
        self.available_models = ("real-tui-app",)
        self.available_model_choices = (ModelChoice(provider_name="pty-proof", model="real-tui-app"),)
        self.scoped_model_choices: tuple[ModelChoice, ...] = ()
        self.available_providers = ("pty-proof",)
        self.tools: tuple[AgentTool, ...] = ()
        self.skills: tuple[object, ...] = ()
        self.prompt_templates: tuple[object, ...] = ()
        self.context_files = (
            ProjectContextFile(path=str(self.cwd / "AGENTS.md"), content="PTY proof mode."),
        )
        self.context_token_estimate = 0
        self.auto_compact_token_threshold = 200000
        self.context_window_tokens = 216384
        self.thinking_level = "medium"
        self.available_thinking_levels = ("off", "minimal", "low", "medium", "high")
        self.resource_diagnostics: tuple[object, ...] = ()
        self.session_manager = None
        self.state = _ProofSessionState()
        self.messages: tuple[AgentMessage, ...] = ()

    def handle_command(self, text: str) -> CommandResult:
        del text
        return CommandResult(handled=False)

    def queue_update_event(self) -> QueueUpdateEvent:
        return QueueUpdateEvent(steering=(), follow_up=())

    async def prompt(
        self,
        text: str,
        *,
        streaming_behavior: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        del text, streaming_behavior
        if False:
            yield QueueUpdateEvent(steering=(), follow_up=())


def test_pty_ready_line_includes_run_id() -> None:
    assert pty_ready_line("run-123") == "TAU_TUI_PTY_READY run_id=run-123"


def test_pty_input_received_line_normalizes_input() -> None:
    assert (
        pty_input_received_line("run-123", "  hello   from browser  ")
        == "TAU_TUI_PTY_INPUT_RECEIVED run_id=run-123 input=hello from browser"
    )


@pytest.mark.anyio
async def test_real_tui_app_pty_proof_marker_updates_from_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAU_TUI_PTY_PROOF", "1")
    monkeypatch.setenv("TAU_TUI_PTY_RUN_ID", "real-app-run")
    app = TauTuiApp(cast(CodingSession, _ProofSession()))

    async with app.run_test(size=(120, 30)) as pilot:
        marker = app.query_one("#pty-proof-ready", Static)
        assert marker.render().plain == "TAU_TUI_PTY_READY run_id=real-app-run"

        prompt = app.query_one("#prompt")
        prompt.text = "TAU_TUI_PTY_BROWSER_INPUT from real app"
        await pilot.pause()

        assert (
            marker.render().plain
            == "TAU_TUI_PTY_INPUT_RECEIVED run_id=real-app-run input=TAU_TUI_PTY_BROWSER_INPUT from real app"
        )
