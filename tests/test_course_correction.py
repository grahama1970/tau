import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.course_correction import (
    CODING_COURSE_CORRECTION_TRIGGERS,
    COURSE_CORRECTION_SCHEMA,
    build_course_correction_receipt,
    resolve_course_correction_skill_routes,
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


def test_coding_course_correction_missing_node_id_is_invalid_input() -> None:
    payload = build_course_correction_receipt(
        trigger="patch_stale",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        agent="coder",
        attempt=1,
    )

    assert payload["input_valid"] is False
    assert "missing_node_id" in payload["alert_codes"]


def test_coding_course_correction_missing_agent_is_invalid_input() -> None:
    payload = build_course_correction_receipt(
        trigger="patch_stale",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        attempt=1,
    )

    assert payload["input_valid"] is False
    assert "missing_agent" in payload["alert_codes"]


def test_coding_course_correction_missing_attempt_is_invalid_input() -> None:
    payload = build_course_correction_receipt(
        trigger="patch_stale",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
    )

    assert payload["input_valid"] is False
    assert "missing_attempt" in payload["alert_codes"]


def test_coding_course_correction_zero_attempt_is_invalid_input() -> None:
    payload = build_course_correction_receipt(
        trigger="patch_stale",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=0,
    )

    assert payload["input_valid"] is False
    assert "missing_attempt" in payload["alert_codes"]


def test_course_correction_blocks_unsupported_trigger_as_invalid_input() -> None:
    payload = build_course_correction_receipt(
        trigger="invented_retry_reason",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=1,
    )

    assert payload["trigger"] == "invented_retry_reason"
    assert payload["required_next_action"] == "block_run"
    assert payload["input_valid"] is False
    assert "unsupported_course_correction_trigger" in payload["alert_codes"]
    assert payload["forbidden_next_routes"] == ["continue_normally"]


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


def test_course_correction_supports_all_required_coding_triggers() -> None:
    expected = {
        "patch_stale",
        "patch_failed",
        "lsp_diagnostics_regressed",
        "reviewer_p0",
        "reviewer_p1",
        "test_failed_twice",
        "debugger_evidence_required",
        "worker_result_missing",
        "worker_changed_forbidden_path",
        "receipt_timeout",
        "provider_crashed",
        "herdr_stale",
    }

    assert expected == CODING_COURSE_CORRECTION_TRIGGERS
    for trigger in sorted(expected):
        payload = build_course_correction_receipt(
            trigger=trigger,
            dag_id="dag-1",
            goal_hash="sha256:goal",
            node_id="coder",
            agent="coder",
            attempt=2,
            observed_state={"attempt_count": 2},
        )

        assert payload["known_coding_trigger"] is True
        assert payload["required_next_action"] != "block_run"
        assert payload["forbidden_next_routes"] != ["continue_normally"]
        assert payload["required_evidence_before_retry"] != ["blocker_summary"]
        assert payload["alert_codes"] == []


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


def test_course_correction_two_failed_attempts_routes_reviewer_or_debug() -> None:
    payload = build_course_correction_receipt(
        trigger="two_failed_attempts",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=2,
    )

    assert payload["required_next_action"] == "route_reviewer_or_debug"
    assert payload["allowed_next_routes"] == ["reviewer", "debug", "goal-guardian", "human"]
    assert "retry_same_context" in payload["forbidden_next_routes"]
    assert "run_more_unrelated_tests" in payload["forbidden_next_routes"]
    assert payload["required_evidence_before_retry"] == [
        "two_attempt_failure_receipt",
        "replan_or_debug_receipt",
    ]
    assert payload["alert_codes"] == []


def test_course_correction_two_failed_attempts_warns_without_attempt_evidence() -> None:
    payload = build_course_correction_receipt(
        trigger="two_failed_attempts",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=1,
    )

    assert payload["required_next_action"] == "route_reviewer_or_debug"
    assert payload["input_valid"] is False
    assert "attempt_evidence_below_required_threshold" in payload["alert_codes"]


def test_course_correction_maps_debug_to_debugger_skill() -> None:
    payload = build_course_correction_receipt(
        trigger="debugger_evidence_required",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=1,
        capability_registry=_registry(),
        capability_providers=_capability_providers(),
    )

    assert payload["skill_routes"]["status"] == "PASS"
    assert {
        route["capability"]: route["skill"] for route in payload["skill_routes"]["routes"]
    } == {
        "debug_runtime_state": "debugger",
        "code_review": "review-code",
    }


def test_course_correction_maps_review_to_review_code_skill() -> None:
    payload = build_course_correction_receipt(
        trigger="reviewer_revise",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="reviewer",
        agent="reviewer",
        attempt=1,
    )

    routes = resolve_course_correction_skill_routes(
        payload,
        capability_registry=_registry(),
        capability_providers=_capability_providers(),
    )

    assert routes["status"] == "PASS"
    assert routes["routes"][0]["capability"] == "code_review"
    assert routes["routes"][0]["skill"] == "review-code"


def test_course_correction_maps_research_to_dogpile_skill() -> None:
    payload = build_course_correction_receipt(
        trigger="brave_search_required_after_two_attempts",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=2,
    )

    routes = resolve_course_correction_skill_routes(
        payload,
        capability_registry=_registry(),
        capability_providers=_capability_providers(),
    )

    assert routes["status"] == "PASS"
    assert routes["routes"][0]["capability"] == "deep_research"
    assert routes["routes"][0]["skill"] == "dogpile"
    assert routes["routes"][0]["pre_gate"] == "tau.research_query_safety_receipt.v1"


def test_course_correction_blocks_when_required_skill_missing() -> None:
    payload = build_course_correction_receipt(
        trigger="brave_search_required_after_two_attempts",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=2,
        capability_registry=_registry(),
        capability_providers={"code_review": "review-code"},
    )

    assert payload["skill_routes"]["status"] == "BLOCKED"
    assert payload["input_valid"] is False
    assert "skill_capability_route_unavailable" in payload["alert_codes"]
    assert "required skill capability missing: deep_research" in payload["skill_routes"]["errors"]


def test_course_correction_allows_available_alternative_skill() -> None:
    payload = build_course_correction_receipt(
        trigger="debugger_evidence_required",
        dag_id="dag-1",
        goal_hash="sha256:goal",
        node_id="coder",
        agent="coder",
        attempt=1,
        capability_registry=_registry(),
        capability_providers={"debug_runtime_state": "debugger"},
    )

    assert payload["skill_routes"]["status"] == "PASS"
    assert payload["skill_routes"]["route_count"] == 1
    assert payload["skill_routes"]["routes"][0]["skill"] == "debugger"
    assert "skill_capability_route_unavailable" not in payload["alert_codes"]


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
    assert payload["required_next_action"] == "repair_provider_auth_then_retry_or_route_human"
    assert payload["allowed_next_routes"] == ["auth-repair", "provider-readiness", "human"]
    assert "regenerate_artifacts_before_auth_repair" in payload["forbidden_next_routes"]
    assert payload["required_evidence_before_retry"] == [
        "provider_auth_repair_receipt",
        "provider_readiness_receipt",
    ]
    assert payload["observed_state"] == {"herdr_state": "auth_required"}
    assert payload["observed_artifact"]["path"] == str(receipt_path.resolve())
    assert payload["observed_artifact"]["exists"] is False
    assert payload["live"] is True
    assert payload["provider_live"] is True


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capability_providers() -> dict[str, str]:
    return {
        "debug_runtime_state": "debugger",
        "bounded_code_fix": "code-runner",
        "code_review": "review-code",
        "deep_research": "dogpile",
        "evidence_case": "create-evidence-case",
        "model_worker": "scillm",
    }


def _registry() -> dict:
    return {
        "schema": "tau.skill_capability_registry.v1",
        "capabilities": {
            "debug_runtime_state": {
                "skill": "debugger",
                "native_artifact_schema": "debugger.proof.v1",
                "tau_receipt_schema": "tau.debug_session_receipt.v1",
            },
            "bounded_code_fix": {
                "skill": "code-runner",
                "native_artifact_schema": "code_runner.result.v1",
                "tau_receipt_schema": "tau.code_patch_receipt.v1",
            },
            "code_review": {
                "skill": "review-code",
                "native_artifact_schema": "review_result.json",
                "tau_receipt_schema": "tau.review_findings.v1",
            },
            "deep_research": {
                "skill": "dogpile",
                "native_artifact_schema": "dogpile.report.v1",
                "pre_gate": "tau.research_query_safety_receipt.v1",
                "tau_receipt_schema": "tau.research_source_receipt.v1",
            },
            "evidence_case": {
                "skill": "create-evidence-case",
                "native_artifact_schema": "create_evidence_case.result.v1",
                "tau_receipt_schema": "tau.evidence_case_gate_receipt.v1",
            },
            "model_worker": {
                "skill": "scillm",
                "native_artifact_schema": "scillm.worker_result.v1",
                "tau_receipt_schema": "tau.scillm_worker_receipt.v1",
            },
        },
    }
