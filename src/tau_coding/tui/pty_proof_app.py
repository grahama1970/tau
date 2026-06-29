"""Tiny T'au-owned Textual app for proving browser PTY transport."""

from __future__ import annotations

import os

from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from tau_coding.tui.pty_proof import pty_input_received_line, pty_ready_line


class TauPtyProofApp(App[None]):
    """Minimal Textual app that emits deterministic PTY proof markers."""

    CSS = """
    Screen {
        background: #020617;
        color: #dbeafe;
    }
    #ready {
        color: #67e8f9;
        padding: 1;
    }
    #receipt {
        color: #86efac;
        padding: 1;
    }
    Input {
        margin: 1;
    }
    """

    def __init__(self, *, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def compose(self) -> ComposeResult:
        yield Static(pty_ready_line(self.run_id), id="ready")
        yield Input(placeholder="type proof input and press enter", id="proof-input")
        yield Static("waiting for browser input", id="receipt")

    def on_mount(self) -> None:
        self.query_one("#proof-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = pty_input_received_line(self.run_id, event.value)
        self.query_one("#receipt", Static).update(line)
        self.query_one("#proof-input", Input).value = ""


def run_pty_proof_app() -> None:
    """Run the deterministic PTY proof app."""

    run_id = os.environ.get("TAU_TUI_PTY_RUN_ID", "tau-pty-proof")
    TauPtyProofApp(run_id=run_id).run()
