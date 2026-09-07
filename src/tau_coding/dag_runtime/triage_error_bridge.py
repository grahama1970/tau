"""Bridge Tau DAG failures to the shared triage-error vocabulary."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class TriageErrorClassification(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    code: str = Field(min_length=1)
    layer: str = Field(min_length=1)
    cause: str = Field(min_length=1)
    next_command: str | None = None
    ambiguous: bool = False


def classify_tau_failure(text: str, *, layer: str = "tau") -> dict[str, Any]:
    """Classify a Tau boundary failure, minting an ambiguous code if classification fails."""

    runner = _triage_runner()
    if runner is None:
        return _mint("tau_triage_unavailable", "triage-error runner not found")
    try:
        completed = subprocess.run(
            [str(runner), "classify", "--text", text, "--layer", layer],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if completed.returncode != 0:
            signal = completed.stderr.strip() or completed.stdout.strip()
            return _mint(
                "tau_triage_classification_failed",
                f"triage-error classify exited {completed.returncode}: {signal}",
            )
        return TriageErrorClassification.model_validate_json(completed.stdout).model_dump()
    except (OSError, subprocess.TimeoutExpired, ValidationError, ValueError) as exc:
        return _mint("tau_triage_classification_failed", str(exc))


def _triage_runner() -> Path | None:
    configured = os.environ.get("TAU_TRIAGE_ERROR_RUN_SH")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.append(Path.home() / "workspace/experiments/agent-skills/skills/triage-error/run.sh")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _mint(prefix: str, cause: str) -> dict[str, Any]:
    digest = hashlib.sha256(cause.encode("utf-8", errors="replace")).hexdigest()[:8]
    return {
        "code": f"{prefix}_unclassified_{digest}",
        "layer": "tau",
        "cause": cause,
        "next_command": None,
        "ambiguous": True,
    }
