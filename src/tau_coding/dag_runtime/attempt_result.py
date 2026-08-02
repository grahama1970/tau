"""Canonical DAG attempt result admission."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256

DAG_ATTEMPT_RESULT_SCHEMA = "tau.dag_attempt_result.v1"
DAG_ATTEMPT_RESULT_VALIDATION_SCHEMA = "tau.dag_attempt_result_validation.v1"
ATTEMPT_RESULT_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "CANCELLED"})
VERDICT_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
IDENTITY_CLAIM_FIELDS = ("run_id", "plan_sha256", "attempt_id")


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
    claimed_schema = raw.get("schema")
    claims_identity = claimed_schema == DAG_ATTEMPT_RESULT_SCHEMA or any(
        field in raw for field in IDENTITY_CLAIM_FIELDS
    )
    if claimed_schema is not None and not isinstance(claimed_schema, str):
        raise DagAttemptResultAdmissionError("dag_attempt_result_schema_invalid", "$.schema")
    claimed_node = raw.get("node_id")
    if claimed_node is not None and claimed_node != node_id:
        raise DagAttemptResultAdmissionError("dag_attempt_result_node_mismatch", "$.node_id")
    if claims_identity:
        _require_claim(raw, "run_id", identity.run_id)
        _require_claim(raw, "plan_sha256", plan_sha256)
        _require_claim(raw, "node_id", node_id)
        _require_claim(raw, "attempt_id", identity.attempt_id)
        _require_claim(raw, "attempt", identity.attempt)

    status = _required_string(raw, "status")
    if status not in ATTEMPT_RESULT_STATUSES:
        raise DagAttemptResultAdmissionError("dag_attempt_result_status_invalid", "$.status")
    verdict = _required_string(raw, "verdict")
    if not VERDICT_RE.fullmatch(verdict):
        raise DagAttemptResultAdmissionError("dag_attempt_result_verdict_invalid", "$.verdict")
    if status == "PASS" and verdict != "PASS":
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_pass_verdict_mismatch",
            "$.verdict",
        )
    if status != "PASS" and verdict == "PASS":
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_non_pass_verdict_mismatch",
            "$.verdict",
        )

    retryable = raw.get("retryable")
    if retryable is None:
        retryable = status not in {"PASS", "CANCELLED"}
    elif not isinstance(retryable, bool):
        raise DagAttemptResultAdmissionError("dag_attempt_result_retryable_invalid", "$.retryable")
    if status == "CANCELLED" and retryable is not False:
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_cancelled_retryable",
            "$.retryable",
        )

    accepted_output = raw.get("accepted_output")
    if status != "PASS" and accepted_output is not None:
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_non_pass_accepted_output",
            "$.accepted_output",
        )
    if accepted_output is not None and not isinstance(accepted_output, Mapping):
        raise DagAttemptResultAdmissionError(
            "dag_attempt_result_accepted_output_invalid",
            "$.accepted_output",
        )
    errors = _string_array(raw.get("errors", ()), "$.errors")
    alert_codes = _string_array(raw.get("alert_codes", ()), "$.alert_codes")

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
        "status": status,
        "verdict": verdict,
        "retryable": retryable,
        "accepted_output": dict(accepted_output) if isinstance(accepted_output, Mapping) else None,
        "errors": errors,
        "alert_codes": alert_codes,
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


def _required_string(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DagAttemptResultAdmissionError(
            f"dag_attempt_result_{field}_invalid",
            f"$.{field}",
        )
    return value


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


def _string_array(value: object, path: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise DagAttemptResultAdmissionError("dag_attempt_result_string_array_invalid", path)
    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise DagAttemptResultAdmissionError(
                "dag_attempt_result_string_array_invalid",
                f"{path}[{index}]",
            )
        values.append(item)
    return values
