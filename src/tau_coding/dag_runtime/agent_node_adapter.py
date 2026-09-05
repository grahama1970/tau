"""Scheduler dispatch for ``tau_native_agent_loop`` DAG nodes (tau#310).

Turns a compiled ``DagPlanNode`` with adapter kind ``tau_native_agent_loop``
into an ``AgentNodeRun`` executed under the canonical scheduler's injected
``execute_node`` boundary. The provider is supplied by the caller (SciLLM
transport for live runs, ``FakeProvider`` for deterministic fixtures) — the
adapter itself never talks to a provider SDK.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tau_agent.tools import AgentTool
from tau_coding.dag_runtime.agent_node import AgentNodeError, AgentNodeRun, ToolPolicy
from tau_coding.dag_runtime.model import DagPlanNode, canonical_sha256
from tau_coding.runtime_backends.contracts import RuntimeRequirement

TAU_NATIVE_ADAPTER_KIND = "tau_native_agent_loop"
TAU_AGENT_WORKER_HANDSHAKE_SCHEMA = "tau.agent_node_worker_handshake.v1"
TAU_AGENT_WORKER_RUNTIME_VERSION = "tau-herdr-headless-agent-worker.v1"

ProviderFactory = Callable[[DagPlanNode, Mapping[str, Any]], Any]
ToolsFactory = Callable[[DagPlanNode, Mapping[str, Any]], Sequence[AgentTool]]


def execute_tau_agent_node(
    plan_node: DagPlanNode,
    accepted_inputs: tuple[dict[str, Any], ...],
    execution: Any,
    *,
    goal_hash: str,
    provider_factory: ProviderFactory,
    tools_factory: ToolsFactory,
    run_store: Any | None = None,
    lease: Any | None = None,
    runtime_backend: Any | None = None,
    runtime_cwd: Path | None = None,
) -> dict[str, Any]:
    """Execute one Tau-native agent node and map its settlement to the scheduler."""
    config = dict(plan_node.adapter_config.to_value() or {})
    runtime_requirement = RuntimeRequirement.from_payload(
        plan_node.runtime_requirement.to_value()
    )
    prompt = str(config.get("prompt", ""))
    if not prompt:
        return _result(plan_node, "FAIL", "AGENT_NODE_PROMPT_MISSING", errors=["prompt missing"])
    context_lines = [
        f"[accepted upstream {item.get('source_node_id', '?')}]: "
        + str(item.get("accepted_output"))
        for item in accepted_inputs
    ]
    if context_lines:
        prompt = "\n".join(["Accepted upstream context:", *context_lines, "", prompt])
    work_order = {
        "schema": "tau.agent_node.v1",
        "run_id": execution.run_id,
        "node_id": plan_node.node_id,
        "attempt_id": execution.attempt_id,
        "attempt": execution.attempt,
        "goal_hash": goal_hash,
        "plan_sha256": getattr(plan_node, "plan_sha256", None) or "0" * 64,
        "model": str(config.get("model", "profile-owned")),
        "harness": str(config.get("harness", "tau_native_agent_loop")),
        "role": config.get("role"),
        "required_evidence": list(config.get("required_evidence", [])),
        "transport_profile_selection": config.get("transport_profile_selection"),
    }
    tools = list(tools_factory(plan_node, config))
    policy = ToolPolicy(
        goal_hash=goal_hash,
        allowed_tools=tuple(config.get("allowed_tools", [tool.name for tool in tools])),
        allowed_paths=tuple(config.get("allowed_paths", ["**"])),
        max_tool_calls=int(config.get("max_tool_calls", 16)),
    )
    if runtime_requirement.backend == "herdr":
        return _execute_tau_agent_node_with_herdr(
            plan_node=plan_node,
            execution=execution,
            config=config,
            prompt=prompt,
            accepted_inputs=accepted_inputs,
            work_order=work_order,
            policy=policy,
            run_store=run_store,
            lease=lease,
            runtime_backend=runtime_backend,
            runtime_cwd=runtime_cwd,
        )
    if runtime_requirement.backend not in {"local", "none"}:
        return _result(
            plan_node,
            "BLOCKED",
            "RUNTIME_BACKEND_UNSUPPORTED",
            errors=[
                "tau_native_agent_loop cannot execute requested backend "
                f"{runtime_requirement.backend!r}"
            ],
        )
    event_sink = None
    prior_effects: dict[str, Any] = {}
    if run_store is not None and lease is not None:
        from tau_coding.dag_runtime.agent_events import (
            DurableAgentEventSink,
            admitted_tool_effects,
            load_agent_events,
        )

        event_sink = DurableAgentEventSink(
            store=run_store,
            lease=lease,
            plan_sha256=work_order["plan_sha256"],
            goal_hash=goal_hash,
            work_order_sha256=canonical_sha256(work_order),
            attempt_id=execution.attempt_id,
        )
        # SQLite connections are thread-affine; this executor runs on a
        # scheduler worker thread, so read prior effects on a fresh connection.
        from tau_coding.dag_runtime.run_store import SqliteDagRunStore

        reader = SqliteDagRunStore(run_store.path)
        try:
            prior_effects = admitted_tool_effects(
                load_agent_events(reader, lease.run_id, node_id=plan_node.node_id)
            )
        finally:
            reader.close()
    try:
        run = AgentNodeRun(
            work_order=work_order,
            policy=policy,
            provider=provider_factory(plan_node, config),
            tools=tools,
            max_turns=int(config.get("max_turns", 8)),
            event_sink=event_sink,
            prior_tool_effects=prior_effects,
        )
        asyncio.run(run.run(prompt))
        if run.tool_effect_receipts and all(r["ok"] for r in run.tool_effect_receipts):
            run.add_evidence(
                "tool_effect_receipt",
                {"receipts": [r["sha256"] for r in run.tool_effect_receipts]},
            )
        settlement = run.settle()
    except AgentNodeError as error:
        return _result(plan_node, "FAIL", error.code.upper(), errors=[str(error)])
    status_by_state = {
        "completed": "PASS",
        "failed": "FAIL",
        "cancelled": "FAIL",
        "blocked": "BLOCKED",
    }
    status = status_by_state[settlement["state"]]
    return _result(
        plan_node,
        status,
        settlement["state"].upper() if settlement["state"] != "completed" else "PASS",
        accepted_output=(
            {
                "settlement": settlement,
                "final_text": (
                    run.turn_receipts[-1]["assistant_text"] if run.turn_receipts else ""
                ),
            }
            if status == "PASS"
            else None
        ),
        errors=list(settlement["blockers"]),
    )


def _execute_tau_agent_node_with_herdr(
    *,
    plan_node: DagPlanNode,
    execution: Any,
    config: Mapping[str, Any],
    prompt: str,
    accepted_inputs: tuple[dict[str, Any], ...],
    work_order: dict[str, Any],
    policy: ToolPolicy,
    run_store: Any | None,
    lease: Any | None,
    runtime_backend: Any | None,
    runtime_cwd: Path | None,
) -> dict[str, Any]:
    if runtime_backend is None:
        return _result(
            plan_node,
            "BLOCKED",
            "HERDR_RUNTIME_BACKEND_REQUIRED",
            errors=[
                "tau_native_agent_loop requested Herdr, but this adapter call "
                "did not provide a Herdr worker backend"
            ],
        )
    if run_store is None or lease is None:
        return _result(
            plan_node,
            "BLOCKED",
            "HERDR_RUN_STORE_REQUIRED",
            errors=["Herdr-backed Tau agent nodes require the durable run store"],
        )
    command = _string_list(config.get("worker_command"), "worker_command")
    if not command:
        return _result(
            plan_node,
            "BLOCKED",
            "HERDR_WORKER_COMMAND_REQUIRED",
            errors=["Herdr-backed Tau agent nodes require adapter_config.worker_command"],
        )
    cwd = (runtime_cwd or Path(str(config.get("cwd") or "."))).expanduser().resolve()
    if not cwd.is_dir():
        return _result(
            plan_node,
            "BLOCKED",
            "HERDR_WORKER_CWD_INVALID",
            errors=[f"Herdr worker cwd is not an existing directory: {cwd}"],
        )
    attempt_dir = (
        Path(run_store.path).parent
        / "agent-workers"
        / plan_node.node_id
        / f"attempt-{execution.attempt:03d}"
    )
    settlement_path = _worker_path(
        config.get("worker_settlement_path") or attempt_dir / "settlement.json",
        cwd=cwd,
    )
    handshake_path = _worker_path(
        config.get("worker_handshake_path") or attempt_dir / "handshake.json",
        cwd=cwd,
    )
    handshake = _worker_handshake(
        prompt=prompt,
        accepted_inputs=accepted_inputs,
        work_order=work_order,
        policy=policy,
        run_store_path=Path(run_store.path),
        settlement_path=settlement_path,
        config=config,
    )
    handshake_path.parent.mkdir(parents=True, exist_ok=True)
    handshake_path.write_text(
        json.dumps(handshake, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    formatted_command = [
        item.format(
            handshake_path=str(handshake_path),
            settlement_path=str(settlement_path),
            run_store_path=str(run_store.path),
            work_order_sha256=canonical_sha256(work_order),
        )
        for item in command
    ]
    try:
        from tau_coding.runtime_backends.event_bridge import RuntimeEventBridge
        from tau_coding.runtime_backends.herdr import (
            herdr_runtime_scope_request,
            herdr_runtime_spawn_request,
        )

        owner = str(config.get("runtime_owner") or "tau")
        scope = runtime_backend.ensure_scope(
            herdr_runtime_scope_request(
                run_id=execution.run_id,
                owner=owner,
                cwd=cwd,
                label=str(config.get("runtime_scope_label") or "tau-agent-workers"),
            )
        ).to_value()
        endpoint = runtime_backend.spawn(
            herdr_runtime_spawn_request(
                run_id=execution.run_id,
                plan_revision=work_order["plan_sha256"],
                dag_id=str(config.get("dag_id") or execution.run_id),
                node_id=plan_node.node_id,
                attempt_id=execution.attempt_id,
                attempt_number=execution.attempt,
                execution_token=execution.idempotency_key,
                scope_id=str(scope["scope_id"]),
                command=formatted_command,
                cwd=cwd,
                work_order_sha256=canonical_sha256(work_order),
                goal_hash=work_order["goal_hash"],
                owner=owner,
                label=plan_node.node_id,
                environment={
                    "TAU_AGENT_WORKER_HANDSHAKE": str(handshake_path),
                    "TAU_AGENT_WORK_ORDER_SHA256": canonical_sha256(work_order),
                },
                lease_seconds=float(config.get("runtime_lease_seconds") or 3600.0),
            )
        )
        RuntimeEventBridge(run_store).wait_and_append(
            lease=lease,
            backend=runtime_backend,
            endpoint=endpoint,
            cursor=None,
            deadline=datetime.now(UTC)
            + timedelta(seconds=float(config.get("runtime_observe_seconds") or 5.0)),
        )
        settlement = _wait_for_settlement(
            settlement_path,
            timeout_seconds=float(config.get("worker_settlement_timeout_seconds") or 60.0),
        )
    except Exception as exc:  # noqa: BLE001
        return _result(
            plan_node,
            "BLOCKED",
            "HERDR_WORKER_EXECUTION_BLOCKED",
            errors=[f"{type(exc).__name__}:{exc}"],
        )
    settlement_error = _settlement_error(settlement, work_order)
    if settlement_error is not None:
        return _result(
            plan_node,
            "BLOCKED",
            "HERDR_WORKER_SETTLEMENT_REJECTED",
            errors=[settlement_error],
        )
    status_by_state = {
        "completed": "PASS",
        "failed": "FAIL",
        "cancelled": "FAIL",
        "blocked": "BLOCKED",
    }
    status = status_by_state.get(str(settlement["state"]), "BLOCKED")
    return _result(
        plan_node,
        status,
        str(settlement["state"]).upper() if status != "PASS" else "PASS",
        accepted_output=(
            {
                "settlement": settlement,
                "final_text": str(settlement.get("final_text") or ""),
            }
            if status == "PASS"
            else None
        ),
        errors=list(settlement.get("blockers") or []),
    )


def _worker_handshake(
    *,
    prompt: str,
    accepted_inputs: tuple[dict[str, Any], ...],
    work_order: dict[str, Any],
    policy: ToolPolicy,
    run_store_path: Path,
    settlement_path: Path,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema": TAU_AGENT_WORKER_HANDSHAKE_SCHEMA,
        "worker_runtime_version": TAU_AGENT_WORKER_RUNTIME_VERSION,
        "capabilities": [
            "headless",
            "durable_agent_events",
            "operator_actions_v1",
            "tau_authoritative_settlement",
        ],
        "work_order": work_order,
        "work_order_sha256": canonical_sha256(work_order),
        # The worker must run the same AgentNodeRun the local backend runs, so
        # it needs the exact prompt and accepted inputs, not only their digests.
        "prompt": prompt,
        "prompt_sha256": canonical_sha256(prompt),
        "accepted_inputs": [dict(item) for item in accepted_inputs],
        "accepted_inputs_sha256": canonical_sha256(list(accepted_inputs)),
        "run_store_path": str(run_store_path),
        "settlement_path": str(settlement_path),
        "selected_transport_profile": work_order.get("transport_profile_selection"),
        "policy": {
            "goal_hash": policy.goal_hash,
            "allowed_tools": list(policy.allowed_tools),
            "allowed_paths": list(policy.allowed_paths),
            "max_tool_calls": policy.max_tool_calls,
        },
        "policy_hash": canonical_sha256(
            {
                "goal_hash": policy.goal_hash,
                "allowed_tools": list(policy.allowed_tools),
                "allowed_paths": list(policy.allowed_paths),
                "max_tool_calls": policy.max_tool_calls,
            }
        ),
        "data_boundary_hash": config.get("data_boundary_hash"),
        "worktree_hash": config.get("worktree_hash"),
        "redaction_policy": config.get("redaction_policy", "no_raw_provider_credentials"),
        "deadline": (
            datetime.now(UTC)
            + timedelta(seconds=float(config.get("worker_deadline_seconds") or 3600.0))
        ).isoformat(),
        "cancellation": {
            "safe_point": "scheduler_boundary",
            "on_scheduler_loss": "continue_to_tau_receipt_or_explicit_cancel",
        },
        "cleanup": {"owned_endpoint_cleanup_requires_exact_authorization": True},
    }
    payload["handshake_sha256"] = canonical_sha256(payload)
    return payload


def _wait_for_settlement(path: Path, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05)
            continue
        if isinstance(payload, dict):
            return payload
        last_error = ValueError("settlement payload must be an object")
        time.sleep(0.05)
    raise TimeoutError(f"settlement not readable after {timeout_seconds}s: {path}: {last_error}")


def _settlement_error(settlement: Mapping[str, Any], work_order: Mapping[str, Any]) -> str | None:
    expected = {
        "schema": "tau.agent_node_settlement.v1",
        "run_id": work_order["run_id"],
        "node_id": work_order["node_id"],
        "attempt_id": work_order["attempt_id"],
        "attempt": work_order["attempt"],
        "goal_hash": work_order["goal_hash"],
        "plan_sha256": work_order["plan_sha256"],
        "harness": work_order["harness"],
    }
    for key, value in expected.items():
        if settlement.get(key) != value:
            return f"settlement_{key}_mismatch"
    if settlement.get("work_order_sha256") not in {None, canonical_sha256(work_order)}:
        return "settlement_work_order_sha256_mismatch"
    if settlement.get("state") not in {"completed", "failed", "cancelled", "blocked"}:
        return "settlement_state_invalid"
    blockers = settlement.get("blockers", [])
    if not isinstance(blockers, list):
        return "settlement_blockers_invalid"
    return None


def _string_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{label} must contain non-empty strings")
    return list(value)


def _worker_path(value: Any, *, cwd: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _result(
    plan_node: DagPlanNode,
    status: str,
    verdict: str,
    *,
    accepted_output: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": plan_node.node_id,
        "status": status,
        "verdict": verdict,
        "accepted_output": accepted_output,
        "errors": errors or [],
    }
