"""Generic receipt-gated DAG runner for Tau orchestration."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    receipt_path: Path
    timeout_seconds: float
    max_attempts: int
    work_order_path: Path | None


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
    spec = _read_json_object(resolved_spec_path, label="generic DAG spec")
    nodes = _validate_spec(spec, spec_path=resolved_spec_path)
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
    final_status = "PASS"
    final_verdict = "PASS"
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

    for node in _topological_order(nodes):
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
            active_node_id=node.node_id,
        )
        missing_dependencies = [dep for dep in node.depends_on if dep not in completed]
        if missing_dependencies:
            final_status = "BLOCKED"
            final_verdict = "DEPENDENCY_NOT_SATISFIED"
            node_results.append(
                _blocked_node_record(
                    node,
                    verdict=final_verdict,
                    errors=[f"missing passed dependencies: {', '.join(missing_dependencies)}"],
                )
            )
            break
        result = _run_node(
            node,
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            resume=resume,
        )
        node_results.append(result)
        if result["status"] == "PASS" and result["verdict"] == "PASS":
            completed.add(node.node_id)
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
            continue
        final_status = "BLOCKED"
        final_verdict = str(result.get("verdict") or "NODE_BLOCKED")
        break

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
    live = True if provider_live else False
    receipt = {
        "schema": GENERIC_DAG_RUN_RECEIPT_SCHEMA,
        "ok": final_status == "PASS",
        "status": final_status,
        "verdict": final_verdict,
        "mocked": False,
        "live": live,
        "provider_live": provider_live,
        "execution": "local_subprocess_receipt_gated_dag",
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
        "proof_scope": _proof_scope(provider_live=provider_live),
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
            "receipt_paths": {
                node_id: str(node.receipt_path) for node_id, node in nodes.items()
            },
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

    command_results: list[dict[str, Any]] = []
    last_errors: list[str] = []
    for attempt in range(1, node.max_attempts + 1):
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
        result = _run_command(node.command, cwd=run_dir, timeout_seconds=node.timeout_seconds)
        elapsed = time.monotonic() - started_at
        command_results.append(_command_result_dict(result, elapsed_seconds=elapsed))
        if result.returncode != 0:
            last_errors = [_command_error(result)]
            verdict = "SUBAGENT_TIMEOUT" if result.returncode == 124 else "SUBAGENT_ERROR"
            if attempt >= node.max_attempts:
                return _blocked_node_record(
                    node,
                    verdict=verdict,
                    errors=last_errors,
                    attempt_count=attempt,
                    command_results=command_results,
                    started_at=node_started_at,
                    finished_at=_utc_stamp(),
                    duration_seconds=time.monotonic() - node_started_monotonic,
                )
            continue
        if not node.receipt_path.exists():
            last_errors = [f"node receipt did not appear: {node.receipt_path}"]
            if attempt >= node.max_attempts:
                return _blocked_node_record(
                    node,
                    verdict="RECEIPT_MISSING",
                    errors=last_errors,
                    attempt_count=attempt,
                    command_results=command_results,
                    started_at=node_started_at,
                    finished_at=_utc_stamp(),
                    duration_seconds=time.monotonic() - node_started_monotonic,
                )
            continue
        receipt = _read_json_object(node.receipt_path, label=f"{node.node_id} receipt")
        errors = _validate_node_receipt(receipt, node)
        if errors:
            last_errors = errors
            if attempt >= node.max_attempts:
                return _blocked_node_record(
                    node,
                    verdict="INVALID_RECEIPT",
                    errors=last_errors,
                    attempt_count=attempt,
                    command_results=command_results,
                    started_at=node_started_at,
                    finished_at=_utc_stamp(),
                    duration_seconds=time.monotonic() - node_started_monotonic,
                )
            continue
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
    return _blocked_node_record(
        node,
        verdict="MAX_ATTEMPTS_EXHAUSTED",
        errors=last_errors or ["node did not complete"],
        attempt_count=node.max_attempts,
        command_results=command_results,
        started_at=node_started_at,
        finished_at=_utc_stamp(),
        duration_seconds=time.monotonic() - node_started_monotonic,
    )


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


def _parse_node(raw_node: dict[str, Any], *, base_dir: Path) -> DagNode:
    node_id = _required_string(raw_node, "node_id")
    role = str(raw_node.get("role") or node_id)
    command = raw_node.get("command")
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise RuntimeError(f"node {node_id} command must be a non-empty string list")
    depends_on = raw_node.get("depends_on", [])
    if not isinstance(depends_on, list) or not all(isinstance(dep, str) for dep in depends_on):
        raise RuntimeError(f"node {node_id} depends_on must be a string list")
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
    return DagNode(
        node_id=node_id,
        role=role,
        command=command,
        depends_on=tuple(depends_on),
        receipt_path=receipt_path,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        work_order_path=work_order_path,
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
    if expected_work_order_hash is not None and receipt.get("work_order_sha256") != expected_work_order_hash:
        errors.append(
            "work_order_sha256 must match current work_order_path "
            f"{node.work_order_path}"
        )
    return errors


def _work_order_sha256(node: DagNode) -> str | None:
    if node.work_order_path is None:
        return None
    try:
        data = node.work_order_path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _proof_scope(*, provider_live: bool) -> dict[str, list[str]]:
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
    else:
        does_not_prove.extend(
            [
                "live provider CLI execution",
                "Herdr pane visibility",
            ]
        )
    return {"proves": proves, "does_not_prove": does_not_prove}


def _spec_path_from_run_metadata(run_dir: Path) -> tuple[Path, Path]:
    for path in (run_dir / "current-state.json", run_dir / "checkpoint.json", run_dir / "run-receipt.json"):
        payload = _optional_json_object(path)
        spec_path = payload.get("spec_path")
        if isinstance(spec_path, str) and spec_path:
            resolved = Path(spec_path).expanduser()
            if not resolved.is_absolute():
                resolved = run_dir / resolved
            return resolved.resolve(), path
    raise RuntimeError(
        "generic DAG run metadata does not record spec_path; "
        "rerun tau dag-run <dag-spec> directly"
    )


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
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
        return subprocess.CompletedProcess(command, 124, stdout=stdout, stderr=stderr)


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
