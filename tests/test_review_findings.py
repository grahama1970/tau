import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.review_findings import (
    REVIEW_FINDINGS_SCHEMA,
    validate_review_findings,
    write_review_findings_receipt,
)


def test_review_findings_blocks_on_p0() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="BLOCKED",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P0",
                    "confidence": 0.9,
                    "file": "src/example.py",
                    "line": 3,
                    "claim": "The patch skips policy validation.",
                    "evidence": ["src/example.py:3 calls apply without policy"],
                    "required_action": "block",
                }
            ],
        ),
        expected_goal_hash="sha256:goal",
    )

    assert receipt["schema"] == REVIEW_FINDINGS_SCHEMA
    assert receipt["ok"] is True
    assert receipt["derived_verdict"] == "BLOCKED"
    assert receipt["blocking_finding_count"] == 1


def test_review_findings_revises_on_p1() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="REVISE",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P1",
                    "confidence": 0.8,
                    "file": "tests/test_example.py",
                    "line": 7,
                    "claim": "Missing stale-hash regression coverage.",
                    "evidence": ["tests omit stale base hash case"],
                    "required_action": "revise",
                }
            ],
        )
    )

    assert receipt["ok"] is True
    assert receipt["derived_verdict"] == "REVISE"
    assert receipt["revision_finding_count"] == 1


def test_review_findings_passes_with_no_blocking_findings(tmp_path: Path) -> None:
    findings_path = tmp_path / "findings.json"
    findings_path.write_text(json.dumps(_payload(verdict="PASS", findings=[])), encoding="utf-8")

    receipt = write_review_findings_receipt(
        findings_path=findings_path,
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["derived_verdict"] == "PASS"
    assert receipt["finding_count"] == 0
    assert "The reviewer is correct." in receipt["proof_scope"]["does_not_prove"]


def test_review_findings_blocks_goal_hash_mismatch() -> None:
    receipt = validate_review_findings(
        _payload(verdict="PASS", findings=[]),
        expected_goal_hash="sha256:other",
    )

    assert receipt["status"] == "BLOCKED"
    assert "goal_hash_mismatch" in receipt["alert_codes"]


def test_review_findings_requires_evidence_for_p0_p1() -> None:
    receipt = validate_review_findings(
        _payload(
            verdict="BLOCKED",
            findings=[
                {
                    "id": "finding-001",
                    "severity": "P0",
                    "confidence": 0.9,
                    "file": "src/example.py",
                    "line": 3,
                    "claim": "High-risk claim without evidence.",
                    "evidence": [],
                    "required_action": "block",
                }
            ],
        )
    )

    assert receipt["status"] == "BLOCKED"
    assert "missing_finding_evidence" in receipt["alert_codes"]


def test_cli_review_findings_writes_receipt(tmp_path: Path) -> None:
    findings_path = tmp_path / "findings.json"
    receipt_path = tmp_path / "receipt.json"
    findings_path.write_text(json.dumps(_payload(verdict="PASS", findings=[])), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "review-findings",
            "--findings",
            str(findings_path),
            "--out",
            str(receipt_path),
            "--goal-hash",
            "sha256:goal",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["schema"] == REVIEW_FINDINGS_SCHEMA
    assert payload["derived_verdict"] == "PASS"


def _payload(*, verdict: str, findings: list[dict]) -> dict:
    return {
        "schema": REVIEW_FINDINGS_SCHEMA,
        "goal_hash": "sha256:goal",
        "reviewer": "reviewer",
        "verdict": verdict,
        "findings": findings,
    }
