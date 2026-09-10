"""Bridge Tau DAG failures to the shared triage-error vocabulary.

Diagram ID: tau.failure-repair
Excalidraw source: docs/explain/boards/tau-failure-repair.excalidraw
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from tau_coding.dag_runtime.attempt_result import is_dag_machine_code
from tau_coding.dag_runtime.correction import ALLOWED_REPAIR_HANDLER_IDS, REPAIR_FAMILIES
from tau_coding.dag_runtime.model import canonical_sha256

TRIAGE_CLASSIFICATION_SCHEMA = "tau.triage_error_classification.v1"
TRIAGE_CONTRACT_INVALID_CODE = "triage_contract_invalid"
TRIAGE_REPAIR_ARGS_SCHEMA = "tau.scheduler_correction_repair_args.v1"
TRIAGE_CAUSE_MAX_LENGTH = 512
TRIAGE_LAYERS = frozenset(
    {
        "tau",
        "dag-runtime",
        "scheduler",
        "adapter",
        "worker",
        "resource",
        "workspace",
        "replay",
        "transition",
        "correction",
    }
)
TRIAGE_DISPOSITIONS = frozenset(
    {"KNOWN_REPAIR", "AMBIGUOUS", "NEEDS_HUMAN", "CONTRACT_INVALID", "UNAVAILABLE"}
)
SHELL_SNIPPET_MARKERS = ("&&", "||", "$(", "`", "\n", "\r", ";", "|", ">", "<")


class TriageContractError(ValueError):
    """Raised when classifier JSON cannot cross Tau's triage boundary."""

    def __init__(self, code: str, path: str = "$") -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code}:{path}")


class SchedulerCorrectionRepairArgs(BaseModel):
    """Closed repair-argument schema for scheduler-owned correction handlers."""

    model_config = ConfigDict(extra="forbid", strict=True)

    strategy: Literal["same_semantic_node_rerun"]
    repair_family: str
    classification_code: str

    @field_validator("repair_family")
    @classmethod
    def _repair_family_known(cls, value: str) -> str:
        if value not in REPAIR_FAMILIES:
            raise ValueError("triage_repair_family_invalid")
        return value

    @field_validator("classification_code")
    @classmethod
    def _classification_code_machine(cls, value: str) -> str:
        if not is_dag_machine_code(value):
            raise ValueError("triage_repair_classification_code_invalid")
        return value


class TriageErrorClassification(BaseModel):
    """Closed, versioned classification contract admitted by Tau DAG repair logic."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_: Literal[TRIAGE_CLASSIFICATION_SCHEMA] = Field(alias="schema")
    code: str = Field(min_length=1)
    layer: str = Field(min_length=1)
    cause: str = Field(min_length=1)
    repair_family: str = Field(min_length=1)
    disposition: str = Field(min_length=1)
    repair_handler_id: str | None = None
    repair_args: dict[str, Any] = Field(default_factory=dict)
    repair_args_schema: str | None = None
    repair_args_sha256: str | None = None
    requires_human: bool
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_control_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "next_command" in data:
                raise ValueError("triage_next_command_forbidden")
            if "ambiguous" in data:
                raise ValueError("triage_ambiguous_bool_forbidden")
        return data

    @field_validator("code")
    @classmethod
    def _code_machine_token(cls, value: str) -> str:
        if not is_dag_machine_code(value):
            raise ValueError("triage_code_invalid")
        return value

    @field_validator("layer")
    @classmethod
    def _layer_known(cls, value: str) -> str:
        if value not in TRIAGE_LAYERS:
            raise ValueError("triage_layer_invalid")
        return value

    @field_validator("cause", mode="before")
    @classmethod
    def _cause_diagnostic_only(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("triage_cause_invalid")
        return _sanitize_cause(value)

    @field_validator("repair_family")
    @classmethod
    def _repair_family_known(cls, value: str) -> str:
        if value not in REPAIR_FAMILIES:
            raise ValueError("triage_repair_family_invalid")
        return value

    @field_validator("disposition")
    @classmethod
    def _disposition_known(cls, value: str) -> str:
        if value not in TRIAGE_DISPOSITIONS:
            raise ValueError("triage_disposition_invalid")
        return value

    @model_validator(mode="after")
    def _repair_action_contract(self) -> TriageErrorClassification:
        _reject_non_finite(self.repair_args, "$.repair_args")
        _reject_shell_snippets(self.repair_args, "$.repair_args")
        if self.disposition != "KNOWN_REPAIR" or self.requires_human:
            if (
                self.repair_handler_id is not None
                or self.repair_args
                or self.repair_args_schema is not None
            ):
                raise ValueError("triage_non_repair_action_forbidden")
            return self
        if self.repair_handler_id not in ALLOWED_REPAIR_HANDLER_IDS:
            raise ValueError("triage_repair_handler_invalid")
        if self.repair_args_schema != TRIAGE_REPAIR_ARGS_SCHEMA:
            raise ValueError("triage_repair_args_schema_invalid")
        repair_args = SchedulerCorrectionRepairArgs.model_validate(self.repair_args)
        self.repair_args = repair_args.model_dump()
        self.repair_args_sha256 = canonical_sha256(self.repair_args)
        return self


def _classifier_command(runner: Path, text: str, layer: str) -> list[str]:
    """The triage-error skill must emit tau's strict canonical contract; the
    plain classify shape is legacy and degrades to triage_contract_invalid."""
    return [str(runner), "classify", "--text", text, "--layer", layer,
            "--contract", "tau"]


def classify_tau_failure(text: str, *, layer: str = "tau") -> dict[str, Any]:
    """Classify a Tau boundary failure behind a fail-closed typed contract."""

    runner = _triage_runner()
    if runner is None:
        native = _native_fallback(text, layer=layer)
        if native is not None:
            return native
        return _mint("tau_triage_unavailable", "triage-error runner not found")
    try:
        completed = subprocess.run(
            _classifier_command(runner, text, layer),
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
        return _validate_external_classification(completed.stdout, runner=runner)
    except (ValidationError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        return _contract_invalid(str(exc), layer=layer, classifier_path=str(runner))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _mint("tau_triage_classification_failed", str(exc))


def admit_triage_classification(
    payload: Mapping[str, Any], *, layer: str = "tau"
) -> dict[str, Any]:
    """Validate scheduler-supplied or monkeypatched classifications before use."""

    try:
        return _classification_payload(dict(payload))
    except (ValidationError, ValueError, RuntimeError) as exc:
        return _contract_invalid(str(exc), layer=layer)


#: Canonical installed-skills fallback. The watchdog -> ask -> tau execution
#: environment carries none of the TAU_* skills-root variables, so without this
#: default every boundary failure mints tau_triage_unavailable (observed
#: 2026-09-10 across agent-skills#1616/#1618/#1619/#1641 and tau#343).
def _default_triage_runner_candidates() -> tuple[Path, ...]:
    return (Path.home() / ".pi" / "agent" / "skills" / "triage-error" / "run.sh",)


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
    candidates.extend(_default_triage_runner_candidates())
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _native_fallback(text: str, *, layer: str) -> dict[str, Any] | None:
    lowered = text.lower()
    if "missing required evidence" not in lowered:
        return None
    return _classification_payload(
        {
            "schema": TRIAGE_CLASSIFICATION_SCHEMA,
            "code": "tau_project_dag_missing_required_evidence",
            "layer": _known_layer_or_tau(layer),
            "cause": "DAG node failed because required evidence was missing.",
            "repair_family": "result_contract_invalid",
            "disposition": "KNOWN_REPAIR",
            "repair_handler_id": "scheduler.correction_handler",
            "repair_args_schema": TRIAGE_REPAIR_ARGS_SCHEMA,
            "repair_args": {
                "strategy": "same_semantic_node_rerun",
                "repair_family": "result_contract_invalid",
                "classification_code": "tau_project_dag_missing_required_evidence",
            },
            "requires_human": False,
            "diagnostics": {
                "classifier_kind": "NATIVE_FALLBACK",
                "classifier_version": "tau.dag_runtime.triage_error_bridge.v1",
            },
        }
    )


def _mint(prefix: str, cause: str) -> dict[str, Any]:
    sanitized_cause = _sanitize_cause(cause)
    digest = hashlib.sha256(sanitized_cause.encode("utf-8", errors="replace")).hexdigest()[:8]
    disposition = "UNAVAILABLE" if prefix == "tau_triage_unavailable" else "AMBIGUOUS"
    return _classification_payload(
        {
            "schema": TRIAGE_CLASSIFICATION_SCHEMA,
            "code": f"{prefix}_unclassified_{digest}",
            "layer": "tau",
            "cause": sanitized_cause,
            "repair_family": "triage_unavailable",
            "disposition": disposition,
            "requires_human": True,
            "diagnostics": {"classifier_kind": "UNAVAILABLE"},
        }
    )


def _contract_invalid(
    cause: str, *, layer: str = "tau", classifier_path: str | None = None
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "classifier_kind": "CONTRACT_INVALID",
        "contract_error": _sanitize_cause(cause),
    }
    if classifier_path:
        diagnostics["classifier_path"] = classifier_path
    return _classification_payload(
        {
            "schema": TRIAGE_CLASSIFICATION_SCHEMA,
            "code": TRIAGE_CONTRACT_INVALID_CODE,
            "layer": _known_layer_or_tau(layer),
            "cause": "Classifier output failed Tau triage contract validation.",
            "repair_family": "triage_unavailable",
            "disposition": "CONTRACT_INVALID",
            "requires_human": True,
            "diagnostics": diagnostics,
        }
    )


def _validate_external_classification(stdout: str, *, runner: Path) -> dict[str, Any]:
    raw = _load_canonical_classifier_json(stdout)
    payload = _classification_payload(raw)
    diagnostics = dict(payload.get("diagnostics") or {})
    diagnostics["classifier_kind"] = "EXTERNAL_CLASSIFIER"
    diagnostics["classifier_path"] = str(runner)
    payload["diagnostics"] = diagnostics
    return payload


def _load_canonical_classifier_json(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise TriageContractError("triage_contract_not_object")
    _reject_non_finite(parsed, "$")
    canonical = json.dumps(
        parsed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if text != canonical:
        raise TriageContractError("triage_contract_non_canonical_json")
    return parsed


def _classification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    classification = TriageErrorClassification.model_validate(payload)
    return classification.model_dump(by_alias=True, exclude_none=True)


def _sanitize_cause(value: str) -> str:
    cleaned = "".join(
        char if (char.isprintable() and char not in "\r\n\t") else " " for char in value
    )
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        cleaned = "classifier produced an empty diagnostic"
    return cleaned[:TRIAGE_CAUSE_MAX_LENGTH]


def _known_layer_or_tau(layer: str) -> str:
    return layer if layer in TRIAGE_LAYERS else "tau"


def _reject_non_finite(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise TriageContractError("triage_repair_args_non_finite", path)
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_non_finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_finite(item, f"{path}[{index}]")


def _reject_shell_snippets(value: Any, path: str) -> None:
    if isinstance(value, str) and any(marker in value for marker in SHELL_SNIPPET_MARKERS):
        raise TriageContractError("triage_repair_args_shell_snippet", path)
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_shell_snippets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_shell_snippets(item, f"{path}[{index}]")
