"""Deterministic PTY proof markers for Tau's browser-embedded TUI smoke test."""

from __future__ import annotations

from dataclasses import dataclass

READY_PREFIX = "TAU_TUI_PTY_READY"
INPUT_PREFIX = "TAU_TUI_PTY_INPUT_RECEIVED"


@dataclass(frozen=True)
class PtyProofMarker:
    """Small structured marker rendered by the PTY smoke app."""

    run_id: str
    value: str

    def ready_line(self) -> str:
        return f"{READY_PREFIX} run_id={self.run_id}"

    def input_line(self) -> str:
        clean_value = " ".join(self.value.strip().split())
        return f"{INPUT_PREFIX} run_id={self.run_id} input={clean_value}"


def normalize_pty_proof_input(value: str) -> str:
    """Return stable input text for proof receipts."""

    return " ".join(value.strip().split())


def pty_ready_line(run_id: str) -> str:
    """Return the deterministic ready line expected by browser PTY proof."""

    return PtyProofMarker(run_id=run_id, value="").ready_line()


def pty_input_received_line(run_id: str, value: str) -> str:
    """Return the deterministic input receipt line expected by browser PTY proof."""

    return PtyProofMarker(run_id=run_id, value=normalize_pty_proof_input(value)).input_line()
