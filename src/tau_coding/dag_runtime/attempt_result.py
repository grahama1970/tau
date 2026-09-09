"""Canonical DAG attempt result admission."""

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
MACHINE_TOKEN_MAX_LENGTH = 128
MACHINE_TOKEN_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-."
)


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

    model_config = ConfigDict(extra="allow", strict=True)

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
    errors: list[str] = Field(default_factory=list)
    alert_codes: list[str] = Field(default_factory=list)

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

    @field_validator("run_id", "plan_sha256", "node_id", "attempt_id")
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
) -> DagAttemptResultAdmission:
    """Normalize one raw adapter result into ``tau.dag_attempt_result.v1``."""

    if not isinstance(result, Mapping):
        raise DagAttemptResultAdmissionError("dag_attempt_result_not_object", "$")
    raw = dict(result)
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

    retryable = parsed.retryable
    if retryable is None:
        retryable = parsed.status not in {"PASS", "CANCELLED"}

    reserved = {
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
        "errors",
        "alert_codes",
    }
    extras = {key: value for key, value in raw.items() if key not in reserved}
    if isinstance(claimed_schema, str) and claimed_schema != DAG_ATTEMPT_RESULT_SCHEMA:
        extras.setdefault("source_schema", claimed_schema)
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
        "errors": parsed.errors,
        "alert_codes": parsed.alert_codes,
        **extras,
    }
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
        },
    )


def _machine_token(value: str) -> bool:
    return is_dag_machine_code(value)


def is_dag_machine_code(value: str) -> bool:
    """Return whether ``value`` satisfies Tau's canonical machine-code grammar."""

    return bool(value) and len(value) <= MACHINE_TOKEN_MAX_LENGTH and all(
        char in MACHINE_TOKEN_CHARS for char in value
    ) and (
        value == value.upper() or any(separator in value for separator in ("_", ":", "-", "."))
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
    if not location:
        return "dag_attempt_result_invalid"
    field = str(location[0])
    if field in {"status", "retryable", "accepted_output"}:
        return f"dag_attempt_result_{field}_invalid"
    if field in {"errors", "alert_codes"}:
        return "dag_attempt_result_string_array_invalid"
    if field in {"schema", "schema_"}:
        return "dag_attempt_result_schema_invalid"
    return "dag_attempt_result_invalid"


def _validation_error_path(location: tuple[Any, ...], code: str) -> str:
    if code in _MODEL_ERROR_PATHS:
        return _MODEL_ERROR_PATHS[code]
    return "$" + "".join(
        f".{part}" if isinstance(part, str) else f"[{part}]" for part in location
    )


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
