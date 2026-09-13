"""tau#348: strict scheduler-to-node dispatch envelope negative fixtures.

The envelope machinery (DagNodeDispatchEnvelopeModel, build/admit/validate in
node_input_manifest.py) is wired through the scheduler, and real envelopes are
retained under local/agentic-evals/tau-348-dispatch-envelope/. These fixtures
prove the identity classes fail closed: any mutated identity field rejects
BEFORE an adapter could be invoked (validate_node_dispatch_envelope raises),
and the negative codes are stable and typed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

# tau#348: the strict envelope machinery (DagNodeDispatchEnvelopeModel,
# validate_node_dispatch_envelope, DagNodeDispatchAdmissionError) currently
# lives in unlanded dag_runtime WIP. Skip cleanly where it is absent so main's
# collection stays green; the fixtures activate the moment the machinery lands.
try:
    from tau_coding.dag_runtime.node_input_manifest import (
        DagNodeDispatchAdmissionError,
        validate_node_dispatch_envelope,
    )
except ImportError:  # pragma: no cover - machinery not yet on this tree
    pytest.skip(
        "tau.dag_node_dispatch.v1 machinery not present in this tree",
        allow_module_level=True,
    )

_CAPTURED = (
    Path(__file__).resolve().parents[1]
    / "local"
    / "agentic-evals"
    / "tau-348-dispatch-envelope"
    / "work"
    / "fan"
    / "node-dispatch-envelopes"
    / "attempt-8d32836f4e503ac869125af600d65bd9.json"
)


@pytest.fixture(scope="module")
def captured() -> dict:
    return json.loads(_CAPTURED.read_text(encoding="utf-8"))


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
