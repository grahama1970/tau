"""Project-agent DAG contract runner for Tau handoff loops."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.handoff_dispatch import (
    dispatch_agent_handoff_command_once,
    load_agent_dispatch_command_spec,
    write_agent_handoff_command_loop_receipt,
)

try:  # YAML is available in the project lock through docs tooling, but keep JSON first.
    import yaml
except ImportError:  # pragma: no cover - exercised only in stripped runtime environments.
    yaml = None  # type: ignore[assignment]


DAG_CONTRACT_SCHEMA = "tau.dag_contract.v1"
DAG_RECEIPT_SCHEMA = "tau.dag_receipt.v1"


@dataclass(frozen=True, slots=True)
class ProjectDagNode:
    node_id: str
    agent: str
    executor: str
    max_attempts: int
    command_spec: str | None
    required_evidence: tuple[str, ...]
    reviewer: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ProjectDagEdge:
    source: str
    target: str
    condition: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectDagContract:
    payload: dict[str, Any]
    dag_id: str
    goal: dict[str, Any]
    target: dict[str, Any]
    entry_node: str
    terminal_nodes: tuple[str, ...]
    nodes: dict[str, ProjectDagNode]
    edges: tuple[ProjectDagEdge, ...]
    limits: dict[str, Any]
    required_evidence: tuple[str, ...]
    fail_closed_on: tuple[str, ...]


def run_project_dag_contract(
    *,
    contract_path: Path,
    receipt_dir: Path | None = None,
    agents_root: Path,
    command_spec_root: Path | None = None,
    scheduler: str = "handoff-loop",
) -> dict[str, Any]:
    """Run a project-agent DAG contract through the existing handoff command loop."""

    resolved_contract_path = contract_path.expanduser().resolve()
    payload = load_dag_contract_payload(resolved_contract_path)
    contract = validate_dag_contract(payload)
    resolved_receipt_dir = _resolve_receipt_dir(
        contract=contract,
        contract_path=resolved_contract_path,
        receipt_dir=receipt_dir,
    )
    resolved_receipt_dir.mkdir(parents=True, exist_ok=True)

    compiled_spec_root = _compile_command_specs(
        contract=contract,
        contract_path=resolved_contract_path,
        receipt_dir=resolved_receipt_dir,
        fallback_root=command_spec_root,
    )
    if scheduler not in {"handoff-loop", "bounded-ready-queue"}:
        raise RuntimeError(f"unknown project DAG scheduler: {scheduler}")
    if scheduler == "bounded-ready-queue":
        return _run_bounded_ready_queue_project_dag(
            contract=contract,
            contract_path=resolved_contract_path,
            receipt_dir=resolved_receipt_dir,
            agents_root=agents_root.expanduser().resolve(),
            command_spec_root=compiled_spec_root,
        )

    start_handoff = _start_handoff(contract, contract_path=resolved_contract_path)
    start_path = resolved_receipt_dir / "start-handoff.json"
    _write_json(start_path, start_handoff)

    max_steps = _max_steps(contract)
    loop_dir = resolved_receipt_dir / "command-loop"
    loop = write_agent_handoff_command_loop_receipt(
        start_handoff,
        loop_dir,
        agent_registry_root=agents_root.expanduser().resolve(),
        command_spec_root=compiled_spec_root,
        active_goal_hash=str(contract.goal["goal_hash"]),
        max_steps=max_steps,
    )
    loop_payload = loop.as_dict()
    loop_receipt_path = loop_dir / "command-loop-receipt.json"
    if loop_receipt_path.exists():
        loop_payload = _read_json_object(loop_receipt_path, label="command-loop receipt")

    alerts = _evaluate_loop_against_contract(contract, loop_payload)
    status = "PASS" if not alerts and loop_payload.get("ok") is True else "BLOCKED"
    verdict = "PASS" if status == "PASS" else _blocked_verdict(alerts, loop_payload)
    receipt = {
        "schema": DAG_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": verdict,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "scheduler": scheduler,
        "execution": "project_agent_dag_via_handoff_command_loop",
        "dag_id": contract.dag_id,
        "contract_path": str(resolved_contract_path),
        "contract_sha256": f"sha256:{_sha256(resolved_contract_path)}",
        "run_dir": str(resolved_receipt_dir),
        "active_goal_hash": contract.goal["goal_hash"],
        "target": contract.target,
        "entry_node": contract.entry_node,
        "terminal_nodes": list(contract.terminal_nodes),
        "node_count": len(contract.nodes),
        "edge_count": len(contract.edges),
        "max_steps": max_steps,
        "command_loop_receipt": str(loop_receipt_path),
        "selected_agents": [
            dispatch.get("selected_agent")
            for dispatch in _dispatches(loop_payload)
            if dispatch.get("selected_agent")
        ],
        "observed_edges": _observed_edges(contract, loop_payload),
        "node_attempts": _node_attempts(contract, loop_payload),
        "reviewer_verdicts": _reviewer_verdicts(contract, loop_payload),
        "alerts": alerts,
        "artifacts": [
            str(start_path),
            str(loop_receipt_path),
            *[
                str(path)
                for path in sorted((resolved_receipt_dir / "compiled-command-specs").rglob("*"))
                if path.is_file()
            ],
        ],
        "proof_scope": {
            "mocked": False,
            "live": True,
            "proves": [
                "DAG contract parsed and validated.",
                "Entry node was compiled into a tau.agent_handoff.v1 start handoff.",
                "Node routing was executed by the real local command-loop subprocess runner.",
                "Observed edges and retry counts were checked against the DAG contract.",
                "Reviewer verdict evidence was checked against the immutable goal hash.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "Parallel DAG scheduling.",
                "GitHub mutation or ticket closure.",
                "Unbounded autonomous operation.",
            ],
        },
        "errors": list(loop_payload.get("errors", [])) if isinstance(loop_payload, dict) else [],
        "timestamp": _utc_stamp(),
    }
    _write_json(resolved_receipt_dir / "dag-receipt.json", receipt)
    return receipt


def _run_bounded_ready_queue_project_dag(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path,
    agents_root: Path,
    command_spec_root: Path | None,
) -> dict[str, Any]:
    """Run acyclic project DAG nodes when dependencies are ready."""

    graph_alerts = _ready_queue_contract_alerts(contract)
    if graph_alerts:
        receipt = _ready_queue_receipt(
            contract=contract,
            contract_path=contract_path,
            receipt_dir=receipt_dir,
            command_spec_root=command_spec_root,
            status="BLOCKED",
            verdict=str(graph_alerts[0]["code"]).upper(),
            alerts=graph_alerts,
            dispatches=[],
            events=[],
            node_attempts={},
            reviewer_verdicts=[],
            observed_edges=[],
            execution_seconds=0.0,
            max_observed_concurrency=0,
            errors=[],
        )
        _write_json(receipt_dir / "dag-receipt.json", receipt)
        return receipt

    max_concurrency = _max_concurrency(contract)
    runnable_nodes = {
        node_id
        for node_id, node in contract.nodes.items()
        if node.executor != "human" and node_id not in contract.terminal_nodes
    }
    predecessors = _predecessors(contract)
    successors = _successors(contract)
    completed: set[str] = set()
    failed = False
    dispatches: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    responses: dict[str, dict[str, Any]] = {}
    node_artifacts: dict[str, list[str]] = {}
    node_attempts: dict[str, int] = {}
    alerts: list[dict[str, Any]] = []
    errors: list[str] = []
    intervals: list[tuple[float, float]] = []
    started_at = time.monotonic()

    def mark_virtual_ready_nodes() -> None:
        changed = True
        while changed:
            changed = False
            for node_id in sorted(runnable_nodes - completed):
                node = contract.nodes[node_id]
                if node.command_spec:
                    continue
                if not _node_dependencies_satisfied(node_id, predecessors, completed):
                    continue
                completed.add(node_id)
                events.append(
                    {
                        "event": "virtual_node_completed",
                        "node_id": node_id,
                        "agent": node.agent,
                        "ts": _utc_stamp(),
                    }
                )
                changed = True

    def ready_nodes(running: set[str]) -> list[str]:
        return [
            node_id
            for node_id in sorted(runnable_nodes - completed - running)
            if contract.nodes[node_id].command_spec
            and _node_dependencies_satisfied(node_id, predecessors, completed)
        ]

    mark_virtual_ready_nodes()
    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        futures: dict[Future[dict[str, Any]], str] = {}
        while len(completed) < len(runnable_nodes):
            if failed:
                break
            running_node_ids = set(futures.values())
            for node_id in ready_nodes(running_node_ids):
                if len(futures) >= max_concurrency:
                    break
                node = contract.nodes[node_id]
                node_attempts[node_id] = node_attempts.get(node_id, 0) + 1
                if node_attempts[node_id] > node.max_attempts:
                    alerts.append(
                        _alert(
                            "BLOCK",
                            "max_attempts_exceeded",
                            "Node exceeded its DAG max_attempts.",
                            {
                                "node_id": node_id,
                                "attempts": node_attempts[node_id],
                                "max_attempts": node.max_attempts,
                            },
                        )
                    )
                    failed = True
                    break
                start_payload = _node_start_handoff(
                    contract,
                    node,
                    contract_path=contract_path,
                    predecessor_responses=[
                        responses[item]
                        for item in sorted(predecessors.get(node_id, set()))
                        if item in responses
                    ],
                )
                artifact_dir = receipt_dir / "ready-queue" / node_id / f"attempt-{node_attempts[node_id]:03d}"
                future = executor.submit(
                    _dispatch_ready_node,
                    node=node,
                    start_payload=start_payload,
                    agents_root=agents_root,
                    command_spec_root=command_spec_root,
                    artifact_dir=artifact_dir,
                )
                futures[future] = node_id
                events.append(
                    {
                        "event": "node_started",
                        "node_id": node_id,
                        "agent": node.agent,
                        "attempt": node_attempts[node_id],
                        "ts": _utc_stamp(),
                    }
                )
            if failed:
                break
            if not futures:
                remaining = sorted(runnable_nodes - completed)
                alerts.append(
                    _alert(
                        "BLOCK",
                        "ready_queue_stalled",
                        "No runnable DAG node had satisfied dependencies.",
                        {"remaining_nodes": remaining, "completed_nodes": sorted(completed)},
                    )
                )
                break
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                node_id = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive executor boundary.
                    result = {
                        "ok": False,
                        "dispatch": None,
                        "response": None,
                        "started_monotonic": time.monotonic(),
                        "completed_monotonic": time.monotonic(),
                        "errors": [str(exc)],
                    }
                intervals.append(
                    (
                        float(result["started_monotonic"]),
                        float(result["completed_monotonic"]),
                    )
                )
                dispatch = result.get("dispatch")
                if isinstance(dispatch, dict):
                    dispatches.append(dispatch)
                    dispatch_path = (
                        receipt_dir
                        / "ready-queue"
                        / node_id
                        / f"attempt-{node_attempts[node_id]:03d}"
                        / "dispatch-receipt.json"
                    )
                    _write_json(dispatch_path, dispatch)
                node_artifacts[node_id] = [
                    str(path)
                    for path in sorted((receipt_dir / "ready-queue" / node_id).rglob("*"))
                    if path.is_file()
                ]
                events.append(
                    {
                        "event": "node_completed",
                        "node_id": node_id,
                        "agent": contract.nodes[node_id].agent,
                        "attempt": node_attempts[node_id],
                        "ok": result.get("ok") is True,
                        "ts": _utc_stamp(),
                    }
                )
                if result.get("ok") is not True:
                    stop_reason = "node_dispatch_failed"
                    if isinstance(dispatch, dict):
                        stop_reason = str(dispatch.get("stop_reason") or stop_reason)
                    alerts.append(
                        _alert(
                            "BLOCK",
                            stop_reason,
                            "Ready-queue node dispatch did not pass.",
                            {"node_id": node_id, "errors": result.get("errors", [])},
                        )
                    )
                    errors.extend(str(item) for item in result.get("errors", []))
                    failed = True
                    continue
                response = result.get("response")
                if isinstance(response, dict):
                    responses[node_id] = response
                    node_alerts = _node_response_alerts(contract, contract.nodes[node_id], response)
                    if node_alerts:
                        alerts.extend(node_alerts)
                        failed = True
                    else:
                        completed.add(node_id)
                        mark_virtual_ready_nodes()
                else:
                    alerts.append(
                        _alert(
                            "BLOCK",
                            "missing_node_response",
                            "Ready-queue node did not return a JSON handoff response.",
                            {"node_id": node_id},
                        )
                    )
                    failed = True

    execution_seconds = round(time.monotonic() - started_at, 6)
    if not alerts and not _terminal_reachable_from_completed(contract, completed, successors):
        alerts.append(
            _alert(
                "BLOCK",
                "missing_terminal_route",
                "Completed DAG nodes do not reach a declared terminal node.",
                {"completed_nodes": sorted(completed), "terminal_nodes": list(contract.terminal_nodes)},
            )
        )
    observed_edges = _ready_queue_observed_edges(contract, completed)
    reviewer_verdicts = [
        verdict
        for node_id, response in responses.items()
        if contract.nodes[node_id].reviewer is not None
        for verdict in _reviewer_verdict_evidence(response)
    ]
    status = "PASS" if not alerts else "BLOCKED"
    verdict = "PASS" if status == "PASS" else str(alerts[0]["code"]).upper()
    receipt = _ready_queue_receipt(
        contract=contract,
        contract_path=contract_path,
        receipt_dir=receipt_dir,
        command_spec_root=command_spec_root,
        status=status,
        verdict=verdict,
        alerts=alerts,
        dispatches=dispatches,
        events=events,
        node_attempts=node_attempts,
        reviewer_verdicts=reviewer_verdicts,
        observed_edges=observed_edges,
        execution_seconds=execution_seconds,
        max_observed_concurrency=_max_observed_concurrency(intervals),
        errors=errors,
        node_artifacts=node_artifacts,
    )
    _write_json(receipt_dir / "dag-receipt.json", receipt)
    return receipt


def load_dag_contract_payload(path: Path) -> dict[str, Any]:
    """Load a JSON or YAML DAG contract object."""

    text = path.expanduser().resolve().read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("YAML DAG contracts require PyYAML")
        payload = yaml.safe_load(text)
    else:
        payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError(f"DAG contract root must be an object: {path}")
    return payload


def validate_dag_contract(payload: dict[str, Any]) -> ProjectDagContract:
    """Validate the strict project-agent DAG contract used by `tau dag-run`."""

    errors: list[str] = []
    if payload.get("schema") != DAG_CONTRACT_SCHEMA:
        errors.append(f"schema must be {DAG_CONTRACT_SCHEMA}")
    dag_id = _required_string(payload, "dag_id", errors)
    goal = _required_mapping(payload, "goal", errors)
    for key in ("goal_id", "goal_hash"):
        _required_string(goal, key, errors)
    if not isinstance(goal.get("goal_version"), (int, str)) or isinstance(
        goal.get("goal_version"), bool
    ):
        errors.append("goal.goal_version must be an integer or string")
    target = _required_mapping(payload, "target", errors)
    for key in ("repo", "target"):
        _required_string(target, key, errors)
    entry_node = _required_string(payload, "entry_node", errors)
    terminal_nodes = _string_list(payload.get("terminal_nodes"), "terminal_nodes", errors)
    limits = _required_mapping(payload, "limits", errors)
    if _int_value(limits.get("max_total_attempts"), "limits.max_total_attempts", errors) < 1:
        errors.append("limits.max_total_attempts must be at least 1")
    required_evidence = _string_list(
        payload.get("required_evidence"),
        "required_evidence",
        errors,
    )
    fail_closed_on = _string_list(payload.get("fail_closed_on"), "fail_closed_on", errors)
    nodes = _parse_nodes(payload.get("nodes"), errors)
    edges = _parse_edges(payload.get("edges"), errors)
    node_ids = set(nodes)
    if entry_node and entry_node not in node_ids:
        errors.append(f"entry_node is not a declared node: {entry_node}")
    for edge in edges:
        if edge.source not in node_ids:
            errors.append(f"edge.from is not a declared node: {edge.source}")
        if edge.target not in node_ids and edge.target not in terminal_nodes:
            errors.append(f"edge.to is not a declared node or terminal node: {edge.target}")
    if terminal_nodes and not any(edge.target in terminal_nodes for edge in edges):
        errors.append("at least one edge must route to a terminal node")
    agent_to_nodes: dict[str, list[str]] = {}
    for node in nodes.values():
        agent_to_nodes.setdefault(node.agent, []).append(node.node_id)
    ambiguous_agents = {agent: ids for agent, ids in agent_to_nodes.items() if len(ids) > 1}
    if ambiguous_agents:
        errors.append(f"node.agent values must be unique for handoff routing: {ambiguous_agents}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return ProjectDagContract(
        payload=payload,
        dag_id=dag_id,
        goal=goal,
        target=target,
        entry_node=entry_node,
        terminal_nodes=tuple(terminal_nodes),
        nodes=nodes,
        edges=tuple(edges),
        limits=limits,
        required_evidence=tuple(required_evidence),
        fail_closed_on=tuple(fail_closed_on),
    )


def _parse_nodes(value: object, errors: list[str]) -> dict[str, ProjectDagNode]:
    if not isinstance(value, list) or not value:
        errors.append("nodes must be a non-empty list")
        return {}
    nodes: dict[str, ProjectDagNode] = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"nodes[{index}] must be an object")
            continue
        node_id = _required_string(item, "id", errors, prefix=f"nodes[{index}]")
        agent = _required_string(item, "agent", errors, prefix=f"nodes[{index}]")
        executor = str(item.get("executor") or "local")
        max_attempts = int(item.get("max_attempts", 1))
        if max_attempts < 1:
            errors.append(f"nodes[{index}].max_attempts must be at least 1")
        required_evidence = _string_list(
            item.get("required_evidence", []),
            f"nodes[{index}].required_evidence",
            errors,
        )
        command_spec = item.get("command_spec")
        reviewer = item.get("reviewer")
        if reviewer is not None and not isinstance(reviewer, dict):
            errors.append(f"nodes[{index}].reviewer must be an object")
            reviewer = None
        if node_id in nodes:
            errors.append(f"duplicate node id: {node_id}")
            continue
        nodes[node_id] = ProjectDagNode(
            node_id=node_id,
            agent=agent,
            executor=executor,
            max_attempts=max_attempts,
            command_spec=str(command_spec) if isinstance(command_spec, str) else None,
            required_evidence=tuple(required_evidence),
            reviewer=reviewer,
        )
    return nodes


def _parse_edges(value: object, errors: list[str]) -> list[ProjectDagEdge]:
    if not isinstance(value, list) or not value:
        errors.append("edges must be a non-empty list")
        return []
    edges: list[ProjectDagEdge] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"edges[{index}] must be an object")
            continue
        source = _required_string(item, "from", errors, prefix=f"edges[{index}]")
        target = _required_string(item, "to", errors, prefix=f"edges[{index}]")
        condition = item.get("condition")
        key = (source, target)
        if key in seen:
            errors.append(f"duplicate edge: {source}->{target}")
            continue
        seen.add(key)
        edges.append(
            ProjectDagEdge(
                source=source,
                target=target,
                condition=str(condition) if isinstance(condition, str) else None,
            )
        )
    return edges


def _compile_command_specs(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path,
    fallback_root: Path | None,
) -> Path | None:
    nodes_with_specs = [node for node in contract.nodes.values() if node.command_spec]
    if not nodes_with_specs:
        return fallback_root.expanduser().resolve() if fallback_root is not None else None
    compiled_root = receipt_dir / "compiled-command-specs"
    for node in nodes_with_specs:
        source = Path(str(node.command_spec))
        if not source.is_absolute():
            source = contract_path.parent / source
        if source.is_dir():
            source = source / "tau-dispatch-command.json"
        if not source.is_file():
            raise RuntimeError(f"command_spec for node {node.node_id} does not exist: {source}")
        target = compiled_root / node.agent / "tau-dispatch-command.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    if fallback_root is not None:
        for node in contract.nodes.values():
            if node.command_spec:
                continue
            source = fallback_root.expanduser().resolve() / node.agent / "tau-dispatch-command.json"
            if source.is_file():
                target = compiled_root / node.agent / "tau-dispatch-command.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    return compiled_root


def _start_handoff(contract: ProjectDagContract, *, contract_path: Path) -> dict[str, Any]:
    entry = contract.nodes[contract.entry_node]
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": contract.target["repo"],
            "target": contract.target["target"],
        },
        "goal": contract.goal,
        "previous_subagent": "human",
        "context": {
            "summary": f"Dispatch DAG contract {contract.dag_id}.",
            "artifacts": [str(contract_path)],
        },
        "result": {
            "status": "DAG_DISPATCH_REQUESTED",
            "summary": f"Tau is dispatching entry node {contract.entry_node}.",
            "evidence": [
                {
                    "kind": "dag_contract",
                    "schema": DAG_CONTRACT_SCHEMA,
                    "path": str(contract_path),
                    "sha256": f"sha256:{_sha256(contract_path)}",
                }
            ],
        },
        "rationale": "The DAG contract is the authoritative workflow and immutable goal boundary.",
        "next_agent": {
            "name": entry.agent,
            "executor": entry.executor,
            "reason": f"Entry node for DAG {contract.dag_id}.",
        },
        "required_evidence": list(contract.required_evidence),
        "stop_condition": "Stop at a terminal DAG node or any fail-closed invariant violation.",
    }


def _evaluate_loop_against_contract(
    contract: ProjectDagContract,
    loop_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if loop_payload.get("ok") is not True:
        stop_reason = str(loop_payload.get("stop_reason") or "command_loop_blocked")
        alerts.append(
            _alert(
                "BLOCK",
                stop_reason,
                "Underlying handoff command loop did not pass.",
                {"errors": loop_payload.get("errors", [])},
            )
        )
    dispatches = _dispatches(loop_payload)
    if not dispatches:
        alerts.append(_alert("BLOCK", "missing_dispatch", "DAG did not dispatch any node.", {}))
        return alerts

    selected = [str(dispatch.get("selected_agent")) for dispatch in dispatches]
    expected_entry_agent = contract.nodes[contract.entry_node].agent
    if selected[0] != expected_entry_agent:
        alerts.append(
            _alert(
                "BLOCK",
                "entry_node_mismatch",
                "First selected agent does not match DAG entry node.",
                {"expected": expected_entry_agent, "observed": selected[0]},
            )
        )
    for edge in _observed_edges(contract, loop_payload):
        if not _edge_allowed(contract, str(edge["from_node"]), str(edge["to_node"])):
            alerts.append(
                _alert(
                    "BLOCK",
                    "unexpected_edge",
                    "Observed handoff route is not allowed by DAG contract.",
                    edge,
                )
            )
    attempts = _node_attempts(contract, loop_payload)
    for node_id, count in attempts.items():
        max_attempts = contract.nodes[node_id].max_attempts
        if count > max_attempts:
            alerts.append(
                _alert(
                    "BLOCK",
                    "max_attempts_exceeded",
                    "Node exceeded its DAG max_attempts.",
                    {"node_id": node_id, "attempts": count, "max_attempts": max_attempts},
                )
            )
    for dispatch in dispatches:
        node = _node_for_agent(contract, str(dispatch.get("selected_agent")))
        if node is None:
            alerts.append(
                _alert(
                    "BLOCK",
                    "unexpected_node",
                    "Selected agent is not declared in the DAG contract.",
                    {"selected_agent": dispatch.get("selected_agent")},
                )
            )
            continue
        response = _response_payload(dispatch)
        if response is None:
            continue
        missing = _missing_required_evidence(node.required_evidence, response)
        if missing:
            alerts.append(
                _alert(
                    "BLOCK",
                    "missing_required_evidence",
                    "Node response did not include required evidence.",
                    {"node_id": node.node_id, "missing": missing},
                )
            )
        if node.reviewer is not None:
            reviewer_alerts = _reviewer_alerts(contract, node, response)
            alerts.extend(reviewer_alerts)
    return alerts


def _reviewer_alerts(
    contract: ProjectDagContract,
    node: ProjectDagNode,
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    verdicts = _reviewer_verdict_evidence(response)
    if not verdicts:
        return [
            _alert(
                "BLOCK",
                "missing_reviewer_verdict",
                "Reviewer node did not emit reviewer_verdict evidence.",
                {"node_id": node.node_id},
            )
        ]
    alerts: list[dict[str, Any]] = []
    expected_reviewed = node.reviewer.get("reviews_node") if isinstance(node.reviewer, dict) else None
    for verdict in verdicts:
        if verdict.get("goal_hash") != contract.goal["goal_hash"]:
            alerts.append(
                _alert(
                    "BLOCK",
                    "reviewer_goal_hash_mismatch",
                    "Reviewer verdict does not cite the immutable goal hash.",
                    {
                        "node_id": node.node_id,
                        "expected_goal_hash": contract.goal["goal_hash"],
                        "observed_goal_hash": verdict.get("goal_hash"),
                    },
                )
            )
        if expected_reviewed and verdict.get("reviewed_node_id") != expected_reviewed:
            alerts.append(
                _alert(
                    "BLOCK",
                    "reviewer_target_mismatch",
                    "Reviewer verdict did not review the expected creator node.",
                    {
                        "node_id": node.node_id,
                        "expected_reviewed_node": expected_reviewed,
                        "observed_reviewed_node": verdict.get("reviewed_node_id"),
                    },
                )
            )
    return alerts


def _node_response_alerts(
    contract: ProjectDagContract,
    node: ProjectDagNode,
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    missing = _missing_required_evidence(node.required_evidence, response)
    if missing:
        alerts.append(
            _alert(
                "BLOCK",
                "missing_required_evidence",
                "Node response did not include required evidence.",
                {"node_id": node.node_id, "missing": missing},
            )
        )
    if node.reviewer is not None:
        alerts.extend(_reviewer_alerts(contract, node, response))
    return alerts


def _ready_queue_contract_alerts(contract: ProjectDagContract) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if _cycle_detected(contract):
        alerts.append(
            _alert(
                "BLOCK",
                "cycle_detected",
                "Bounded ready-queue scheduler requires an acyclic DAG contract.",
                {},
            )
        )
    mutating_nodes = [
        node.node_id
        for node in contract.nodes.values()
        if bool(contract.payload.get("mutating")) or bool(_node_payload(contract, node.node_id).get("mutates"))
    ]
    if mutating_nodes:
        alerts.append(
            _alert(
                "BLOCK",
                "mutating_node_not_allowed",
                "Bounded ready-queue scheduler only accepts non-mutating local nodes in this slice.",
                {"node_ids": mutating_nodes},
            )
        )
    return alerts


def _node_payload(contract: ProjectDagContract, node_id: str) -> dict[str, Any]:
    raw_nodes = contract.payload.get("nodes")
    if not isinstance(raw_nodes, list):
        return {}
    for item in raw_nodes:
        if isinstance(item, dict) and item.get("id") == node_id:
            return item
    return {}


def _cycle_detected(contract: ProjectDagContract) -> bool:
    graph = _successors(contract, include_terminals=False)
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in permanent:
            return False
        if node_id in temporary:
            return True
        temporary.add(node_id)
        for child in graph.get(node_id, set()):
            if visit(child):
                return True
        temporary.remove(node_id)
        permanent.add(node_id)
        return False

    return any(visit(node_id) for node_id in contract.nodes)


def _predecessors(contract: ProjectDagContract) -> dict[str, set[str]]:
    predecessors: dict[str, set[str]] = {node_id: set() for node_id in contract.nodes}
    for edge in contract.edges:
        if edge.target in predecessors and edge.source in contract.nodes:
            predecessors[edge.target].add(edge.source)
    return predecessors


def _successors(
    contract: ProjectDagContract,
    *,
    include_terminals: bool = True,
) -> dict[str, set[str]]:
    successors: dict[str, set[str]] = {node_id: set() for node_id in contract.nodes}
    for edge in contract.edges:
        if edge.source not in successors:
            continue
        if edge.target in contract.nodes or include_terminals:
            successors[edge.source].add(edge.target)
    return successors


def _node_dependencies_satisfied(
    node_id: str,
    predecessors: dict[str, set[str]],
    completed: set[str],
) -> bool:
    return predecessors.get(node_id, set()).issubset(completed)


def _terminal_reachable_from_completed(
    contract: ProjectDagContract,
    completed: set[str],
    successors: dict[str, set[str]],
) -> bool:
    return any(
        node_id in completed and any(target in contract.terminal_nodes for target in successors[node_id])
        for node_id in completed
    )


def _ready_queue_observed_edges(
    contract: ProjectDagContract,
    completed: set[str],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for edge in contract.edges:
        if edge.source not in completed:
            continue
        if edge.target not in completed and edge.target not in contract.terminal_nodes:
            continue
        source_node = contract.nodes[edge.source]
        target_node = contract.nodes.get(edge.target)
        edges.append(
            {
                "from_node": edge.source,
                "from_agent": source_node.agent,
                "to_node": edge.target,
                "to_agent": target_node.agent if target_node else edge.target,
            }
        )
    return edges


def _node_start_handoff(
    contract: ProjectDagContract,
    node: ProjectDagNode,
    *,
    contract_path: Path,
    predecessor_responses: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence: list[Any] = [
        {
            "kind": "dag_contract",
            "schema": DAG_CONTRACT_SCHEMA,
            "path": str(contract_path),
            "sha256": f"sha256:{_sha256(contract_path)}",
        }
    ]
    artifacts: list[str] = [str(contract_path)]
    for response in predecessor_responses:
        evidence.extend(_result_evidence(response))
        context = response.get("context")
        if isinstance(context, dict):
            artifacts.extend(str(item) for item in context.get("artifacts", []) if isinstance(item, str))
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": contract.target["repo"],
            "target": contract.target["target"],
        },
        "goal": contract.goal,
        "previous_subagent": "human",
        "context": {
            "summary": f"Dispatch ready DAG node {node.node_id}.",
            "artifacts": artifacts,
        },
        "result": {
            "status": "DAG_NODE_READY",
            "summary": f"Dependencies are satisfied for DAG node {node.node_id}.",
            "evidence": evidence,
        },
        "rationale": "The DAG contract is the authoritative workflow and immutable goal boundary.",
        "next_agent": {
            "name": node.agent,
            "executor": node.executor,
            "reason": f"Ready node for DAG {contract.dag_id}.",
        },
        "required_evidence": list(node.required_evidence),
        "stop_condition": "Stop at a terminal DAG node or any fail-closed invariant violation.",
    }


def _dispatch_ready_node(
    *,
    node: ProjectDagNode,
    start_payload: dict[str, Any],
    agents_root: Path,
    command_spec_root: Path | None,
    artifact_dir: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        spec = load_agent_dispatch_command_spec(
            agents_root,
            node.agent,
            command_spec_root=command_spec_root,
        )
        dispatch = dispatch_agent_handoff_command_once(
            start_payload,
            list(spec["command"]),
            timeout_s=float(spec["timeout_s"]),
            cwd=spec.get("cwd") if isinstance(spec.get("cwd"), Path) else None,
            active_goal_hash=str(start_payload["goal"]["goal_hash"]),
            agent_registry_root=agents_root,
            artifact_dir=artifact_dir,
        )
        dispatch_payload = dispatch.as_dict()
        response = _response_payload(dispatch_payload)
        return {
            "ok": dispatch.ok,
            "dispatch": dispatch_payload,
            "response": response,
            "started_monotonic": started,
            "completed_monotonic": time.monotonic(),
            "errors": list(dispatch.errors),
        }
    except Exception as exc:
        return {
            "ok": False,
            "dispatch": None,
            "response": None,
            "started_monotonic": started,
            "completed_monotonic": time.monotonic(),
            "errors": [str(exc)],
        }


def _max_concurrency(contract: ProjectDagContract) -> int:
    raw = contract.limits.get("max_concurrency", 2)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return 2
    return raw


def _max_observed_concurrency(intervals: list[tuple[float, float]]) -> int:
    points: list[tuple[float, int]] = []
    for start, end in intervals:
        points.append((start, 1))
        points.append((end, -1))
    active = 0
    max_active = 0
    for _, delta in sorted(points, key=lambda item: (item[0], -item[1])):
        active += delta
        max_active = max(max_active, active)
    return max_active


def _ready_queue_receipt(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path,
    command_spec_root: Path | None,
    status: str,
    verdict: str,
    alerts: list[dict[str, Any]],
    dispatches: list[dict[str, Any]],
    events: list[dict[str, Any]],
    node_attempts: dict[str, int],
    reviewer_verdicts: list[dict[str, Any]],
    observed_edges: list[dict[str, Any]],
    execution_seconds: float,
    max_observed_concurrency: int,
    errors: list[str],
    node_artifacts: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    return {
        "schema": DAG_RECEIPT_SCHEMA,
        "ok": status == "PASS",
        "status": status,
        "verdict": verdict,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "scheduler": "bounded-ready-queue",
        "execution": "project_agent_dag_bounded_ready_queue",
        "dag_id": contract.dag_id,
        "contract_path": str(contract_path),
        "contract_sha256": f"sha256:{_sha256(contract_path)}",
        "run_dir": str(receipt_dir),
        "active_goal_hash": contract.goal["goal_hash"],
        "target": contract.target,
        "entry_node": contract.entry_node,
        "terminal_nodes": list(contract.terminal_nodes),
        "node_count": len(contract.nodes),
        "edge_count": len(contract.edges),
        "max_steps": _max_steps(contract),
        "max_concurrency": _max_concurrency(contract),
        "max_observed_concurrency": max_observed_concurrency,
        "execution_seconds": execution_seconds,
        "command_spec_root": str(command_spec_root) if command_spec_root else None,
        "selected_agents": [
            dispatch.get("selected_agent")
            for dispatch in dispatches
            if dispatch.get("selected_agent")
        ],
        "observed_edges": observed_edges,
        "node_attempts": node_attempts,
        "reviewer_verdicts": reviewer_verdicts,
        "scheduler_events": events,
        "dispatches": dispatches,
        "alerts": alerts,
        "artifacts": [
            str(path)
            for path in sorted(receipt_dir.rglob("*"))
            if path.is_file() and path.name != "dag-receipt.json"
        ],
        "node_artifacts": node_artifacts or {},
        "proof_scope": {
            "mocked": False,
            "live": True,
            "proves": [
                "DAG contract parsed and validated.",
                "Ready nodes were dispatched by the bounded ready-queue scheduler.",
                "Independent ready nodes can run concurrently when dependencies are satisfied.",
                "Each dispatched node used the real local command subprocess runner.",
                "Node evidence and reviewer verdicts were checked against the immutable goal hash.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "GitHub mutation or ticket closure.",
                "Mutating branch safety.",
                "Unbounded autonomous operation.",
            ],
        },
        "errors": errors,
        "timestamp": _utc_stamp(),
    }


def _observed_edges(contract: ProjectDagContract, loop_payload: dict[str, Any]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for dispatch in _dispatches(loop_payload):
        from_node = _node_for_agent(contract, str(dispatch.get("selected_agent")))
        response_projection = dispatch.get("response_projection")
        if from_node is None or not isinstance(response_projection, dict):
            continue
        to_agent = response_projection.get("next_agent")
        to_node = _node_id_for_agent_or_terminal(contract, str(to_agent))
        edges.append(
            {
                "from_node": from_node.node_id,
                "from_agent": from_node.agent,
                "to_node": to_node,
                "to_agent": to_agent,
            }
        )
    return edges


def _node_attempts(contract: ProjectDagContract, loop_payload: dict[str, Any]) -> dict[str, int]:
    attempts: dict[str, int] = {}
    for dispatch in _dispatches(loop_payload):
        node = _node_for_agent(contract, str(dispatch.get("selected_agent")))
        if node is not None:
            attempts[node.node_id] = attempts.get(node.node_id, 0) + 1
    return attempts


def _reviewer_verdicts(
    contract: ProjectDagContract,
    loop_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    verdicts: list[dict[str, Any]] = []
    for dispatch in _dispatches(loop_payload):
        node = _node_for_agent(contract, str(dispatch.get("selected_agent")))
        if node is None or node.reviewer is None:
            continue
        response = _response_payload(dispatch)
        if response is not None:
            verdicts.extend(_reviewer_verdict_evidence(response))
    return verdicts


def _reviewer_verdict_evidence(response: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = _result_evidence(response)
    return [
        item
        for item in evidence
        if isinstance(item, dict) and item.get("kind") == "reviewer_verdict"
    ]


def _missing_required_evidence(required: tuple[str, ...], response: dict[str, Any]) -> list[str]:
    if not required:
        return []
    haystack = json.dumps(_result_evidence(response), sort_keys=True)
    return [item for item in required if item not in haystack]


def _result_evidence(response: dict[str, Any]) -> list[Any]:
    result = response.get("result")
    if not isinstance(result, dict):
        return []
    evidence = result.get("evidence")
    return evidence if isinstance(evidence, list) else []


def _response_payload(dispatch: dict[str, Any]) -> dict[str, Any] | None:
    command_results = dispatch.get("command_results")
    if not isinstance(command_results, list) or not command_results:
        return None
    first = command_results[0]
    if not isinstance(first, dict) or not isinstance(first.get("stdout"), str):
        return None
    try:
        payload = json.loads(first["stdout"])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _dispatches(loop_payload: dict[str, Any]) -> list[dict[str, Any]]:
    dispatches = loop_payload.get("dispatches")
    if not isinstance(dispatches, list):
        return []
    return [item for item in dispatches if isinstance(item, dict)]


def _edge_allowed(contract: ProjectDagContract, source: str, target: str) -> bool:
    return any(edge.source == source and edge.target == target for edge in contract.edges)


def _node_for_agent(contract: ProjectDagContract, agent: str) -> ProjectDagNode | None:
    for node in contract.nodes.values():
        if node.agent == agent or node.node_id == agent:
            return node
    return None


def _node_id_for_agent_or_terminal(contract: ProjectDagContract, value: str) -> str:
    node = _node_for_agent(contract, value)
    if node is not None:
        return node.node_id
    return value


def _resolve_receipt_dir(
    *,
    contract: ProjectDagContract,
    contract_path: Path,
    receipt_dir: Path | None,
) -> Path:
    if receipt_dir is not None:
        return receipt_dir.expanduser().resolve()
    raw_run_dir = contract.payload.get("run_dir")
    if isinstance(raw_run_dir, str) and raw_run_dir.strip():
        run_dir = Path(raw_run_dir)
        if not run_dir.is_absolute():
            run_dir = contract_path.parent / run_dir
        return run_dir.expanduser().resolve()
    return (contract_path.parent / f"{contract.dag_id}-run").resolve()


def _max_steps(contract: ProjectDagContract) -> int:
    raw = contract.limits.get("max_total_attempts")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return max(sum(node.max_attempts for node in contract.nodes.values()), 1)


def _blocked_verdict(alerts: list[dict[str, Any]], loop_payload: dict[str, Any]) -> str:
    if alerts:
        return str(alerts[0]["code"]).upper()
    return str(loop_payload.get("stop_reason") or "COMMAND_LOOP_BLOCKED").upper()


def _alert(
    severity: str,
    code: str,
    message: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "evidence": evidence,
    }


def _required_mapping(value: dict[str, Any], key: str, errors: list[str]) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        errors.append(f"{key} must be an object")
        return {}
    return item


def _required_string(
    value: dict[str, Any],
    key: str,
    errors: list[str],
    *,
    prefix: str | None = None,
) -> str:
    item = value.get(key)
    label = f"{prefix}.{key}" if prefix else key
    if not isinstance(item, str) or not item.strip():
        errors.append(f"{label} must be a non-empty string")
        return ""
    return item


def _string_list(value: object, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        errors.append(f"{label} must be a string list")
        return []
    return list(value)


def _int_value(value: object, label: str, errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} must be an integer")
        return 0
    return value


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
