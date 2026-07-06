from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.orchestration_redteam import (
    ORCHESTRATION_REDTEAM_SCHEMA,
    run_orchestration_redteam,
)


def test_orchestration_redteam_requires_expected_fail_closed_receipts(tmp_path: Path) -> None:
    receipt = run_orchestration_redteam(run_dir=tmp_path / "redteam")

    assert receipt["schema"] == ORCHESTRATION_REDTEAM_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["attempt_count"] == 8
    assert receipt["passed_attempt_count"] == 8
    attempts = {attempt["attempt_id"]: attempt for attempt in receipt["attempts"]}
    assert attempts["herdr_stale"]["observed_trigger"] == "herdr_stale"
    assert attempts["provider_auth_required"]["observed_trigger"] == "provider_auth_required"
    assert attempts["provider_interstitial"]["observed_trigger"] == "provider_interstitial"
    assert attempts["provider_crashed"]["observed_trigger"] == "provider_crashed"
    assert attempts["receipt_timeout"]["observed_trigger"] == "receipt_timeout"
    assert attempts["provider_receipt_wrong_pane"]["observed_trigger"] == (
        "herdr_binding_mismatch"
    )
    assert attempts["blocked_run_without_course_correction"]["expected_alert_code"] == (
        "blocked_without_course_correction"
    )
    assert attempts["unhandled_herdr_observation_block"]["expected_alert_code"] == (
        "unhandled_herdr_observation_block"
    )
    assert all(attempt["status"] == "PASS" for attempt in receipt["attempts"])
    assert "A course-correction action was executed." in receipt["proof_scope"]["does_not_prove"]


def test_cli_orchestration_redteam_writes_receipt(tmp_path: Path) -> None:
    run_dir = tmp_path / "cli-redteam"

    result = CliRunner().invoke(app, ["orchestration-redteam", "--run-dir", str(run_dir)])
    payload = json.loads(result.output)
    written = json.loads(
        (run_dir / "orchestration-redteam-receipt.json").read_text(encoding="utf-8")
    )

    assert result.exit_code == 0
    assert payload == written
    assert payload["schema"] == ORCHESTRATION_REDTEAM_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["passed_attempt_count"] == payload["attempt_count"]
