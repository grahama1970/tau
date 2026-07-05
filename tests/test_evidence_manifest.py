import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.evidence_manifest import (
    EVIDENCE_MANIFEST_SCHEMA,
    EVIDENCE_VALIDATION_RECEIPT_SCHEMA,
    write_evidence_validation_receipt,
)


def test_evidence_manifest_validation_passes_for_hash_and_schema(
    tmp_path: Path,
) -> None:
    evidence = _write_evidence(tmp_path, schema="tau.reviewer_verdict.v1")
    manifest = _write_manifest(tmp_path, evidence, schema="tau.reviewer_verdict.v1")

    receipt = write_evidence_validation_receipt(manifest_path=manifest)

    assert receipt["schema"] == EVIDENCE_VALIDATION_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    assert receipt["item_count"] == 1
    assert receipt["checked_items"][0]["valid"] is True
    assert receipt["checked_items"][0]["observed_schema"] == "tau.reviewer_verdict.v1"
    assert receipt["checked_items"][0]["observed_kind"] == "reviewer_verdict"
    assert Path(str(receipt["receipt_path"])).exists()


def test_evidence_manifest_validation_blocks_sha_mismatch(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path, schema="tau.reviewer_verdict.v1")
    manifest = _write_manifest(
        tmp_path,
        evidence,
        schema="tau.reviewer_verdict.v1",
        sha256="sha256:" + ("0" * 64),
    )

    receipt = write_evidence_validation_receipt(manifest_path=manifest)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "items[0].sha256 mismatch" in receipt["errors"][0]
    assert receipt["checked_items"][0]["valid"] is False


def test_evidence_manifest_validation_blocks_schema_mismatch(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path, schema="tau.other_receipt.v1")
    manifest = _write_manifest(tmp_path, evidence, schema="tau.reviewer_verdict.v1")

    receipt = write_evidence_validation_receipt(manifest_path=manifest)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["errors"] == [
        "items[0].schema mismatch: expected tau.reviewer_verdict.v1, observed tau.other_receipt.v1"
    ]


def test_evidence_manifest_validation_blocks_goal_hash_mismatch(tmp_path: Path) -> None:
    evidence = _write_evidence(
        tmp_path,
        schema="tau.reviewer_verdict.v1",
        goal_hash="sha256:other-goal",
    )
    manifest = _write_manifest(tmp_path, evidence, schema="tau.reviewer_verdict.v1")

    receipt = write_evidence_validation_receipt(manifest_path=manifest)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["checked_items"][0]["observed_goal_hash"] == "sha256:other-goal"
    assert receipt["errors"] == [
        "items[0].goal_hash mismatch: expected sha256:active-goal, observed sha256:other-goal"
    ]


def test_evidence_manifest_validation_blocks_kind_mismatch(tmp_path: Path) -> None:
    evidence = _write_evidence(
        tmp_path,
        schema="tau.reviewer_verdict.v1",
        kind="creator_artifact",
    )
    manifest = _write_manifest(tmp_path, evidence, schema="tau.reviewer_verdict.v1")

    receipt = write_evidence_validation_receipt(manifest_path=manifest)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["checked_items"][0]["observed_kind"] == "creator_artifact"
    assert receipt["errors"] == [
        "items[0].kind mismatch: expected reviewer_verdict, observed creator_artifact"
    ]


def test_evidence_manifest_validation_blocks_non_tau_validator(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path, schema="tau.reviewer_verdict.v1")
    manifest = _write_manifest(
        tmp_path,
        evidence,
        schema="tau.reviewer_verdict.v1",
        validator="external validator",
    )

    receipt = write_evidence_validation_receipt(manifest_path=manifest)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["errors"] == [
        "items[0].validator must use tau evidence-validate, observed external validator"
    ]


def test_cli_evidence_validate_writes_receipt(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path, schema="tau.reviewer_verdict.v1")
    manifest = _write_manifest(tmp_path, evidence, schema="tau.reviewer_verdict.v1")
    out = tmp_path / "validation-receipt.json"

    result = CliRunner().invoke(app, ["evidence-validate", str(manifest), "--receipt", str(out)])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema"] == EVIDENCE_VALIDATION_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert out.exists()
    persisted = json.loads(out.read_text(encoding="utf-8"))
    assert persisted["schema"] == EVIDENCE_VALIDATION_RECEIPT_SCHEMA


def _write_evidence(
    tmp_path: Path,
    *,
    schema: str,
    goal_hash: str = "sha256:active-goal",
    kind: str = "reviewer_verdict",
) -> Path:
    evidence = tmp_path / "reviewer-verdict.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": schema,
                "kind": kind,
                "goal_hash": goal_hash,
                "verdict": "PASS",
            }
        ),
        encoding="utf-8",
    )
    return evidence


def _write_manifest(
    tmp_path: Path,
    evidence: Path,
    *,
    schema: str,
    sha256: str | None = None,
    validator: str = "tau evidence-validate reviewer-verdict",
) -> Path:
    digest = sha256 or f"sha256:{hashlib.sha256(evidence.read_bytes()).hexdigest()}"
    manifest = tmp_path / "evidence-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": EVIDENCE_MANIFEST_SCHEMA,
                "run_id": "run-001",
                "dag_id": "dag-001",
                "goal_hash": "sha256:active-goal",
                "items": [
                    {
                        "kind": "reviewer_verdict",
                        "path": str(evidence),
                        "sha256": digest,
                        "schema": schema,
                        "validator": validator,
                        "valid": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest
