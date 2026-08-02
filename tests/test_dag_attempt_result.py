from __future__ import annotations

import math
from typing import Any

import pytest

from tau_coding.dag_runtime.attempt_result import (
    DAG_ATTEMPT_RESULT_SCHEMA,
    DAG_ATTEMPT_RESULT_VALIDATION_SCHEMA,
    DagAttemptResultAdmissionError,
    admit_dag_attempt_result,
)
from tau_coding.dag_runtime.run_store import DagAttemptIdentity


def test_admit_dag_attempt_result_normalizes_pass_payload() -> None:
    admission = admit_dag_attempt_result(
        plan_sha256="sha256:" + "1" * 64,
        identity=_identity(),
        node_id="producer",
        result={
            "schema": "tau.generic_dag_node_receipt.v1",
            "node_id": "producer",
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"value": 1},
            "artifacts": ["receipt.json"],
        },
    )

    assert admission.normalized == {
        "schema": DAG_ATTEMPT_RESULT_SCHEMA,
        "run_id": "run-1",
        "plan_sha256": "sha256:" + "1" * 64,
        "node_id": "producer",
        "attempt_id": "attempt-1",
        "attempt": 1,
        "status": "PASS",
        "verdict": "PASS",
        "retryable": False,
        "accepted_output": {"value": 1},
        "errors": [],
        "alert_codes": [],
        "artifacts": ["receipt.json"],
        "source_schema": "tau.generic_dag_node_receipt.v1",
    }
    assert admission.validation["schema"] == DAG_ATTEMPT_RESULT_VALIDATION_SCHEMA
    assert admission.validation["result_sha256"].startswith("sha256:")


@pytest.mark.parametrize(
    ("status", "verdict", "retryable"),
    [
        ("BLOCKED", "REVIEW_REQUIRED", True),
        ("CANCELLED", "CANCELLED", False),
        ("FAIL", "ASSERTION_FAILED", True),
    ],
)
def test_admit_dag_attempt_result_normalizes_terminal_non_pass_payloads(
    status: str,
    verdict: str,
    retryable: bool,
) -> None:
    admission = admit_dag_attempt_result(
        plan_sha256="sha256:" + "2" * 64,
        identity=_identity(),
        node_id="producer",
        result={
            "node_id": "producer",
            "status": status,
            "verdict": verdict,
            "retryable": retryable,
            "errors": ["detail"],
            "alert_codes": ["ALERT"],
        },
    )

    assert admission.normalized["status"] == status
    assert admission.normalized["verdict"] == verdict
    assert admission.normalized["retryable"] is retryable
    assert admission.normalized["accepted_output"] is None


@pytest.mark.parametrize(
    ("payload", "code", "path"),
    [
        ({"status": True, "verdict": "PASS"}, "dag_attempt_result_status_invalid", "$.status"),
        (
            {"status": "PASS", "verdict": "FAIL"},
            "dag_attempt_result_pass_verdict_mismatch",
            "$.verdict",
        ),
        (
            {"status": "BLOCKED", "verdict": "PASS"},
            "dag_attempt_result_non_pass_verdict_mismatch",
            "$.verdict",
        ),
        (
            {"status": "PASS", "verdict": "pass"},
            "dag_attempt_result_verdict_invalid",
            "$.verdict",
        ),
        (
            {"status": "PASS", "verdict": "PASS", "retryable": "false"},
            "dag_attempt_result_retryable_invalid",
            "$.retryable",
        ),
        (
            {"status": "CANCELLED", "verdict": "CANCELLED", "retryable": True},
            "dag_attempt_result_cancelled_retryable",
            "$.retryable",
        ),
        (
            {"status": "BLOCKED", "verdict": "X", "accepted_output": {}},
            "dag_attempt_result_non_pass_accepted_output",
            "$.accepted_output",
        ),
        (
            {"status": "PASS", "verdict": "PASS", "errors": [3]},
            "dag_attempt_result_string_array_invalid",
            "$.errors[0]",
        ),
        (
            {"status": "PASS", "verdict": "PASS", "accepted_output": {"nan": math.nan}},
            "dag_attempt_result_non_canonical_json",
            "$",
        ),
        (
            {"schema": DAG_ATTEMPT_RESULT_SCHEMA, "status": "PASS", "verdict": "PASS"},
            "dag_attempt_result_run_id_missing",
            "$.run_id",
        ),
        (
            {"run_id": "wrong", "status": "PASS", "verdict": "PASS"},
            "dag_attempt_result_run_id_mismatch",
            "$.run_id",
        ),
        (
            {"node_id": "wrong", "status": "PASS", "verdict": "PASS"},
            "dag_attempt_result_node_mismatch",
            "$.node_id",
        ),
    ],
)
def test_admit_dag_attempt_result_rejects_malformed_payloads(
    payload: dict[str, Any],
    code: str,
    path: str,
) -> None:
    with pytest.raises(DagAttemptResultAdmissionError) as excinfo:
        admit_dag_attempt_result(
            plan_sha256="sha256:" + "3" * 64,
            identity=_identity(),
            node_id="producer",
            result=payload,
        )

    assert excinfo.value.code == code
    assert excinfo.value.path == path


def _identity() -> DagAttemptIdentity:
    return DagAttemptIdentity(
        run_id="run-1",
        node_id="producer",
        attempt=1,
        attempt_id="attempt-1",
        idempotency_key="sha256:" + "a" * 64,
    )
