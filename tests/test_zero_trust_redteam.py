from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.zero_trust_redteam import ZERO_TRUST_REDTEAM_RECEIPT_SCHEMA, run_zero_trust_redteam


def test_zero_trust_redteam_requires_expected_fail_closed_alerts(tmp_path: Path) -> None:
    receipt = run_zero_trust_redteam(run_dir=tmp_path / "redteam")

    assert receipt["schema"] == ZERO_TRUST_REDTEAM_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["attempt_count"] == 6
    assert receipt["passed_attempt_count"] == 6
    attempts = {attempt["attempt_id"]: attempt for attempt in receipt["attempts"]}
    assert attempts["controlled_snippet_in_research_query"]["expected_alert_code"] == (
        "controlled_artifact_snippet_in_query"
    )
    assert attempts["foreign_person_actor_for_itar_boundary"]["expected_alert_code"] == (
        "foreign_person_actor_blocked"
    )
    assert attempts["unverified_human_approval"]["expected_alert_code"] == "actor_not_verified"
    assert attempts["cloud_provider_requested_for_itar"]["expected_alert_code"] == (
        "external_provider_denied"
    )
    assert attempts["public_mutation_for_itar_boundary"]["expected_alert_code"] == (
        "public_repo_denied"
    )
    assert attempts["unsigned_critical_receipt"]["expected_alert_code"] == (
        "critical_receipt_not_pass"
    )
    assert all(attempt["status"] == "PASS" for attempt in receipt["attempts"])
    assert "ITAR compliance." in receipt["proof_scope"]["does_not_prove"]


def test_cli_zero_trust_redteam_writes_receipt(tmp_path: Path) -> None:
    run_dir = tmp_path / "cli-redteam"

    result = CliRunner().invoke(app, ["zero-trust-redteam", "--run-dir", str(run_dir)])
    payload = json.loads(result.output)
    written = json.loads((run_dir / "zero-trust-redteam-receipt.json").read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert payload == written
    assert payload["schema"] == ZERO_TRUST_REDTEAM_RECEIPT_SCHEMA
    assert payload["status"] == "PASS"
    assert payload["passed_attempt_count"] == payload["attempt_count"]
