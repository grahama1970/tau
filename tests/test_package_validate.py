from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.package_validate import (
    COMPLIANCE_PACKAGE_VALIDATION_RECEIPT_SCHEMA,
    write_compliance_package_validation_receipt,
)


def test_compliance_package_validate_marks_complete_package_review_ready(
    tmp_path: Path,
) -> None:
    package_dir = _write_package(tmp_path)

    receipt = write_compliance_package_validation_receipt(
        package_dir=package_dir,
        receipt_path=tmp_path / "validation-receipt.json",
    )

    assert receipt["schema"] == COMPLIANCE_PACKAGE_VALIDATION_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["review_ready"] is True
    assert receipt["compliant"] == "NOT_CLAIMED"
    assert receipt["alert_codes"] == []
    assert receipt["recommended_action"]["next_agent"] == "reviewer"
    assert "ITAR compliance." in receipt["proof_scope"]["does_not_prove"]


def test_compliance_package_validate_blocks_missing_required_artifact(
    tmp_path: Path,
) -> None:
    package_dir = _write_package(tmp_path)
    (package_dir / "sandbox-run-receipt.json").unlink()

    receipt = write_compliance_package_validation_receipt(
        package_dir=package_dir,
        receipt_path=tmp_path / "validation-receipt.json",
    )

    assert receipt["ok"] is False
    assert receipt["review_ready"] is False
    assert "missing_required_artifact" in receipt["alert_codes"]
    assert receipt["compliant"] == "NOT_CLAIMED"


def test_compliance_package_validate_blocks_blocked_critical_receipt(
    tmp_path: Path,
) -> None:
    package_dir = _write_package(tmp_path)
    _write_json(
        package_dir / "sandbox-run-receipt.json",
        {"schema": "tau.sandbox_run_receipt.v1", "status": "BLOCKED", "goal_hash": "sha256:g"},
    )

    receipt = write_compliance_package_validation_receipt(
        package_dir=package_dir,
        receipt_path=tmp_path / "validation-receipt.json",
    )

    assert receipt["ok"] is False
    assert "critical_receipt_not_pass" in receipt["alert_codes"]


def test_compliance_package_validate_blocks_goal_hash_mismatch(tmp_path: Path) -> None:
    package_dir = _write_package(tmp_path)
    _write_json(
        package_dir / "memory-intent-gate-receipt.json",
        {
            "schema": "tau.memory_intent_gate_receipt.v1",
            "status": "PASS",
            "goal_hash": "sha256:other",
        },
    )

    receipt = write_compliance_package_validation_receipt(
        package_dir=package_dir,
        receipt_path=tmp_path / "validation-receipt.json",
    )

    assert receipt["ok"] is False
    assert "goal_hash_mismatch" in receipt["alert_codes"]


def test_cli_compliance_package_validate_writes_blocked_receipt(tmp_path: Path) -> None:
    package_dir = _write_package(tmp_path)
    (package_dir / "non-claims.md").write_text("This package is compliant.\n", encoding="utf-8")
    receipt_path = tmp_path / "package-validation-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "compliance-package-validate",
            str(package_dir),
            "--receipt",
            str(receipt_path),
        ],
    )
    payload = json.loads(result.output)
    written = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert payload == written
    assert payload["schema"] == COMPLIANCE_PACKAGE_VALIDATION_RECEIPT_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert payload["review_ready"] is False
    assert payload["compliant"] == "NOT_CLAIMED"
    assert "non_claims_missing_boundary_language" in payload["alert_codes"]


def _write_package(tmp_path: Path) -> Path:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    _write_json(
        package_dir / "data-boundary.json",
        {
            "schema": "tau.data_boundary.v1",
            "classification": "ITAR",
            "export_controlled": True,
            "itar": True,
            "technical_data": True,
            "external_provider_allowed": False,
            "external_research_allowed": False,
            "public_repo_allowed": False,
            "foreign_person_access": "prohibited",
        },
    )
    _write_json(
        package_dir / "policy-profile.json",
        {
            "schema": "tau.policy_profile.v1",
            "profile_id": "itar-local-only",
            "default_decision": "deny",
            "requires_data_boundary": True,
        },
    )
    _write_json(
        package_dir / "actor-access-manifest.json",
        {
            "schema": "tau.actor_access_manifest.v1",
            "actor_id": "human:graham",
            "goal_hash": "sha256:g",
        },
    )
    _write_json(
        package_dir / "environment-manifest.json",
        {"schema": "tau.environment_manifest.v1", "goal_hash": "sha256:g"},
    )
    for filename, schema in {
        "zero-trust-preflight-receipt.json": "tau.zero_trust_preflight_receipt.v1",
        "memory-intent-gate-receipt.json": "tau.memory_intent_gate_receipt.v1",
        "evidence-case-gate-receipt.json": "tau.evidence_case_gate_receipt.v1",
        "evidence-validation-receipt.json": "tau.evidence_validation_receipt.v1",
        "sandbox-run-receipt.json": "tau.sandbox_run_receipt.v1",
        "signed-receipt-verification.json": "tau.signed_receipt_verification.v1",
        "itar-access-preflight-receipt.json": "tau.itar_access_preflight_receipt.v1",
    }.items():
        _write_json(package_dir / filename, {"schema": schema, "status": "PASS", "goal_hash": "sha256:g"})
    (package_dir / "non-claims.md").write_text(
        "This package does not prove ITAR compliance or legal sufficiency.\n",
        encoding="utf-8",
    )
    return package_dir


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
