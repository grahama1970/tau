from pathlib import Path

from tau_coding.memory_evidence_gate import (
    EVIDENCE_CASE_GATE_RECEIPT_SCHEMA,
    MEMORY_INTENT_GATE_RECEIPT_SCHEMA,
    evaluate_memory_evidence_gate,
    write_memory_evidence_gate_receipts,
)
from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA


def test_memory_evidence_gate_allows_valid_intent_and_evidence_case(tmp_path: Path) -> None:
    intent_receipt, evidence_receipt = evaluate_memory_evidence_gate(
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
        memory_intent=_memory_intent(),
        evidence_case=_evidence_case(),
    )

    assert intent_receipt["schema"] == MEMORY_INTENT_GATE_RECEIPT_SCHEMA
    assert intent_receipt["ok"] is True
    assert intent_receipt["status"] == "PASS"
    assert intent_receipt["mocked"] is False
    assert intent_receipt["live"] is False
    assert evidence_receipt["schema"] == EVIDENCE_CASE_GATE_RECEIPT_SCHEMA
    assert evidence_receipt["ok"] is True
    assert evidence_receipt["allowed_to_dispatch"] is True

    intent_payload, evidence_payload = write_memory_evidence_gate_receipts(
        receipt_dir=tmp_path,
        intent_receipt=intent_receipt,
        evidence_receipt=evidence_receipt,
    )

    assert Path(str(intent_payload["receipt_path"])).exists()
    assert Path(str(evidence_payload["receipt_path"])).exists()
    assert intent_payload["evidence_case_receipt"] == evidence_payload["receipt_path"]


def test_memory_evidence_gate_blocks_missing_required_intent() -> None:
    intent_receipt, evidence_receipt = evaluate_memory_evidence_gate(
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
        memory_intent=None,
        evidence_case=None,
    )

    assert intent_receipt["ok"] is False
    assert intent_receipt["alert_codes"] == ["missing_memory_intent"]
    assert evidence_receipt["ok"] is True


def test_memory_evidence_gate_blocks_clarify_route() -> None:
    intent = _memory_intent()
    intent["route"] = "CLARIFY"

    intent_receipt, _ = evaluate_memory_evidence_gate(
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
        memory_intent=intent,
        evidence_case=_evidence_case(),
    )

    assert intent_receipt["ok"] is False
    assert "intent_clarify_required" in intent_receipt["alert_codes"]


def test_memory_evidence_gate_blocks_inline_evidence() -> None:
    intent = _memory_intent()
    intent["evidence"] = [{"claim": "inline evidence is not admissible"}]

    intent_receipt, _ = evaluate_memory_evidence_gate(
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
        memory_intent=intent,
        evidence_case=_evidence_case(),
    )

    assert intent_receipt["ok"] is False
    assert "intent_contains_inline_evidence" in intent_receipt["alert_codes"]


def test_memory_evidence_gate_blocks_evidence_case_boundary_mismatch() -> None:
    evidence = _evidence_case()
    evidence["data_boundary"] = {**_data_boundary(), "classification": "internal"}

    intent_receipt, evidence_receipt = evaluate_memory_evidence_gate(
        policy_profile=_policy_profile(),
        data_boundary=_data_boundary(),
        memory_intent=_memory_intent(),
        evidence_case=evidence,
    )

    assert intent_receipt["ok"] is False
    assert "missing_evidence_case" in intent_receipt["alert_codes"]
    assert evidence_receipt["ok"] is False
    assert "evidence_case_boundary_mismatch" in evidence_receipt["alert_codes"]


def _policy_profile() -> dict:
    return {
        "schema": POLICY_PROFILE_SCHEMA,
        "profile_id": "itar-zero-trust-local-only",
        "default_decision": "deny",
        "memory": {
            "intent_required": True,
            "evidence_case_required_for": ["COMPLIANCE"],
            "min_intent_confidence": 0.75,
            "clarify_blocks_dispatch": True,
            "deflect_blocks_dispatch": True,
        },
    }


def _data_boundary() -> dict:
    return {
        "schema": DATA_BOUNDARY_SCHEMA,
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
    }


def _memory_intent() -> dict:
    return {
        "schema": "memory.intent.v1",
        "memory_first": True,
        "planner_only": True,
        "route": "COMPLIANCE",
        "confidence": 0.91,
        "recall_profile": "proof_retrieval",
        "required_artifacts": [],
        "tool_calls": [{"name": "create_evidence_case"}],
        "evidence_case_required": True,
    }


def _evidence_case() -> dict:
    return {
        "schema": "memory.evidence_case.v1",
        "source": "graph-memory-operator:/create-evidence-case",
        "sha256": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        "question": "Can Tau dispatch this zero-trust DAG?",
        "data_boundary": _data_boundary(),
        "policy_profile": {
            "schema": POLICY_PROFILE_SCHEMA,
            "profile_id": "itar-zero-trust-local-only",
            "default_decision": "deny",
        },
    }
