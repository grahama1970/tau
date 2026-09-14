"""Canonical DAG attempt result admission.

Diagram ID: tau.attempt-result-boundary
Excalidraw source: docs/explain/boards/tau-attempt-result-boundary.excalidraw
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tau_coding.dag_runtime.model import canonical_sha256

DAG_ATTEMPT_RESULT_SCHEMA = "tau.dag_attempt_result.v1"
DAG_ATTEMPT_RESULT_VALIDATION_SCHEMA = "tau.dag_attempt_result_validation.v1"
ATTEMPT_RESULT_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "CANCELLED"})
IDENTITY_CLAIM_FIELDS = ("run_id", "plan_sha256", "attempt_id")
OUTPUT_CONTRACT_ANY_OBJECT = "tau.accepted_output.any_object.v1"
OUTPUT_CONTRACT_COMPAT_OPTIONAL_OBJECT = "tau.accepted_output.compat_optional_object.v1"
OUTPUT_CONTRACT_NONE = "tau.accepted_output.none.v1"
OUTPUT_CONTRACT_SOURCE_NODE = "tau.accepted_output.source_node.v1"
OUTPUT_CONTRACT_IDS = frozenset(
    {
        OUTPUT_CONTRACT_ANY_OBJECT,
        OUTPUT_CONTRACT_COMPAT_OPTIONAL_OBJECT,
        OUTPUT_CONTRACT_NONE,
        OUTPUT_CONTRACT_SOURCE_NODE,
    }
)
MACHINE_TOKEN_MAX_LENGTH = 128
MACHINE_TOKEN_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-."
)
AUXILIARY_FIELD_MAX_BYTES = 32768


class SourceNodeAcceptedOutput(BaseModel):
    """Strict accepted-output contract for ordinary DAG node completions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_node_id: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class DagAttemptResultAdmission:
    normalized: dict[str, Any]
    validation: dict[str, Any]


class DagAttemptResultAdmissionError(ValueError):
    """Raised when an adapter result cannot cross the scheduler boundary."""

    def __init__(self, code: str, path: str) -> None:
        super().__init__(f"{code}:{path}")
        self.code = code
        self.path = path


class DagAttemptResultModel(BaseModel):
    """Strict node-to-scheduler attempt result boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_: str | None = Field(default=None, alias="schema")
    run_id: str | None = None
    plan_sha256: str | None = None
    node_id: str | None = None
    attempt_id: str | None = None
    attempt: int | None = None
    status: str = Field(min_length=1)
    verdict: str = Field(min_length=1)
    retryable: bool | None = None
    accepted_output: dict[str, Any] | None = None
    accepted_output_sha256: str | None = None
    errors: list[str] = Field(default_factory=list)
    alert_codes: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)
    output_contract_id: str | None = None
    output_schema_version: str | None = None
    source_schema: str | None = None
    attempt_count: int | None = None
    scheduler_attempt_id: str | None = None
    scheduler_attempt: int | None = None
    scheduler_attempts: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("schema_")
    @classmethod
    def _schema_string(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("dag_attempt_result_schema_invalid")
        return value

    @field_validator("status")
    @classmethod
    def _status_known(cls, value: str) -> str:
        if value not in ATTEMPT_RESULT_STATUSES:
            raise ValueError("dag_attempt_result_status_invalid")
        return value

    @field_validator("verdict")
    @classmethod
    def _verdict_machine_code(cls, value: str) -> str:
        if not _machine_token(value):
            raise ValueError("dag_attempt_result_verdict_invalid")
        return value

    @field_validator(
        "run_id",
        "plan_sha256",
        "node_id",
        "attempt_id",
        "accepted_output_sha256",
        "output_schema_version",
        "source_schema",
        "scheduler_attempt_id",
    )
    @classmethod
    def _optional_non_empty_string(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("dag_attempt_result_string_invalid")
        return value

    @field_validator("errors", "alert_codes")
    @classmethod
    def _string_array(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item:
                raise ValueError("dag_attempt_result_string_array_invalid")
        return value

    @field_validator("diagnostics", "extensions")
    @classmethod
    def _bounded_auxiliary(cls, value: dict[str, Any]) -> dict[str, Any]:
        _bounded_auxiliary_mapping(value)
        return value

    @field_validator("scheduler_attempts")
    @classmethod
    def _bounded_scheduler_attempts(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _bounded_auxiliary_mapping({"scheduler_attempts": value})
        return value

    @field_validator("output_contract_id")
    @classmethod
    def _output_contract_known(cls, value: str | None) -> str | None:
        if value is not None and value not in OUTPUT_CONTRACT_IDS:
            raise ValueError("dag_attempt_result_output_contract_unknown")
        return value

    @model_validator(mode="after")
    def _status_contract(self) -> DagAttemptResultModel:
        if self.status == "PASS" and self.verdict != "PASS":
            raise ValueError("dag_attempt_result_pass_verdict_mismatch")
        if self.status != "PASS" and self.verdict == "PASS":
            raise ValueError("dag_attempt_result_non_pass_verdict_mismatch")
        if self.status == "CANCELLED" and self.retryable is not False:
            raise ValueError("dag_attempt_result_cancelled_retryable")
        if self.status != "PASS" and self.accepted_output is not None:
            raise ValueError("dag_attempt_result_non_pass_accepted_output")
        return self


def admit_dag_attempt_result(
    *,
    plan_sha256: str,
    identity: Any,
    node_id: str,
    result: Mapping[str, Any],
    output_contract_id: str | None = None,
) -> DagAttemptResultAdmission:
    """Normalize one raw adapter result into ``tau.dag_attempt_result.v1``."""

    explicit_output_contract = output_contract_id is not None
    output_contract_id, output_schema_version = normalize_output_contract_id(
        output_contract_id or OUTPUT_CONTRACT_ANY_OBJECT
    )
    if output_contract_id not in OUTPUT_CONTRACT_IDS:
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_output_contract_unknown",
            "$.output_contract_id",
        )
    if not isinstance(result, Mapping):
        raise DagAttemptResultAdmissionError("dag_attempt_result_not_object", "$")
    raw = dict(result)
    if raw.get("schema") != DAG_ATTEMPT_RESULT_SCHEMA:
        raw = _lift_legacy_receipt_fields(raw)
    raw = _compact_admission_extensions(raw)
    try:
        parsed = DagAttemptResultModel.model_validate(raw)
    except ValidationError as exc:
        raise _admission_error_from_validation(exc) from exc

    claimed_schema = parsed.schema_
    claims_identity = claimed_schema == DAG_ATTEMPT_RESULT_SCHEMA or any(
        field in raw for field in IDENTITY_CLAIM_FIELDS
    )
    claimed_node = parsed.node_id
    if claimed_node is not None and claimed_node != node_id:
        raise DagAttemptResultAdmissionError("dag_attempt_result_node_mismatch", "$.node_id")
    if claims_identity:
        _require_claim(raw, "run_id", identity.run_id)
        _require_claim(raw, "plan_sha256", plan_sha256)
        _require_claim(raw, "node_id", node_id)
        _require_claim(raw, "attempt_id", identity.attempt_id)
        _require_claim(raw, "attempt", identity.attempt)
    parsed_contract: str | None = None
    if parsed.output_contract_id is not None:
        parsed_contract, _ = normalize_output_contract_id(parsed.output_contract_id)
    if parsed_contract is not None and parsed_contract != output_contract_id:
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_output_contract_mismatch",
            "$.output_contract_id",
        )
    if (
        parsed.output_schema_version is not None
        and parsed.output_schema_version != output_schema_version
    ):
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_output_contract_mismatch",
            "$.output_schema_version",
        )

    accepted_output_hash = _validate_accepted_output(
        node_id=node_id,
        output_contract_id=output_contract_id,
        accepted_output=parsed.accepted_output,
        status=parsed.status,
    )
    if (
        parsed.accepted_output_sha256 is not None
        and parsed.accepted_output_sha256 != accepted_output_hash
    ):
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_output_hash_mismatch",
            "$.accepted_output_sha256",
        )
    retryable = parsed.retryable
    if retryable is None:
        retryable = parsed.status not in {"PASS", "CANCELLED"}

    normalized = {
        "schema": DAG_ATTEMPT_RESULT_SCHEMA,
        "run_id": identity.run_id,
        "plan_sha256": plan_sha256,
        "node_id": node_id,
        "attempt_id": identity.attempt_id,
        "attempt": identity.attempt,
        "status": parsed.status,
        "verdict": parsed.verdict,
        "retryable": retryable,
        "accepted_output": parsed.accepted_output,
        "accepted_output_sha256": accepted_output_hash,
        "output_contract_id": output_contract_id,
        "errors": parsed.errors,
        "alert_codes": parsed.alert_codes,
        "diagnostics": parsed.diagnostics,
        "extensions": parsed.extensions,
    }
    if explicit_output_contract or parsed.output_schema_version is not None:
        normalized["output_schema_version"] = output_schema_version
    for key in ("attempt_count", "scheduler_attempt_id", "scheduler_attempt"):
        value = getattr(parsed, key)
        if value is not None:
            normalized[key] = value
    if parsed.scheduler_attempts:
        normalized["scheduler_attempts"] = parsed.scheduler_attempts
    if isinstance(claimed_schema, str) and claimed_schema != DAG_ATTEMPT_RESULT_SCHEMA:
        normalized["source_schema"] = claimed_schema
    try:
        result_sha256 = canonical_sha256(normalized)
    except RuntimeError as exc:
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_non_canonical_json",
            "$",
        ) from exc
    return DagAttemptResultAdmission(
        normalized=normalized,
        validation={
            "schema": DAG_ATTEMPT_RESULT_VALIDATION_SCHEMA,
            "status": "PASS",
            "node_id": node_id,
            "run_id": identity.run_id,
            "plan_sha256": plan_sha256,
            "attempt_id": identity.attempt_id,
            "attempt": identity.attempt,
            "result_sha256": result_sha256,
            "output_contract_id": output_contract_id,
            "output_schema_version": output_schema_version,
            "accepted_output_sha256": accepted_output_hash,
        },
    )


def _lift_legacy_receipt_fields(raw: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema",
        "run_id",
        "plan_sha256",
        "node_id",
        "attempt_id",
        "attempt",
        "status",
        "verdict",
        "retryable",
        "accepted_output",
        "accepted_output_sha256",
        "errors",
        "alert_codes",
        "diagnostics",
        "extensions",
        "output_contract_id",
        "output_schema_version",
        "source_schema",
        "attempt_count",
        "scheduler_attempt_id",
        "scheduler_attempt",
        "scheduler_attempts",
    }
    extras = {key: value for key, value in raw.items() if key not in allowed}
    if not extras:
        return raw
    lifted = {key: value for key, value in raw.items() if key in allowed}
    extensions = dict(lifted.get("extensions") or {})
    generic_receipt = dict(extensions.get("generic_receipt") or {})
    extras = dict(extras)
    if "command_results" in extras:
        extras["command_results"] = _compact_legacy_command_results(extras["command_results"])
    generic_receipt.update(extras)
    extensions["generic_receipt"] = generic_receipt
    lifted["extensions"] = extensions
    return lifted


def _compact_admission_extensions(raw: dict[str, Any]) -> dict[str, Any]:
    extensions = raw.get("extensions")
    if not isinstance(extensions, Mapping):
        return raw
    generic = extensions.get("generic_receipt")
    if not isinstance(generic, Mapping):
        return raw
    updated_extensions = dict(extensions)
    updated_generic = dict(generic)
    if "command_results" not in updated_generic and "attempts" not in updated_generic:
        return raw
    if "command_results" in updated_generic:
        updated_generic["command_results"] = _compact_legacy_command_results(
            updated_generic["command_results"]
        )
    if "attempts" in updated_generic:
        updated_generic["attempts"] = _compact_legacy_attempts(updated_generic["attempts"])
    updated_extensions["generic_receipt"] = updated_generic
    projected = {**raw, "extensions": updated_extensions}
    try:
        size_probe = (canonical_sha256(projected["extensions"]) + repr(projected["extensions"])).encode(
            "utf-8"
        )
    except RuntimeError:
        return projected
    if len(size_probe) <= AUXILIARY_FIELD_MAX_BYTES:
        return projected
    if "command_results" in updated_generic:
        updated_generic["command_results"] = _compact_large_runtime_command_results(
            updated_generic["command_results"]
        )
    updated_extensions["generic_receipt"] = updated_generic
    return {**raw, "extensions": updated_extensions}


def _compact_large_runtime_command_results(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    compacted: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        projected = dict(item)
        for key in ("runtime_endpoint_lease", "runtime_submit_receipt", "runtime_event"):
            nested = projected.get(key)
            if isinstance(nested, Mapping):
                projected[key] = {
                    "sha256": canonical_sha256(nested),
                    "bytes": len(repr(nested).encode("utf-8")),
                }
        artifacts = projected.get("runtime_artifacts")
        if isinstance(artifacts, list):
            projected["runtime_artifact_count"] = len(artifacts)
            projected.pop("runtime_artifacts", None)
        compacted.append(projected)
    return compacted


def _compact_legacy_attempts(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    compacted: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        projected: dict[str, Any] = {}
        for key in (
            "attempt",
            "candidate_manifest_path",
            "candidate_manifest_sha256",
            "validation_receipt_path",
            "review_feedback_path",
            "review_feedback_sha256",
            "review_verdict",
            "review_live",
            "review_provider_live",
            "review_provider",
            "review_model",
            "producer_live",
            "producer_provider_live",
            "producer_provider",
            "producer_model",
            "course_correction_receipt_path",
            "course_correction_trigger",
        ):
            if key in item:
                projected[key] = item[key]
        for key in ("producer_execution_evidence", "review_execution_evidence"):
            evidence = item.get(key)
            if isinstance(evidence, Mapping):
                projected[key] = {
                    subkey: evidence[subkey]
                    for subkey in ("kind", "command_result_sha256")
                    if subkey in evidence
                }
        compacted.append(projected)
    return compacted


def _compact_legacy_command_results(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    compacted: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        projected: dict[str, Any] = dict(item)
        if "command" in projected:
            projected["command"] = "[REDACTED]"
        redacted = False
        for stream in ("stdout", "stderr"):
            text = projected.pop(stream, None)
            if isinstance(text, str):
                redacted = True
                encoded = text.encode("utf-8", errors="replace")
                projected.setdefault(f"{stream}_bytes", len(encoded))
                projected.setdefault(f"{stream}_sha256", canonical_sha256(text))
        if redacted:
            projected["redaction_marker"] = "[REDACTED]"
        compacted.append(projected)
    return compacted


def normalize_output_contract_id(output_contract_id: str) -> tuple[str, str]:
    """Return the trusted contract id and version encoded by Tau's registry."""

    if not isinstance(output_contract_id, str):
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_output_contract_unknown",
            "$.output_contract_id",
        )
    if output_contract_id not in OUTPUT_CONTRACT_IDS:
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_output_contract_unknown",
            "$.output_contract_id",
        )
    return output_contract_id, output_contract_id.rsplit(".", 1)[-1]


def _validate_accepted_output(
    *,
    node_id: str,
    output_contract_id: str,
    accepted_output: dict[str, Any] | None,
    status: str,
) -> str | None:
    if status != "PASS":
        return None
    if output_contract_id == OUTPUT_CONTRACT_NONE:
        if accepted_output is not None:
            raise DagAttemptResultAdmissionError(
                "dag_attempt_result_output_forbidden",
                "$.accepted_output",
            )
        return None
    if accepted_output is None:
        if output_contract_id == OUTPUT_CONTRACT_COMPAT_OPTIONAL_OBJECT:
            return None
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_output_required",
            "$.accepted_output",
        )
    if output_contract_id == OUTPUT_CONTRACT_SOURCE_NODE:
        try:
            validated = SourceNodeAcceptedOutput.model_validate(accepted_output)
        except ValidationError as exc:
            path = "$.accepted_output" + "".join(
                f".{part}" if isinstance(part, str) else f"[{part}]"
                for part in tuple(exc.errors()[0].get("loc", ()))
            )
            raise DagAttemptResultAdmissionError(
                "dag_attempt_result_output_schema_invalid",
                path,
            ) from exc
        if validated.source_node_id != node_id:
            raise DagAttemptResultAdmissionError(
                "dag_attempt_result_output_schema_invalid",
                "$.accepted_output.source_node_id",
            )
    try:
        return canonical_sha256(accepted_output)
    except RuntimeError as exc:
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_non_canonical_json",
            "$.accepted_output",
        ) from exc


def _bounded_auxiliary_mapping(value: dict[str, Any]) -> None:
    try:
        encoded = canonical_sha256(value) + repr(value)
    except RuntimeError as exc:
        raise ValueError("dag_attempt_result_auxiliary_non_canonical") from exc
    if len(encoded.encode("utf-8")) > AUXILIARY_FIELD_MAX_BYTES:
        raise ValueError("dag_attempt_result_auxiliary_too_large")


def _machine_token(value: str) -> bool:
    return is_dag_machine_code(value)


def is_dag_machine_code(value: str) -> bool:
    """Return whether ``value`` satisfies Tau's canonical machine-code grammar."""

    return (
        bool(value)
        and len(value) <= MACHINE_TOKEN_MAX_LENGTH
        and all(char in MACHINE_TOKEN_CHARS for char in value)
        and (
            value == value.upper() or any(separator in value for separator in ("_", ":", "-", "."))
        )
    )


def _admission_error_from_validation(exc: ValidationError) -> DagAttemptResultAdmissionError:
    first = exc.errors()[0]
    location = tuple(first.get("loc", ()))
    message = str(first.get("msg") or "")
    code = _validation_error_code(location, message)
    return DagAttemptResultAdmissionError(code, _validation_error_path(location, code))


def _validation_error_code(location: tuple[Any, ...], message: str) -> str:
    for candidate in _MODEL_ERROR_PATHS:
        if candidate in message:
            return candidate
    if "Extra inputs are not permitted" in message:
        return "dag_attempt_result_unknown_field"
    if not location:
        return "dag_attempt_result_invalid"
    field = str(location[0])
    if field in {"status", "retryable", "accepted_output"}:
        return f"dag_attempt_result_{field}_invalid"
    if field in {"errors", "alert_codes"}:
        return "dag_attempt_result_string_array_invalid"
    if field in {"schema", "schema_"}:
        return "dag_attempt_result_schema_invalid"
    if field in {"diagnostics", "extensions"}:
        return "dag_attempt_result_auxiliary_invalid"
    if field == "output_contract_id":
        return "dag_attempt_result_output_contract_invalid"
    return "dag_attempt_result_invalid"


def _validation_error_path(location: tuple[Any, ...], code: str) -> str:
    if code in _MODEL_ERROR_PATHS:
        return _MODEL_ERROR_PATHS[code]
    return "$" + "".join(f".{part}" if isinstance(part, str) else f"[{part}]" for part in location)


_MODEL_ERROR_PATHS = {
    "dag_attempt_result_schema_invalid": "$.schema",
    "dag_attempt_result_status_invalid": "$.status",
    "dag_attempt_result_verdict_invalid": "$.verdict",
    "dag_attempt_result_pass_verdict_mismatch": "$.verdict",
    "dag_attempt_result_non_pass_verdict_mismatch": "$.verdict",
    "dag_attempt_result_cancelled_retryable": "$.retryable",
    "dag_attempt_result_non_pass_accepted_output": "$.accepted_output",
}


def _require_claim(raw: Mapping[str, Any], field: str, expected: object) -> None:
    if field not in raw:
        raise DagAttemptResultAdmissionError(
            f"dag_attempt_result_{field}_missing",
            f"$.{field}",
        )
    if raw[field] != expected:
        raise DagAttemptResultAdmissionError(
            f"dag_attempt_result_{field}_mismatch",
            f"$.{field}",
        )
