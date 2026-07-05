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
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == EVIDENCE_VALIDATION_RECEIPT_SCHEMA


def _write_evidence(tmp_path: Path, *, schema: str) -> Path:
    evidence = tmp_path / "reviewer-verdict.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": schema,
                "goal_hash": "sha256:active-goal",
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
                        "validator": "tau evidence-validate reviewer-verdict",
                        "valid": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest
