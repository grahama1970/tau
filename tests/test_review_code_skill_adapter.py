import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.review_code_skill_adapter import (
    REVIEW_CODE_ADAPTER_RECEIPT_SCHEMA,
    write_review_code_skill_adapter_receipt,
)
from tau_coding.review_findings import REVIEW_FINDINGS_SCHEMA


def test_review_code_adapter_maps_blocked_to_p0_or_block(tmp_path: Path) -> None:
    review = _write_review_result(
        tmp_path,
        verdict="BLOCKED",
        findings=[
            {
                "id": "finding-001",
                "severity": "P0",
                "file": "src/example.py",
                "line": 4,
                "claim": "Patch bypasses policy validation.",
                "evidence": ["src/example.py:4 skips policy gate"],
                "required_action": "block",
            }
        ],
    )

    receipt = write_review_code_skill_adapter_receipt(
        review_path=review,
        output_path=tmp_path / "review-code-adapter-receipt.json",
        repo_root=tmp_path,
        expected_goal_hash="sha256:goal",
    )

    findings = json.loads((tmp_path / "review-code-findings.json").read_text(encoding="utf-8"))
    assert receipt["schema"] == REVIEW_CODE_ADAPTER_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["tau_review_findings_derived_verdict"] == "BLOCKED"
    assert receipt["course_correction"]["trigger"] == "reviewer_blocked"
    assert findings["schema"] == REVIEW_FINDINGS_SCHEMA
    assert findings["findings"][0]["severity"] == "P0"
    assert findings["findings"][0]["required_action"] == "block"


def test_review_code_adapter_maps_needs_changes_to_revise(tmp_path: Path) -> None:
    review = _write_review_result(
        tmp_path,
        verdict="NEEDS_CHANGES",
        findings=[
            {
                "id": "finding-001",
                "severity": "P1",
                "file": "tests/test_example.py",
                "claim": "Missing regression coverage.",
                "evidence": ["tests/test_example.py has no stale hash case"],
            }
        ],
    )

    receipt = write_review_code_skill_adapter_receipt(
        review_path=review,
        output_path=tmp_path / "review-code-adapter-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "PASS"
    assert receipt["tau_review_findings_derived_verdict"] == "REVISE"
    assert receipt["course_correction"]["trigger"] == "reviewer_revise"


def test_review_code_adapter_blocks_pass_with_unresolved_blockers(tmp_path: Path) -> None:
    review = _write_review_result(
        tmp_path,
        verdict="PASS",
        findings=[
            {
                "id": "finding-001",
                "severity": "P0",
                "file": "src/example.py",
                "claim": "PASS cannot hide a blocking issue.",
                "evidence": ["src/example.py:1"],
                "required_action": "block",
            }
        ],
    )

    receipt = write_review_code_skill_adapter_receipt(
        review_path=review,
        output_path=tmp_path / "review-code-adapter-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "verdict_understates_findings" in receipt["alert_codes"]
    assert receipt["course_correction"]["required_next_action"] == "route_reviewer"


def test_review_code_adapter_requires_evidence_for_p0_p1(tmp_path: Path) -> None:
    review = _write_review_result(
        tmp_path,
        verdict="BLOCKED",
        findings=[
            {
                "id": "finding-001",
                "severity": "P1",
                "file": "src/example.py",
                "claim": "High-risk review claim lacks evidence.",
                "required_action": "revise",
            }
        ],
    )

    receipt = write_review_code_skill_adapter_receipt(
        review_path=review,
        output_path=tmp_path / "review-code-adapter-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "missing_finding_evidence" in receipt["alert_codes"]
    assert receipt["course_correction"]["trigger"] == "invalid_review_code_artifact"


def test_review_code_adapter_blocks_invalid_schema(tmp_path: Path) -> None:
    review = _write_review_result(tmp_path, verdict="PASS", findings=[])
    payload = json.loads(review.read_text(encoding="utf-8"))
    payload["schema"] = "review_code.unknown.v1"
    review.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_review_code_skill_adapter_receipt(
        review_path=review,
        output_path=tmp_path / "review-code-adapter-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "schema must be review_code.result.v1" in receipt["errors"]
    assert receipt["course_correction"]["trigger"] == "invalid_review_code_artifact"


def test_cli_review_code_skill_adapter_writes_receipt(tmp_path: Path) -> None:
    review = _write_review_result(tmp_path, verdict="PASS", findings=[])
    out = tmp_path / "review-code-adapter-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "review-code-skill-adapter",
            "--review",
            str(review),
            "--out",
            str(out),
            "--repo-root",
            str(tmp_path),
            "--goal-hash",
            "sha256:goal",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == REVIEW_CODE_ADAPTER_RECEIPT_SCHEMA
    assert payload["tau_review_findings_derived_verdict"] == "PASS"


def _write_review_result(
    tmp_path: Path,
    *,
    verdict: str,
    findings: list[dict[str, object]],
) -> Path:
    payload = {
        "schema": "review_code.result.v1",
        "goal_hash": "sha256:goal",
        "reviewer": "review-code:unit",
        "verdict": verdict,
        "allowed_paths": ["src/**", "tests/**", "review-code-bundle.md"],
        "findings": findings,
    }
    path = tmp_path / "review_result.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
