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
        native = _native_fallback(text, layer=layer)
        if native is not None:
            return native
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
        payload = TriageErrorClassification.model_validate_json(completed.stdout).model_dump()
        payload.setdefault("classifier_kind", "EXTERNAL_CLASSIFIER")
        payload.setdefault("classifier_path", str(runner))
        return payload
    except (OSError, subprocess.TimeoutExpired, ValidationError, ValueError) as exc:
        return _mint("tau_triage_classification_failed", str(exc))


def _triage_runner() -> Path | None:
    configured = os.environ.get("TAU_TRIAGE_ERROR_RUN_SH")
    candidates = [Path(configured).expanduser()] if configured else []
    skills_root = os.environ.get("TAU_SKILLS_ROOT")
    if skills_root:
        candidates.append(Path(skills_root).expanduser() / "triage-error" / "run.sh")
    agent_skills_root = os.environ.get("TAU_AGENT_SKILLS_ROOT")
    if agent_skills_root:
        candidates.append(
            Path(agent_skills_root).expanduser() / "skills" / "triage-error" / "run.sh"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _native_fallback(text: str, *, layer: str) -> dict[str, Any] | None:
    lowered = text.lower()
    if "missing required evidence" not in lowered:
        return None
    return {
        "code": "tau_project_dag_missing_required_evidence",
        "layer": layer,
        "cause": "DAG node failed because required evidence was missing.",
        "next_command": "rerun the same semantic node after attaching required evidence",
        "ambiguous": False,
        "classifier_kind": "NATIVE_FALLBACK",
        "classifier_version": "tau.dag_runtime.triage_error_bridge.v1",
    }


def _mint(prefix: str, cause: str) -> dict[str, Any]:
    digest = hashlib.sha256(cause.encode("utf-8", errors="replace")).hexdigest()[:8]
    return {
        "code": f"{prefix}_unclassified_{digest}",
        "layer": "tau",
        "cause": cause,
        "next_command": None,
        "ambiguous": True,
        "classifier_kind": "UNAVAILABLE",
    }
