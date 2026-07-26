"""Structured diagnostic logging for Tau runtime surfaces."""

from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from tau_agent.events import ErrorEvent
from tau_coding.paths import TauPaths

DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL_ENV = "TAU_LOG_LEVEL"
LOG_PATH_ENV = "TAU_LOG_PATH"

_CONFIGURED = False


@dataclass(frozen=True, slots=True)
class AgentCallDiagnosticContext:
    """Non-secret context attached to an agent-call diagnostic entry."""

    provider_name: str
    model: str
    cwd: Path
    session_id: str | None
    run_id: str


class AgentCallDiagnosticLogger:
    """Append structured JSONL diagnostics for agent-call failures."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def from_paths(cls, paths: TauPaths | None = None) -> AgentCallDiagnosticLogger:
        """Create a logger using Tau's default path layout."""
        return cls((paths or TauPaths()).agent_calls_log_path)

    def log_exception(
        self,
        *,
        context: AgentCallDiagnosticContext,
        phase: str,
        exc: BaseException,
    ) -> Path:
        """Log an unexpected exception with traceback and return the log path."""
        entry = _base_entry(context, phase=phase, kind="exception")
        entry["exception"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        self._append(entry)
        return self.path

    def log_error_event(
        self,
        *,
        context: AgentCallDiagnosticContext,
        phase: str,
        event: ErrorEvent,
    ) -> Path:
        """Log an agent error event with safe provider diagnostic details."""
        entry = _base_entry(context, phase=phase, kind="error_event")
        entry["error"] = {
            "message": event.message,
            "recoverable": event.recoverable,
        }
        if event.data is not None:
            entry["error"]["data"] = event.data
        self._append(entry)
        return self.path

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, sort_keys=True) + "\n")


def new_agent_call_run_id() -> str:
    """Return a stable id for one coding-session agent call."""
    return uuid4().hex


def configure_tau_logging(
    *,
    log_path: Path | None = None,
    level: str | None = None,
    verbose: bool = False,
) -> Path | None:
    """Configure Tau's Loguru logger and return the active file sink path."""

    global _CONFIGURED
    resolved_level = _normalize_log_level(
        level or os.environ.get(LOG_LEVEL_ENV) or ("DEBUG" if verbose else DEFAULT_LOG_LEVEL)
    )
    resolved_path = log_path or _env_log_path()

    logger.remove()
    logger.add(
        sys.stderr,
        level=resolved_level,
        format=(
            "<green>{time:YYYY-MM-DDTHH:mm:ss.SSSZ}</green> "
            "<level>{level}</level> {message} {extra}"
        ),
        enqueue=False,
    )
    if resolved_path is not None:
        resolved_path = resolved_path.expanduser().resolve()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(resolved_path),
            level=resolved_level,
            serialize=True,
            enqueue=False,
        )
    _CONFIGURED = True
    return resolved_path


def configure_dag_logging(run_dir: Path, *, level: str | None = None) -> Path:
    """Configure a per-run DAG diagnostic JSONL sink."""

    log_path = _env_log_path() or run_dir / "tau-diagnostics.jsonl"
    resolved = configure_tau_logging(log_path=log_path, level=level)
    assert resolved is not None
    return resolved


def tau_logger(**context: Any):
    """Return the configured Tau logger bound to optional structured context."""

    if not _CONFIGURED:
        configure_tau_logging()
    return logger.bind(**context)


def _base_entry(
    context: AgentCallDiagnosticContext,
    *,
    phase: str,
    kind: str,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "kind": kind,
        "phase": phase,
        "run_id": context.run_id,
        "session_id": context.session_id,
        "provider_name": context.provider_name,
        "model": context.model,
        "cwd": str(context.cwd),
    }


def _env_log_path() -> Path | None:
    raw = os.environ.get(LOG_PATH_ENV)
    if raw is None or not raw.strip():
        return None
    return Path(raw)


def _normalize_log_level(value: str) -> str:
    level = value.strip().upper()
    allowed = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
    if level not in allowed:
        raise ValueError(f"unsupported Tau log level: {value}")
    return level
