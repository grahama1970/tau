import json
from pathlib import Path

from tau_coding.skill_composition_redteam import (
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
    on_disk = json.loads(
        (tmp_path / "skill-composition-redteam-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert on_disk == receipt
