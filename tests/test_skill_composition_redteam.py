import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.skill_composition_redteam import (
    REQUIRED_UNPROVEN_CLAIMS,
    SKILL_COMPOSITION_REDTEAM_RECEIPT_SCHEMA,
    run_skill_composition_redteam,
)


def test_skill_composition_redteam_requires_fail_closed_skill_adapters(
    tmp_path: Path,
) -> None:
    receipt = run_skill_composition_redteam(run_dir=tmp_path)

    assert receipt["schema"] == SKILL_COMPOSITION_REDTEAM_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["ok"] is True
    assert receipt["attempt_count"] == 7
    assert receipt["passed_attempt_count"] == 7
    assert {attempt["name"] for attempt in receipt["attempts"]} == {
        "debugger_proof_missing_goal_hash",
        "review_code_pass_with_blocking_finding",
        "code_runner_patch_outside_allowlist",
        "dogpile_report_without_query_safety_receipt",
        "create_evidence_case_boundary_mismatch",
        "skill_invocation_artifact_outside_repo",
        "skill_invocation_mocked_but_high_stakes_requires_live",
    }
    assert all(attempt["observed_status"] == "BLOCKED" for attempt in receipt["attempts"])
    assert all(attempt["expected_error_seen"] is True for attempt in receipt["attempts"])
    assert all(
        attempt["course_correction_present"] is True
        for attempt in receipt["attempts"]
        if attempt["course_correction_required"]
    )
    assert receipt["proof_scope"]["does_not_prove"] == REQUIRED_UNPROVEN_CLAIMS
    for unproven_claim in REQUIRED_UNPROVEN_CLAIMS:
        assert _claim_is_not_in_proves(unproven_claim, receipt["proof_scope"]["proves"])
    on_disk = json.loads(
        (tmp_path / "skill-composition-redteam-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert on_disk == receipt


def test_cli_skill_composition_redteam_writes_receipt(tmp_path: Path) -> None:
    run_dir = tmp_path / "cli-redteam"

    result = CliRunner().invoke(app, ["skill-composition-redteam", "--run-dir", str(run_dir)])
    payload = json.loads(result.output)
    written = json.loads(
        (run_dir / "skill-composition-redteam-receipt.json").read_text(
            encoding="utf-8"
        )
    )

    assert result.exit_code == 0
    assert payload == written
    assert payload["schema"] == SKILL_COMPOSITION_REDTEAM_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["passed_attempt_count"] == payload["attempt_count"]
    assert payload["proof_scope"]["does_not_prove"] == REQUIRED_UNPROVEN_CLAIMS


def test_skill_composition_redteam_checks_explicit_unproven_claims(
    tmp_path: Path,
) -> None:
    receipt = run_skill_composition_redteam(run_dir=tmp_path)

    assert REQUIRED_UNPROVEN_CLAIMS == [
        "Live skill execution.",
        "Provider/model semantic quality.",
        "Exhaustive skill attack coverage.",
        "Future route correctness.",
        "Skill output correctness without Tau adapter validation.",
    ]
    assert set(REQUIRED_UNPROVEN_CLAIMS).issubset(
        set(receipt["proof_scope"]["does_not_prove"])
    )
    assert all(
        _claim_is_not_in_proves(claim, receipt["proof_scope"]["proves"])
        for claim in REQUIRED_UNPROVEN_CLAIMS
    )


def _claim_is_not_in_proves(claim: str, proves: list[str]) -> bool:
    normalized_claim = claim.rstrip(".").lower()
    return all(normalized_claim not in prove.lower() for prove in proves)
