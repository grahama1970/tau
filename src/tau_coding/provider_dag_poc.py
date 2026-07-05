"""Receipt-gated provider DAG proof of concept for Tau."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tau_coding.herdr_cleanup import HERDR_WORKSPACE_LEASE_SCHEMA, run_herdr_cleanup
from tau_coding.orchestration_evidence import build_orchestration_evidence
from tau_coding.provider_pane_poc import run_provider_readiness_poc

PROVIDER_DAG_RUN_SCHEMA = "tau.dag_run_receipt.v1"
PROVIDER_DAG_MANIFEST_SCHEMA = "tau.provider_dag_runtime_manifest.v1"
PROVIDER_DAG_NODE_RECEIPT_SCHEMA = "tau.provider_dag_node_receipt.v1"
PROVIDER_DAG_PLANNER_RECEIPT_SCHEMA = "tau.dag_planner_receipt.v1"
DAG_RUN_SPEC_SCHEMA = "tau.dag_run_spec.v1"


def run_provider_dag_poc(
    *,
    repo: Path,
    run_root: Path,
    label: str = "tau-provider-dag-poc",
    max_attempts: int = 2,
    receipt_timeout_seconds: float = 300.0,
    force_reviewer_revise_attempts: tuple[int, ...] = (),
    allow_final_forced_revise: bool = False,
    reviewer_model: str | None = None,
    coder_mode: str = "codex",
    herdr_workstation: Path | None = None,
    herdr_bin: str = "herdr",
    session: str | None = None,
    install_integrations: bool = True,
    cleanup_mode: str = "dry-run",
) -> dict[str, Any]:
    """Plan and execute a bounded coder/reviewer provider loop."""

    _validate_cleanup_mode(cleanup_mode)
    planner_receipt = plan_provider_dag_poc(
        repo=repo,
        run_root=run_root,
        label=label,
        max_attempts=max_attempts,
        force_reviewer_revise_attempts=force_reviewer_revise_attempts,
        allow_final_forced_revise=allow_final_forced_revise,
        reviewer_model=reviewer_model,
        coder_mode=coder_mode,
    )
    dag_spec = Path(str(planner_receipt["dag_spec"]))
    return run_provider_dag_orchestrator(
        dag_spec=dag_spec,
        repo=repo,
        receipt_timeout_seconds=receipt_timeout_seconds,
        herdr_workstation=herdr_workstation,
        herdr_bin=herdr_bin,
        session=session,
        install_integrations=install_integrations,
        cleanup_mode=cleanup_mode,
    )


def plan_provider_dag_poc(
    *,
    repo: Path,
    run_root: Path,
    label: str = "tau-provider-dag-poc",
    max_attempts: int = 2,
    force_reviewer_revise_attempts: tuple[int, ...] = (),
    allow_final_forced_revise: bool = False,
    reviewer_model: str | None = None,
    coder_mode: str = "codex",
) -> dict[str, Any]:
    """Create a scratch coder/reviewer DAG spec without executing it."""

    if max_attempts < 1:
        raise RuntimeError("max_attempts must be at least 1")
    _validate_coder_mode(coder_mode)
    _validate_forced_revise_attempts(
        force_reviewer_revise_attempts,
        max_attempts,
        allow_final=allow_final_forced_revise,
    )
    resolved_repo = repo.expanduser().resolve()
    if not resolved_repo.exists():
        raise RuntimeError(f"repo does not exist: {resolved_repo}")
    resolved_run_root = run_root.expanduser().resolve()
    resolved_run_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{_compact_stamp()}-{_slug(label)}"
    run_dir = resolved_run_root / run_id
    events_path = run_dir / "events.jsonl"
    work_order_dir = run_dir / "work-orders"
    receipt_dir = run_dir / "receipts"
    scratch_dir = run_dir / "scratch-worktree"
    logs_dir = run_dir / "logs"
    for path in (work_order_dir, receipt_dir, scratch_dir, logs_dir):
        path.mkdir(parents=True, exist_ok=True)
    target_file = scratch_dir / "message.txt"
    target_file.write_text("TODO: replace this line with a completed implementation.\n", encoding="utf-8")

    dag_spec = _dag_spec(
        run_id,
        label,
        max_attempts,
        resolved_repo,
        run_dir,
        scratch_dir,
        target_file,
        force_reviewer_revise_attempts=force_reviewer_revise_attempts,
        allow_final_forced_revise=allow_final_forced_revise,
        reviewer_model=reviewer_model,
        coder_mode=coder_mode,
    )
    _write_json(run_dir / "dag-spec.json", dag_spec)
    _append_event(
        events_path,
        "dag_spec_created",
        {
            "run_id": run_id,
            "actor": "planner",
            "dag_spec": str(run_dir / "dag-spec.json"),
        },
    )
    planner_receipt = {
        "schema": PROVIDER_DAG_PLANNER_RECEIPT_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "repo": str(resolved_repo),
        "dag_spec": str(run_dir / "dag-spec.json"),
        "events_jsonl": str(events_path),
        "scratch_worktree": str(scratch_dir),
        "target_file": str(target_file),
        "max_attempts": max_attempts,
        "proof_controls": dag_spec["proof_controls"],
        "proof_scope": {
            "proves": [
                "Tau planner can emit a tau.dag_run_spec.v1 scratch coder/reviewer DAG",
                "Planner output names DAG nodes, dependencies, policies, and artifact paths",
            ],
            "does_not_prove": [
                "Provider readiness",
                "Provider execution",
                "Node receipt validation",
                "GitHub ticket closure",
                "remote Tailscale monitoring",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(run_dir / "planner-receipt.json", planner_receipt)
    return planner_receipt


def run_provider_dag_orchestrator(
    *,
    dag_spec: Path,
    repo: Path,
    receipt_timeout_seconds: float = 300.0,
    herdr_workstation: Path | None = None,
    herdr_bin: str = "herdr",
    session: str | None = None,
    install_integrations: bool = True,
    cleanup_mode: str = "dry-run",
) -> dict[str, Any]:
    """Execute a planner-created provider DAG spec through visible provider panes."""

    _validate_cleanup_mode(cleanup_mode)
    resolved_repo = repo.expanduser().resolve()
    if not resolved_repo.exists():
        raise RuntimeError(f"repo does not exist: {resolved_repo}")
    resolved_spec_path = dag_spec.expanduser().resolve()
    spec = _read_json_object(resolved_spec_path, label="DAG spec")
    _validate_dag_spec(spec)
    run_id = str(spec["run_id"])
    label = str(spec["label"])
    run_dir = Path(str(spec["run_dir"])).expanduser().resolve()
    events_path = Path(str(spec["events_jsonl"])).expanduser().resolve()
    work_order_dir = Path(str(spec["work_order_dir"])).expanduser().resolve()
    receipt_dir = Path(str(spec["receipt_dir"])).expanduser().resolve()
    scratch_dir = Path(str(spec["scratch_worktree"])).expanduser().resolve()
    logs_dir = Path(str(spec["logs_dir"])).expanduser().resolve()
    target_file = Path(str(spec["target_file"])).expanduser().resolve()
    max_attempts = int(spec["max_attempts"])
    proof_controls = spec.get("proof_controls") if isinstance(spec.get("proof_controls"), dict) else {}
    force_reviewer_revise_attempts = {
        int(attempt)
        for attempt in proof_controls.get("force_reviewer_revise_attempts", [])
        if isinstance(attempt, int)
    }
    reviewer_model = proof_controls.get("reviewer_model")
    if reviewer_model is not None:
        reviewer_model = str(reviewer_model)
    coder_mode = str(proof_controls.get("coder_mode") or "codex")
    _validate_coder_mode(coder_mode)
    for path in (work_order_dir, receipt_dir, scratch_dir, logs_dir):
        path.mkdir(parents=True, exist_ok=True)
    _append_event(
        events_path,
        "orchestrator_started",
        {"run_id": run_id, "actor": "orchestrator", "dag_spec": str(resolved_spec_path)},
    )

    readiness = run_provider_readiness_poc(
        repo=resolved_repo,
        run_root=run_dir / "provider-readiness",
        label=f"{label}-readiness",
        herdr_workstation=herdr_workstation,
        herdr_bin=herdr_bin,
        session=session,
        install_integrations=install_integrations,
        provider_node_context={
            "codex": {
                "dag_id": run_id,
                "node_id": "coder",
                "agent": "coder",
            },
            "opencode": {
                "dag_id": run_id,
                "node_id": "reviewer",
                "agent": "reviewer",
            },
        },
    )
    _write_json(run_dir / "provider-readiness-receipt.json", readiness)
    if readiness.get("ok") is not True:
        receipt = _blocked_run_receipt(
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            errors=["provider readiness failed"],
            readiness_receipt=readiness,
            attempts=[],
            final_status="BLOCKED",
        )
        _write_json(run_dir / "run-receipt.json", receipt)
        return receipt

    provider_map = _provider_map(readiness)
    missing = [provider for provider in ("codex", "opencode") if provider not in provider_map]
    if missing:
        receipt = _blocked_run_receipt(
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            errors=[f"missing readiness provider records: {', '.join(missing)}"],
            readiness_receipt=readiness,
            attempts=[],
            final_status="BLOCKED",
        )
        _write_json(run_dir / "run-receipt.json", receipt)
        return receipt

    command_results: list[dict[str, Any]] = []
    provider_sessions = _provider_sessions(provider_map)
    control_sessions = _start_control_role_panes(
        run_id=run_id,
        run_dir=run_dir,
        repo=resolved_repo,
        provider_map=provider_map,
        herdr_bin=herdr_bin,
        command_results=command_results,
    )
    visible_subagents = {
        "planner": control_sessions.get("planner", {}),
        "orchestrator": control_sessions.get("orchestrator", {}),
        "coder": provider_sessions.get("codex", {}),
        "reviewer": provider_sessions.get("opencode", {}),
    }

    attempts: list[dict[str, Any]] = []
    reached: set[str] = {
        "planner_spec_consumed",
        "visible_roles_started",
        "structured_readiness_required",
    }
    reviewer_feedback = ""
    final_status = "BLOCKED"
    final_verdict = "MAX_ATTEMPTS_EXHAUSTED"
    for attempt in range(1, max_attempts + 1):
        coder_receipt_path = receipt_dir / f"attempt-{attempt:02d}-coder.json"
        reviewer_receipt_path = receipt_dir / f"attempt-{attempt:02d}-reviewer.json"
        coder_work_order_path = work_order_dir / f"attempt-{attempt:02d}-coder.json"
        reviewer_work_order_path = work_order_dir / f"attempt-{attempt:02d}-reviewer.json"
        coder_work_order = _coder_work_order(
            run_id=run_id,
            dag_id=run_id,
            goal_hash=str(spec["goal"]["goal_hash"]),
            attempt=attempt,
            max_attempts=max_attempts,
            repo=resolved_repo,
            scratch_dir=scratch_dir,
            target_file=target_file,
            receipt_path=coder_receipt_path,
            reviewer_feedback=reviewer_feedback,
            provider_record=provider_map["codex"],
        )
        _write_json(coder_work_order_path, coder_work_order)
        coder_work_order_sha256 = str(coder_work_order["work_order_sha256"])
        _append_event(
            events_path,
            "coder_dispatch",
            {
                "run_id": run_id,
                "attempt": attempt,
                "provider_id": "codex",
                "pane_id": provider_map["codex"]["pane_id"],
                "work_order_path": str(coder_work_order_path),
                "receipt_path": str(coder_receipt_path),
            },
        )
        if coder_mode == "deterministic-visible":
            coder_worker = _start_visible_deterministic_coder_pane(
                run_id=run_id,
                attempt=attempt,
                repo=resolved_repo,
                provider_record=provider_map["codex"],
                work_order_path=coder_work_order_path,
                herdr_bin=herdr_bin,
                command_results=command_results,
            )
            visible_subagents["coder"] = coder_worker
            if coder_worker.get("visible") is not True:
                final_verdict = "CODER_SEND_FAILED"
                attempts.append(
                    _attempt_record(
                        attempt=attempt,
                        coder_receipt_path=coder_receipt_path,
                        reviewer_receipt_path=reviewer_receipt_path,
                        coder_receipt={},
                        reviewer_receipt={},
                        errors=[str(coder_worker.get("error") or "deterministic coder failed")],
                    )
                )
                break
        else:
            coder_send_results = _send_pane_prompt(
                herdr_bin=herdr_bin,
                pane_id=str(provider_map["codex"]["pane_id"]),
                text=_coder_prompt(coder_work_order_path, coder_receipt_path),
                cwd=resolved_repo,
                timeout_seconds=_pane_send_timeout(receipt_timeout_seconds),
            )
            command_results.extend(_command_result_dict(result) for result in coder_send_results)
            if any(result.returncode != 0 for result in coder_send_results):
                final_verdict = "CODER_SEND_FAILED"
                attempts.append(
                    _attempt_record(
                        attempt=attempt,
                        coder_receipt_path=coder_receipt_path,
                        reviewer_receipt_path=reviewer_receipt_path,
                        coder_receipt={},
                        reviewer_receipt={},
                        errors=_send_errors(coder_send_results),
                    )
                )
                break
        reached.add("coder_dispatched")
        coder_receipt, coder_errors = _wait_for_node_receipt(
            coder_receipt_path,
            expected_node_id="coder",
            expected_provider_id="codex",
            expected_attempt=attempt,
            work_order_path=coder_work_order_path,
            work_order_sha256=coder_work_order_sha256,
            expected_herdr=coder_work_order["herdr"],
            expected_goal_hash=str(spec["goal"]["goal_hash"]),
            expected_dag_id=run_id,
            timeout_seconds=receipt_timeout_seconds,
        )
        if coder_errors:
            attempts.append(
                _attempt_record(
                    attempt=attempt,
                    coder_receipt_path=coder_receipt_path,
                    reviewer_receipt_path=reviewer_receipt_path,
                    coder_receipt=coder_receipt,
                    reviewer_receipt={},
                    errors=coder_errors,
                )
            )
            final_verdict = "CODER_RECEIPT_INVALID"
            break
        reached.add("coder_receipt_validated")
        _append_event(
            events_path,
            "coder_receipt_validated",
            {"run_id": run_id, "attempt": attempt, "receipt_path": str(coder_receipt_path)},
        )

        reviewer_work_order = _reviewer_work_order(
            run_id=run_id,
            dag_id=run_id,
            goal_hash=str(spec["goal"]["goal_hash"]),
            attempt=attempt,
            max_attempts=max_attempts,
            repo=resolved_repo,
            scratch_dir=scratch_dir,
            target_file=target_file,
            receipt_path=reviewer_receipt_path,
            coder_receipt_path=coder_receipt_path,
            force_revise=attempt in force_reviewer_revise_attempts,
            provider_record=provider_map["opencode"],
        )
        _write_json(reviewer_work_order_path, reviewer_work_order)
        reviewer_work_order_sha256 = str(reviewer_work_order["work_order_sha256"])
        _append_event(
            events_path,
            "reviewer_dispatch",
            {
                "run_id": run_id,
                "attempt": attempt,
                "provider_id": "opencode",
                "pane_id": provider_map["opencode"]["pane_id"],
                "work_order_path": str(reviewer_work_order_path),
                "receipt_path": str(reviewer_receipt_path),
            },
        )
        reviewer_worker = _start_visible_opencode_run_pane(
            run_id=run_id,
            attempt=attempt,
            repo=resolved_repo,
            provider_record=provider_map["opencode"],
            prompt=_reviewer_prompt(reviewer_work_order_path, reviewer_receipt_path),
            model=reviewer_model,
            herdr_bin=herdr_bin,
            command_results=command_results,
        )
        visible_subagents["reviewer"] = reviewer_worker
        if reviewer_worker.get("visible") is not True:
            final_verdict = "REVIEWER_SEND_FAILED"
            attempts.append(
                _attempt_record(
                    attempt=attempt,
                    coder_receipt_path=coder_receipt_path,
                    reviewer_receipt_path=reviewer_receipt_path,
                    coder_receipt=coder_receipt,
                    reviewer_receipt={},
                    errors=[str(reviewer_worker.get("error") or "opencode reviewer worker failed")],
                )
            )
            break
        reached.add("reviewer_dispatched")
        reviewer_receipt, reviewer_errors = _wait_for_node_receipt(
            reviewer_receipt_path,
            expected_node_id="reviewer",
            expected_provider_id="opencode",
            expected_attempt=attempt,
            work_order_path=reviewer_work_order_path,
            work_order_sha256=reviewer_work_order_sha256,
            expected_herdr=reviewer_work_order["herdr"],
            expected_goal_hash=str(spec["goal"]["goal_hash"]),
            expected_dag_id=run_id,
            timeout_seconds=receipt_timeout_seconds,
        )
        if reviewer_errors:
            attempts.append(
                _attempt_record(
                    attempt=attempt,
                    coder_receipt_path=coder_receipt_path,
                    reviewer_receipt_path=reviewer_receipt_path,
                    coder_receipt=coder_receipt,
                    reviewer_receipt=reviewer_receipt,
                    errors=reviewer_errors,
                )
            )
            final_verdict = "REVIEWER_RECEIPT_INVALID"
            break
        reached.add("reviewer_receipt_validated")
        _append_event(
            events_path,
            "reviewer_receipt_validated",
            {"run_id": run_id, "attempt": attempt, "receipt_path": str(reviewer_receipt_path)},
        )
        attempts.append(
            _attempt_record(
                attempt=attempt,
                coder_receipt_path=coder_receipt_path,
                reviewer_receipt_path=reviewer_receipt_path,
                coder_receipt=coder_receipt,
                reviewer_receipt=reviewer_receipt,
                errors=[],
            )
        )
        verdict = str(reviewer_receipt.get("verdict") or "").upper()
        if verdict == "PASS":
            final_status = "PASS"
            final_verdict = "PASS"
            reached.add("loop_stopped")
            break
        if verdict == "REVISE" and attempt < max_attempts:
            reviewer_feedback = str(reviewer_receipt.get("handoff_summary") or "")
            _append_event(
                events_path,
                "reviewer_requested_revision",
                {"run_id": run_id, "attempt": attempt, "feedback": reviewer_feedback},
            )
            continue
        final_verdict = verdict or "REVIEWER_DID_NOT_PASS"
        reached.add("loop_stopped")
        break

    _capture_visible_logs(
        {**provider_map, **visible_subagents},
        logs_dir,
        herdr_bin,
        resolved_repo,
        command_results,
    )
    runtime_manifest = {
        "schema": PROVIDER_DAG_MANIFEST_SCHEMA,
        "run_id": run_id,
        "label": label,
        "repo": str(resolved_repo),
        "run_dir": str(run_dir),
        "scratch_worktree": str(scratch_dir),
        "dag_spec": str(resolved_spec_path),
        "events_jsonl": str(events_path),
        "provider_readiness_receipt": str(run_dir / "provider-readiness-receipt.json"),
        "provider_sessions": provider_sessions,
        "visible_subagents": visible_subagents,
        "attempts": attempts,
        "logs_dir": str(logs_dir),
    }
    _write_json(run_dir / "runtime-manifest.json", runtime_manifest)
    cleanup_receipt = _run_provider_dag_cleanup(
        run_dir=run_dir,
        mode=cleanup_mode,
        herdr_bin=herdr_bin,
    )
    final_receipt = {
        "schema": PROVIDER_DAG_RUN_SCHEMA,
        "ok": final_status == "PASS",
        "status": final_status,
        "verdict": final_verdict,
        "mocked": False,
        "live": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "runtime_manifest": str(run_dir / "runtime-manifest.json"),
        "herdr_cleanup_receipt": cleanup_receipt.get("receipt_path"),
        "herdr_cleanup": cleanup_receipt,
        "events_jsonl": str(events_path),
        "scratch_worktree": str(scratch_dir),
        "dag_spec": str(resolved_spec_path),
        "provider_readiness_receipt": str(run_dir / "provider-readiness-receipt.json"),
        "provider_sessions": provider_sessions,
        "visible_subagents": visible_subagents,
        "attempt_count": len(attempts),
        "max_attempts": max_attempts,
        "attempts": attempts,
        "command_results": command_results,
        "proof_scope": {
            "proves": _provider_dag_proof_claims(reached),
            "does_not_prove": [
                "GitHub ticket closure",
                "remote Tailscale monitoring",
                "general semantic coding quality beyond the scratch fixture task",
                "unbounded autonomous repair",
                "production repository mutation",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(run_dir / "run-receipt.json", final_receipt)
    orchestration_evidence = _run_orchestration_evidence(run_dir)
    final_receipt["orchestration_evidence_receipt"] = orchestration_evidence.get("receipt_path")
    final_receipt["orchestration_evidence"] = orchestration_evidence
    _write_json(run_dir / "run-receipt.json", final_receipt)
    return final_receipt


def inspect_provider_dag_run(run_dir: Path) -> dict[str, Any]:
    """Return a compact summary for a provider DAG POC run directory."""

    resolved = run_dir.expanduser().resolve()
    manifest = _read_json_object(resolved / "runtime-manifest.json", label="runtime manifest")
    receipt = _read_json_object(resolved / "run-receipt.json", label="run receipt")
    events_path = Path(str(manifest["events_jsonl"]))
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {
        "schema": "tau.provider_dag_inspect.v1",
        "ok": receipt.get("ok") is True,
        "status": receipt.get("status"),
        "verdict": receipt.get("verdict"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "run_id": receipt.get("run_id"),
        "run_dir": str(resolved),
        "scratch_worktree": receipt.get("scratch_worktree"),
        "events_count": len(events),
        "attempt_count": receipt.get("attempt_count"),
        "max_attempts": receipt.get("max_attempts"),
        "attempts": receipt.get("attempts"),
        "provider_sessions": receipt.get("provider_sessions"),
        "visible_subagents": receipt.get("visible_subagents"),
        "herdr_cleanup_receipt": receipt.get("herdr_cleanup_receipt"),
        "herdr_cleanup": receipt.get("herdr_cleanup"),
        "orchestration_evidence_receipt": receipt.get("orchestration_evidence_receipt"),
        "orchestration_evidence": receipt.get("orchestration_evidence"),
        "proof_scope": receipt.get("proof_scope"),
    }


def _run_orchestration_evidence(run_dir: Path) -> dict[str, Any]:
    receipt = build_orchestration_evidence(run_dir=run_dir)
    return {
        "receipt_path": str(run_dir / "orchestration-evidence-receipt.json"),
        "status": receipt.get("status"),
        "ok": receipt.get("ok"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "provider_live": receipt.get("provider_live"),
        "feature_counts": receipt.get("feature_counts"),
        "errors": receipt.get("errors"),
    }


def _run_provider_dag_cleanup(
    *,
    run_dir: Path,
    mode: str,
    herdr_bin: str,
) -> dict[str, Any]:
    if mode == "off":
        return {
            "mode": "off",
            "receipt_path": None,
            "status": "SKIPPED",
            "mocked": False,
            "live": False,
        }
    if mode not in {"audit", "dry-run", "apply"}:
        raise RuntimeError("cleanup_mode must be off, audit, dry-run, or apply")
    workspace_lease_path = _write_provider_dag_workspace_lease(run_dir, cleanup_mode=mode)
    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode=mode,
        herdr_bin=herdr_bin,
        workspace_lease_path=workspace_lease_path,
    )
    return {
        "mode": mode,
        "receipt_path": str(run_dir / "herdr-cleanup-receipt.json"),
        "status": receipt.get("status"),
        "ok": receipt.get("ok"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "candidate_count": receipt.get("candidate_count"),
        "resource_count": receipt.get("resource_count"),
        "workspace_lease": receipt.get("workspace_lease"),
        "workspace_lease_sha256": receipt.get("workspace_lease_sha256"),
        "applied_action_count": _count(receipt.get("applied_actions")),
        "post_verified_absent_count": _post_verified_absent_count(receipt.get("applied_actions")),
    }


def _write_provider_dag_workspace_lease(run_dir: Path, *, cleanup_mode: str) -> Path:
    manifest = _read_json_object(run_dir / "runtime-manifest.json", label="runtime manifest")
    workspace_ids = sorted(
        {
            str(record.get("workspace_id") or "")
            for records_key in ("provider_sessions", "visible_subagents")
            for record in (
                manifest.get(records_key).values()
                if isinstance(manifest.get(records_key), dict)
                else []
            )
            if isinstance(record, dict) and record.get("workspace_id")
        }
    )
    now = datetime.now(UTC).replace(microsecond=0)
    lease = {
        "schema": HERDR_WORKSPACE_LEASE_SCHEMA,
        "run_id": manifest.get("run_id"),
        "dag_id": manifest.get("label") or manifest.get("run_id"),
        "owner": "tau-orchestrator",
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "cleanup_policy": cleanup_mode,
        "workspace_ids": workspace_ids,
        "source_runtime_manifest": str(run_dir / "runtime-manifest.json"),
    }
    lease_path = run_dir / "herdr-workspace-lease.json"
    _write_json(lease_path, lease)
    return lease_path


def _count(value: Any) -> int:
    if isinstance(value, (list, dict)):
        return len(value)
    return 0


def _post_verified_absent_count(value: Any) -> int:
    if not isinstance(value, list):
        return 0
    return sum(1 for item in value if isinstance(item, dict) and item.get("post_verified_absent") is True)


def _validate_cleanup_mode(mode: str) -> None:
    if mode not in {"off", "audit", "dry-run", "apply"}:
        raise RuntimeError("cleanup_mode must be off, audit, dry-run, or apply")


def _validate_coder_mode(mode: str) -> None:
    if mode not in {"codex", "deterministic-visible"}:
        raise RuntimeError("coder_mode must be codex or deterministic-visible")


def _provider_goal_hash(*, run_id: str, label: str) -> str:
    payload = json.dumps(
        {
            "run_id": run_id,
            "label": label,
            "contract": "tau.provider_dag_work_order.v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _with_work_order_sha256(payload: dict[str, Any]) -> dict[str, Any]:
    canonical = dict(payload)
    canonical.pop("work_order_sha256", None)
    data = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["work_order_sha256"] = hashlib.sha256(data).hexdigest()
    return payload


def _dag_spec(
    run_id: str,
    label: str,
    max_attempts: int,
    repo: Path,
    run_dir: Path,
    scratch_dir: Path,
    target_file: Path,
    *,
    force_reviewer_revise_attempts: tuple[int, ...] = (),
    allow_final_forced_revise: bool = False,
    reviewer_model: str | None = None,
    coder_mode: str = "codex",
) -> dict[str, Any]:
    coder_provider_id = "codex" if coder_mode == "codex" else "tau-deterministic-visible"
    return {
        "schema": DAG_RUN_SPEC_SCHEMA,
        "run_id": run_id,
        "label": label,
        "run_dir": str(run_dir),
        "events_jsonl": str(run_dir / "events.jsonl"),
        "work_order_dir": str(run_dir / "work-orders"),
        "receipt_dir": str(run_dir / "receipts"),
        "logs_dir": str(run_dir / "logs"),
        "goal": {
            "goal_id": label,
            "goal_version": 1,
            "goal_hash": _provider_goal_hash(run_id=run_id, label=label),
        },
        "target": {
            "repo": str(repo),
            "allowed_paths": [str(target_file)],
            "scratch_worktree": str(scratch_dir),
        },
        "max_attempts": max_attempts,
        "scratch_worktree": str(scratch_dir),
        "target_file": str(target_file),
        "proof_controls": {
            "force_reviewer_revise_attempts": list(force_reviewer_revise_attempts),
            "allow_final_forced_revise": allow_final_forced_revise,
            "reviewer_model": reviewer_model,
            "coder_mode": coder_mode,
        },
        "nodes": [
            {
                "node_id": "coder",
                "role": "coder",
                "provider_id": coder_provider_id,
                "depends_on": [],
                "receipt_schema": PROVIDER_DAG_NODE_RECEIPT_SCHEMA,
            },
            {
                "node_id": "reviewer",
                "role": "reviewer",
                "provider_id": "opencode",
                "depends_on": ["coder"],
                "receipt_schema": PROVIDER_DAG_NODE_RECEIPT_SCHEMA,
            },
        ],
        "policy": {
            "require_structured_readiness": True,
            "allow_visible_text_readiness_gate": False,
            "max_attempts": max_attempts,
            "forbidden": ["ticket_closure", "tailscale_proof", "real_repo_mutation"],
        },
        "planner": {
            "subagent": "planner",
            "handoff": "planner creates the DAG spec only; it does not execute providers.",
        },
        "orchestrator": {
            "subagent": "orchestrator",
            "handoff": "orchestrator executes the DAG spec and emits the final receipt.",
        },
    }


def _validate_dag_spec(spec: dict[str, Any]) -> None:
    if spec.get("schema") != DAG_RUN_SPEC_SCHEMA:
        raise RuntimeError(f"DAG spec schema must be {DAG_RUN_SPEC_SCHEMA}")
    for key in (
        "run_id",
        "label",
        "run_dir",
        "events_jsonl",
        "work_order_dir",
        "receipt_dir",
        "logs_dir",
        "scratch_worktree",
        "target_file",
    ):
        if not isinstance(spec.get(key), str) or not spec.get(key):
            raise RuntimeError(f"DAG spec {key} must be a non-empty string")
    if not isinstance(spec.get("max_attempts"), int) or spec["max_attempts"] < 1:
        raise RuntimeError("DAG spec max_attempts must be a positive integer")
    proof_controls = spec.get("proof_controls")
    if proof_controls is not None:
        if not isinstance(proof_controls, dict):
            raise RuntimeError("DAG spec proof_controls must be an object")
        forced = proof_controls.get("force_reviewer_revise_attempts", [])
        if not isinstance(forced, list) or not all(isinstance(item, int) for item in forced):
            raise RuntimeError(
                "DAG spec proof_controls.force_reviewer_revise_attempts must be integer list"
            )
        _validate_forced_revise_attempts(
            tuple(forced),
            int(spec["max_attempts"]),
            allow_final=proof_controls.get("allow_final_forced_revise") is True,
        )
        reviewer_model = proof_controls.get("reviewer_model")
        if reviewer_model is not None and (
            not isinstance(reviewer_model, str) or not reviewer_model.strip()
        ):
            raise RuntimeError("DAG spec proof_controls.reviewer_model must be a non-empty string")
        coder_mode = proof_controls.get("coder_mode", "codex")
        if not isinstance(coder_mode, str):
            raise RuntimeError("DAG spec proof_controls.coder_mode must be a string")
        _validate_coder_mode(coder_mode)
    nodes = spec.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 2:
        raise RuntimeError("DAG spec nodes must contain coder and reviewer")
    by_id = {node.get("node_id"): node for node in nodes if isinstance(node, dict)}
    if set(by_id) != {"coder", "reviewer"}:
        raise RuntimeError("DAG spec nodes must be coder and reviewer")
    coder = by_id["coder"]
    reviewer = by_id["reviewer"]
    expected_coder_provider = (
        "codex"
        if (proof_controls or {}).get("coder_mode", "codex") == "codex"
        else "tau-deterministic-visible"
    )
    if coder.get("provider_id") != expected_coder_provider:
        raise RuntimeError(f"DAG spec coder provider_id must be {expected_coder_provider}")
    if reviewer.get("provider_id") != "opencode":
        raise RuntimeError("DAG spec reviewer provider_id must be opencode")
    if reviewer.get("depends_on") != ["coder"]:
        raise RuntimeError("DAG spec reviewer must depend on coder")
    policy = spec.get("policy")
    if not isinstance(policy, dict):
        raise RuntimeError("DAG spec policy must be an object")
    if policy.get("require_structured_readiness") is not True:
        raise RuntimeError("DAG spec must require structured readiness")
    forbidden = policy.get("forbidden")
    for blocked in ("ticket_closure", "tailscale_proof", "real_repo_mutation"):
        if not isinstance(forbidden, list) or blocked not in forbidden:
            raise RuntimeError(f"DAG spec policy.forbidden must include {blocked}")


def _coder_work_order(
    *,
    run_id: str,
    dag_id: str,
    goal_hash: str,
    attempt: int,
    max_attempts: int,
    repo: Path,
    scratch_dir: Path,
    target_file: Path,
    receipt_path: Path,
    reviewer_feedback: str,
    provider_record: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema": "tau.provider_dag_work_order.v1",
        "dag_id": dag_id,
        "run_id": run_id,
        "goal": {
            "goal_id": run_id,
            "goal_version": 1,
            "goal_hash": goal_hash,
        },
        "node": {
            "node_id": "coder",
            "agent": "coder",
            "attempt": attempt,
            "max_attempts": max_attempts,
        },
        "target": {
            "repo": str(repo),
            "allowed_paths": [str(target_file)],
            "scratch_worktree": str(scratch_dir),
        },
        "herdr": {
            "workspace_id": str(provider_record.get("workspace_id") or ""),
            "pane_id": str(provider_record.get("pane_id") or ""),
            "terminal_id": str(provider_record.get("terminal_id") or ""),
        },
        "node_id": "coder",
        "provider_id": "codex",
        "attempt": attempt,
        "max_attempts": max_attempts,
        "scratch_worktree": str(scratch_dir),
        "target_file": str(target_file),
        "receipt_path": str(receipt_path),
        "required_evidence": ["target_file_updated", "node_receipt_written"],
        "forbidden_actions": ["modify_tau_repository", "github_mutation", "tailscale_access"],
        "reviewer_feedback": reviewer_feedback,
        "task": (
            "Modify only target_file. Replace the TODO line with a short completed "
            "implementation message. Then write the node receipt JSON exactly at receipt_path."
        ),
    }
    return _with_work_order_sha256(payload)


def _reviewer_work_order(
    *,
    run_id: str,
    dag_id: str,
    goal_hash: str,
    attempt: int,
    max_attempts: int,
    repo: Path,
    scratch_dir: Path,
    target_file: Path,
    receipt_path: Path,
    coder_receipt_path: Path,
    force_revise: bool,
    provider_record: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema": "tau.provider_dag_work_order.v1",
        "dag_id": dag_id,
        "run_id": run_id,
        "goal": {
            "goal_id": run_id,
            "goal_version": 1,
            "goal_hash": goal_hash,
        },
        "node": {
            "node_id": "reviewer",
            "agent": "reviewer",
            "attempt": attempt,
            "max_attempts": max_attempts,
        },
        "target": {
            "repo": str(repo),
            "allowed_paths": [str(target_file), str(coder_receipt_path)],
            "scratch_worktree": str(scratch_dir),
        },
        "herdr": {
            "workspace_id": str(provider_record.get("workspace_id") or ""),
            "pane_id": str(provider_record.get("pane_id") or ""),
            "terminal_id": str(provider_record.get("terminal_id") or ""),
        },
        "node_id": "reviewer",
        "provider_id": "opencode",
        "attempt": attempt,
        "max_attempts": max_attempts,
        "scratch_worktree": str(scratch_dir),
        "target_file": str(target_file),
        "coder_receipt_path": str(coder_receipt_path),
        "receipt_path": str(receipt_path),
        "required_evidence": ["coder_receipt_reviewed", "target_file_reviewed"],
        "forbidden_actions": ["modify_files", "github_mutation", "tailscale_access"],
        "force_revise": force_revise,
        "task": (
            "Review target_file and the coder receipt. If force_revise is true, return REVISE "
            "with actionable feedback even if the file is acceptable. Otherwise return PASS if "
            "the TODO line is gone and the coder receipt is schema-valid enough to inspect; "
            "otherwise return REVISE."
        ),
    }
    return _with_work_order_sha256(payload)


def _coder_prompt(work_order_path: Path, receipt_path: Path) -> str:
    work_order = _read_json_object(work_order_path, label="coder work order")
    herdr = work_order.get("herdr") if isinstance(work_order.get("herdr"), dict) else {}
    return f"""
Tau provider DAG POC coder task.

Read this work order JSON:
{work_order_path}

Rules:
- Operate only inside the scratch_worktree named in the work order.
- Do not modify the Tau repository.
- Do not use GitHub.
- Do not use Tailscale.
- Modify only target_file.
- Write a JSON receipt exactly here:
{receipt_path}

Receipt JSON shape:
{{
  "schema": "{PROVIDER_DAG_NODE_RECEIPT_SCHEMA}",
  "dag_id": "{work_order["dag_id"]}",
  "goal_hash": "{work_order["goal"]["goal_hash"]}",
  "node_id": "coder",
  "provider_id": "codex",
  "attempt": {work_order["attempt"]},
  "workspace_id": "{herdr.get("workspace_id")}",
  "pane_id": "{herdr.get("pane_id")}",
  "terminal_id": "{herdr.get("terminal_id")}",
  "work_order_sha256": "{work_order["work_order_sha256"]}",
  "status": "PASS",
  "verdict": "PASS",
  "work_order_path": "{work_order_path}",
  "changed_files": ["<target_file>"],
  "commands_run": ["<commands you ran>"],
  "artifacts": ["<target_file>"],
  "handoff_summary": "What changed.",
  "errors": [],
  "policy_exceptions": []
}}
""".strip()


def _reviewer_prompt(work_order_path: Path, receipt_path: Path) -> str:
    work_order = _read_json_object(work_order_path, label="reviewer work order")
    herdr = work_order.get("herdr") if isinstance(work_order.get("herdr"), dict) else {}
    return f"""
Tau provider DAG POC reviewer task.

Read this work order JSON:
{work_order_path}

Rules:
- Operate only inside the scratch_worktree named in the work order.
- Do not modify files except the reviewer receipt.
- Do not use GitHub.
- Do not use Tailscale.
- Review target_file and coder_receipt_path.
- If the work order has "force_revise": true, write verdict "REVISE" with
  concrete feedback for the next coder attempt.
- Write a JSON receipt exactly here:
{receipt_path}

Receipt JSON shape:
{{
  "schema": "{PROVIDER_DAG_NODE_RECEIPT_SCHEMA}",
  "dag_id": "{work_order["dag_id"]}",
  "goal_hash": "{work_order["goal"]["goal_hash"]}",
  "node_id": "reviewer",
  "provider_id": "opencode",
  "attempt": {work_order["attempt"]},
  "workspace_id": "{herdr.get("workspace_id")}",
  "pane_id": "{herdr.get("pane_id")}",
  "terminal_id": "{herdr.get("terminal_id")}",
  "work_order_sha256": "{work_order["work_order_sha256"]}",
  "status": "PASS",
  "verdict": "PASS or REVISE",
  "work_order_path": "{work_order_path}",
  "changed_files": [],
  "commands_run": ["<commands you ran>"],
  "artifacts": ["<target_file>", "<coder_receipt_path>"],
  "handoff_summary": "Review finding or pass rationale.",
  "errors": [],
  "policy_exceptions": []
}}
""".strip()


def _send_pane_prompt(
    *,
    herdr_bin: str,
    pane_id: str,
    text: str,
    cwd: Path,
    timeout_seconds: float,
) -> list[subprocess.CompletedProcess[str]]:
    send_text = _run_pane_command(
        [herdr_bin, "pane", "send-text", pane_id, text + "\n"],
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    if send_text.returncode != 0:
        return [send_text]
    send_enter = _run_pane_command(
        [herdr_bin, "pane", "send-keys", pane_id, "enter"],
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    return [send_text, send_enter]


def _run_pane_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        if stderr:
            stderr = f"{stderr}\n"
        stderr += f"timed out after {timeout_seconds:.1f}s"
        return subprocess.CompletedProcess(argv, 124, stdout=stdout, stderr=stderr)


def _pane_send_timeout(receipt_timeout_seconds: float) -> float:
    return max(5.0, min(30.0, receipt_timeout_seconds / 2.0))


def _send_errors(results: list[subprocess.CompletedProcess[str]]) -> list[str]:
    errors: list[str] = []
    for result in results:
        if result.returncode == 0:
            continue
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or "no output"
        errors.append(f"{' '.join(result.args)} exited {result.returncode}: {detail}")
    return errors or ["provider pane send failed"]


def _wait_for_node_receipt(
    path: Path,
    *,
    expected_node_id: str,
    expected_provider_id: str,
    expected_attempt: int,
    work_order_path: Path,
    work_order_sha256: str,
    expected_herdr: dict[str, Any],
    expected_goal_hash: str,
    expected_dag_id: str,
    timeout_seconds: float,
) -> tuple[dict[str, Any], list[str]]:
    deadline = time.monotonic() + timeout_seconds
    last_errors: list[str] = [f"receipt did not appear: {path}"]
    while time.monotonic() < deadline:
        if path.exists():
            receipt = _read_json_object(path, label=f"{expected_node_id} receipt")
            errors = _validate_node_receipt(
                receipt,
                expected_node_id=expected_node_id,
                expected_provider_id=expected_provider_id,
                expected_attempt=expected_attempt,
                work_order_path=work_order_path,
                work_order_sha256=work_order_sha256,
                expected_herdr=expected_herdr,
                expected_goal_hash=expected_goal_hash,
                expected_dag_id=expected_dag_id,
            )
            if not errors:
                return receipt, []
            last_errors = errors
        time.sleep(1.0)
    if path.exists():
        try:
            receipt = _read_json_object(path, label=f"{expected_node_id} receipt")
        except RuntimeError as exc:
            return {}, [str(exc)]
        return receipt, last_errors
    return {}, last_errors


def _validate_node_receipt(
    receipt: dict[str, Any],
    *,
    expected_node_id: str,
    expected_provider_id: str,
    expected_attempt: int,
    work_order_path: Path,
    work_order_sha256: str,
    expected_herdr: dict[str, Any],
    expected_goal_hash: str,
    expected_dag_id: str,
) -> list[str]:
    errors: list[str] = []
    if receipt.get("schema") != PROVIDER_DAG_NODE_RECEIPT_SCHEMA:
        errors.append(f"schema must be {PROVIDER_DAG_NODE_RECEIPT_SCHEMA}")
    if receipt.get("dag_id") != expected_dag_id:
        errors.append(f"dag_id must be {expected_dag_id}")
    if receipt.get("goal_hash") != expected_goal_hash:
        errors.append(f"goal_hash must be {expected_goal_hash}")
    if receipt.get("node_id") != expected_node_id:
        errors.append(f"node_id must be {expected_node_id}")
    if receipt.get("provider_id") != expected_provider_id:
        errors.append(f"provider_id must be {expected_provider_id}")
    if receipt.get("attempt") != expected_attempt:
        errors.append(f"attempt must be {expected_attempt}")
    if receipt.get("work_order_path") != str(work_order_path):
        errors.append(f"work_order_path must be {work_order_path}")
    if receipt.get("work_order_sha256") != work_order_sha256:
        errors.append("work_order_sha256 must match the dispatched work order")
    for key in ("workspace_id", "pane_id", "terminal_id"):
        expected = str(expected_herdr.get(key) or "")
        if not expected:
            errors.append(f"expected {key} must be available from Herdr readiness")
        elif receipt.get(key) != expected:
            errors.append(f"{key} must be {expected}")
    if str(receipt.get("status") or "").upper() not in {"PASS", "BLOCKED"}:
        errors.append("status must be PASS or BLOCKED")
    if str(receipt.get("verdict") or "").upper() not in {"PASS", "REVISE", "BLOCKED"}:
        errors.append("verdict must be PASS, REVISE, or BLOCKED")
    for key in ("changed_files", "commands_run", "artifacts", "errors", "policy_exceptions"):
        if not isinstance(receipt.get(key), list):
            errors.append(f"{key} must be a list")
    if not isinstance(receipt.get("handoff_summary"), str) or not receipt.get("handoff_summary"):
        errors.append("handoff_summary must be a non-empty string")
    return errors


def _validate_forced_revise_attempts(
    attempts: tuple[int, ...],
    max_attempts: int,
    *,
    allow_final: bool = False,
) -> None:
    if len(set(attempts)) != len(attempts):
        raise RuntimeError("force_reviewer_revise_attempts must not contain duplicates")
    for attempt in attempts:
        if attempt < 1:
            raise RuntimeError("force_reviewer_revise_attempts must be positive")
        if attempt > max_attempts:
            raise RuntimeError("force_reviewer_revise_attempts must not exceed max_attempts")
        if attempt == max_attempts and not allow_final:
            raise RuntimeError(
                "force_reviewer_revise_attempts must leave a later attempt for PASS"
            )


def _provider_dag_proof_claims(reached: set[str]) -> list[str]:
    claims_by_stage = [
        (
            "planner_spec_consumed",
            "Tau orchestrator can consume a planner-created tau.dag_run_spec.v1",
        ),
        (
            "visible_roles_started",
            "Tau exposes planner, orchestrator, coder, and reviewer roles as Herdr-visible subagents",
        ),
        (
            "structured_readiness_required",
            "Tau requires structured provider readiness before dispatch",
        ),
        (
            "coder_dispatched",
            "Tau can dispatch a bounded coder work order to Codex in a Herdr pane",
        ),
        (
            "coder_receipt_validated",
            "Tau waits for and validates a canonical coder node receipt",
        ),
        (
            "reviewer_dispatched",
            "Tau dispatches the reviewer only after the coder receipt validates",
        ),
        (
            "reviewer_receipt_validated",
            "Tau waits for and validates a canonical reviewer node receipt",
        ),
        (
            "loop_stopped",
            "Tau stops the while loop on reviewer PASS, failure, or max attempts",
        ),
    ]
    return [claim for stage, claim in claims_by_stage if stage in reached]


def _attempt_record(
    *,
    attempt: int,
    coder_receipt_path: Path,
    reviewer_receipt_path: Path,
    coder_receipt: dict[str, Any],
    reviewer_receipt: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "coder_receipt_path": str(coder_receipt_path),
        "reviewer_receipt_path": str(reviewer_receipt_path),
        "coder_status": coder_receipt.get("status"),
        "coder_verdict": coder_receipt.get("verdict"),
        "reviewer_status": reviewer_receipt.get("status"),
        "reviewer_verdict": reviewer_receipt.get("verdict"),
        "errors": errors,
    }


def _provider_map(readiness_receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = readiness_receipt.get("readiness_records")
    if not isinstance(records, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if isinstance(record, dict) and record.get("ready") is True:
            provider_id = str(record.get("provider_id") or "")
            if provider_id:
                result[provider_id] = record
    return result


def _provider_sessions(provider_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    sessions: dict[str, dict[str, Any]] = {}
    for provider_id, record in provider_map.items():
        evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
        diagnostics = (
            record.get("diagnostics") if isinstance(record.get("diagnostics"), dict) else {}
        )
        session_state = (
            record.get("provider_session_state")
            if isinstance(record.get("provider_session_state"), dict)
            else {}
        )
        sessions[provider_id] = {
            "role": "coder" if provider_id == "codex" else "reviewer",
            "provider_id": provider_id,
            "workspace_id": record.get("workspace_id"),
            "pane_id": record.get("pane_id"),
            "terminal_id": record.get("terminal_id"),
            "state": record.get("state"),
            "ready": record.get("ready"),
            "source": record.get("source"),
            "visible": bool(record.get("pane_id") and record.get("terminal_id")),
            "foreground_command": evidence.get("foreground_command"),
            "process_alive": evidence.get("process_alive"),
            "visible_log_path": evidence.get("visible_log_path"),
            "provider_readiness_path": evidence.get("provider_readiness_path"),
            "provider_session_state_path": evidence.get("provider_session_state_path"),
            "provider_session_state": session_state,
            "visible_prompt_is_gate": diagnostics.get("visible_prompt_is_gate"),
        }
    return sessions


def _start_control_role_panes(
    *,
    run_id: str,
    run_dir: Path,
    repo: Path,
    provider_map: dict[str, dict[str, Any]],
    herdr_bin: str,
    command_results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    workspace_id = _shared_workspace_id(provider_map)
    if not workspace_id:
        return {}
    control_sessions: dict[str, dict[str, Any]] = {}
    for role in ("planner", "orchestrator"):
        pane = _start_visible_control_pane(
            role=role,
            run_id=run_id,
            run_dir=run_dir,
            repo=repo,
            workspace_id=workspace_id,
            herdr_bin=herdr_bin,
            command_results=command_results,
        )
        if pane:
            control_sessions[role] = pane
    return control_sessions


def _shared_workspace_id(provider_map: dict[str, dict[str, Any]]) -> str:
    workspace_ids = {
        str(record.get("workspace_id") or "")
        for record in provider_map.values()
        if record.get("workspace_id")
    }
    return sorted(workspace_ids)[0] if workspace_ids else ""


def _start_visible_control_pane(
    *,
    role: str,
    run_id: str,
    run_dir: Path,
    repo: Path,
    workspace_id: str,
    herdr_bin: str,
    command_results: list[dict[str, Any]],
) -> dict[str, Any]:
    message = (
        f"Tau {role} subagent visible monitor\\n"
        f"run_id: {run_id}\\n"
        f"run_dir: {run_dir}\\n"
        f"dag_spec: {run_dir / 'dag-spec.json'}\\n"
        f"planner_receipt: {run_dir / 'planner-receipt.json'}\\n"
        f"final_receipt: {run_dir / 'run-receipt.json'}\\n"
        "canonical truth: Tau JSON receipts; this pane is human-visible telemetry.\\n"
    )
    command = f"printf '%s\\n' {json.dumps(message)}; exec sleep 86400"
    result = subprocess.run(
        [
            herdr_bin,
            "agent",
            "start",
            f"{run_id}-{role}",
            "--cwd",
            str(repo),
            "--workspace",
            workspace_id,
            "--no-focus",
            "--",
            "bash",
            "-lc",
            command,
        ],
        cwd=str(repo),
        text=True,
        capture_output=True,
    )
    command_results.append(_command_result_dict(result))
    if result.returncode != 0:
        return {
            "role": role,
            "visible": False,
            "workspace_id": workspace_id,
            "error": result.stderr or result.stdout,
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "role": role,
            "visible": False,
            "workspace_id": workspace_id,
            "error": "herdr agent start returned non-json output",
        }
    agent = payload.get("result", {}).get("agent", {}) if isinstance(payload, dict) else {}
    if not isinstance(agent, dict):
        agent = {}
    return {
        "role": role,
        "provider_id": "tau",
        "workspace_id": agent.get("workspace_id") or workspace_id,
        "pane_id": agent.get("pane_id"),
        "terminal_id": agent.get("terminal_id"),
        "tab_id": agent.get("tab_id"),
        "visible": bool(agent.get("pane_id") and agent.get("terminal_id")),
        "command": "bash -lc <tau-visible-monitor>",
    }


def _start_visible_deterministic_coder_pane(
    *,
    run_id: str,
    attempt: int,
    repo: Path,
    provider_record: dict[str, Any],
    work_order_path: Path,
    herdr_bin: str,
    command_results: list[dict[str, Any]],
) -> dict[str, Any]:
    workspace_id = str(provider_record.get("workspace_id") or "")
    if not workspace_id:
        return {
            "role": "coder",
            "provider_id": "tau-deterministic-visible",
            "visible": False,
            "error": "missing codex workspace_id for deterministic coder pane",
        }
    script = (
        "import json, sys; "
        "from pathlib import Path; "
        "wo=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); "
        "target=Path(wo['target_file']); "
        "target.write_text('completed implementation by deterministic visible coder attempt %s\\n' % wo['attempt'], encoding='utf-8'); "
        "receipt=Path(wo['receipt_path']); "
        "receipt.parent.mkdir(parents=True, exist_ok=True); "
        "payload={"
        f"'schema':{PROVIDER_DAG_NODE_RECEIPT_SCHEMA!r},"
        "'dag_id':wo['dag_id'],"
        "'goal_hash':wo['goal']['goal_hash'],"
        "'node_id':'coder',"
        "'provider_id':wo['provider_id'],"
        "'attempt':wo['attempt'],"
        "'workspace_id':wo['herdr']['workspace_id'],"
        "'pane_id':wo['herdr']['pane_id'],"
        "'terminal_id':wo['herdr']['terminal_id'],"
        "'work_order_sha256':wo['work_order_sha256'],"
        "'status':'PASS',"
        "'verdict':'PASS',"
        "'work_order_path':str(Path(sys.argv[1])),"
        "'changed_files':[str(target)],"
        "'commands_run':['python deterministic visible coder'],"
        "'artifacts':[str(target)],"
        "'handoff_summary':'Deterministic visible coder updated the scratch target file.',"
        "'errors':[],"
        "'policy_exceptions':[]"
        "}; "
        "receipt.write_text(json.dumps(payload, indent=2, sort_keys=True)+'\\n', encoding='utf-8'); "
        "print(json.dumps({'wrote_receipt': str(receipt), 'target_file': str(target)}))"
    )
    result = _run_pane_command(
        [
            herdr_bin,
            "agent",
            "start",
            f"{run_id}-coder-attempt-{attempt:02d}",
            "--cwd",
            str(repo),
            "--workspace",
            workspace_id,
            "--no-focus",
            "--",
            "python3",
            "-c",
            script,
            str(work_order_path),
        ],
        cwd=repo,
        timeout_seconds=30.0,
    )
    command_results.append(_command_result_dict(result))
    if result.returncode != 0:
        return {
            "role": "coder",
            "provider_id": "tau-deterministic-visible",
            "workspace_id": workspace_id,
            "visible": False,
            "error": result.stderr or result.stdout or "herdr deterministic coder start failed",
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "role": "coder",
            "provider_id": "tau-deterministic-visible",
            "workspace_id": workspace_id,
            "visible": False,
            "error": "herdr deterministic coder start returned non-json output",
        }
    agent = payload.get("result", {}).get("agent", {}) if isinstance(payload, dict) else {}
    if not isinstance(agent, dict):
        agent = {}
    return {
        "role": "coder",
        "provider_id": "tau-deterministic-visible",
        "workspace_id": agent.get("workspace_id") or workspace_id,
        "pane_id": agent.get("pane_id"),
        "terminal_id": agent.get("terminal_id"),
        "tab_id": agent.get("tab_id"),
        "visible": bool(agent.get("pane_id") and agent.get("terminal_id")),
        "command": "python3 -c <tau-deterministic-visible-coder> <work-order>",
        "work_order_path": str(work_order_path),
        "readiness_pane_id": provider_record.get("pane_id"),
    }


def _start_visible_opencode_run_pane(
    *,
    run_id: str,
    attempt: int,
    repo: Path,
    provider_record: dict[str, Any],
    prompt: str,
    model: str | None,
    herdr_bin: str,
    command_results: list[dict[str, Any]],
) -> dict[str, Any]:
    workspace_id = str(provider_record.get("workspace_id") or "")
    if not workspace_id:
        return {
            "role": "reviewer",
            "provider_id": "opencode",
            "visible": False,
            "error": "missing opencode workspace_id",
        }
    argv = [
        herdr_bin,
        "agent",
        "start",
        f"{run_id}-reviewer-attempt-{attempt:02d}",
        "--cwd",
        str(repo),
        "--workspace",
        workspace_id,
        "--no-focus",
        "--",
        "opencode",
        "run",
        "--dir",
        str(repo),
    ]
    if model:
        argv.extend(["--model", model])
    argv.append(prompt)
    result = _run_pane_command(
        argv,
        cwd=repo,
        timeout_seconds=30.0,
    )
    command_results.append(_command_result_dict(result))
    if result.returncode != 0:
        return {
            "role": "reviewer",
            "provider_id": "opencode",
            "workspace_id": workspace_id,
            "visible": False,
            "error": result.stderr or result.stdout or "herdr opencode run start failed",
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "role": "reviewer",
            "provider_id": "opencode",
            "workspace_id": workspace_id,
            "visible": False,
            "error": "herdr opencode run start returned non-json output",
        }
    agent = payload.get("result", {}).get("agent", {}) if isinstance(payload, dict) else {}
    if not isinstance(agent, dict):
        agent = {}
    return {
        "role": "reviewer",
        "provider_id": "opencode",
        "workspace_id": agent.get("workspace_id") or workspace_id,
        "pane_id": agent.get("pane_id"),
        "terminal_id": agent.get("terminal_id"),
        "tab_id": agent.get("tab_id"),
        "visible": bool(agent.get("pane_id") and agent.get("terminal_id")),
        "command": "opencode run --dir <repo> [--model <reviewer-model>] <reviewer-prompt>",
        "reviewer_model": model,
        "readiness_pane_id": provider_record.get("pane_id"),
    }


def _capture_visible_logs(
    records: dict[str, dict[str, Any]],
    logs_dir: Path,
    herdr_bin: str,
    cwd: Path,
    command_results: list[dict[str, Any]],
) -> None:
    seen_panes: set[str] = set()
    for name, record in records.items():
        pane_id = str(record.get("pane_id") or "")
        if not pane_id or pane_id in seen_panes:
            continue
        seen_panes.add(pane_id)
        read = subprocess.run(
            [herdr_bin, "pane", "read", pane_id, "--source", "visible", "--lines", "120"],
            cwd=str(cwd),
            text=True,
            capture_output=True,
        )
        command_results.append(_command_result_dict(read))
        safe_name = _slug(f"{name}-{pane_id}")
        log_text = read.stdout
        if read.returncode != 0:
            log_text = f"pane_read_failed returncode={read.returncode}\n{read.stderr or read.stdout}"
        (logs_dir / f"{safe_name}.visible.txt").write_text(log_text, encoding="utf-8")


def _blocked_run_receipt(
    *,
    run_id: str,
    run_dir: Path,
    events_path: Path,
    errors: list[str],
    readiness_receipt: dict[str, Any],
    attempts: list[dict[str, Any]],
    final_status: str,
) -> dict[str, Any]:
    return {
        "schema": PROVIDER_DAG_RUN_SCHEMA,
        "ok": False,
        "status": final_status,
        "verdict": "BLOCKED",
        "mocked": False,
        "live": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "events_jsonl": str(events_path),
        "provider_readiness_receipt": readiness_receipt,
        "attempts": attempts,
        "errors": errors,
        "timestamp": _utc_stamp(),
    }


def _command_result_dict(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "argv": result.args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_event(path: Path, kind: str, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema": "tau.provider_dag_event.v1",
        "kind": kind,
        "timestamp": _utc_stamp(),
        **payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _compact_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "-" for char in value.strip()]
    return "-".join(part for part in "".join(chars).split("-") if part)[:80] or "provider-dag"
