"""Generic receipt-gated DAG runner for Tau orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from tau_coding.approval_gate import evaluate_approval_gate
from tau_coding.browser_cdp_proof import (
    BROWSER_DAG_RECEIPT_SCHEMA,
    BrowserDagSpec,
    execute_browser_dag_node,
    parse_browser_dag_spec,
)
from tau_coding.course_correction import write_course_correction_receipt
from tau_coding.dag_runtime.admission import write_durable_json
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.run_store import DagRunLease, SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.dag_viewer.source_artifact import write_dag_source_artifact
from tau_coding.diagnostics import configure_dag_logging, tau_logger
from tau_coding.generic_artifact_transaction import (
    TRANSACTION_RECEIPT_SCHEMA,
    ArtifactTransactionSpec,
    accepted_projection,
    canonical_command_sha256,
    file_sha256,
    load_json,
    parse_transaction_spec,
    revalidate_accepted_manifest,
    validate_acceptance_policy,
    validate_candidate_manifest,
    validate_review_feedback,
    write_accepted_manifest,
    write_attempt_context,
    write_json,
    write_review_context,
)
from tau_coding.public_dag_contracts import (
    strict_non_empty_string,
    strict_optional_path,
    strict_positive_int,
    strict_positive_number,
    validate_generic_dag_public_boundary,
)
from tau_coding.runtime_backends.local import (
    LocalRuntimeBackend,
    LocalRuntimeExecutionResult,
    local_runtime_request,
)
from tau_coding.skill_dag_adapter import (
    SkillDagSpec,
    execute_skill_dag_node,
    parse_skill_dag_spec,
)

try:
    import yaml
except ImportError:  # pragma: no cover - only in stripped runtime environments.
    yaml = None  # type: ignore[assignment]

GENERIC_DAG_SPEC_SCHEMA = "tau.generic_dag_spec.v1"
GENERIC_DAG_RUN_RECEIPT_SCHEMA = "tau.generic_dag_run_receipt.v1"
GENERIC_DAG_NODE_RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"
GENERIC_DAG_EVENT_SCHEMA = "tau.generic_dag_event.v1"
GENERIC_DAG_CHECKPOINT_SCHEMA = "tau.generic_dag_checkpoint.v1"
GENERIC_DAG_COST_ACCOUNTING_SCHEMA = "tau.generic_dag_cost_accounting.v1"


@dataclass(frozen=True)
class DagNode:
    node_id: str
    role: str
    command: tuple[str, ...]
    depends_on: tuple[str, ...]
    accepted_context_from: tuple[str, ...]
    receipt_path: Path
    timeout_seconds: float
    max_attempts: int
    work_order_path: Path | None
    transaction: ArtifactTransactionSpec | None
    skill: SkillDagSpec | None
    browser: BrowserDagSpec | None


def run_generic_dag(
    *,
    spec_path: Path,
    resume: bool = True,
    resume_source: dict[str, Any] | None = None,
    diagnostic_step_delay_seconds: float = 0.0,
    diagnostic_fault_injector: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute a schema-valid, command-backed DAG spec.

    This runner intentionally uses local subprocess workers. Provider-specific
    adapters such as Herdr/Codex/OpenCode should generate commands or wrap this
    scheduler rather than changing the scheduler's receipt contract.
    """

    if diagnostic_step_delay_seconds < 0:
        raise ValueError("diagnostic_step_delay_seconds_must_be_non_negative")
    resolved_spec_path = spec_path.expanduser().resolve()
    spec = load_generic_dag_spec(resolved_spec_path)
    nodes = validate_generic_dag_spec(spec, source_path=resolved_spec_path)
    run_dir = Path(str(spec["run_dir"])).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_log_path = configure_dag_logging(run_dir)
    tau_logger(
        run_id=str(spec["run_id"]),
        spec_path=str(resolved_spec_path),
        run_dir=str(run_dir),
        diagnostics_log_path=str(diagnostics_log_path),
    ).info("generic_dag_run_configured")
    events_path = Path(str(spec.get("events_jsonl") or run_dir / "events.jsonl")).expanduser()
    if not events_path.is_absolute():
        events_path = run_dir / events_path
    events_path.parent.mkdir(parents=True, exist_ok=True)

    run_id = str(spec["run_id"])
    completed: set[str] = set()
    node_results: list[dict[str, Any]] = []
    checkpoint_path = run_dir / "checkpoint.json"
    current_state_path = run_dir / "current-state.json"
    nodes_by_id = nodes
    plan = compile_generic_dag_plan(spec, source_path=resolved_spec_path)
    plan_payload = plan.to_payload()
    profile_resolution = plan_payload["source_extensions"].get("execution_profile_resolution", {})
    goal_hash = _generic_goal_hash(spec)
    run_store_path = run_dir / "dag-run.sqlite3"
    budget = _dag_budget_from_spec(spec, run_dir=run_dir)
    active_lease: DagRunLease | None = None

    def initialize_run_artifacts(lease: DagRunLease) -> None:
        nonlocal active_lease
        active_lease = lease
        write_dag_source_artifact(
            source_payload=spec,
            source_schema=str(spec["schema"]),
            source_path=resolved_spec_path,
            run_dir=run_dir,
        )
        _append_event(
            events_path,
            "dag_started",
            {
                "run_id": run_id,
                "spec_path": str(resolved_spec_path),
                "resume": resume,
                "resume_source": resume_source,
            },
        )
        _write_checkpoint(
            path=checkpoint_path,
            current_state_path=current_state_path,
            run_id=run_id,
            spec_path=resolved_spec_path,
            run_dir=run_dir,
            events_path=events_path,
            nodes=nodes,
            node_results=node_results,
            completed=completed,
            status="RUNNING",
            verdict="RUNNING",
            active_node_id=None,
        )

    def record_transaction_progress(
        scheduler_attempt: int,
        node_id: str,
        attempt: int,
        phase: str,
        evidence: dict[str, Any],
    ) -> None:
        lease = active_lease
        if lease is None:
            raise RuntimeError("dag_diagnostic_event_lease_missing")
        payload = {
            "schema": "tau.dag_diagnostic_event.v1",
            "diagnostic_kind": "generic_artifact_transaction_progress",
            "node_id": node_id,
            "scheduler_attempt": scheduler_attempt,
            "attempt": attempt,
            "phase": phase,
            "evidence": evidence,
            "authority": "diagnostic_only",
        }
        try:
            with SqliteDagRunStore(run_store_path) as progress_store:
                progress_store.append_diagnostic_event(
                    lease,
                    event_key=(f"transaction:{node_id}:{scheduler_attempt}:{attempt}:{phase}"),
                    node_id=node_id,
                    payload=payload,
                )
        except Exception:
            # Transaction progress is diagnostic-only. Scheduler transitions and
            # committed receipts remain authoritative when this side channel fails.
            return
        if diagnostic_step_delay_seconds:
            time.sleep(diagnostic_step_delay_seconds)

    def execute_plan_node(
        plan_node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        node = nodes_by_id[plan_node.node_id]
        node_log = tau_logger(
            run_id=run_id,
            scheduler_run_id=execution.run_id,
            node_id=plan_node.node_id,
            attempt=execution.attempt,
            attempt_id=execution.attempt_id,
            idempotency_key=execution.idempotency_key,
            receipt_path=str(node.receipt_path),
        )
        node_log.info("generic_dag_node_started")
        legacy_context: tuple[Path, str] | None = None
        if (
            node.skill is None
            and node.browser is None
            and node.transaction is None
            and not (resume and node.receipt_path.exists())
        ):
            legacy_context = _write_legacy_node_context(
                node=node,
                run_id=run_id,
                run_dir=run_dir,
                accepted_inputs=list(accepted_inputs),
            )
            _append_legacy_node_dispatch_event(
                node,
                run_id=run_id,
                events_path=events_path,
                attempt=execution.attempt,
            )
        _write_checkpoint(
            path=checkpoint_path,
            current_state_path=current_state_path,
            run_id=run_id,
            spec_path=resolved_spec_path,
            run_dir=run_dir,
            events_path=events_path,
            nodes=nodes,
            node_results=node_results,
            completed=completed,
            status="RUNNING",
            verdict="RUNNING",
            active_node_id=plan_node.node_id,
        )
        try:
            result = _run_node(
                node,
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                resume=resume,
                accepted_inputs=list(accepted_inputs),
                goal_hash=goal_hash,
                scheduler_attempt=execution.attempt,
                runtime_identity={
                    "run_id": execution.run_id,
                    "plan_revision": plan.plan_sha256,
                    "dag_id": plan.plan_id,
                    "node_id": plan_node.node_id,
                    "attempt_id": execution.attempt_id,
                    "attempt": execution.attempt,
                    "execution_token": execution.idempotency_key,
                    "goal": goal_hash or plan.runtime_goal_hash,
                },
                cancel_event=execution.cancel_event,
                legacy_context=legacy_context,
                progress_sink=lambda node_id, attempt, phase, evidence: record_transaction_progress(
                    execution.attempt,
                    node_id,
                    attempt,
                    phase,
                    evidence,
                ),
            )
        except BaseException:
            node_log.exception("generic_dag_node_exception")
            raise
        result["cost_accounting"] = _node_cost_accounting(result)
        budget_blocker = _cost_budget_blocker(
            budget=budget,
            node_results=[*node_results, result],
            node_id=plan_node.node_id,
        )
        if budget_blocker is not None:
            errors = result.get("errors") if isinstance(result.get("errors"), list) else []
            result = {
                **result,
                "status": "BLOCKED",
                "verdict": "BUDGET_EXCEEDED",
                "accepted_output": None,
                "errors": [*errors, "budget_exceeded"],
                "budget_blocker": budget_blocker,
            }
        node_log.bind(
            status=result.get("status"),
            verdict=result.get("verdict"),
        ).info("generic_dag_node_finished")
        if result.get("status") == "PASS" and result.get("verdict") == "PASS":
            node_results.append(result)
            completed.add(plan_node.node_id)
            _write_checkpoint(
                path=checkpoint_path,
                current_state_path=current_state_path,
                run_id=run_id,
                spec_path=resolved_spec_path,
                run_dir=run_dir,
                events_path=events_path,
                nodes=nodes,
                node_results=node_results,
                completed=completed,
                status="RUNNING",
                verdict="RUNNING",
                active_node_id=None,
            )
        return result

    operator_cancel_event = Event()
    prior_signal_handlers = _install_operator_stop_handlers(operator_cancel_event)
    try:
        with SqliteDagRunStore(run_store_path) as run_store:
            scheduler_run_id = run_store.execution_run_id(run_id)
            scheduler_result = run_dag_plan(
                plan,
                execute_node=execute_plan_node,
                max_concurrency=int(spec.get("max_concurrency", 1)),
                run_store=run_store,
                run_id=scheduler_run_id,
                allow_lease_takeover=True,
                on_lease_acquired=initialize_run_artifacts,
                fault_injector=diagnostic_fault_injector,
                cancel_requested=operator_cancel_event.is_set,
            )
    finally:
        _restore_signal_handlers(prior_signal_handlers)
    node_results = list(scheduler_result.node_results)
    completed = set(scheduler_result.completed_node_ids)
    final_status = scheduler_result.status
    final_verdict = scheduler_result.verdict

    _write_checkpoint(
        path=checkpoint_path,
        current_state_path=current_state_path,
        run_id=run_id,
        spec_path=resolved_spec_path,
        run_dir=run_dir,
        events_path=events_path,
        nodes=nodes,
        node_results=node_results,
        completed=completed,
        status=final_status,
        verdict=final_verdict,
        active_node_id=None,
    )
    provider_live = any(result.get("provider_live") is True for result in node_results)
    skill_live = any(result.get("skill_live") is True for result in node_results)
    live = provider_live or any(result.get("live") is True for result in node_results)
    receipt = {
        "schema": GENERIC_DAG_RUN_RECEIPT_SCHEMA,
        "ok": final_status == "PASS",
        "status": final_status,
        "verdict": final_verdict,
        "mocked": False,
        "live": live,
        "provider_live": provider_live,
        "execution": "local_subprocess_receipt_gated_dag",
        "scheduler": "dag_plan_ready_queue",
        "dag_plan_sha256": plan.plan_sha256,
        "execution_profile": {
            "profile_id": profile_resolution.get("profile_id"),
            "resolution_sha256": profile_resolution.get("resolution_sha256"),
            "resolved_controls_sha256": profile_resolution.get("resolved_controls_sha256"),
            "compatibility_default": profile_resolution.get("compatibility_default"),
        },
        "max_observed_concurrency": scheduler_result.max_observed_concurrency,
        "durable": scheduler_result.durable,
        "run_store_path": str(run_store_path),
        "scheduler_run_id": scheduler_result.run_id,
        "lease_epoch": scheduler_result.lease_epoch,
        "replayed_event_count": scheduler_result.replayed_event_count,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "spec_path": str(resolved_spec_path),
        "resume_requested": resume,
        "resume_source": resume_source
        or {"mode": "spec_path", "spec_path": str(resolved_spec_path)},
        "events_jsonl": str(events_path),
        "diagnostics_log_path": str(diagnostics_log_path),
        "checkpoint_path": str(checkpoint_path),
        "current_state_path": str(current_state_path),
        "node_count": len(nodes),
        "completed_node_count": len(completed),
        "nodes": node_results,
        "cost_accounting": _run_cost_accounting(node_results, budget=budget),
        "proof_scope": _proof_scope(provider_live=provider_live, skill_live=skill_live),
        "timestamp": _utc_stamp(),
    }
    _write_json(run_dir / "run-receipt.json", receipt)
    _append_event(
        events_path,
        "dag_finished",
        {"run_id": run_id, "status": final_status, "verdict": final_verdict},
    )
    return receipt


def _install_operator_stop_handlers(cancel_event: Event) -> dict[int, Any]:
    """Translate operator stop signals into cooperative DAG cancellation."""

    prior_handlers: dict[int, Any] = {}

    def request_stop(signum: int, frame: Any) -> None:
        del frame
        if cancel_event.is_set():
            raise KeyboardInterrupt(f"second operator stop signal received: {signum}")
        cancel_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            prior_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        except (OSError, RuntimeError, ValueError):
            prior_handlers.pop(signum, None)
    return prior_handlers


def _restore_signal_handlers(prior_handlers: Mapping[int, Any]) -> None:
    for signum, handler in prior_handlers.items():
        try:
            signal.signal(signum, handler)
        except (OSError, RuntimeError, ValueError):
            continue


def resume_generic_dag_from_run(run_dir: Path) -> dict[str, Any]:
    """Resume a generic DAG using the spec path recorded in an existing run."""

    resolved = run_dir.expanduser().resolve()
    spec_path, metadata_path = _spec_path_from_run_metadata(resolved)
    return run_generic_dag(
        spec_path=spec_path,
        resume=True,
        resume_source={
            "mode": "run_metadata",
            "run_dir": str(resolved),
            "metadata_path": str(metadata_path),
            "spec_path": str(spec_path),
        },
    )


def inspect_generic_dag_run(run_dir: Path) -> dict[str, Any]:
    """Return a compact summary for a generic DAG run."""

    resolved = run_dir.expanduser().resolve()
    receipt = _read_json_object(resolved / "run-receipt.json", label="generic DAG run receipt")
    events_path = Path(str(receipt["events_jsonl"])).expanduser()
    events = _read_events(events_path)
    checkpoint = _optional_json_object(Path(str(receipt.get("checkpoint_path") or "")))
    nodes = [node for node in receipt.get("nodes", []) if isinstance(node, dict)]
    return {
        "schema": "tau.generic_dag_inspect.v1",
        "ok": receipt.get("ok") is True,
        "status": receipt.get("status"),
        "verdict": receipt.get("verdict"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "execution": receipt.get("execution"),
        "run_id": receipt.get("run_id"),
        "run_dir": str(resolved),
        "spec_path": receipt.get("spec_path"),
        "resume_requested": receipt.get("resume_requested"),
        "resume_source": receipt.get("resume_source"),
        "node_count": receipt.get("node_count"),
        "completed_node_count": receipt.get("completed_node_count"),
        "execution_profile": receipt.get("execution_profile"),
        "resumed_node_count": len([node for node in nodes if node.get("resumed") is True]),
        "dispatched_node_count": len([node for node in nodes if node.get("attempt_count")]),
        "blocked_node_count": len(
            [node for node in nodes if str(node.get("status") or "").upper() == "BLOCKED"]
        ),
        "events_count": len(events),
        "event_kind_counts": _event_kind_counts(events),
        "checkpoint_path": receipt.get("checkpoint_path"),
        "current_state_path": receipt.get("current_state_path"),
        "checkpoint": _checkpoint_summary(checkpoint),
        "nodes": [
            {
                "node_id": node.get("node_id"),
                "role": node.get("role"),
                "status": node.get("status"),
                "verdict": node.get("verdict"),
                "attempt_count": node.get("attempt_count"),
                "receipt_path": node.get("receipt_path"),
                "work_order_path": node.get("work_order_path"),
                "work_order_sha256": node.get("work_order_sha256"),
                "resumed": node.get("resumed"),
                "live": node.get("live"),
                "provider_live": node.get("provider_live"),
                "provider_status": node.get("provider_status"),
                "provider_verdict": node.get("provider_verdict"),
                "goal_hash": node.get("goal_hash"),
                "attempt": node.get("attempt"),
                "workspace_id": node.get("workspace_id"),
                "pane_id": node.get("pane_id"),
                "terminal_id": node.get("terminal_id"),
                "visible_log_path": node.get("visible_log_path"),
                "visible_log_sha256": node.get("visible_log_sha256"),
                "started_at": node.get("started_at"),
                "finished_at": node.get("finished_at"),
                "duration_seconds": node.get("duration_seconds"),
                "artifact_count": len(node.get("artifacts", []))
                if isinstance(node.get("artifacts"), list)
                else 0,
                "artifacts": _artifact_summary_map(node.get("artifacts")),
                "cost_accounting": node.get("cost_accounting")
                if isinstance(node.get("cost_accounting"), dict)
                else _empty_cost_accounting(),
                "budget_blocker": node.get("budget_blocker")
                if isinstance(node.get("budget_blocker"), dict)
                else None,
            }
            for node in nodes
        ],
        "proof_scope": receipt.get("proof_scope"),
    }


def _event_kind_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        kind = str(event.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def _artifact_summary_map(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    artifacts: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        path = item.get("path")
        if isinstance(kind, str) and kind and isinstance(path, str) and path:
            artifacts[kind] = path
    return artifacts


def _dag_budget_from_spec(spec: dict[str, Any], *, run_dir: Path) -> dict[str, float] | None:
    override_path = run_dir / "budget-override.json"
    if override_path.is_file():
        override = _read_json_object(override_path, label="DAG budget override")
        return _budget_from_mapping(override, label="DAG budget override")
    raw = spec.get("budget")
    if raw is None:
        raw = spec.get("cost_budget")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuntimeError("DAG budget must be an object")
    return _budget_from_mapping(raw, label="DAG budget")


def _budget_from_mapping(raw: dict[str, Any], *, label: str) -> dict[str, float]:
    ceiling = _optional_float(
        raw.get("estimated_cost_usd")
        if raw.get("estimated_cost_usd") is not None
        else raw.get("max_estimated_cost_usd")
    )
    if ceiling is None:
        raise RuntimeError(f"{label} requires estimated_cost_usd or max_estimated_cost_usd")
    if ceiling < 0:
        raise RuntimeError(f"{label} estimated cost ceiling must be non-negative")
    return {"estimated_cost_usd": ceiling}


def _node_cost_accounting(node_result: dict[str, Any]) -> dict[str, Any]:
    usage = _usage_mapping_from_result(node_result)
    input_tokens = _int_or_zero(usage.get("input_tokens") or usage.get("input"))
    output_tokens = _int_or_zero(usage.get("output_tokens") or usage.get("output"))
    cache_read_tokens = _int_or_zero(
        usage.get("cache_read_tokens")
        or usage.get("cache_read")
        or usage.get("cached_tokens")
    )
    cache_write_tokens = _int_or_zero(
        usage.get("cache_write_tokens") or usage.get("cache_write")
    )
    estimated_cost = _optional_float(
        usage.get("estimated_cost_usd")
        if usage.get("estimated_cost_usd") is not None
        else usage.get("estimated_cost")
        if usage.get("estimated_cost") is not None
        else usage.get("cost_usd")
        if usage.get("cost_usd") is not None
        else usage.get("cost")
    )
    has_reported_usage = any(
        value > 0
        for value in (
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
        )
    ) or estimated_cost is not None
    return {
        "schema": GENERIC_DAG_COST_ACCOUNTING_SCHEMA,
        "source": "provider_reported_estimate" if has_reported_usage else "not_reported",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "total_tokens": input_tokens
        + output_tokens
        + cache_read_tokens
        + cache_write_tokens,
        "estimated_cost_usd": estimated_cost,
        "estimated_cost_is_billing_truth": False,
    }


def _usage_mapping_from_result(node_result: dict[str, Any]) -> dict[str, Any]:
    for key in ("usage", "provider_usage", "cost_accounting"):
        value = node_result.get(key)
        if isinstance(value, dict):
            return value
    accepted_output = node_result.get("accepted_output")
    if isinstance(accepted_output, dict):
        for key in ("usage", "provider_usage", "cost_accounting"):
            value = accepted_output.get(key)
            if isinstance(value, dict):
                return value
    cost_estimate = node_result.get("cost_estimate")
    if isinstance(cost_estimate, dict):
        estimated = cost_estimate.get("estimated_cost_usd")
        if estimated is not None:
            return {"estimated_cost_usd": estimated}
    return {}


def _run_cost_accounting(
    node_results: list[dict[str, Any]],
    *,
    budget: dict[str, float] | None,
) -> dict[str, Any]:
    node_costs = [
        item.get("cost_accounting")
        if isinstance(item.get("cost_accounting"), dict)
        else _node_cost_accounting(item)
        for item in node_results
    ]
    estimated_cost_values = [
        value
        for item in node_costs
        if (value := _optional_float(item.get("estimated_cost_usd"))) is not None
    ]
    total_estimated_cost = (
        round(sum(estimated_cost_values), 12) if estimated_cost_values else None
    )
    allowed = budget.get("estimated_cost_usd") if budget is not None else None
    return {
        "schema": GENERIC_DAG_COST_ACCOUNTING_SCHEMA,
        "source": (
            "provider_reported_estimate"
            if any(item.get("source") == "provider_reported_estimate" for item in node_costs)
            else "not_reported"
        ),
        "input_tokens": sum(_int_or_zero(item.get("input_tokens")) for item in node_costs),
        "output_tokens": sum(_int_or_zero(item.get("output_tokens")) for item in node_costs),
        "cache_read_tokens": sum(
            _int_or_zero(item.get("cache_read_tokens")) for item in node_costs
        ),
        "cache_write_tokens": sum(
            _int_or_zero(item.get("cache_write_tokens")) for item in node_costs
        ),
        "total_tokens": sum(_int_or_zero(item.get("total_tokens")) for item in node_costs),
        "estimated_cost_usd": total_estimated_cost,
        "estimated_cost_is_billing_truth": False,
        "budget": (
            {
                "estimated_cost_usd": allowed,
                "state": (
                    "EXCEEDED"
                    if total_estimated_cost is not None
                    and allowed is not None
                    and total_estimated_cost > allowed
                    else "WITHIN_BUDGET"
                ),
            }
            if allowed is not None
            else {"state": "NOT_CONFIGURED"}
        ),
    }


def _cost_budget_blocker(
    *,
    budget: dict[str, float] | None,
    node_results: list[dict[str, Any]],
    node_id: str,
) -> dict[str, Any] | None:
    if budget is None:
        return None
    accounting = _run_cost_accounting(node_results, budget=budget)
    consumed = _optional_float(accounting.get("estimated_cost_usd"))
    allowed = budget["estimated_cost_usd"]
    if consumed is None or consumed <= allowed:
        return None
    return {
        "code": "budget_exceeded",
        "verdict": "BUDGET_EXCEEDED",
        "node_id": node_id,
        "consumed_estimated_cost_usd": consumed,
        "allowed_estimated_cost_usd": allowed,
        "estimated_cost_is_billing_truth": False,
        "resume_action": "raise DAG budget ceiling and resume the run",
    }


def _empty_cost_accounting() -> dict[str, Any]:
    return {
        "schema": GENERIC_DAG_COST_ACCOUNTING_SCHEMA,
        "source": "not_reported",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": None,
        "estimated_cost_is_billing_truth": False,
    }


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(value))
        except ValueError:
            return 0
    return 0


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _write_checkpoint(
    *,
    path: Path,
    current_state_path: Path,
    run_id: str,
    spec_path: Path,
    run_dir: Path,
    events_path: Path,
    nodes: dict[str, DagNode],
    node_results: list[dict[str, Any]],
    completed: set[str],
    status: str,
    verdict: str,
    active_node_id: str | None,
) -> None:
    node_statuses = {
        str(result.get("node_id")): {
            "status": result.get("status"),
            "verdict": result.get("verdict"),
            "attempt_count": result.get("attempt_count"),
            "resumed": result.get("resumed"),
            "receipt_path": result.get("receipt_path"),
        }
        for result in node_results
        if result.get("node_id")
    }
    ready_nodes = [
        node_id
        for node_id, node in nodes.items()
        if node_id not in completed
        and node_id not in node_statuses
        and all(dep in completed for dep in node.depends_on)
    ]
    blocked_nodes = [
        str(result.get("node_id"))
        for result in node_results
        if str(result.get("status") or "").upper() == "BLOCKED"
    ]
    checkpoint = {
        "schema": GENERIC_DAG_CHECKPOINT_SCHEMA,
        "run_id": run_id,
        "spec_path": str(spec_path),
        "run_dir": str(run_dir),
        "events_jsonl": str(events_path),
        "status": status,
        "verdict": verdict,
        "active_node_id": active_node_id,
        "completed_nodes": sorted(completed),
        "ready_nodes": ready_nodes,
        "blocked_nodes": blocked_nodes,
        "node_statuses": node_statuses,
        "resume": {
            "enabled_by_default": True,
            "will_reuse_valid_pass_receipts": True,
            "receipt_paths": {node_id: str(node.receipt_path) for node_id, node in nodes.items()},
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(path, checkpoint)
    _write_json(current_state_path, checkpoint)


def _checkpoint_summary(checkpoint: dict[str, Any]) -> dict[str, Any] | None:
    if not checkpoint:
        return None
    return {
        "schema": checkpoint.get("schema"),
        "status": checkpoint.get("status"),
        "verdict": checkpoint.get("verdict"),
        "active_node_id": checkpoint.get("active_node_id"),
        "completed_nodes": checkpoint.get("completed_nodes"),
        "ready_nodes": checkpoint.get("ready_nodes"),
        "blocked_nodes": checkpoint.get("blocked_nodes"),
    }


def _run_node(
    node: DagNode,
    *,
    run_id: str,
    run_dir: Path,
    events_path: Path,
    resume: bool,
    accepted_inputs: list[dict[str, Any]],
    goal_hash: str | None,
    scheduler_attempt: int,
    runtime_identity: dict[str, Any],
    cancel_event: Event,
    legacy_context: tuple[Path, str] | None = None,
    progress_sink: Callable[[str, int, str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if node.skill is not None:
        return _run_skill_node(
            node,
            run_id=run_id,
            events_path=events_path,
            accepted_inputs=accepted_inputs,
            goal_hash=goal_hash,
            resume=resume,
            cancel_event=cancel_event,
        )
    if node.browser is not None:
        return _run_browser_node(
            node,
            run_id=run_id,
            events_path=events_path,
            goal_hash=goal_hash,
            resume=resume,
        )
    if node.transaction is not None:
        return _run_transaction_node(
            node,
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            resume=resume,
            accepted_inputs=accepted_inputs,
            goal_hash=goal_hash,
            runtime_identity=runtime_identity,
            cancel_event=cancel_event,
            progress_sink=progress_sink,
        )
    dispatch_recorded = legacy_context is not None
    if legacy_context is None:
        legacy_context = _write_legacy_node_context(
            node=node,
            run_id=run_id,
            run_dir=run_dir,
            accepted_inputs=accepted_inputs,
        )
    context_path, context_sha256 = legacy_context
    return _run_legacy_node(
        node,
        run_id=run_id,
        run_dir=run_dir,
        events_path=events_path,
        resume=resume,
        context_path=context_path,
        context_sha256=context_sha256,
        goal_hash=goal_hash,
        attempt=scheduler_attempt,
        runtime_identity=runtime_identity,
        cancel_event=cancel_event,
        dispatch_recorded=dispatch_recorded,
    )


def _run_skill_node(
    node: DagNode,
    *,
    run_id: str,
    events_path: Path,
    accepted_inputs: list[dict[str, Any]],
    goal_hash: str | None,
    resume: bool,
    cancel_event: Event,
) -> dict[str, Any]:
    assert node.skill is not None
    started_at = _utc_stamp()
    started = time.monotonic()
    if resume and node.receipt_path.is_file():
        prior = _read_json_object(node.receipt_path, label=f"{node.node_id} skill receipt")
        errors = _validate_skill_node_receipt(prior, node, expected_goal_hash=goal_hash)
        artifacts = _receipt_artifacts(prior)
        if not errors and prior.get("verdict") == "PASS":
            _append_event(
                events_path,
                "node_resumed",
                {"run_id": run_id, "node_id": node.node_id, "receipt_path": str(node.receipt_path)},
            )
            return {
                "node_id": node.node_id,
                "role": node.role,
                "status": "PASS",
                "verdict": "PASS",
                "mocked": False,
                "live": prior.get("live") is True,
                "provider_live": False,
                "skill_live": prior.get("live") is True,
                "skill_provider": node.skill.provider,
                "capability": node.skill.capability,
                "round_number": prior.get("round_number"),
                "max_rounds": prior.get("max_rounds"),
                "attempt_count": 0,
                "started_at": started_at,
                "finished_at": _utc_stamp(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "receipt_path": str(node.receipt_path),
                "work_order_path": str(node.work_order_path) if node.work_order_path else None,
                "work_order_sha256": _work_order_sha256(node),
                "usage": prior.get("usage") if isinstance(prior.get("usage"), dict) else None,
                "cost_estimate": prior.get("cost_estimate")
                if isinstance(prior.get("cost_estimate"), dict)
                else None,
                "resumed": True,
                "command_results": [],
                "artifacts": artifacts,
                "accepted_output": {
                    "source_node_id": node.node_id,
                    "skill_provider": node.skill.provider,
                    "capability": node.skill.capability,
                    "artifacts": artifacts,
                },
                "errors": [],
            }
        if errors:
            _append_resume_rejected_event(
                events_path,
                run_id=run_id,
                node=node,
                receipt=prior,
                errors=errors,
            )
            return _blocked_node_record(
                node,
                verdict="INVALID_RECEIPT",
                errors=errors,
                attempt_count=0,
                command_results=[],
                started_at=started_at,
                finished_at=_utc_stamp(),
                duration_seconds=time.monotonic() - started,
            )
    receipt = execute_skill_dag_node(
        spec=node.skill,
        run_id=run_id,
        node_id=node.node_id,
        goal_hash=goal_hash,
        work_order_sha256=_work_order_sha256(node),
        accepted_inputs=accepted_inputs,
        cancel_event=cancel_event,
    )
    receipt["goal_hash"] = goal_hash
    receipt["work_order_sha256"] = _work_order_sha256(node)
    # Admission contract S2-S5: skill receipts are authoritative evidence and
    # must never be torn by a crash; the scheduler performs S6-S7 admission
    # from the durable bytes (#203).
    write_durable_json(node.receipt_path, receipt)
    receipt_artifacts = receipt.get("artifacts")
    artifacts = list(receipt_artifacts) if isinstance(receipt_artifacts, list) else []
    accepted_output = (
        {
            "source_node_id": node.node_id,
            "skill_provider": node.skill.provider,
            "capability": node.skill.capability,
            "artifacts": artifacts,
        }
        if receipt.get("status") == "PASS"
        else None
    )
    return {
        "node_id": node.node_id,
        "role": node.role,
        "status": receipt.get("status"),
        "verdict": receipt.get("verdict"),
        "mocked": False,
        "live": receipt.get("live") is True,
        "provider_live": False,
        "skill_live": receipt.get("live") is True,
        "skill_provider": node.skill.provider,
        "capability": node.skill.capability,
        "round_number": receipt.get("round_number"),
        "max_rounds": receipt.get("max_rounds"),
        "attempt_count": 1,
        "started_at": started_at,
        "finished_at": _utc_stamp(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "receipt_path": str(node.receipt_path),
        "work_order_path": str(node.work_order_path) if node.work_order_path else None,
        "work_order_sha256": _work_order_sha256(node),
        "usage": receipt.get("usage") if isinstance(receipt.get("usage"), dict) else None,
        "cost_estimate": receipt.get("cost_estimate")
        if isinstance(receipt.get("cost_estimate"), dict)
        else None,
        "resumed": False,
        "command_results": [],
        "artifacts": artifacts,
        "accepted_output": accepted_output,
        "errors": receipt.get("errors", []),
    }


def _run_browser_node(
    node: DagNode,
    *,
    run_id: str,
    events_path: Path,
    goal_hash: str | None,
    resume: bool,
) -> dict[str, Any]:
    assert node.browser is not None
    started_at = _utc_stamp()
    started = time.monotonic()
    if resume and node.receipt_path.is_file():
        prior = _read_json_object(node.receipt_path, label=f"{node.node_id} browser receipt")
        errors = _validate_browser_node_receipt(prior, node, expected_goal_hash=goal_hash)
        artifacts = _receipt_artifacts(prior)
        if not errors and prior.get("verdict") == "PASS":
            _append_event(
                events_path,
                "node_resumed",
                {"run_id": run_id, "node_id": node.node_id, "receipt_path": str(node.receipt_path)},
            )
            return _browser_node_record(
                node=node,
                receipt=prior,
                artifacts=artifacts,
                attempt_count=0,
                resumed=True,
                started_at=started_at,
                duration_seconds=time.monotonic() - started,
            )
        if errors:
            _append_resume_rejected_event(
                events_path,
                run_id=run_id,
                node=node,
                receipt=prior,
                errors=errors,
            )
            return _blocked_node_record(
                node,
                verdict="INVALID_RECEIPT",
                errors=errors,
                attempt_count=0,
                command_results=[],
                started_at=started_at,
                finished_at=_utc_stamp(),
                duration_seconds=time.monotonic() - started,
            )
    receipt = execute_browser_dag_node(
        spec=node.browser,
        run_id=run_id,
        node_id=node.node_id,
        goal_hash=goal_hash,
        work_order_sha256=_work_order_sha256(node),
    )
    write_json(node.receipt_path, receipt)
    errors = _validate_browser_node_receipt(receipt, node, expected_goal_hash=goal_hash)
    if errors:
        return _blocked_node_record(
            node,
            verdict="INVALID_RECEIPT",
            errors=errors,
            attempt_count=1,
            command_results=[],
            started_at=started_at,
            finished_at=_utc_stamp(),
            duration_seconds=time.monotonic() - started,
        )
    artifacts = _receipt_artifacts(receipt)
    return _browser_node_record(
        node=node,
        receipt=receipt,
        artifacts=artifacts,
        attempt_count=1,
        resumed=False,
        started_at=started_at,
        duration_seconds=time.monotonic() - started,
    )


def _browser_node_record(
    *,
    node: DagNode,
    receipt: dict[str, Any],
    artifacts: list[Any],
    attempt_count: int,
    resumed: bool,
    started_at: str,
    duration_seconds: float,
) -> dict[str, Any]:
    accepted_output = (
        {
            "source_node_id": node.node_id,
            "browser_provider": "surf",
            "capability": "browser_handler",
            "artifacts": artifacts,
        }
        if receipt.get("status") == "PASS"
        else None
    )
    return {
        "node_id": node.node_id,
        "role": node.role,
        "status": receipt.get("status"),
        "verdict": receipt.get("verdict"),
        "mocked": False,
        "live": receipt.get("live") is True,
        "provider_live": False,
        "skill_live": False,
        "browser_live": receipt.get("live") is True,
        "browser_provider": "surf",
        "capability": "browser_handler",
        "attempt_count": attempt_count,
        "started_at": started_at,
        "finished_at": _utc_stamp(),
        "duration_seconds": round(duration_seconds, 3),
        "receipt_path": str(node.receipt_path),
        "work_order_path": str(node.work_order_path) if node.work_order_path else None,
        "work_order_sha256": _work_order_sha256(node),
        "usage": None,
        "cost_estimate": None,
        "resumed": resumed,
        "command_results": [],
        "artifacts": artifacts,
        "accepted_output": accepted_output,
        "errors": receipt.get("errors", []),
    }


def _run_legacy_node(
    node: DagNode,
    *,
    run_id: str,
    run_dir: Path,
    events_path: Path,
    resume: bool,
    context_path: Path,
    context_sha256: str,
    goal_hash: str | None,
    attempt: int,
    runtime_identity: dict[str, Any],
    cancel_event: Event,
    dispatch_recorded: bool = False,
) -> dict[str, Any]:
    node_started_at = _utc_stamp()
    node_started_monotonic = time.monotonic()
    if resume and node.receipt_path.exists():
        existing = _read_json_object(node.receipt_path, label=f"{node.node_id} receipt")
        errors = _validate_node_receipt(existing, node, expected_goal_hash=goal_hash)
        if not errors and existing.get("verdict") == "PASS":
            _append_event(
                events_path,
                "node_resumed",
                {"run_id": run_id, "node_id": node.node_id, "receipt_path": str(node.receipt_path)},
            )
            return _node_record(
                node,
                existing,
                attempt_count=0,
                resumed=True,
                command_results=[],
                started_at=node_started_at,
                finished_at=_utc_stamp(),
                duration_seconds=time.monotonic() - node_started_monotonic,
            )
        if errors:
            _append_resume_rejected_event(
                events_path,
                run_id=run_id,
                node=node,
                receipt=existing,
                errors=errors,
            )
            return _blocked_node_record(
                node,
                verdict="INVALID_RECEIPT",
                errors=errors,
                attempt_count=0,
                command_results=[],
                started_at=node_started_at,
                finished_at=_utc_stamp(),
                duration_seconds=time.monotonic() - node_started_monotonic,
            )

    if not dispatch_recorded:
        _append_legacy_node_dispatch_event(
            node,
            run_id=run_id,
            events_path=events_path,
            attempt=attempt,
        )
    started_at = time.monotonic()
    result = _run_command(
        node.command,
        cwd=run_dir,
        timeout_seconds=node.timeout_seconds,
        env_overrides={
            "TAU_GENERIC_DAG_CONTEXT": str(context_path),
            "TAU_GENERIC_DAG_CONTEXT_SHA256": context_sha256,
        },
        cancel_event=cancel_event,
        runtime_identity={
            **runtime_identity,
            "work_order": _work_order_sha256(node) or context_sha256,
            "goal": goal_hash or runtime_identity["goal"],
            "artifact_dir": (
                node.receipt_path.parent / ".tau-runtime" / node.node_id / f"attempt-{attempt:03d}"
            ),
        },
    )
    command_results = [_command_result_dict(result, elapsed_seconds=time.monotonic() - started_at)]
    if result.returncode != 0:
        if result.termination_cause == "timed_out":
            verdict = "SUBAGENT_TIMEOUT"
        elif result.termination_cause == "cancelled":
            verdict = "CANCELLED"
        else:
            verdict = "SUBAGENT_ERROR"
        blocked = _blocked_node_record(
            node,
            verdict=verdict,
            errors=[_command_error(result)],
            attempt_count=attempt,
            command_results=command_results,
            started_at=node_started_at,
            finished_at=_utc_stamp(),
            duration_seconds=time.monotonic() - node_started_monotonic,
        )
        _write_blocked_node_receipt_if_missing(
            node,
            blocked,
            goal_hash=goal_hash,
            attempt=attempt,
        )
        return blocked
    if not node.receipt_path.exists():
        return _blocked_node_record(
            node,
            verdict="RECEIPT_MISSING",
            errors=[f"node receipt did not appear: {node.receipt_path}"],
            attempt_count=attempt,
            command_results=command_results,
            started_at=node_started_at,
            finished_at=_utc_stamp(),
            duration_seconds=time.monotonic() - node_started_monotonic,
        )
    receipt = _read_json_object(node.receipt_path, label=f"{node.node_id} receipt")
    _attach_local_execution_evidence(receipt, command_results[0])
    write_json(node.receipt_path, receipt)
    errors = _validate_node_receipt(receipt, node, expected_goal_hash=goal_hash)
    if errors:
        return _blocked_node_record(
            node,
            verdict="INVALID_RECEIPT",
            errors=errors,
            attempt_count=attempt,
            command_results=command_results,
            started_at=node_started_at,
            finished_at=_utc_stamp(),
            duration_seconds=time.monotonic() - node_started_monotonic,
        )
    _append_event(
        events_path,
        "node_receipt_validated",
        {
            "run_id": run_id,
            "node_id": node.node_id,
            "attempt": attempt,
            "receipt_path": str(node.receipt_path),
        },
    )
    return _node_record(
        node,
        receipt,
        attempt_count=attempt,
        resumed=False,
        command_results=command_results,
        started_at=node_started_at,
        finished_at=_utc_stamp(),
        duration_seconds=time.monotonic() - node_started_monotonic,
    )


def _run_transaction_node(
    node: DagNode,
    *,
    run_id: str,
    run_dir: Path,
    events_path: Path,
    resume: bool,
    accepted_inputs: list[dict[str, Any]],
    goal_hash: str | None,
    runtime_identity: dict[str, Any],
    cancel_event: Event,
    progress_sink: Callable[[str, int, str, dict[str, Any]], None] | None,
) -> dict[str, Any]:
    """Run one bounded producer/reviewer transaction owned by Tau."""

    spec = node.transaction
    assert spec is not None
    assert node.work_order_path is not None
    started_at = _utc_stamp()
    started_monotonic = time.monotonic()
    work_order_sha256 = _work_order_sha256(node)
    if work_order_sha256 is None:
        return _blocked_node_record(
            node,
            verdict="TRANSACTION_WORK_ORDER_MISSING",
            errors=[f"transaction work order unreadable: {node.work_order_path}"],
        )
    transaction_dir = run_dir / "transactions" / node.node_id
    transaction_receipt_path = transaction_dir / "transaction-receipt.json"
    accepted_manifest_path = transaction_dir / "accepted-manifest.json"
    command_results: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []

    if resume and transaction_receipt_path.exists():
        prior, prior_errors = load_json(transaction_receipt_path, label="transaction receipt")
        if (
            not prior_errors
            and prior.get("schema") == TRANSACTION_RECEIPT_SCHEMA
            and prior.get("state") in {"ACCEPTED", "APPROVAL_REQUIRED", "CONTINUED"}
        ):
            if goal_hash is not None:
                prior_goal_hash = prior.get("goal_hash")
                if not isinstance(prior_goal_hash, str) or not prior_goal_hash.strip():
                    prior_errors.append("transaction_receipt_goal_hash_required")
                elif prior_goal_hash != goal_hash:
                    prior_errors.append("transaction_receipt_goal_hash_mismatch")
            expected_manifest_sha256 = prior.get("accepted_manifest_sha256")
            if not isinstance(expected_manifest_sha256, str):
                prior_errors.append("accepted_manifest_sha256_missing")
            elif not prior_errors:
                accepted, accepted_errors = revalidate_accepted_manifest(
                    path=accepted_manifest_path,
                    expected_sha256=expected_manifest_sha256,
                    spec=spec,
                    node_id=node.node_id,
                    work_order_sha256=work_order_sha256,
                    accepted_inputs=accepted_inputs,
                )
                prior_errors.extend(accepted_errors)
                if not prior_errors:
                    projection = accepted_projection(
                        path=accepted_manifest_path,
                        sha256=expected_manifest_sha256,
                        payload=accepted,
                    )
                    if spec.continuation is None or prior.get("state") == "CONTINUED":
                        return _transaction_record(
                            node=node,
                            state=str(prior["state"]),
                            status="PASS",
                            verdict="PASS",
                            attempts=prior.get("attempts", []),
                            command_results=[],
                            transaction_receipt_path=transaction_receipt_path,
                            accepted_manifest_path=accepted_manifest_path,
                            accepted_manifest_sha256=expected_manifest_sha256,
                            accepted_output=projection,
                            resumed=True,
                            started_at=started_at,
                            duration_seconds=time.monotonic() - started_monotonic,
                            goal_hash=goal_hash,
                        )
                    return _continue_transaction(
                        node=node,
                        run_id=run_id,
                        run_dir=run_dir,
                        spec=spec,
                        accepted=accepted,
                        accepted_manifest_path=accepted_manifest_path,
                        accepted_manifest_sha256=expected_manifest_sha256,
                        projection=projection,
                        attempts=prior.get("attempts", []),
                        transaction_receipt_path=transaction_receipt_path,
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        resumed=True,
                        runtime_identity=runtime_identity,
                        cancel_event=cancel_event,
                        goal_hash=goal_hash,
                    )
            if prior_errors:
                return _transaction_record(
                    node=node,
                    state="BLOCKED",
                    status="BLOCKED",
                    verdict="STALE_ACCEPTED_STATE",
                    attempts=prior.get("attempts", []),
                    command_results=[],
                    transaction_receipt_path=transaction_receipt_path,
                    errors=prior_errors,
                    resumed=False,
                    started_at=started_at,
                    duration_seconds=time.monotonic() - started_monotonic,
                    goal_hash=goal_hash,
                )

    revision: dict[str, Any] | None = None
    previous_artifact_sha256s: set[str] = set()
    repeated_revision_signatures: dict[str, int] = {}
    for attempt in range(1, node.max_attempts + 1):
        attempt_dir = transaction_dir / f"attempt-{attempt:03d}"
        attempt_context_path = attempt_dir / "attempt-context.json"
        candidate_manifest_path = attempt_dir / "candidate-manifest.json"
        review_context_path = attempt_dir / "review-context.json"
        review_feedback_path = attempt_dir / "review-feedback.json"
        validation_context_path = attempt_dir / "validation-context.json"
        validation_receipt_path = attempt_dir / "validation-receipt.json"
        for stale_path in (node.receipt_path, candidate_manifest_path, review_feedback_path):
            stale_path.unlink(missing_ok=True)
        _, attempt_context_sha256 = write_attempt_context(
            path=attempt_context_path,
            run_id=run_id,
            node_id=node.node_id,
            spec=spec,
            attempt=attempt,
            max_attempts=node.max_attempts,
            work_order_path=node.work_order_path,
            work_order_sha256=work_order_sha256,
            accepted_inputs=accepted_inputs,
            revision=revision,
            candidate_manifest_path=candidate_manifest_path,
            producer_receipt_path=node.receipt_path,
            goal_hash=goal_hash,
        )
        _append_event(
            events_path,
            "transaction_producer_dispatch",
            {"run_id": run_id, "node_id": node.node_id, "attempt": attempt},
        )
        _emit_transaction_progress(
            progress_sink,
            node_id=node.node_id,
            attempt=attempt,
            phase="producer_started",
        )
        producer_started = time.monotonic()
        producer_result = _run_command(
            node.command,
            cwd=run_dir,
            timeout_seconds=node.timeout_seconds,
            env_overrides={
                "TAU_GENERIC_DAG_CONTEXT": str(attempt_context_path),
                "TAU_GENERIC_DAG_CONTEXT_SHA256": attempt_context_sha256,
            },
            cancel_event=cancel_event,
            runtime_identity=_transaction_runtime_identity(
                runtime_identity,
                node=node,
                phase="producer",
                attempt=attempt,
                work_order_sha256=work_order_sha256,
                goal_hash=goal_hash,
                artifact_dir=attempt_dir / "runtime" / "producer",
            ),
        )
        command_results.append(
            _command_result_dict(
                producer_result, elapsed_seconds=time.monotonic() - producer_started
            )
        )
        if producer_result.returncode != 0:
            return _transaction_blocked(
                node=node,
                verdict="PRODUCER_ERROR",
                errors=[_command_error(producer_result)],
                attempts=attempts,
                command_results=command_results,
                transaction_receipt_path=transaction_receipt_path,
                started_at=started_at,
                started_monotonic=started_monotonic,
                goal_hash=goal_hash,
            )
        producer_receipt, receipt_errors = load_json(
            node.receipt_path, label="transaction producer receipt"
        )
        if not receipt_errors:
            _attach_local_execution_evidence(producer_receipt, command_results[-1])
            write_json(node.receipt_path, producer_receipt)
            receipt_errors.extend(
                _validate_node_receipt(
                    producer_receipt,
                    node,
                    expected_goal_hash=goal_hash,
                )
            )
            if str(producer_receipt.get("status") or "").upper() != "PASS":
                receipt_errors.append("producer_receipt_not_passed")
        candidate, candidate_errors = validate_candidate_manifest(
            path=candidate_manifest_path,
            spec=spec,
            node_id=node.node_id,
            attempt=attempt,
            work_order_sha256=work_order_sha256,
            attempt_context_sha256=attempt_context_sha256,
        )
        errors = receipt_errors + candidate_errors
        if errors:
            return _transaction_blocked(
                node=node,
                verdict="INVALID_CANDIDATE",
                errors=errors,
                attempts=attempts,
                command_results=command_results,
                transaction_receipt_path=transaction_receipt_path,
                started_at=started_at,
                started_monotonic=started_monotonic,
                goal_hash=goal_hash,
            )
        candidate_manifest_sha256 = file_sha256(candidate_manifest_path)
        _emit_transaction_progress(
            progress_sink,
            node_id=node.node_id,
            attempt=attempt,
            phase="producer_completed",
            evidence={"candidate_manifest_sha256": candidate_manifest_sha256},
        )
        artifacts = candidate["artifacts"]
        if spec.validator is not None:
            write_json(
                validation_context_path,
                {
                    "schema": "tau.generic_artifact_validation_context.v1",
                    "run_id": run_id,
                    "node_id": node.node_id,
                    "transaction_id": spec.transaction_id,
                    "attempt": attempt,
                    "validator_id": spec.validator.validator_id,
                    "candidate_manifest_path": str(candidate_manifest_path),
                    "candidate_manifest_sha256": candidate_manifest_sha256,
                    "artifacts": artifacts,
                    "output_contract": {"validation_receipt_path": str(validation_receipt_path)},
                },
            )
            validation_context_sha256 = file_sha256(validation_context_path)
            validator_result = _run_command(
                list(spec.validator.command),
                cwd=run_dir,
                timeout_seconds=spec.validator.timeout_seconds,
                env_overrides={
                    "TAU_GENERIC_DAG_VALIDATION_CONTEXT": str(validation_context_path),
                    "TAU_GENERIC_DAG_VALIDATION_CONTEXT_SHA256": validation_context_sha256,
                },
                cancel_event=cancel_event,
                runtime_identity=_transaction_runtime_identity(
                    runtime_identity,
                    node=node,
                    phase="validator",
                    attempt=attempt,
                    work_order_sha256=work_order_sha256,
                    goal_hash=goal_hash,
                    artifact_dir=attempt_dir / "runtime" / "validator",
                ),
            )
            command_results.append(_command_result_dict(validator_result, elapsed_seconds=0.0))
            validation, validation_errors = load_json(
                validation_receipt_path, label="artifact validation receipt"
            )
            expected_validation = {
                "schema": "tau.generic_artifact_validation.v1",
                "status": "PASS",
                "node_id": node.node_id,
                "transaction_id": spec.transaction_id,
                "attempt": attempt,
                "validator_id": spec.validator.validator_id,
                "validation_context_sha256": validation_context_sha256,
                "candidate_manifest_sha256": candidate_manifest_sha256,
            }
            validation_errors.extend(
                f"validation_binding_mismatch:{key}"
                for key, value in expected_validation.items()
                if validation.get(key) != value
            )
            if validator_result.returncode != 0 or validation_errors:
                return _transaction_blocked(
                    node=node,
                    verdict="VALIDATOR_BLOCKED",
                    errors=validation_errors or [_command_error(validator_result)],
                    attempts=attempts,
                    command_results=command_results,
                    transaction_receipt_path=transaction_receipt_path,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    goal_hash=goal_hash,
                )
            _emit_transaction_progress(
                progress_sink,
                node_id=node.node_id,
                attempt=attempt,
                phase="validator_completed",
                evidence={
                    "status": "PASS",
                    "validation_receipt_path": str(validation_receipt_path),
                },
            )
        _, review_context_sha256 = write_review_context(
            path=review_context_path,
            run_id=run_id,
            node_id=node.node_id,
            spec=spec,
            attempt=attempt,
            attempt_context_path=attempt_context_path,
            attempt_context_sha256=attempt_context_sha256,
            candidate_manifest_path=candidate_manifest_path,
            candidate_manifest_sha256=candidate_manifest_sha256,
            artifacts=artifacts,
            review_feedback_path=review_feedback_path,
            goal_hash=goal_hash,
        )
        _emit_transaction_progress(
            progress_sink,
            node_id=node.node_id,
            attempt=attempt,
            phase="reviewer_started",
        )
        reviewer_started = time.monotonic()
        reviewer_result = _run_command(
            list(spec.reviewer.command),
            cwd=run_dir,
            timeout_seconds=spec.reviewer.timeout_seconds,
            env_overrides={
                "TAU_GENERIC_DAG_REVIEW_CONTEXT": str(review_context_path),
                "TAU_GENERIC_DAG_REVIEW_CONTEXT_SHA256": review_context_sha256,
            },
            cancel_event=cancel_event,
            runtime_identity=_transaction_runtime_identity(
                runtime_identity,
                node=node,
                phase="reviewer",
                attempt=attempt,
                work_order_sha256=work_order_sha256,
                goal_hash=goal_hash,
                artifact_dir=attempt_dir / "runtime" / "reviewer",
            ),
        )
        command_results.append(
            _command_result_dict(
                reviewer_result, elapsed_seconds=time.monotonic() - reviewer_started
            )
        )
        if reviewer_result.returncode != 0:
            return _transaction_blocked(
                node=node,
                verdict="REVIEWER_ERROR",
                errors=[_command_error(reviewer_result)],
                attempts=attempts,
                command_results=command_results,
                transaction_receipt_path=transaction_receipt_path,
                started_at=started_at,
                started_monotonic=started_monotonic,
                goal_hash=goal_hash,
            )
        feedback_payload, feedback_load_errors = load_json(
            review_feedback_path, label="review feedback"
        )
        if feedback_load_errors:
            feedback = feedback_payload
            feedback_errors = feedback_load_errors
        else:
            _attach_local_execution_evidence(feedback_payload, command_results[-1])
            write_json(review_feedback_path, feedback_payload)
            feedback, feedback_errors = validate_review_feedback(
                path=review_feedback_path,
                spec=spec,
                node_id=node.node_id,
                attempt=attempt,
                review_context_sha256=review_context_sha256,
                candidate_manifest_sha256=candidate_manifest_sha256,
                artifact_ids={str(item["artifact_id"]) for item in artifacts},
                expected_goal_hash=goal_hash,
            )
        producer_execution = producer_receipt.get("provider_execution")
        producer_provider_live = producer_receipt.get("provider_live") is True or (
            isinstance(producer_execution, dict) and producer_execution.get("provider_live") is True
        )
        attempt_record = {
            "attempt": attempt,
            "attempt_context_path": str(attempt_context_path),
            "attempt_context_sha256": attempt_context_sha256,
            "candidate_manifest_path": str(candidate_manifest_path),
            "candidate_manifest_sha256": candidate_manifest_sha256,
            "validation_receipt_path": (
                str(validation_receipt_path) if spec.validator is not None else None
            ),
            "review_feedback_path": str(review_feedback_path),
            "review_feedback_sha256": file_sha256(review_feedback_path)
            if review_feedback_path.exists()
            else None,
            "review_verdict": feedback.get("verdict"),
            "review_live": feedback.get("live") is True,
            "review_provider_live": feedback.get("provider_live") is True,
            "review_execution_evidence": feedback.get("execution_evidence")
            if isinstance(feedback.get("execution_evidence"), dict)
            else None,
            "review_model": feedback.get("model"),
            "producer_live": producer_receipt.get("live") is True,
            "producer_provider_live": producer_provider_live,
            "producer_execution_evidence": producer_receipt.get("execution_evidence")
            if isinstance(producer_receipt.get("execution_evidence"), dict)
            else None,
            "producer_provider": (
                producer_execution.get("provider")
                if isinstance(producer_execution, dict)
                else producer_receipt.get("provider")
            ),
            "producer_model": (
                producer_execution.get("model")
                if isinstance(producer_execution, dict)
                else producer_receipt.get("model")
            ),
        }
        if str(feedback.get("verdict") or "").upper() == "REVISE":
            attempt_record["review_revision_signature"] = _review_revision_signature(feedback)
        attempts.append(attempt_record)
        if feedback_errors:
            return _transaction_blocked(
                node=node,
                verdict="INVALID_REVIEW",
                errors=feedback_errors,
                attempts=attempts,
                command_results=command_results,
                transaction_receipt_path=transaction_receipt_path,
                started_at=started_at,
                started_monotonic=started_monotonic,
                goal_hash=goal_hash,
            )
        verdict = str(feedback["verdict"]).upper()
        _emit_transaction_progress(
            progress_sink,
            node_id=node.node_id,
            attempt=attempt,
            phase="reviewer_completed",
            evidence={
                "verdict": verdict,
                "review_feedback_sha256": attempt_record["review_feedback_sha256"],
            },
        )
        if verdict == "BLOCKED":
            return _transaction_blocked(
                node=node,
                verdict="REVIEW_BLOCKED",
                errors=[str(feedback["summary"])],
                attempts=attempts,
                command_results=command_results,
                transaction_receipt_path=transaction_receipt_path,
                started_at=started_at,
                started_monotonic=started_monotonic,
                goal_hash=goal_hash,
            )
        if verdict == "REVISE":
            revision_signature = str(attempt_record["review_revision_signature"])
            repeated_revision_signatures[revision_signature] = (
                repeated_revision_signatures.get(revision_signature, 0) + 1
            )
            if repeated_revision_signatures[revision_signature] >= 2:
                course_correction_path = (
                    transaction_dir / f"course-correction-attempt-{attempt:03d}.json"
                )
                course_correction = write_course_correction_receipt(
                    course_correction_path,
                    trigger="brave_search_required_after_two_attempts",
                    run_id=run_id,
                    dag_id=str(runtime_identity.get("dag_id") or ""),
                    goal_hash=goal_hash,
                    target={
                        "kind": "generic_artifact_transaction",
                        "transaction_id": spec.transaction_id,
                        "node_id": node.node_id,
                    },
                    node_id=node.node_id,
                    agent=node.role,
                    attempt=attempt,
                    observed_state={
                        "attempt_count": attempt,
                        "repeated_revision_signature": revision_signature,
                        "repeated_revision_count": repeated_revision_signatures[
                            revision_signature
                        ],
                        "review_feedback_paths": [
                            item.get("review_feedback_path")
                            for item in attempts
                            if item.get("review_revision_signature") == revision_signature
                        ],
                        "advisory_only": True,
                    },
                    observed_artifact_path=review_feedback_path,
                    errors=[
                        (
                            "two identical reviewer revision signatures observed; "
                            "normal retry blocked before another same-context attempt"
                        )
                    ],
                    reason=(
                        "The same reviewer revision signature repeated across two attempts; "
                        "external research or a new plan is required before another attempt."
                    ),
                    required_action={
                        "type": "advisory_escalation_required",
                        "skill": "brave-search",
                        "skill_reference": "$brave-search",
                        "advisory_evidence_only": True,
                        "does_not_satisfy_acceptance_gate": True,
                    },
                    blocked_report_required={
                        "required": True,
                        "fields": [
                            "blocker_summary",
                            "attempt_count",
                            "repeated_revision_signature",
                            "searches_performed",
                            "searches_not_performed",
                        ],
                    },
                    mocked=False,
                    live=False,
                    provider_live=False,
                )
                attempt_record["course_correction_receipt_path"] = str(
                    course_correction_path
                )
                attempt_record["course_correction_trigger"] = course_correction["trigger"]
                return _transaction_blocked(
                    node=node,
                    verdict="COURSE_CORRECTION_REQUIRED",
                    errors=[
                        "brave_search_required_after_two_attempts",
                        f"course_correction_receipt_path:{course_correction_path}",
                    ],
                    attempts=attempts,
                    command_results=command_results,
                    transaction_receipt_path=transaction_receipt_path,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    goal_hash=goal_hash,
                )
            previous_artifact_sha256s = {
                str(item["sha256"]) for item in artifacts if isinstance(item.get("sha256"), str)
            }
            revision = {
                "source_attempt": attempt,
                "review_feedback_path": str(review_feedback_path),
                "review_feedback_sha256": file_sha256(review_feedback_path),
                "summary": feedback["summary"],
                "findings": feedback["findings"],
            }
            _emit_transaction_progress(
                progress_sink,
                node_id=node.node_id,
                attempt=attempt,
                phase="revision_committed",
                evidence={
                    "review_feedback_sha256": revision["review_feedback_sha256"],
                    "instruction": revision["summary"],
                },
            )
            continue
        acceptance_errors = validate_acceptance_policy(
            spec=spec,
            producer_receipt=producer_receipt,
            review_feedback=feedback,
            artifacts=artifacts,
            previous_artifact_sha256s=previous_artifact_sha256s,
            accepted_inputs=accepted_inputs,
        )
        if acceptance_errors:
            return _transaction_blocked(
                node=node,
                verdict="ACCEPTANCE_POLICY_BLOCKED",
                errors=acceptance_errors,
                attempts=attempts,
                command_results=command_results,
                transaction_receipt_path=transaction_receipt_path,
                started_at=started_at,
                started_monotonic=started_monotonic,
                goal_hash=goal_hash,
            )
        accepted, accepted_sha256 = write_accepted_manifest(
            path=accepted_manifest_path,
            run_id=run_id,
            node_id=node.node_id,
            spec=spec,
            attempt=attempt,
            work_order_sha256=work_order_sha256,
            candidate_manifest_path=candidate_manifest_path,
            review_feedback_path=review_feedback_path,
            artifacts=artifacts,
            accepted_inputs=accepted_inputs,
            validation_receipt_path=(
                validation_receipt_path if spec.validator is not None else None
            ),
        )
        projection = accepted_projection(
            path=accepted_manifest_path, sha256=accepted_sha256, payload=accepted
        )
        _emit_transaction_progress(
            progress_sink,
            node_id=node.node_id,
            attempt=attempt,
            phase="accepted_manifest_written",
            evidence={"accepted_manifest_sha256": accepted_sha256},
        )
        if spec.continuation is not None:
            return _continue_transaction(
                node=node,
                run_id=run_id,
                run_dir=run_dir,
                spec=spec,
                accepted=accepted,
                accepted_manifest_path=accepted_manifest_path,
                accepted_manifest_sha256=accepted_sha256,
                projection=projection,
                attempts=attempts,
                transaction_receipt_path=transaction_receipt_path,
                started_at=started_at,
                started_monotonic=started_monotonic,
                resumed=False,
                runtime_identity=runtime_identity,
                cancel_event=cancel_event,
                goal_hash=goal_hash,
            )
        _write_transaction_receipt(
            path=transaction_receipt_path,
            run_id=run_id,
            node=node,
            state="ACCEPTED",
            attempts=attempts,
            accepted_manifest_path=accepted_manifest_path,
            accepted_manifest_sha256=accepted_sha256,
            goal_hash=goal_hash,
        )
        return _transaction_record(
            node=node,
            state="ACCEPTED",
            status="PASS",
            verdict="PASS",
            attempts=attempts,
            command_results=command_results,
            transaction_receipt_path=transaction_receipt_path,
            accepted_manifest_path=accepted_manifest_path,
            accepted_manifest_sha256=accepted_sha256,
            accepted_output=projection,
            resumed=False,
            started_at=started_at,
            duration_seconds=time.monotonic() - started_monotonic,
            goal_hash=goal_hash,
        )
    return _transaction_blocked(
        node=node,
        verdict="MAX_ATTEMPTS_EXHAUSTED",
        errors=["review requested revision after final bounded attempt"],
        attempts=attempts,
        command_results=command_results,
        transaction_receipt_path=transaction_receipt_path,
        started_at=started_at,
        started_monotonic=started_monotonic,
        goal_hash=goal_hash,
    )


def _emit_transaction_progress(
    sink: Callable[[str, int, str, dict[str, Any]], None] | None,
    *,
    node_id: str,
    attempt: int,
    phase: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    if sink is not None:
        sink(node_id, attempt, phase, evidence or {})


def _review_revision_signature(feedback: dict[str, Any]) -> str:
    canonical = {
        "summary": feedback.get("summary"),
        "findings": feedback.get("findings") if isinstance(feedback.get("findings"), list) else [],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _continuation_arg(command: tuple[str, ...], option: str) -> str | None:
    for index, item in enumerate(command):
        if item == option and index + 1 < len(command):
            return command[index + 1]
        prefix = f"{option}="
        if item.startswith(prefix):
            return item.removeprefix(prefix)
    return None


def _continuation_request(command: tuple[str, ...]) -> dict[str, Any]:
    request_path = _continuation_arg(command, "--request")
    if request_path is None:
        return {}
    try:
        payload = json.loads(Path(request_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _approval_target_for_continuation(
    *,
    run_id: str,
    node: DagNode,
    spec: ArtifactTransactionSpec,
    accepted_manifest_sha256: str,
    command_sha256: str,
    action: str,
    goal_hash: str | None,
) -> dict[str, str]:
    command = spec.continuation.command if spec.continuation is not None else ()
    request = _continuation_request(command)
    target = {
        "id": f"generic-dag-transaction:{run_id}:{spec.transaction_id}",
        "run_id": run_id,
        "node_id": node.node_id,
        "transaction_id": spec.transaction_id,
        "action": action,
        "accepted_manifest_sha256": accepted_manifest_sha256,
        "continuation_command_sha256": command_sha256,
    }
    if goal_hash is not None:
        target["goal_hash"] = goal_hash
    for option, field in (
        ("--json-output", "result_json_path"),
        ("--markdown-output", "result_markdown_path"),
        ("--rollback-receipt", "rollback_artifact_path"),
        ("--ledger", "side_effect_ledger_path"),
    ):
        value = _continuation_arg(command, option)
        if value is not None:
            target[field] = str(Path(value).expanduser())
    publish_path = request.get("publish_path")
    if isinstance(publish_path, str) and publish_path.strip():
        target["expected_side_effect_path"] = str(Path(publish_path).expanduser())
        target.setdefault(
            "side_effect_ledger_path",
            str(Path(publish_path).expanduser() / "publication-ledger.json"),
        )
    return target


def _continue_transaction(
    *,
    node: DagNode,
    run_id: str,
    run_dir: Path,
    spec: ArtifactTransactionSpec,
    accepted: dict[str, Any],
    accepted_manifest_path: Path,
    accepted_manifest_sha256: str,
    projection: dict[str, Any],
    attempts: list[dict[str, Any]],
    transaction_receipt_path: Path,
    started_at: str,
    started_monotonic: float,
    resumed: bool,
    runtime_identity: dict[str, Any],
    cancel_event: Event,
    goal_hash: str | None,
) -> dict[str, Any]:
    continuation = spec.continuation
    assert continuation is not None
    command_sha256 = canonical_command_sha256(continuation.command)
    approval_receipt_path = transaction_receipt_path.parent / "approval-gate-receipt.json"
    if continuation.approval is not None:
        expected_target = _approval_target_for_continuation(
            run_id=run_id,
            node=node,
            spec=spec,
            accepted_manifest_sha256=accepted_manifest_sha256,
            command_sha256=command_sha256,
            action=continuation.approval.action,
            goal_hash=goal_hash,
        )
        approval = evaluate_approval_gate(
            approval_packet=continuation.approval.packet_path,
            requested_action=continuation.approval.action,
            run_dir=transaction_receipt_path.parent,
            output=approval_receipt_path,
            expected_target=expected_target,
        )
        if approval["status"] != "PASS":
            _write_transaction_receipt(
                path=transaction_receipt_path,
                run_id=run_id,
                node=node,
                state="APPROVAL_REQUIRED",
                attempts=attempts,
                accepted_manifest_path=accepted_manifest_path,
                accepted_manifest_sha256=accepted_manifest_sha256,
                approval_gate_receipt_path=approval_receipt_path,
                goal_hash=goal_hash,
            )
            return _transaction_record(
                node=node,
                state="APPROVAL_REQUIRED",
                status="BLOCKED",
                verdict="APPROVAL_REQUIRED",
                attempts=attempts,
                command_results=[],
                transaction_receipt_path=transaction_receipt_path,
                accepted_manifest_path=accepted_manifest_path,
                accepted_manifest_sha256=accepted_manifest_sha256,
                errors=approval["errors"],
                resumed=resumed,
                started_at=started_at,
                duration_seconds=time.monotonic() - started_monotonic,
                goal_hash=goal_hash,
            )
    continuation_context = transaction_receipt_path.parent / "continuation-context.json"
    write_json(
        continuation_context,
        {
            "schema": "tau.generic_artifact_continuation_context.v1",
            "run_id": run_id,
            "node_id": node.node_id,
            "transaction_id": spec.transaction_id,
            "accepted_manifest_path": str(accepted_manifest_path),
            "accepted_manifest_sha256": accepted_manifest_sha256,
            "artifacts": accepted["artifacts"],
            "continuation_command_sha256": command_sha256,
            **({"goal_hash": goal_hash} if goal_hash is not None else {}),
        },
    )
    result = _run_command(
        list(continuation.command),
        cwd=run_dir,
        timeout_seconds=continuation.timeout_seconds,
        env_overrides={"TAU_GENERIC_DAG_CONTEXT": str(continuation_context)},
        cancel_event=cancel_event,
        runtime_identity=_transaction_runtime_identity(
            runtime_identity,
            node=node,
            phase="continuation",
            attempt=int(accepted.get("attempt") or max(len(attempts), 1)),
            work_order_sha256=str(accepted.get("work_order_sha256") or accepted_manifest_sha256),
            goal_hash=(
                str(runtime_identity.get("goal"))
                if runtime_identity.get("goal") is not None
                else None
            ),
            artifact_dir=transaction_receipt_path.parent / "runtime" / "continuation",
        ),
    )
    if result.returncode != 0:
        return _transaction_blocked(
            node=node,
            verdict="CONTINUATION_ERROR",
            errors=[_command_error(result)],
            attempts=attempts,
            command_results=[_command_result_dict(result, elapsed_seconds=0.0)],
            transaction_receipt_path=transaction_receipt_path,
            started_at=started_at,
            started_monotonic=started_monotonic,
            goal_hash=goal_hash,
        )
    _write_transaction_receipt(
        path=transaction_receipt_path,
        run_id=run_id,
        node=node,
        state="CONTINUED",
        attempts=attempts,
        accepted_manifest_path=accepted_manifest_path,
        accepted_manifest_sha256=accepted_manifest_sha256,
        approval_gate_receipt_path=approval_receipt_path
        if continuation.approval is not None
        else None,
        continuation={"command_sha256": command_sha256, "returncode": result.returncode},
        goal_hash=goal_hash,
    )
    return _transaction_record(
        node=node,
        state="CONTINUED",
        status="PASS",
        verdict="PASS",
        attempts=attempts,
        command_results=[_command_result_dict(result, elapsed_seconds=0.0)],
        transaction_receipt_path=transaction_receipt_path,
        accepted_manifest_path=accepted_manifest_path,
        accepted_manifest_sha256=accepted_manifest_sha256,
        accepted_output=projection,
        resumed=resumed,
        started_at=started_at,
        duration_seconds=time.monotonic() - started_monotonic,
        goal_hash=goal_hash,
    )


def _transaction_blocked(
    *,
    node: DagNode,
    verdict: str,
    errors: list[str],
    attempts: list[dict[str, Any]],
    command_results: list[dict[str, Any]],
    transaction_receipt_path: Path,
    started_at: str,
    started_monotonic: float,
    goal_hash: str | None,
) -> dict[str, Any]:
    return _transaction_record(
        node=node,
        state="BLOCKED",
        status="BLOCKED",
        verdict=verdict,
        attempts=attempts,
        command_results=command_results,
        transaction_receipt_path=transaction_receipt_path,
        errors=errors,
        resumed=False,
        started_at=started_at,
        duration_seconds=time.monotonic() - started_monotonic,
        goal_hash=goal_hash,
    )


def _transaction_record(
    *,
    node: DagNode,
    state: str,
    status: str,
    verdict: str,
    attempts: list[dict[str, Any]],
    command_results: list[dict[str, Any]],
    transaction_receipt_path: Path,
    accepted_manifest_path: Path | None = None,
    accepted_manifest_sha256: str | None = None,
    accepted_output: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    resumed: bool,
    started_at: str,
    duration_seconds: float,
    goal_hash: str | None,
) -> dict[str, Any]:
    spec = node.transaction
    assert spec is not None
    producer_provider_live = any(item.get("producer_provider_live") is True for item in attempts)
    reviewer_provider_live = any(item.get("review_provider_live") is True for item in attempts)
    provider_live = producer_provider_live or reviewer_provider_live
    live = provider_live or any(
        item.get("review_live") is True or item.get("producer_live") is True for item in attempts
    )
    return {
        "node_id": node.node_id,
        "role": node.role,
        "status": status,
        "verdict": verdict,
        "transaction_id": spec.transaction_id,
        "transaction_state": state,
        "mocked": False,
        "live": live,
        "provider_live": provider_live,
        "producer_provider_live": producer_provider_live,
        "reviewer_provider_live": reviewer_provider_live,
        "transaction_receipt_path": str(transaction_receipt_path),
        "accepted_manifest_path": str(accepted_manifest_path) if accepted_manifest_path else None,
        "accepted_manifest_sha256": accepted_manifest_sha256,
        "accepted_output": accepted_output,
        "artifacts": accepted_output.get("artifacts", []) if accepted_output else [],
        "attempt_count": len(attempts),
        "attempts": attempts,
        "command_results": command_results,
        "receipt_path": str(node.receipt_path),
        "work_order_path": str(node.work_order_path),
        "work_order_sha256": _work_order_sha256(node),
        "goal_hash": goal_hash,
        "resumed": resumed,
        "started_at": started_at,
        "finished_at": _utc_stamp(),
        "duration_seconds": round(duration_seconds, 3),
        "errors": errors or [],
    }


def _write_transaction_receipt(
    *,
    path: Path,
    run_id: str,
    node: DagNode,
    state: str,
    attempts: list[dict[str, Any]],
    accepted_manifest_path: Path,
    accepted_manifest_sha256: str,
    approval_gate_receipt_path: Path | None = None,
    continuation: dict[str, Any] | None = None,
    goal_hash: str | None = None,
) -> None:
    spec = node.transaction
    assert spec is not None
    write_json(
        path,
        {
            "schema": TRANSACTION_RECEIPT_SCHEMA,
            "status": "PASS" if state in {"ACCEPTED", "CONTINUED"} else "BLOCKED",
            "state": state,
            "run_id": run_id,
            "node_id": node.node_id,
            "transaction_id": spec.transaction_id,
            "goal_hash": goal_hash,
            "work_order_sha256": _work_order_sha256(node),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "accepted_manifest_path": str(accepted_manifest_path),
            "accepted_manifest_sha256": accepted_manifest_sha256,
            "approval_gate_receipt_path": str(approval_gate_receipt_path)
            if approval_gate_receipt_path
            else None,
            "continuation": continuation,
            "errors": [],
        },
    )


def _write_legacy_node_context(
    *,
    node: DagNode,
    run_id: str,
    run_dir: Path,
    accepted_inputs: list[dict[str, Any]],
) -> tuple[Path, str]:
    path = run_dir / "node-contexts" / f"{node.node_id}.json"
    write_json(
        path,
        {
            "schema": "tau.generic_dag_node_context.v1",
            "run_id": run_id,
            "node_id": node.node_id,
            "accepted_inputs": accepted_inputs,
        },
    )
    return path, file_sha256(path)


def _node_record(
    node: DagNode,
    receipt: dict[str, Any],
    *,
    attempt_count: int,
    resumed: bool,
    command_results: list[dict[str, Any]],
    started_at: str,
    finished_at: str,
    duration_seconds: float,
) -> dict[str, Any]:
    accepted_output = receipt.get("accepted_output")
    return {
        "node_id": node.node_id,
        "role": node.role,
        "status": str(receipt.get("status") or "UNKNOWN").upper(),
        "verdict": str(receipt.get("verdict") or "UNKNOWN").upper(),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "provider_live": receipt.get("provider_live"),
        "provider_status": receipt.get("provider_status"),
        "provider_verdict": receipt.get("provider_verdict"),
        "goal_hash": receipt.get("goal_hash"),
        "attempt": receipt.get("attempt"),
        "workspace_id": receipt.get("workspace_id"),
        "pane_id": receipt.get("pane_id"),
        "terminal_id": receipt.get("terminal_id"),
        "visible_log_path": receipt.get("visible_log_path"),
        "visible_log_sha256": receipt.get("visible_log_sha256"),
        "execution_evidence": receipt.get("execution_evidence")
        if isinstance(receipt.get("execution_evidence"), dict)
        else None,
        "usage": receipt.get("usage") if isinstance(receipt.get("usage"), dict) else None,
        "cost_estimate": receipt.get("cost_estimate")
        if isinstance(receipt.get("cost_estimate"), dict)
        else None,
        "attempt_count": attempt_count,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(duration_seconds, 3),
        "receipt_path": str(node.receipt_path),
        "work_order_path": str(node.work_order_path) if node.work_order_path else None,
        "work_order_sha256": _work_order_sha256(node),
        "resumed": resumed,
        "command_results": command_results,
        "artifacts": receipt.get("artifacts") if isinstance(receipt.get("artifacts"), list) else [],
        "accepted_output": accepted_output if isinstance(accepted_output, dict) else None,
        "errors": receipt.get("errors") if isinstance(receipt.get("errors"), list) else [],
    }


def _blocked_node_record(
    node: DagNode,
    *,
    verdict: str,
    errors: list[str],
    attempt_count: int = 0,
    command_results: list[dict[str, Any]] | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    now = _utc_stamp()
    return {
        "node_id": node.node_id,
        "role": node.role,
        "status": "BLOCKED",
        "verdict": verdict,
        "attempt_count": attempt_count,
        "started_at": started_at or now,
        "finished_at": finished_at or now,
        "duration_seconds": round(duration_seconds or 0.0, 3),
        "receipt_path": str(node.receipt_path),
        "work_order_path": str(node.work_order_path) if node.work_order_path else None,
        "work_order_sha256": _work_order_sha256(node),
        "resumed": False,
        "command_results": command_results or [],
        "artifacts": [],
        "accepted_output": None,
        "errors": errors,
    }


def _write_blocked_node_receipt_if_missing(
    node: DagNode,
    record: dict[str, Any],
    *,
    goal_hash: str | None,
    attempt: int,
) -> None:
    if node.receipt_path.exists():
        return
    payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": node.node_id,
        "role": node.role,
        "status": record["status"],
        "verdict": record["verdict"],
        "mocked": False,
        "live": False,
        "provider_live": False,
        "goal_hash": goal_hash,
        "attempt": attempt,
        "accepted_output": None,
        "artifacts": [],
        "commands_run": record.get("command_results", []),
        "handoff_summary": f"{node.node_id} blocked with verdict {record['verdict']}",
        "errors": record.get("errors", []),
        "policy_exceptions": [],
        "receipt_path": str(node.receipt_path),
        "timestamp": _utc_stamp(),
    }
    write_json(node.receipt_path, payload)


def _validate_spec(spec: dict[str, Any], *, spec_path: Path) -> dict[str, DagNode]:
    validate_generic_dag_public_boundary(spec)
    if spec.get("schema") != GENERIC_DAG_SPEC_SCHEMA:
        raise RuntimeError(f"generic DAG spec schema must be {GENERIC_DAG_SPEC_SCHEMA}")
    for key in ("run_id", "run_dir", "nodes"):
        if key not in spec:
            raise RuntimeError(f"generic DAG spec missing {key}")
    if not isinstance(spec["run_id"], str) or not spec["run_id"].strip():
        raise RuntimeError("generic DAG spec run_id must be a non-empty string")
    if not isinstance(spec["run_dir"], str) or not spec["run_dir"].strip():
        raise RuntimeError("generic DAG spec run_dir must be a non-empty string")
    goal = spec.get("goal")
    legacy_goal_hash = spec.get("goal_hash")
    if goal is not None:
        if not isinstance(goal, dict):
            raise RuntimeError("generic DAG goal must be an object")
        full_goal_keys = (
            "goal_id",
            "goal_version",
            "goal_hash",
            "summary",
            "completion_criteria",
        )
        if any(key in goal for key in full_goal_keys):
            for key in full_goal_keys:
                if key not in goal:
                    raise RuntimeError(f"generic DAG goal missing {key}")
            if not isinstance(goal["goal_id"], str) or not goal["goal_id"].strip():
                raise RuntimeError("generic DAG goal.goal_id must be a non-empty string")
            if not isinstance(goal["goal_version"], (int, str)) or isinstance(
                goal["goal_version"], bool
            ):
                raise RuntimeError("generic DAG goal.goal_version must be an integer or string")
            if not isinstance(goal["summary"], str) or not goal["summary"].strip():
                raise RuntimeError("generic DAG goal.summary must be a non-empty string")
            criteria = goal["completion_criteria"]
            if (
                not isinstance(criteria, list)
                or not criteria
                or not all(isinstance(item, str) and item.strip() for item in criteria)
            ):
                raise RuntimeError(
                    "generic DAG goal.completion_criteria must be a non-empty string list"
                )
            hash_input = {key: value for key, value in goal.items() if key != "goal_hash"}
            expected = canonical_sha256(hash_input)
            if goal["goal_hash"] != expected:
                raise RuntimeError("generic DAG goal.goal_hash does not match canonical goal")
            if legacy_goal_hash is not None and legacy_goal_hash != goal["goal_hash"]:
                raise RuntimeError("generic DAG goal_hash does not match goal.goal_hash")
    max_concurrency = spec.get("max_concurrency", 1)
    if type(max_concurrency) is not int or max_concurrency < 1:
        raise RuntimeError("generic DAG spec max_concurrency must be a positive integer")
    raw_nodes = spec["nodes"]
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise RuntimeError("generic DAG spec nodes must be a non-empty list")
    base_dir = spec_path.parent
    nodes: dict[str, DagNode] = {}
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            raise RuntimeError("generic DAG spec node entries must be objects")
        node = _parse_node(raw_node, base_dir=base_dir)
        if node.node_id in nodes:
            raise RuntimeError(f"duplicate DAG node_id: {node.node_id}")
        nodes[node.node_id] = node
    for node in nodes.values():
        for dep in node.depends_on:
            if dep not in nodes:
                raise RuntimeError(f"node {node.node_id} depends on unknown node {dep}")
    _topological_order(nodes)
    return nodes


def _generic_goal_hash(spec: dict[str, Any]) -> str | None:
    goal = spec.get("goal")
    if isinstance(goal, dict) and isinstance(goal.get("goal_hash"), str):
        return str(goal["goal_hash"])
    value = spec.get("goal_hash")
    return str(value) if isinstance(value, str) and value else None


def load_generic_dag_spec(path: Path) -> dict[str, Any]:
    """Load a generic DAG source document without executing it."""

    return _read_source_object(path.expanduser().resolve(), label="generic DAG spec")


def validate_generic_dag_spec(payload: dict[str, Any], *, source_path: Path) -> dict[str, DagNode]:
    """Public pure validation boundary shared by runtime and DagPlan compiler."""

    return _validate_spec(payload, spec_path=source_path.expanduser().resolve())


def _parse_node(raw_node: dict[str, Any], *, base_dir: Path) -> DagNode:
    node_id = _required_string(raw_node, "node_id")
    role = (
        strict_non_empty_string(raw_node["role"], f"node {node_id} role")
        if "role" in raw_node
        else node_id
    )
    command = raw_node.get("command")
    skill_raw = raw_node.get("skill")
    skill = (
        parse_skill_dag_spec(skill_raw, base_dir=base_dir, node_id=node_id)
        if skill_raw is not None
        else None
    )
    browser_raw = raw_node.get("browser")
    browser = (
        parse_browser_dag_spec(browser_raw, base_dir=base_dir, node_id=node_id)
        if browser_raw is not None
        else None
    )
    transaction_raw = raw_node.get("transaction")
    if skill is None and browser is None and (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise RuntimeError(f"node {node_id} command must be a non-empty string list")
    if skill is not None and command is not None:
        raise RuntimeError(f"node {node_id} cannot declare both command and skill")
    if browser is not None and command is not None:
        raise RuntimeError(f"node {node_id} cannot declare both command and browser")
    if skill is not None and browser is not None:
        raise RuntimeError(
            f"node {node_id} must declare exactly one of command, skill, browser, transaction"
        )
    command = tuple(command) if isinstance(command, list) else ()
    depends_on = raw_node.get("depends_on", [])
    if not isinstance(depends_on, list) or not all(isinstance(dep, str) for dep in depends_on):
        raise RuntimeError(f"node {node_id} depends_on must be a string list")
    accepted_context_from = raw_node.get("accepted_context_from", depends_on)
    if not isinstance(accepted_context_from, list) or not all(
        isinstance(dep, str) for dep in accepted_context_from
    ):
        raise RuntimeError(f"node {node_id} accepted_context_from must be a string list")
    if not set(accepted_context_from).issubset(set(depends_on)):
        raise RuntimeError(f"node {node_id} accepted_context_from must be a subset of depends_on")
    timeout_seconds = (
        strict_positive_number(raw_node["timeout_seconds"], f"node {node_id} timeout_seconds")
        if "timeout_seconds" in raw_node
        else 60.0
    )
    max_attempts = (
        strict_positive_int(raw_node["max_attempts"], f"node {node_id} max_attempts")
        if "max_attempts" in raw_node
        else 1
    )
    receipt_path = _resolve_path(_required_string(raw_node, "receipt_path"), base_dir=base_dir)
    work_order_raw = raw_node.get("work_order_path")
    work_order_text = strict_optional_path(work_order_raw, f"node {node_id} work_order_path")
    work_order_path = (
        _resolve_path(work_order_text, base_dir=base_dir) if work_order_text is not None else None
    )
    transaction = (
        parse_transaction_spec(transaction_raw, base_dir=base_dir, node_id=node_id)
        if transaction_raw is not None
        else None
    )
    if transaction is not None and work_order_path is None:
        raise RuntimeError(f"node {node_id} transaction requires work_order_path")
    if skill is not None and work_order_path is None:
        raise RuntimeError(f"node {node_id} skill requires work_order_path")
    if transaction is not None and (skill is not None or browser is not None):
        raise RuntimeError(
            f"node {node_id} cannot declare transaction with skill or browser"
        )
    return DagNode(
        node_id=node_id,
        role=role,
        command=command,
        depends_on=tuple(depends_on),
        accepted_context_from=tuple(accepted_context_from),
        receipt_path=receipt_path,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        work_order_path=work_order_path,
        transaction=transaction,
        skill=skill,
        browser=browser,
    )


def _topological_order(nodes: dict[str, DagNode]) -> list[DagNode]:
    ordered: list[DagNode] = []
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in permanent:
            return
        if node_id in temporary:
            raise RuntimeError(f"DAG cycle detected at node {node_id}")
        temporary.add(node_id)
        for dep in nodes[node_id].depends_on:
            visit(dep)
        temporary.remove(node_id)
        permanent.add(node_id)
        ordered.append(nodes[node_id])

    for node_id in nodes:
        visit(node_id)
    return ordered


def _validate_node_receipt(
    receipt: dict[str, Any],
    node: DagNode,
    *,
    expected_goal_hash: str | None = None,
) -> list[str]:
    errors = []
    if receipt.get("schema") != GENERIC_DAG_NODE_RECEIPT_SCHEMA:
        errors.append(f"schema must be {GENERIC_DAG_NODE_RECEIPT_SCHEMA}")
    if receipt.get("node_id") != node.node_id:
        errors.append(f"node_id must be {node.node_id}")
    if str(receipt.get("status") or "").upper() not in {"PASS", "BLOCKED"}:
        errors.append("status must be PASS or BLOCKED")
    allowed_verdicts = {
        "PASS",
        "REVISE",
        "BLOCKED",
        "CANCELLED",
        "SUBAGENT_ERROR",
        "SUBAGENT_TIMEOUT",
    }
    if str(receipt.get("verdict") or "").upper() not in allowed_verdicts:
        errors.append(
            "verdict must be PASS, REVISE, BLOCKED, CANCELLED, "
            "SUBAGENT_ERROR, or SUBAGENT_TIMEOUT"
        )
    for key in ("artifacts", "commands_run", "errors", "policy_exceptions"):
        if not isinstance(receipt.get(key), list):
            errors.append(f"{key} must be a list")
    if not isinstance(receipt.get("handoff_summary"), str) or not receipt["handoff_summary"]:
        errors.append("handoff_summary must be a non-empty string")
    expected_work_order_hash = _work_order_sha256(node)
    if node.work_order_path is not None and expected_work_order_hash is None:
        errors.append(f"work_order_path not found or unreadable: {node.work_order_path}")
    if (
        expected_work_order_hash is not None
        and receipt.get("work_order_sha256") != expected_work_order_hash
    ):
        errors.append(
            f"work_order_sha256 must match current work_order_path {node.work_order_path}"
        )
    if expected_goal_hash is not None:
        observed_goal_hash = receipt.get("goal_hash")
        if not isinstance(observed_goal_hash, str) or not observed_goal_hash.strip():
            errors.append("goal_hash must be a non-empty string")
        elif observed_goal_hash != expected_goal_hash:
            errors.append("goal_hash does not match the active DAG goal")
    if (
        node.skill is None
        and receipt.get("live") is True
        and receipt.get("provider_live") is not True
    ):
        errors.extend(_validate_local_execution_evidence(receipt.get("execution_evidence")))
    errors.extend(_validate_provider_live_receipt(receipt))
    return errors


def _validate_skill_node_receipt(
    receipt: dict[str, Any],
    node: DagNode,
    *,
    expected_goal_hash: str | None,
) -> list[str]:
    assert node.skill is not None
    errors = _validate_node_receipt(receipt, node, expected_goal_hash=expected_goal_hash)
    if receipt.get("skill_provider") != node.skill.provider:
        errors.append(f"skill_provider must be {node.skill.provider}")
    if receipt.get("capability") != node.skill.capability:
        errors.append(f"capability must be {node.skill.capability}")
    for artifact_error in _skill_resume_artifact_errors(_receipt_artifacts(receipt)):
        errors.append(artifact_error)
    return errors


def _validate_browser_node_receipt(
    receipt: dict[str, Any],
    node: DagNode,
    *,
    expected_goal_hash: str | None,
) -> list[str]:
    assert node.browser is not None
    errors: list[str] = []
    if receipt.get("schema") != GENERIC_DAG_NODE_RECEIPT_SCHEMA:
        errors.append(f"schema must be {GENERIC_DAG_NODE_RECEIPT_SCHEMA}")
    if receipt.get("node_id") != node.node_id:
        errors.append(f"node_id must be {node.node_id}")
    if str(receipt.get("status") or "").upper() not in {"PASS", "BLOCKED"}:
        errors.append("status must be PASS or BLOCKED")
    if str(receipt.get("verdict") or "").upper() not in {"PASS", "BROWSER_HANDLER_BLOCKED"}:
        errors.append("verdict must be PASS or BROWSER_HANDLER_BLOCKED")
    if receipt.get("browser_provider") != "surf":
        errors.append("browser_provider must be surf")
    if receipt.get("capability") != "browser_handler":
        errors.append("capability must be browser_handler")
    for key in ("artifacts", "commands_run", "errors", "policy_exceptions"):
        if not isinstance(receipt.get(key), list):
            errors.append(f"{key} must be a list")
    if not isinstance(receipt.get("handoff_summary"), str) or not receipt["handoff_summary"]:
        errors.append("handoff_summary must be a non-empty string")
    expected_work_order_hash = _work_order_sha256(node)
    if node.work_order_path is not None and expected_work_order_hash is None:
        errors.append(f"work_order_path not found or unreadable: {node.work_order_path}")
    if (
        expected_work_order_hash is not None
        and receipt.get("work_order_sha256") != expected_work_order_hash
    ):
        errors.append(
            f"work_order_sha256 must match current work_order_path {node.work_order_path}"
        )
    if expected_goal_hash is not None:
        observed_goal_hash = receipt.get("goal_hash")
        if not isinstance(observed_goal_hash, str) or not observed_goal_hash.strip():
            errors.append("goal_hash must be a non-empty string")
        elif observed_goal_hash != expected_goal_hash:
            errors.append("goal_hash does not match the active DAG goal")
    typed_receipt_path = receipt.get("browser_receipt_path")
    if not isinstance(typed_receipt_path, str) or not typed_receipt_path.strip():
        errors.append("browser_receipt_path must be a non-empty string")
    else:
        typed_path = Path(typed_receipt_path).expanduser().resolve()
        if not typed_path.is_file():
            errors.append(f"browser_receipt_missing:{typed_path}")
        else:
            typed = _read_json_object(typed_path, label="browser DAG receipt")
            if typed.get("schema") != BROWSER_DAG_RECEIPT_SCHEMA:
                errors.append(f"browser_receipt_schema must be {BROWSER_DAG_RECEIPT_SCHEMA}")
            typed_screenshot = typed.get("screenshot")
            if receipt.get("status") == "PASS":
                errors.extend(_browser_screenshot_hash_errors(typed_screenshot))
    for artifact_error in _skill_resume_artifact_errors(_receipt_artifacts(receipt)):
        errors.append(artifact_error)
    return errors


def _browser_screenshot_hash_errors(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["browser_screenshot must be an object"]
    path_value = value.get("path")
    sha256_value = value.get("sha256")
    if not isinstance(path_value, str) or not path_value.strip():
        return ["browser_screenshot.path must be a non-empty string"]
    if not isinstance(sha256_value, str) or not sha256_value.startswith("sha256:"):
        return ["browser_screenshot.sha256 must be sha256-prefixed"]
    screenshot_path = Path(path_value).expanduser().resolve()
    if not screenshot_path.is_file():
        return [f"browser_screenshot_missing:{screenshot_path}"]
    actual = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
    if sha256_value != f"sha256:{actual}":
        return [f"browser_screenshot_hash_mismatch:{screenshot_path}"]
    return []


def _receipt_artifacts(receipt: dict[str, Any]) -> list[Any]:
    artifacts = receipt.get("artifacts")
    return list(artifacts) if isinstance(artifacts, list) else []


def _skill_resume_artifact_errors(artifacts: list[Any]) -> list[str]:
    errors: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            errors.append("skill_resume_artifact_invalid")
            continue
        path = Path(artifact["path"]).expanduser().resolve()
        if not path.is_file():
            errors.append(f"skill_resume_artifact_missing:{path}")
        elif artifact.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
            errors.append(f"skill_resume_artifact_hash_mismatch:{path}")
    return errors


def _append_resume_rejected_event(
    events_path: Path,
    *,
    run_id: str,
    node: DagNode,
    receipt: dict[str, Any],
    errors: list[str],
) -> None:
    _append_event(
        events_path,
        "node_resume_rejected",
        {
            "run_id": run_id,
            "node_id": node.node_id,
            "receipt_path": str(node.receipt_path),
            "expected_schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
            "observed_schema": receipt.get("schema"),
            "errors": errors,
            "resume_action": "blocked_no_rerun",
        },
    )


def _attach_local_execution_evidence(
    receipt: dict[str, Any],
    command_result: dict[str, Any],
) -> None:
    if receipt.get("live") is not True or receipt.get("provider_live") is True:
        return
    runtime_event = command_result.get("runtime_event")
    runtime_event_state = runtime_event.get("state") if isinstance(runtime_event, dict) else None
    runtime_submit = command_result.get("runtime_submit_receipt")
    delivery_status = (
        runtime_submit.get("delivery_status") if isinstance(runtime_submit, dict) else None
    )
    receipt["execution_evidence"] = {
        "kind": "local_subprocess",
        "returncode": command_result.get("returncode"),
        "runtime_backend": command_result.get("runtime_backend"),
        "runtime_event_state": runtime_event_state,
        "runtime_submit_delivery_status": delivery_status,
        "runtime_artifact_count": len(command_result.get("runtime_artifacts", []))
        if isinstance(command_result.get("runtime_artifacts"), list)
        else 0,
        "command_result_sha256": canonical_sha256(command_result),
    }


def _validate_local_execution_evidence(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["live true requires local execution evidence"]
    expected = {
        "kind": "local_subprocess",
        "returncode": 0,
        "runtime_backend": "local",
        "runtime_event_state": "EXITED",
        "runtime_submit_delivery_status": "CONFIRMED",
    }
    errors = [
        f"execution_evidence.{key} must be {expected_value}"
        for key, expected_value in expected.items()
        if value.get(key) != expected_value
    ]
    if not isinstance(value.get("command_result_sha256"), str) or not str(
        value["command_result_sha256"]
    ).startswith("sha256:"):
        errors.append("execution_evidence.command_result_sha256 must be sha256")
    artifact_count = value.get("runtime_artifact_count")
    if not isinstance(artifact_count, int) or artifact_count < 1:
        errors.append("execution_evidence.runtime_artifact_count must be positive")
    return errors


def _has_local_execution_evidence_error(errors: list[str]) -> bool:
    return any(
        error == "live true requires local execution evidence"
        or error.startswith("execution_evidence.")
        for error in errors
    )


def _validate_provider_live_receipt(receipt: dict[str, Any]) -> list[str]:
    if receipt.get("provider_live") is not True:
        return []

    errors: list[str] = []
    if receipt.get("live") is not True:
        errors.append("live must be true when provider_live is true")
    for key in ("goal_hash", "workspace_id", "pane_id", "terminal_id"):
        if not isinstance(receipt.get(key), str) or not str(receipt.get(key)).strip():
            errors.append(f"{key} must be a non-empty string when provider_live is true")

    attempt = receipt.get("attempt")
    if not isinstance(attempt, int) or attempt < 1:
        errors.append("attempt must be a positive integer when provider_live is true")

    visible_log_path = receipt.get("visible_log_path")
    visible_log_sha256 = receipt.get("visible_log_sha256")
    if not isinstance(visible_log_path, str) or not visible_log_path.strip():
        errors.append("visible_log_path must be a non-empty string when provider_live is true")
    if not isinstance(visible_log_sha256, str) or not visible_log_sha256.strip():
        errors.append("visible_log_sha256 must be a non-empty string when provider_live is true")
    if isinstance(visible_log_path, str) and visible_log_path.strip():
        resolved_visible_log = Path(visible_log_path).expanduser()
        if not resolved_visible_log.exists():
            errors.append(f"visible_log_path does not exist: {visible_log_path}")
        elif isinstance(visible_log_sha256, str) and visible_log_sha256.strip():
            actual_sha256 = hashlib.sha256(resolved_visible_log.read_bytes()).hexdigest()
            if visible_log_sha256 != actual_sha256:
                errors.append("visible_log_sha256 must match visible_log_path contents")

    provider_binding = receipt.get("provider_binding")
    if isinstance(provider_binding, dict) and provider_binding.get("status") != "PASS":
        errors.append("provider_binding.status must be PASS when provider_live is true")
    return errors


def _work_order_sha256(node: DagNode) -> str | None:
    if node.work_order_path is None:
        return None
    try:
        data = node.work_order_path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _proof_scope(*, provider_live: bool, skill_live: bool) -> dict[str, list[str]]:
    proves = [
        "Tau can validate a generic DAG spec",
        "Tau can execute local subprocess workers in dependency order",
        "Tau can gate downstream nodes on schema-valid node receipts",
        "Tau can resume from existing valid node receipts",
        "Tau can reject stale work-order receipts when work_order_sha256 no longer matches",
        "Tau can write durable checkpoint and current-state artifacts",
        "Tau can fail closed on timeout, non-zero exit, invalid receipt, or blocked verdict",
    ]
    does_not_prove = [
        "remote Tailscale monitoring",
        "GitHub ticket closure",
        "production repository mutation",
    ]
    if provider_live:
        proves.append(
            "Tau can carry live provider-backed node evidence through the generic DAG receipt"
        )
    if skill_live:
        proves.append(
            "Tau can invoke a registered skill capability and hash-bind its returned artifacts"
        )
        does_not_prove.extend(
            [
                "skill output semantic correctness",
                "future skill route correctness",
            ]
        )
    else:
        does_not_prove.extend(
            [
                "live provider CLI execution",
                "Herdr pane visibility",
            ]
        )
    return {"proves": proves, "does_not_prove": does_not_prove}


def _spec_path_from_run_metadata(run_dir: Path) -> tuple[Path, Path]:
    for path in (
        run_dir / "current-state.json",
        run_dir / "checkpoint.json",
        run_dir / "run-receipt.json",
    ):
        payload = _optional_json_object(path)
        spec_path = payload.get("spec_path")
        if isinstance(spec_path, str) and spec_path:
            resolved = Path(spec_path).expanduser()
            if not resolved.is_absolute():
                resolved = run_dir / resolved
            return resolved.resolve(), path
    raise RuntimeError(
        "generic DAG run metadata does not record spec_path; rerun tau dag-run <dag-spec> directly"
    )


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    env_overrides: dict[str, str] | None = None,
    cancel_event: Event | None = None,
    runtime_identity: dict[str, Any] | None = None,
) -> LocalRuntimeExecutionResult:
    identity = runtime_identity or {}
    identity_seed = {
        "command": command,
        "cwd": str(cwd),
        "environment_names": sorted((env_overrides or {}).keys()),
    }
    identity_hash = canonical_sha256(identity_seed)
    attempt = int(identity.get("attempt", 1))
    node_id = str(identity.get("node_id") or "local-command")
    backend = LocalRuntimeBackend()
    return backend.execute(
        local_runtime_request(
        command=tuple(command),
            run_id=str(identity.get("run_id") or f"local:{identity_hash[-16:]}"),
            plan_revision=str(identity.get("plan_revision") or identity_hash),
            dag_id=str(identity.get("dag_id") or "generic-local-command"),
            node_id=node_id,
            attempt_id=str(identity.get("attempt_id") or f"{node_id}:attempt-{attempt:03d}"),
            attempt_number=attempt,
            execution_token=str(identity.get("execution_token") or identity_hash),
            work_order=identity.get("work_order", identity_seed),
            goal=identity.get("goal", {"kind": "development-local-command"}),
            cwd=cwd,
            env={**os.environ, **(env_overrides or {})},
            timeout_seconds=timeout_seconds,
            artifact_dir=(
                identity.get("artifact_dir")
                if isinstance(identity.get("artifact_dir"), Path)
                else None
            ),
            cancel_event=cancel_event,
        )
    )


def _transaction_runtime_identity(
    base: dict[str, Any],
    *,
    node: DagNode,
    phase: str,
    attempt: int,
    work_order_sha256: str,
    goal_hash: str | None,
    artifact_dir: Path,
) -> dict[str, Any]:
    attempt_id = f"{base['attempt_id']}:{phase}:{attempt:03d}"
    return {
        **base,
        "node_id": node.node_id,
        "attempt_id": attempt_id,
        "attempt": attempt,
        "execution_token": canonical_sha256(
            {
                "scheduler_execution_token": base["execution_token"],
                "attempt_id": attempt_id,
            }
        ),
        "work_order": work_order_sha256,
        "goal": goal_hash or base["goal"],
        "artifact_dir": artifact_dir,
    }


def _command_result_dict(
    result: subprocess.CompletedProcess[str] | LocalRuntimeExecutionResult,
    *,
    elapsed_seconds: float,
) -> dict[str, Any]:
    payload = {
        "argv": result.args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }
    if isinstance(result, LocalRuntimeExecutionResult):
        payload["runtime_backend"] = "local"
        payload["runtime_endpoint_lease"] = result.endpoint_lease.to_payload()
        payload["runtime_submit_receipt"] = result.submit_receipt.to_payload()
        payload["runtime_event"] = result.runtime_event.to_payload()
        payload["runtime_capture"] = result.capture.to_value()
        payload["runtime_artifacts"] = list(result.artifact_paths)
        payload["termination_cause"] = result.termination_cause
    return payload


def _command_error(
    result: subprocess.CompletedProcess[str] | LocalRuntimeExecutionResult,
) -> str:
    detail = (result.stderr or "").strip() or (result.stdout or "").strip() or "no output"
    return f"{' '.join(result.args)} exited {result.returncode}: {detail}"


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{key} must be a non-empty string")
    return value


def _resolve_path(value: str, *, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if isinstance(event, dict):
            events.append(event)
    return events


def _append_event(path: Path, kind: str, payload: dict[str, Any]) -> None:
    event = {
        "schema": GENERIC_DAG_EVENT_SCHEMA,
        "kind": kind,
        "timestamp": _utc_stamp(),
        **payload,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _append_legacy_node_dispatch_event(
    node: DagNode,
    *,
    run_id: str,
    events_path: Path,
    attempt: int,
) -> None:
    _append_event(
        events_path,
        "node_dispatch",
        {
            "run_id": run_id,
            "node_id": node.node_id,
            "attempt": attempt,
            "work_order_path": str(node.work_order_path) if node.work_order_path else None,
            "receipt_path": str(node.receipt_path),
        },
    )


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return payload


def _read_source_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} not found: {path}") from exc
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError(f"{label} YAML requires PyYAML")
        payload = yaml.safe_load(text)
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return payload


def _optional_json_object(path: Path) -> dict[str, Any]:
    if not str(path) or not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
