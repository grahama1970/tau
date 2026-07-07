import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.course_correction import (
    COURSE_CORRECTION_SCHEMA,
    build_course_correction_receipt,
    write_course_correction_receipt,
)


def test_course_correction_receipt_maps_receipt_timeout_to_bounded_retry() -> None:
    payload = build_course_correction_receipt(
        trigger="receipt_timeout",
        run_id="run-1",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        target={"repo": "grahama1970/tau", "target": "issue:63"},
        node_id="coder",
        agent="coder",
        attempt=2,
        observed_state={"receipt_missing": True},
        errors=["node_receipt_timeout: coder receipt did not appear before timeout"],
        live=True,
    )

    assert payload["schema"] == COURSE_CORRECTION_SCHEMA
    assert payload["ok"] is False
    assert payload["status"] == "REQUIRED"
    assert payload["next_allowed"] is False
    assert payload["trigger"] == "receipt_timeout"
    assert payload["required_next_action"] == "retry_node_or_route_goal_guardian"
    assert payload["allowed_next_routes"] == ["retry_node", "goal-guardian", "human"]
    assert "continue_without_bound_receipt" in payload["forbidden_next_routes"]
    assert payload["required_evidence_before_retry"] == [
        "fresh_work_order",
        "node_receipt_or_timeout_diagnostics",
    ]
    assert payload["proof_scope"]["does_not_prove"] == [
        "The agent is truthful.",
        "The task is complete.",
        "The proposed correction is semantically sufficient.",
        "The required next action has been executed.",
    ]


def test_course_correction_receipt_preserves_legacy_required_action_fields(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "course-correction.json"
    payload = write_course_correction_receipt(
        receipt_path,
        trigger="brave_search_required_after_two_attempts",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=2,
        required_action={
            "skill": "brave-search",
            "skill_reference": "$brave-search",
            "query": "tau receipt timeout",
        },
        blocked_report_required={"required": True, "fields": ["blocker_summary"]},
    )

    on_disk = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert on_disk == payload
    assert on_disk["code"] == "brave_search_required_after_two_attempts"
    assert on_disk["required_action"]["skill_reference"] == "$brave-search"
    assert on_disk["blocked_report_required"]["required"] is True
    assert on_disk["required_next_action"] == "run_brave_search_then_retry"
    assert "retry_without_research_receipt" in on_disk["forbidden_next_routes"]


def test_course_correction_created_for_stale_patch() -> None:
    payload = build_course_correction_receipt(
        trigger="patch_stale",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=1,
    )

    assert payload["schema"] == COURSE_CORRECTION_SCHEMA
    assert payload["trigger"] == "patch_stale"
    assert payload["required_next_action"] == "retry_node"
    assert "fresh_code_patch_receipt" in payload["required_evidence_before_retry"]
    assert "apply_unvalidated_patch" in payload["forbidden_next_routes"]


def test_course_correction_records_observed_artifact_descriptor(tmp_path: Path) -> None:
    observed = tmp_path / "stale-code-patch-receipt.json"
    observed.write_text(
        json.dumps({"schema": "tau.code_patch_receipt.v1", "status": "BLOCKED"}) + "\n",
        encoding="utf-8",
    )

    payload = build_course_correction_receipt(
        trigger="patch_stale",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=1,
        observed_artifact_path=observed,
    )

    assert payload["observed_artifact"] == {
        "label": "observed_evidence",
        "path": str(observed.resolve()),
        "exists": True,
        "sha256": f"sha256:{_sha256_file(observed)}",
        "bytes": observed.stat().st_size,
    }


def test_course_correction_missing_goal_hash_is_invalid_input() -> None:
    payload = build_course_correction_receipt(
        trigger="patch_stale",
        dag_id="dag-1",
        node_id="coder",
        agent="coder",
        attempt=1,
    )

    assert payload["input_valid"] is False
    assert "missing_goal_hash" in payload["alert_codes"]


def test_course_correction_created_for_reviewer_p0() -> None:
    payload = build_course_correction_receipt(
        trigger="reviewer_p0",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="reviewer",
        agent="reviewer",
        attempt=1,
    )

    assert payload["trigger"] == "reviewer_p0"
    assert payload["required_next_action"] == "route_goal_guardian"
    assert "structured_review_findings" in payload["required_evidence_before_retry"]
    assert "claim_pass_without_resolving_p0" in payload["forbidden_next_routes"]


def test_course_correction_created_for_worker_forbidden_path() -> None:
    payload = build_course_correction_receipt(
        trigger="worker_changed_forbidden_path",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="omp-worker",
        agent="omp",
        attempt=1,
    )

    assert payload["required_next_action"] == "route_goal_guardian"
    assert "forbidden_path_diff" in payload["required_evidence_before_retry"]
    assert "accept_worker_result" in payload["forbidden_next_routes"]


def test_course_correction_forbids_same_context_after_two_failures() -> None:
    payload = build_course_correction_receipt(
        trigger="test_failed_twice",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=2,
    )

    assert payload["required_next_action"] == "route_reviewer"
    assert payload["input_valid"] is True
    assert payload["alert_codes"] == []
    assert "retry_same_context" in payload["forbidden_next_routes"]
    assert "test_failure_receipt" in payload["required_evidence_before_retry"]


def test_course_correction_created_for_provider_crashed() -> None:
    payload = build_course_correction_receipt(
        trigger="provider_crashed",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=1,
        observed_state={"provider_state": "crashed"},
        live=True,
        provider_live=True,
    )

    assert payload["trigger"] == "provider_crashed"
    assert payload["required_next_action"] == "retry_node_or_route_goal_guardian"
    assert payload["live"] is True
    assert payload["provider_live"] is True
    assert "continue_without_bound_receipt" in payload["forbidden_next_routes"]
    assert (
        "node_receipt_or_timeout_diagnostics"
        in payload["required_evidence_before_retry"]
    )


def test_course_correction_created_for_herdr_stale() -> None:
    payload = build_course_correction_receipt(
        trigger="herdr_stale",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=1,
        observed_state={"pane_age_seconds": 901, "receipt_missing": True},
        live=True,
    )

    assert payload["trigger"] == "herdr_stale"
    assert payload["required_next_action"] == "send_reminder_or_route_human"
    assert "send_reminder" in payload["allowed_next_routes"]
    assert "start_parallel_duplicate_without_policy" in payload["forbidden_next_routes"]
    assert "herdr_monitor_snapshot" in payload["required_evidence_before_retry"]


def test_course_correction_after_two_attempts_warns_without_attempt_evidence() -> None:
    payload = build_course_correction_receipt(
        trigger="brave_search_required_after_two_attempts",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=1,
    )

    assert payload["required_next_action"] == "run_brave_search_then_retry"
    assert payload["input_valid"] is False
    assert "attempt_evidence_below_required_threshold" in payload["alert_codes"]
    assert payload["alerts"][0]["severity"] == "WARN"


def test_course_correction_after_two_attempts_accepts_observed_attempt_count() -> None:
    payload = build_course_correction_receipt(
        trigger="brave_search_required_after_two_attempts",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=1,
        observed_state={"attempt_count": 2},
    )

    assert payload["input_valid"] is True
    assert payload["alert_codes"] == []
    assert "brave_search_receipt" in payload["required_evidence_before_retry"]


def test_cli_course_correction_writes_project_agent_receipt(tmp_path: Path) -> None:
    receipt_path = tmp_path / "course-correction.json"

    result = CliRunner().invoke(
        app,
        [
            "course-correction",
            "--trigger",
            "provider_auth_required",
            "--out",
            str(receipt_path),
            "--run-id",
            "run-1",
            "--dag-id",
            "dag-1",
            "--goal-hash",
            "sha256:goal",
            "--target-json",
            '{"repo":"grahama1970/tau","target":"issue:63"}',
            "--node-id",
            "coder",
            "--agent",
            "coder",
            "--attempt",
            "1",
            "--observed-state-json",
            '{"herdr_state":"auth_required"}',
            "--observed-artifact",
            str(receipt_path),
            "--error",
            "provider requested auth",
            "--live",
            "--provider-live",
        ],
    )

    payload = json.loads(result.output)
    on_disk = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result.exit_code == 1
    assert payload == on_disk
    assert payload["schema"] == COURSE_CORRECTION_SCHEMA
    assert payload["trigger"] == "provider_auth_required"
    assert payload["required_next_action"] == "route_human"
    assert payload["allowed_next_routes"] == ["human"]
    assert payload["observed_state"] == {"herdr_state": "auth_required"}
    assert payload["observed_artifact"]["path"] == str(receipt_path.resolve())
    assert payload["observed_artifact"]["exists"] is False
    assert payload["live"] is True
    assert payload["provider_live"] is True


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
