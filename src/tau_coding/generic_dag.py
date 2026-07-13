"""Generic receipt-gated DAG runner for Tau orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from tau_coding.approval_gate import evaluate_approval_gate
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlanNode
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan
from tau_coding.dag_runtime.subprocess_control import run_cancellable_subprocess
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
from tau_coding.skill_dag_adapter import (
    SkillDagSpec,
    execute_skill_dag_node,
    parse_skill_dag_spec,
)

GENERIC_DAG_SPEC_SCHEMA = "tau.generic_dag_spec.v1"
GENERIC_DAG_RUN_RECEIPT_SCHEMA = "tau.generic_dag_run_receipt.v1"
GENERIC_DAG_NODE_RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"
GENERIC_DAG_EVENT_SCHEMA = "tau.generic_dag_event.v1"
GENERIC_DAG_CHECKPOINT_SCHEMA = "tau.generic_dag_checkpoint.v1"


@dataclass(frozen=True)
class DagNode:
    node_id: str
    role: str
    command: list[str]
    depends_on: tuple[str, ...]
    accepted_context_from: tuple[str, ...]
    receipt_path: Path
    timeout_seconds: float
    max_attempts: int
    work_order_path: Path | None
    transaction: ArtifactTransactionSpec | None
    skill: SkillDagSpec | None


def run_generic_dag(
    *,
    spec_path: Path,
    resume: bool = True,
    resume_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a schema-valid, command-backed DAG spec.

    This runner intentionally uses local subprocess workers. Provider-specific
    adapters such as Herdr/Codex/OpenCode should generate commands or wrap this
    scheduler rather than changing the scheduler's receipt contract.
    """

    resolved_spec_path = spec_path.expanduser().resolve()
    spec = load_generic_dag_spec(resolved_spec_path)
    nodes = validate_generic_dag_spec(spec, source_path=resolved_spec_path)
    run_dir = Path(str(spec["run_dir"])).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    events_path = Path(str(spec.get("events_jsonl") or run_dir / "events.jsonl")).expanduser()
    if not events_path.is_absolute():
        events_path = run_dir / events_path
    events_path.parent.mkdir(parents=True, exist_ok=True)

    run_id = str(spec["run_id"])
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
    completed: set[str] = set()
    node_results: list[dict[str, Any]] = []
    checkpoint_path = run_dir / "checkpoint.json"
    current_state_path = run_dir / "current-state.json"
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

    nodes_by_id = nodes
    plan = compile_generic_dag_plan(spec, source_path=resolved_spec_path)

    def execute_plan_node(
        plan_node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        execution: DagNodeAttempt,
    ) -> dict[str, Any]:
        node = nodes_by_id[plan_node.node_id]
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
        return _run_node(
            node,
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            resume=resume,
            accepted_inputs=list(accepted_inputs),
            goal_hash=str(spec.get("goal_hash")) if spec.get("goal_hash") else None,
            scheduler_attempt=execution.attempt,
            cancel_event=execution.cancel_event,
        )

    scheduler_result = run_dag_plan(
        plan,
        execute_node=execute_plan_node,
        max_concurrency=1,
    )
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
        "max_observed_concurrency": scheduler_result.max_observed_concurrency,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "spec_path": str(resolved_spec_path),
        "resume_requested": resume,
        "resume_source": resume_source
        or {"mode": "spec_path", "spec_path": str(resolved_spec_path)},
        "events_jsonl": str(events_path),
        "checkpoint_path": str(checkpoint_path),
        "current_state_path": str(current_state_path),
        "node_count": len(nodes),
        "completed_node_count": len(completed),
        "nodes": node_results,
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
    cancel_event: Event,
) -> dict[str, Any]:
    if node.skill is not None:
        return _run_skill_node(
            node,
            run_id=run_id,
            accepted_inputs=accepted_inputs,
            goal_hash=goal_hash,
            resume=resume,
            cancel_event=cancel_event,
        )
    if node.transaction is not None:
        return _run_transaction_node(
            node,
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            resume=resume,
            accepted_inputs=accepted_inputs,
            cancel_event=cancel_event,
        )
    context_path, context_sha256 = _write_legacy_node_context(
        node=node,
        run_id=run_id,
        run_dir=run_dir,
        accepted_inputs=accepted_inputs,
    )
    return _run_legacy_node(
        node,
        run_id=run_id,
        run_dir=run_dir,
        events_path=events_path,
        resume=resume,
        context_path=context_path,
        context_sha256=context_sha256,
        attempt=scheduler_attempt,
        cancel_event=cancel_event,
    )


def _run_skill_node(
    node: DagNode,
    *,
    run_id: str,
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
        prior_artifacts = prior.get("artifacts")
        artifacts: list[Any] = list(prior_artifacts) if isinstance(prior_artifacts, list) else []
        artifact_errors = []
        for artifact in artifacts:
            if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
                artifact_errors.append("skill_resume_artifact_invalid")
                continue
            path = Path(artifact["path"]).expanduser().resolve()
            if not path.is_file():
                artifact_errors.append(f"skill_resume_artifact_missing:{path}")
            elif artifact.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
                artifact_errors.append(f"skill_resume_artifact_hash_mismatch:{path}")
        if (
            prior.get("status") == "PASS"
            and prior.get("verdict") == "PASS"
            and prior.get("skill_provider") == node.skill.provider
            and prior.get("capability") == node.skill.capability
            and prior.get("goal_hash") == goal_hash
            and prior.get("work_order_sha256") == _work_order_sha256(node)
            and not artifact_errors
        ):
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
    write_json(node.receipt_path, receipt)
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
        "resumed": False,
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
    attempt: int,
    cancel_event: Event,
) -> dict[str, Any]:
    node_started_at = _utc_stamp()
    node_started_monotonic = time.monotonic()
    if resume and node.receipt_path.exists():
        existing = _read_json_object(node.receipt_path, label=f"{node.node_id} receipt")
        errors = _validate_node_receipt(existing, node)
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
    )
    command_results = [
        _command_result_dict(result, elapsed_seconds=time.monotonic() - started_at)
    ]
    if result.returncode != 0:
        verdict = "SUBAGENT_TIMEOUT" if result.returncode == 124 else "SUBAGENT_ERROR"
        return _blocked_node_record(
            node,
            verdict=verdict,
            errors=[_command_error(result)],
            attempt_count=attempt,
            command_results=command_results,
            started_at=node_started_at,
            finished_at=_utc_stamp(),
            duration_seconds=time.monotonic() - node_started_monotonic,
        )
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
    errors = _validate_node_receipt(receipt, node)
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
    cancel_event: Event,
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
            expected_manifest_sha256 = prior.get("accepted_manifest_sha256")
            if not isinstance(expected_manifest_sha256, str):
                prior_errors.append("accepted_manifest_sha256_missing")
            else:
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
                        cancel_event=cancel_event,
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
                )

    revision: dict[str, Any] | None = None
    previous_artifact_sha256s: set[str] = set()
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
        )
        _append_event(
            events_path,
            "transaction_producer_dispatch",
            {"run_id": run_id, "node_id": node.node_id, "attempt": attempt},
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
            )
        producer_receipt, receipt_errors = load_json(
            node.receipt_path, label="transaction producer receipt"
        )
        if not receipt_errors:
            receipt_errors.extend(_validate_node_receipt(producer_receipt, node))
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
            )
        candidate_manifest_sha256 = file_sha256(candidate_manifest_path)
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
            )
        feedback, feedback_errors = validate_review_feedback(
            path=review_feedback_path,
            spec=spec,
            node_id=node.node_id,
            attempt=attempt,
            review_context_sha256=review_context_sha256,
            candidate_manifest_sha256=candidate_manifest_sha256,
            artifact_ids={str(item["artifact_id"]) for item in artifacts},
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
            "review_model": feedback.get("model"),
            "producer_live": producer_receipt.get("live") is True,
            "producer_provider_live": producer_provider_live,
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
            )
        verdict = str(feedback["verdict"]).upper()
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
            )
        if verdict == "REVISE":
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
                cancel_event=cancel_event,
            )
        _write_transaction_receipt(
            path=transaction_receipt_path,
            run_id=run_id,
            node=node,
            state="ACCEPTED",
            attempts=attempts,
            accepted_manifest_path=accepted_manifest_path,
            accepted_manifest_sha256=accepted_sha256,
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
    )


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
    cancel_event: Event,
) -> dict[str, Any]:
    continuation = spec.continuation
    assert continuation is not None
    command_sha256 = canonical_command_sha256(continuation.command)
    approval_receipt_path = transaction_receipt_path.parent / "approval-gate-receipt.json"
    if continuation.approval is not None:
        expected_target = {
            "id": f"generic-dag-transaction:{run_id}:{spec.transaction_id}",
            "run_id": run_id,
            "node_id": node.node_id,
            "transaction_id": spec.transaction_id,
            "accepted_manifest_sha256": accepted_manifest_sha256,
            "continuation_command_sha256": command_sha256,
        }
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
        },
    )
    result = _run_command(
        list(continuation.command),
        cwd=run_dir,
        timeout_seconds=continuation.timeout_seconds,
        env_overrides={"TAU_GENERIC_DAG_CONTEXT": str(continuation_context)},
        cancel_event=cancel_event,
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
        "errors": errors,
    }


def _validate_spec(spec: dict[str, Any], *, spec_path: Path) -> dict[str, DagNode]:
    if spec.get("schema") != GENERIC_DAG_SPEC_SCHEMA:
        raise RuntimeError(f"generic DAG spec schema must be {GENERIC_DAG_SPEC_SCHEMA}")
    for key in ("run_id", "run_dir", "nodes"):
        if key not in spec:
            raise RuntimeError(f"generic DAG spec missing {key}")
    if not isinstance(spec["run_id"], str) or not spec["run_id"].strip():
        raise RuntimeError("generic DAG spec run_id must be a non-empty string")
    if not isinstance(spec["run_dir"], str) or not spec["run_dir"].strip():
        raise RuntimeError("generic DAG spec run_dir must be a non-empty string")
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


def load_generic_dag_spec(path: Path) -> dict[str, Any]:
    """Load a generic DAG source document without executing it."""

    return _read_json_object(path.expanduser().resolve(), label="generic DAG spec")


def validate_generic_dag_spec(
    payload: dict[str, Any], *, source_path: Path
) -> dict[str, DagNode]:
    """Public pure validation boundary shared by runtime and DagPlan compiler."""

    return _validate_spec(payload, spec_path=source_path.expanduser().resolve())


def _parse_node(raw_node: dict[str, Any], *, base_dir: Path) -> DagNode:
    node_id = _required_string(raw_node, "node_id")
    role = str(raw_node.get("role") or node_id)
    command = raw_node.get("command")
    skill_raw = raw_node.get("skill")
    skill = (
        parse_skill_dag_spec(skill_raw, base_dir=base_dir, node_id=node_id)
        if skill_raw is not None
        else None
    )
    if skill is None and (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise RuntimeError(f"node {node_id} command must be a non-empty string list")
    if skill is not None and command is not None:
        raise RuntimeError(f"node {node_id} cannot declare both command and skill")
    command = command if isinstance(command, list) else []
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
    timeout_seconds = float(raw_node.get("timeout_seconds", 60))
    if timeout_seconds <= 0:
        raise RuntimeError(f"node {node_id} timeout_seconds must be positive")
    max_attempts = int(raw_node.get("max_attempts", 1))
    if max_attempts < 1:
        raise RuntimeError(f"node {node_id} max_attempts must be at least 1")
    receipt_path = _resolve_path(_required_string(raw_node, "receipt_path"), base_dir=base_dir)
    work_order_raw = raw_node.get("work_order_path")
    work_order_path = (
        _resolve_path(work_order_raw, base_dir=base_dir)
        if isinstance(work_order_raw, str) and work_order_raw
        else None
    )
    transaction_raw = raw_node.get("transaction")
    transaction = (
        parse_transaction_spec(transaction_raw, base_dir=base_dir, node_id=node_id)
        if transaction_raw is not None
        else None
    )
    if transaction is not None and work_order_path is None:
        raise RuntimeError(f"node {node_id} transaction requires work_order_path")
    if skill is not None and work_order_path is None:
        raise RuntimeError(f"node {node_id} skill requires work_order_path")
    if transaction is not None and skill is not None:
        raise RuntimeError(f"node {node_id} cannot declare both transaction and skill")
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


def _validate_node_receipt(receipt: dict[str, Any], node: DagNode) -> list[str]:
    errors = []
    if receipt.get("schema") != GENERIC_DAG_NODE_RECEIPT_SCHEMA:
        errors.append(f"schema must be {GENERIC_DAG_NODE_RECEIPT_SCHEMA}")
    if receipt.get("node_id") != node.node_id:
        errors.append(f"node_id must be {node.node_id}")
    if str(receipt.get("status") or "").upper() not in {"PASS", "BLOCKED"}:
        errors.append("status must be PASS or BLOCKED")
    if str(receipt.get("verdict") or "").upper() not in {"PASS", "REVISE", "BLOCKED"}:
        errors.append("verdict must be PASS, REVISE, or BLOCKED")
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
    errors.extend(_validate_provider_live_receipt(receipt))
    return errors


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
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    env_overrides: dict[str, str] | None = None,
    cancel_event: Event | None = None,
) -> subprocess.CompletedProcess[str]:
    return run_cancellable_subprocess(
        command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        env={**os.environ, **(env_overrides or {})},
        cancel_event=cancel_event,
    )


def _command_result_dict(
    result: subprocess.CompletedProcess[str],
    *,
    elapsed_seconds: float,
) -> dict[str, Any]:
    return {
        "argv": result.args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def _command_error(result: subprocess.CompletedProcess[str]) -> str:
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
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
