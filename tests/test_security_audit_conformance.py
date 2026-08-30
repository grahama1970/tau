import json
from pathlib import Path

from tau_coding.security_audit_conformance import (
    ACTION,
    API_MUTATING_REQUEST_RECEIPT_SCHEMA,
    AUDIT_LEDGER_VERIFICATION_SCHEMA,
    TARGET_ID,
    TARGET_LINEAGE,
    _evaluate_api_mutating_request,
    _rbac_policy,
    _token_sha256,
    _verify_audit_ledger,
    _write_audit_ledger,
    _write_json,
)


def _test_auth_token() -> str:
    return "tau-test-auth-token"


def test_api_mutating_request_accepts_authorized_rbac_actor(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "rbac-policy.json"
    receipt_path = tmp_path / "authorized-mutating-request.json"
    approval_receipt = _approval_gate_receipt(status="PASS")
    auth_token = _test_auth_token()
    _write_json(policy_path, _rbac_policy(api_token=auth_token))

    receipt = _evaluate_api_mutating_request(
        request={
            "actor_id": "human:graham",
            "auth_token": auth_token,
            "action": ACTION,
            "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
        },
        policy_path=policy_path,
        approval_gate_receipt=approval_receipt,
        receipt_path=receipt_path,
    )

    assert receipt["schema"] == API_MUTATING_REQUEST_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["role"] == "operator"
    assert receipt["accepted"] is True
    assert receipt["mutation_applied"] is True
    assert receipt["auth_token_sha256"] == _token_sha256(auth_token)
    assert receipt["approval_gate_status"] == "PASS"
    assert receipt["errors"] == []
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt


def test_api_mutating_request_blocks_unauthorized_actor_without_token(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "rbac-policy.json"
    _write_json(policy_path, _rbac_policy(api_token=_test_auth_token()))

    receipt = _evaluate_api_mutating_request(
        request={
            "actor_id": "agent:unauthorized",
            "auth_token": None,
            "action": ACTION,
            "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
        },
        policy_path=policy_path,
        approval_gate_receipt=_approval_gate_receipt(status="PASS"),
        receipt_path=tmp_path / "unauthorized-mutating-request.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["role"] == "viewer"
    assert receipt["accepted"] is False
    assert receipt["mutation_applied"] is False
    assert receipt["auth_token_present"] is False
    assert receipt["auth_token_sha256"] is None
    assert receipt["errors"] == [
        "api authentication failed",
        "rbac denied action",
    ]


def test_api_mutating_request_requires_passed_approval_receipt(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "rbac-policy.json"
    auth_token = _test_auth_token()
    _write_json(policy_path, _rbac_policy(api_token=auth_token))

    receipt = _evaluate_api_mutating_request(
        request={
            "actor_id": "human:graham",
            "auth_token": auth_token,
            "action": ACTION,
            "target": {"id": TARGET_ID, "lineage": TARGET_LINEAGE},
        },
        policy_path=policy_path,
        approval_gate_receipt=_approval_gate_receipt(status="BLOCKED"),
        receipt_path=tmp_path / "blocked-approval-mutating-request.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["accepted"] is False
    assert receipt["mutation_applied"] is False
    assert receipt["errors"] == ["approval gate receipt must PASS"]


def test_api_mutating_request_blocks_wrong_target_lineage(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "rbac-policy.json"
    auth_token = _test_auth_token()
    _write_json(policy_path, _rbac_policy(api_token=auth_token))

    receipt = _evaluate_api_mutating_request(
        request={
            "actor_id": "human:graham",
            "auth_token": auth_token,
            "action": ACTION,
            "target": {"id": TARGET_ID, "lineage": "sha256:wrong-lineage"},
        },
        policy_path=policy_path,
        approval_gate_receipt=_approval_gate_receipt(status="PASS"),
        receipt_path=tmp_path / "wrong-lineage-mutating-request.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["accepted"] is False
    assert receipt["mutation_applied"] is False
    assert receipt["errors"] == ["target.lineage mismatch"]


def test_audit_ledger_verifier_accepts_hash_chained_entries(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "audit-ledger.jsonl"
    receipt_path = tmp_path / "audit-ledger-verification.json"

    _write_audit_ledger(
        ledger_path,
        [
            {"kind": "unauthorized_mutating_request_denied", "status": "BLOCKED"},
            {"kind": "authorized_mutating_request_accepted", "status": "PASS"},
        ],
    )
    lines = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    verification = _verify_audit_ledger(
        ledger_path=ledger_path,
        receipt_path=receipt_path,
    )

    assert lines[0]["sequence"] == 1
    assert lines[0]["previous_hash"] == "sha256:GENESIS"
    assert lines[1]["sequence"] == 2
    assert lines[1]["previous_hash"] == lines[0]["entry_hash"]
    assert verification["schema"] == AUDIT_LEDGER_VERIFICATION_SCHEMA
    assert verification["status"] == "PASS"
    assert verification["entry_count"] == 2
    assert verification["terminal_hash"] == lines[1]["entry_hash"]
    assert verification["errors"] == []
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == verification


def test_audit_ledger_verifier_blocks_tampered_entry(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "audit-ledger.jsonl"
    receipt_path = tmp_path / "audit-ledger-verification.json"

    _write_audit_ledger(
        ledger_path,
        [
            {"kind": "unauthorized_mutating_request_denied", "status": "BLOCKED"},
            {"kind": "authorized_mutating_request_accepted", "status": "PASS"},
        ],
    )
    lines = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    lines[0]["event"]["status"] = "PASS"
    ledger_path.write_text(
        "\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n",
        encoding="utf-8",
    )

    verification = _verify_audit_ledger(
        ledger_path=ledger_path,
        receipt_path=receipt_path,
    )

    assert verification["status"] == "BLOCKED"
    assert verification["entry_count"] == 2
    assert verification["errors"] == ["line 1 entry_hash mismatch"]


def _approval_gate_receipt(*, status: str) -> dict[str, object]:
    return {
        "status": status,
        "approval_packet": "/tmp/tau-approval-packet.json",
    }
