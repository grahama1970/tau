"""tau#348: strict scheduler-to-node dispatch envelope negative fixtures."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.node_input_manifest import (
    DagNodeDispatchAdmissionError,
    validate_node_dispatch_envelope,
)
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan


def _captured_envelope(tmp_path: Path) -> dict:
    captured: dict | None = None
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "dispatch-envelope-fixture",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "producer",
                    "role": "worker",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "producer.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )

    def execute(_node: object, _inputs: object, attempt: DagNodeAttempt) -> dict:
        nonlocal captured
        captured = dict(attempt.dispatch_envelope or {})
        return {
            "node_id": "producer",
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {"source_node_id": "producer"},
        }

    result = run_dag_plan(plan, execute_node=execute)
    assert result.status == "PASS"
    assert captured is not None
    return captured


@pytest.fixture(scope="module")
def captured(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _captured_envelope(tmp_path_factory.mktemp("dispatch-envelope"))


def test_captured_envelope_validates_strict(captured: dict) -> None:
    model = validate_node_dispatch_envelope(captured)
    assert model.schema_ == "tau.dag_node_dispatch.v1"


def _mutated(captured: dict, field: str, value: object) -> dict:
    payload = copy.deepcopy(captured)
    payload[field] = value
    return payload


def test_wrong_plan_sha256_rejected_before_adapter(captured: dict) -> None:
    with pytest.raises(DagNodeDispatchAdmissionError) as exc:
        validate_node_dispatch_envelope(_mutated(captured, "plan_sha256", "sha256:" + "f" * 64))
    assert exc.value.code


def test_wrong_goal_hash_rejected_before_adapter(captured: dict) -> None:
    with pytest.raises(DagNodeDispatchAdmissionError) as exc:
        validate_node_dispatch_envelope(_mutated(captured, "goal_hash", "sha256:" + "e" * 64))
    assert exc.value.code


def test_wrong_attempt_id_rejected_before_adapter(captured: dict) -> None:
    with pytest.raises(DagNodeDispatchAdmissionError) as exc:
        validate_node_dispatch_envelope(_mutated(captured, "attempt_id", "attempt-forged"))
    assert exc.value.code


def test_tampered_accepted_inputs_hash_rejected(captured: dict) -> None:
    with pytest.raises(DagNodeDispatchAdmissionError) as exc:
        validate_node_dispatch_envelope(
            _mutated(captured, "accepted_inputs_sha256", "sha256:" + "d" * 64)
        )
    assert exc.value.code


def test_tampered_accepted_inputs_payload_rejected(captured: dict) -> None:
    payload = copy.deepcopy(captured)
    payload["accepted_inputs"] = [
        {**item, "value": "tampered"} for item in payload.get("accepted_inputs", [])
    ] or [{"value": "tampered"}]
    with pytest.raises(DagNodeDispatchAdmissionError) as exc:
        validate_node_dispatch_envelope(payload)
    assert exc.value.code


def test_unknown_extra_field_rejected(captured: dict) -> None:
    with pytest.raises(DagNodeDispatchAdmissionError) as exc:
        validate_node_dispatch_envelope(_mutated(captured, "surprise_field", 1))
    assert exc.value.code


def test_wrong_schema_rejected(captured: dict) -> None:
    with pytest.raises(DagNodeDispatchAdmissionError) as exc:
        validate_node_dispatch_envelope(_mutated(captured, "schema", "tau.bespoke.v1"))
    assert exc.value.code
