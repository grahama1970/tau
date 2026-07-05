import json
from pathlib import Path

from tau_coding.github_apply_policy import write_github_apply_policy_receipt


def test_github_apply_policy_passes_when_all_required_gates_are_present(tmp_path: Path) -> None:
    paths = _write_policy_fixture(tmp_path)

    receipt = write_github_apply_policy_receipt(
        projection_path=paths["projection"],
        policy_path=paths["policy"],
        receipt_path=paths["receipt"],
        approval_receipt_path=paths["approval"],
        redaction_receipt_path=paths["redaction"],
        preflight_ready=True,
    )
    written = json.loads(paths["receipt"].read_text(encoding="utf-8"))

    assert receipt == written
    assert receipt["schema"] == "tau.github_apply_policy_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["target"] == {"repo": "grahama1970/tau", "target": "issue#47"}
    assert receipt["actions"] == ["comment", "label"]
    assert receipt["requirements"] == {
        "approval_packet": True,
        "preflight": True,
        "redaction": True,
    }
    assert receipt["errors"] == []


def test_github_apply_policy_blocks_when_required_gates_are_missing(tmp_path: Path) -> None:
    paths = _write_policy_fixture(tmp_path)

    receipt = write_github_apply_policy_receipt(
        projection_path=paths["projection"],
        policy_path=paths["policy"],
        receipt_path=paths["receipt"],
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "redaction receipt is required by policy" in receipt["errors"]
    assert "approval receipt is required by policy" in receipt["errors"]
    assert (
        "preflight is required by policy but --preflight-ready was not supplied"
        in receipt["errors"]
    )


def test_github_apply_policy_blocks_denied_actions(tmp_path: Path) -> None:
    paths = _write_policy_fixture(tmp_path)
    policy = json.loads(paths["policy"].read_text(encoding="utf-8"))
    policy["allowed_actions"] = ["comment"]
    policy["denied_actions"] = ["label"]
    paths["policy"].write_text(json.dumps(policy), encoding="utf-8")

    receipt = write_github_apply_policy_receipt(
        projection_path=paths["projection"],
        policy_path=paths["policy"],
        receipt_path=paths["receipt"],
        approval_receipt_path=paths["approval"],
        redaction_receipt_path=paths["redaction"],
        preflight_ready=True,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "projection contains denied GitHub actions: ['label']" in receipt["errors"]
    assert "projection contains actions not allowed by policy: ['label']" in receipt["errors"]


def _write_policy_fixture(tmp_path: Path) -> dict[str, Path]:
    projection_path = tmp_path / "projection.json"
    redacted_projection_path = tmp_path / "projection.redacted.json"
    policy_path = tmp_path / "github-apply-policy.json"
    redaction_path = tmp_path / "github-redaction-receipt.json"
    approval_path = tmp_path / "approval-gate-receipt.json"
    receipt_path = tmp_path / "github-apply-policy-receipt.json"
    projection = {
        "schema": "tau.agent_handoff_projection_receipt.v1",
        "ok": True,
        "target": {"repo": "grahama1970/tau", "target": "issue#47"},
        "comment": {"body": "## Tau Agent Handoff\n"},
        "labels": {"add": ["agent-work"], "remove": ["agent-active"]},
        "errors": [],
    }
    policy = {
        "schema": "tau.github_apply_policy.v1",
        "allowed_repos": ["grahama1970/tau"],
        "allowed_actions": ["comment", "label"],
        "denied_actions": ["close", "merge", "release"],
        "requires_approval_packet": True,
        "requires_preflight": True,
        "requires_redaction": True,
    }
    redaction_receipt = {
        "schema": "tau.github_projection_redaction_receipt.v1",
        "ok": True,
        "status": "PASS",
        "projection": str(projection_path.resolve()),
        "redacted_projection": str(redacted_projection_path.resolve()),
        "errors": [],
    }
    approval_receipt = {
        "schema": "tau.approval_gate_receipt.v1",
        "ok": True,
        "status": "PASS",
        "approved": True,
        "requested_action": "github_apply",
        "errors": [],
    }
    projection_path.write_text(json.dumps(projection), encoding="utf-8")
    redacted_projection_path.write_text(json.dumps(projection), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    redaction_path.write_text(json.dumps(redaction_receipt), encoding="utf-8")
    approval_path.write_text(json.dumps(approval_receipt), encoding="utf-8")
    return {
        "projection": projection_path,
        "redacted_projection": redacted_projection_path,
        "policy": policy_path,
        "redaction": redaction_path,
        "approval": approval_path,
        "receipt": receipt_path,
    }
