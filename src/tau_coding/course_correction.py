"""Course-correction receipts for blocked or drifting Tau orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COURSE_CORRECTION_SCHEMA = "tau.course_correction.v1"
CODING_COURSE_CORRECTION_TRIGGERS = frozenset(
    {
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
)
KNOWN_COURSE_CORRECTION_TRIGGERS = frozenset(
    {
        *CODING_COURSE_CORRECTION_TRIGGERS,
        "brave_search_required_after_two_attempts",
        "goal_hash_mismatch",
        "herdr_binding_mismatch",
        "human_required",
        "invalid_receipt",
        "missing_evidence",
        "pointless_unit_test_drift",
        "provider_auth_required",
        "provider_interstitial",
        "receipt_timeout_after_visible_dispatch",
        "research_required_before_retry",
        "reviewer_revise",
        "test_churn_without_progress",
        "two_failed_attempts",
        "unexpected_edge",
        "unexpected_route",
    }
)


def build_course_correction_receipt(
    *,
    trigger: str,
    run_id: str | None = None,
    dag_id: str | None = None,
    goal_hash: str | None = None,
    target: dict[str, Any] | None = None,
    node_id: str | None = None,
    agent: str | None = None,
    attempt: int | None = None,
    observed_state: dict[str, Any] | None = None,
    observed_artifact_path: Path | None = None,
    errors: list[str] | None = None,
    reason: str | None = None,
    stop_reason: str | None = None,
    required_action: dict[str, Any] | None = None,
    blocked_report_required: dict[str, Any] | None = None,
    mocked: bool = False,
    live: bool = False,
    provider_live: bool = False,
) -> dict[str, Any]:
    """Build a normalized course-correction receipt.

    The receipt intentionally describes the next safe orchestration action. It
    does not claim task success or agent truthfulness.
    """

    normalized_trigger = _normalize_trigger(trigger)
    policy = _policy_for_trigger(normalized_trigger)
    alerts = _input_alerts_for_trigger(
        trigger=normalized_trigger,
        goal_hash=goal_hash,
        node_id=node_id,
        agent=agent,
        attempt=attempt,
        observed_state=observed_state or {},
    )
    payload: dict[str, Any] = {
        "schema": COURSE_CORRECTION_SCHEMA,
        "ok": False,
        "status": "REQUIRED",
        "next_allowed": False,
        "input_valid": not alerts,
        "code": normalized_trigger,
        "trigger": normalized_trigger,
        "known_coding_trigger": normalized_trigger in CODING_COURSE_CORRECTION_TRIGGERS,
        "mocked": mocked,
        "live": live,
        "provider_live": provider_live,
        "run_id": run_id,
        "dag_id": dag_id,
        "goal_hash": goal_hash,
        "target": target or {},
        "node_id": node_id,
        "agent": agent,
        "attempt": attempt,
        "stop_reason": stop_reason or normalized_trigger,
        "reason": reason or policy["reason"],
        "observed_state": observed_state or {},
        "observed_artifact": _artifact_descriptor(
            "observed_evidence",
            observed_artifact_path.expanduser().resolve()
            if observed_artifact_path is not None
            else None,
        ),
        "why_normal_retry_is_unsafe": policy["why_normal_retry_is_unsafe"],
        "required_next_action": policy["required_next_action"],
        "allowed_next_routes": list(policy["allowed_next_routes"]),
        "forbidden_next_routes": list(policy["forbidden_next_routes"]),
        "required_evidence_before_retry": list(policy["required_evidence_before_retry"]),
        "errors": errors or [],
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": {
            "proves": [
                "Tau classified a blocked or drifting orchestration state.",
                "Tau selected a bounded next action from the course-correction policy.",
                "Tau did not mutate the DAG, goal, route, work order, or provider state.",
            ],
            "does_not_prove": [
                "The agent is truthful.",
                "The task is complete.",
                "The proposed correction is semantically sufficient.",
                "The required next action has been executed.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    if required_action is not None:
        payload["required_action"] = required_action
    if blocked_report_required is not None:
        payload["blocked_report_required"] = blocked_report_required
    return payload


def write_course_correction_receipt(
    output_path: Path,
    *,
    trigger: str,
    run_id: str | None = None,
    dag_id: str | None = None,
    goal_hash: str | None = None,
    target: dict[str, Any] | None = None,
    node_id: str | None = None,
    agent: str | None = None,
    attempt: int | None = None,
    observed_state: dict[str, Any] | None = None,
    observed_artifact_path: Path | None = None,
    errors: list[str] | None = None,
    reason: str | None = None,
    stop_reason: str | None = None,
    required_action: dict[str, Any] | None = None,
    blocked_report_required: dict[str, Any] | None = None,
    mocked: bool = False,
    live: bool = False,
    provider_live: bool = False,
) -> dict[str, Any]:
    payload = build_course_correction_receipt(
        trigger=trigger,
        run_id=run_id,
        dag_id=dag_id,
        goal_hash=goal_hash,
        target=target,
        node_id=node_id,
        agent=agent,
        attempt=attempt,
        observed_state=observed_state,
        observed_artifact_path=observed_artifact_path,
        errors=errors,
        reason=reason,
        stop_reason=stop_reason,
        required_action=required_action,
        blocked_report_required=blocked_report_required,
        mocked=mocked,
        live=live,
        provider_live=provider_live,
    )
    resolved = output_path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _policy_for_trigger(trigger: str) -> dict[str, Any]:
    if trigger in {"goal_hash_mismatch", "unexpected_route", "unexpected_edge"}:
        return _policy(
            "route_goal_guardian",
            "Reconcile route or immutable-goal drift before another normal attempt.",
            ["goal-guardian", "human"],
            ["retry_same_context", "route_implementation_agent"],
            ["goal_guardian_reconciliation_receipt"],
        )
    if trigger in {"reviewer_revise", "reviewer_p1", "missing_evidence"}:
        return _policy(
            "route_reviewer",
            "Reviewer or evidence gate found unresolved required evidence.",
            ["reviewer", "goal-guardian", "human"],
            ["claim_pass_without_evidence"],
            ["reviewer_verdict", "required_evidence_receipt"],
        )
    if trigger == "reviewer_p0":
        return _policy(
            "route_goal_guardian",
            "Reviewer reported a P0 finding; normal implementation retry may bypass "
            "a blocking defect.",
            ["goal-guardian", "human"],
            ["retry_same_context", "claim_pass_without_resolving_p0"],
            ["structured_review_findings", "blocking_finding_resolution_plan"],
        )
    if trigger in {"patch_stale", "patch_failed"}:
        return _policy(
            "retry_node",
            "The proposed code patch was stale or failed deterministic patch validation.",
            ["retry_node", "reviewer", "goal-guardian"],
            ["apply_unvalidated_patch", "claim_progress_from_failed_patch"],
            ["fresh_code_patch_receipt", "current_file_sha256"],
        )
    if trigger in {"lsp_diagnostics_regressed", "debugger_evidence_required"}:
        return _policy(
            "debug_or_route_reviewer",
            "Coding evidence regressed or debugger evidence is required before another "
            "implementation attempt.",
            ["debug", "reviewer", "goal-guardian", "human"],
            ["retry_without_diagnostics", "claim_pass_with_regressed_diagnostics"],
            ["diagnostics_receipt", "debug_session_receipt"],
        )
    if trigger == "worker_changed_forbidden_path":
        return _policy(
            "route_goal_guardian",
            "The coding worker touched a forbidden path, so the result must be quarantined.",
            ["goal-guardian", "human"],
            ["accept_worker_result", "route_reviewer_as_if_valid"],
            ["worker_receipt", "forbidden_path_diff", "quarantine_receipt"],
        )
    if trigger == "worker_result_missing":
        return _policy(
            "retry_node_or_route_goal_guardian",
            "The coding worker did not return a structured result artifact.",
            ["retry_node", "goal-guardian", "human"],
            ["parse_worker_prose", "continue_without_worker_receipt"],
            ["worker_stdout_stderr", "fresh_worker_work_order"],
        )
    if trigger == "test_failed_twice":
        return _policy(
            "route_reviewer",
            "The same test failure survived two attempts; normal retry risks churn.",
            ["reviewer", "debug", "goal-guardian", "human"],
            ["retry_same_context", "run_more_unrelated_tests"],
            ["test_failure_receipt", "debug_or_review_plan"],
        )
    if trigger in {"receipt_timeout", "invalid_receipt", "provider_crashed"}:
        return _policy(
            "retry_node_or_route_goal_guardian",
            "The node did not produce an admissible receipt, so blind continuation is unsafe.",
            ["retry_node", "goal-guardian", "human"],
            ["continue_without_bound_receipt"],
            ["fresh_work_order", "node_receipt_or_timeout_diagnostics"],
        )
    if trigger in {"provider_auth_required", "provider_interstitial"}:
        return _policy(
            "route_human",
            "Provider state requires explicit operator action before continuation.",
            ["human"],
            ["retry_same_context", "send_provider_prompt"],
            ["operator_action_receipt"],
        )
    if trigger in {"herdr_stale", "receipt_timeout_after_visible_dispatch"}:
        return _policy(
            "send_reminder_or_route_human",
            "Herdr-visible work is stale or overdue; normal retry may duplicate work.",
            ["send_reminder", "human", "goal-guardian"],
            ["start_parallel_duplicate_without_policy"],
            ["herdr_monitor_snapshot", "visible_log_excerpt"],
        )
    if trigger == "herdr_binding_mismatch":
        return _policy(
            "block_run",
            "Herdr workspace, pane, or terminal identity does not match the dispatched work.",
            ["goal-guardian", "human"],
            ["continue_with_unbound_pane", "accept_unbound_receipt"],
            ["fresh_herdr_snapshot", "work_order_binding_receipt"],
        )
    if trigger in {"pointless_unit_test_drift", "test_churn_without_progress"}:
        return _policy(
            "stop_test_churn_report_blocker_and_replan",
            "The node is spending attempts on checks without producing task evidence.",
            ["reviewer", "goal-guardian", "human"],
            ["run_more_unrelated_tests", "claim_progress_from_test_churn"],
            ["blocked_report", "next_non_test_action"],
        )
    if trigger in {"brave_search_required_after_two_attempts", "research_required_before_retry"}:
        return _policy(
            "run_brave_search_then_retry",
            "The same blocker survived repeated attempts; external research is required.",
            ["goal-guardian", "research-auditor", "human"],
            ["retry_without_research_receipt"],
            ["brave_search_receipt", "blocked_report"],
        )
    if trigger == "two_failed_attempts":
        return _policy(
            "route_reviewer_or_debug",
            "The same coding node failed twice; another same-context retry risks churn.",
            ["reviewer", "debug", "goal-guardian", "human"],
            ["retry_same_context", "run_more_unrelated_tests"],
            ["two_attempt_failure_receipt", "replan_or_debug_receipt"],
        )
    if trigger == "human_required":
        return _policy(
            "route_human",
            "Retry budget or policy requires human review before more work.",
            ["human", "goal-guardian"],
            ["retry_same_context"],
            ["human_or_goal_guardian_decision"],
        )
    return _policy(
        "block_run",
        "Unhandled course-correction trigger requires explicit review.",
        ["human", "goal-guardian"],
        ["continue_normally"],
        ["blocker_summary"],
    )


def _policy(
    required_next_action: str,
    reason: str,
    allowed_next_routes: list[str],
    forbidden_next_routes: list[str],
    required_evidence_before_retry: list[str],
) -> dict[str, Any]:
    return {
        "required_next_action": required_next_action,
        "reason": reason,
        "why_normal_retry_is_unsafe": reason,
        "allowed_next_routes": allowed_next_routes,
        "forbidden_next_routes": forbidden_next_routes,
        "required_evidence_before_retry": required_evidence_before_retry,
    }


def _normalize_trigger(trigger: str) -> str:
    normalized = trigger.strip().lower().replace("-", "_")
    return normalized or "unknown"


def _input_alerts_for_trigger(
    *,
    trigger: str,
    goal_hash: str | None,
    node_id: str | None,
    agent: str | None,
    attempt: int | None,
    observed_state: dict[str, Any],
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    if trigger not in KNOWN_COURSE_CORRECTION_TRIGGERS:
        alerts.append(
            {
                "severity": "BLOCK",
                "code": "unsupported_course_correction_trigger",
                "message": f"unsupported course-correction trigger: {trigger}",
            }
        )
    if not goal_hash:
        alerts.append(
            {
                "severity": "BLOCK",
                "code": "missing_goal_hash",
                "message": "course correction requires goal_hash",
            }
        )
    if trigger in CODING_COURSE_CORRECTION_TRIGGERS:
        if _missing_text(node_id):
            alerts.append(
                {
                    "severity": "BLOCK",
                    "code": "missing_node_id",
                    "message": "coding course correction requires node_id attribution",
                }
            )
        if _missing_text(agent):
            alerts.append(
                {
                    "severity": "BLOCK",
                    "code": "missing_agent",
                    "message": "coding course correction requires agent attribution",
                }
            )
        if not isinstance(attempt, int) or attempt < 1:
            alerts.append(
                {
                    "severity": "BLOCK",
                    "code": "missing_attempt",
                    "message": "coding course correction requires attempt>=1",
                }
            )
    if trigger not in {
        "brave_search_required_after_two_attempts",
        "test_failed_twice",
        "two_failed_attempts",
    }:
        return alerts
    observed_attempt = observed_state.get("attempt_count")
    effective_attempt = attempt
    if isinstance(observed_attempt, int) and (
        effective_attempt is None or observed_attempt > effective_attempt
    ):
        effective_attempt = observed_attempt
    if effective_attempt is None or effective_attempt < 2:
        alerts.append(
            {
                "severity": "WARN",
                "code": "attempt_evidence_below_required_threshold",
                "message": (
                    f"{trigger} course correction requires attempt>=2 or "
                    "observed_state.attempt_count>=2"
                ),
            }
        )
    return alerts


def _missing_text(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


def _artifact_descriptor(label: str, path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return {
        "label": label,
        "path": str(path),
        "exists": path.exists(),
        "sha256": _artifact_sha256_uri(path),
        "bytes": _artifact_size(path),
    }


def _artifact_sha256_uri(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    except OSError:
        return None


def _artifact_size(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
