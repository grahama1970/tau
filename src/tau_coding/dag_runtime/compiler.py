"""Compile public Tau DAG contracts into the canonical internal DagPlan."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tau_coding.dag_join_decision import normalize_join_policy
from tau_coding.dag_route_decision import normalize_route_condition
from tau_coding.dag_runtime.execution_profile import (
    execution_limits_with_profile,
    resolve_execution_profile,
    source_extensions_with_profile,
)
from tau_coding.dag_runtime.model import (
    DAG_PLAN_SCHEMA,
    DagPlan,
    DagPlanContextBinding,
    DagPlanEdge,
    DagPlanNode,
    DagPlanTerminal,
    FrozenJson,
    canonical_sha256,
    require_valid_dag_plan,
)
from tau_coding.public_dag_contracts import explicit_extensions
from tau_coding.runtime_backends.contracts import RuntimeRequirement

PROJECT_ROOT_KEYS = {
    "schema",
    "dag_id",
    "goal",
    "target",
    "entry_node",
    "terminal_nodes",
    "limits",
    "context",
    "nodes",
    "edges",
    "required_evidence",
    "fail_closed_on",
    "evidence_manifest",
    "command_policy",
    "policy_profile",
    "data_boundary",
    "security_mode",
    "execution_profile",
    "execution_profile_policy",
    "actor_access_manifest",
    "environment_manifest",
    "memory_intent",
    "evidence_case",
    "research_query_safety_receipt",
    "itar_access_preflight_receipt",
    "sandbox_run_receipt",
    "compliance_package_validation_receipt",
}
PROJECT_NODE_KEYS = {
    "id",
    "agent",
    "executor",
    "max_attempts",
    "timeout_seconds",
    "command_spec",
    "required_evidence",
    "reviewer",
    "context",
    "requested_capabilities",
    "route",
    "join",
    "provider",
    "model_policy",
    "prompt_contract",
    "persistent_subagent",
    "runtime_backend",
}
GENERIC_ROOT_KEYS = {
    "schema",
    "run_id",
    "run_dir",
    "events_jsonl",
    "goal",
    "goal_hash",
    "max_concurrency",
    "execution_profile",
    "execution_profile_policy",
    "limits",
    "nodes",
}
GENERIC_NODE_KEYS = {
    "node_id",
    "role",
    "command",
    "depends_on",
    "accepted_context_from",
    "receipt_path",
    "timeout_seconds",
    "max_attempts",
    "work_order_path",
    "transaction",
    "skill",
}


def compile_project_dag_plan(
    payload: dict[str, Any],
    *,
    source_path: Path | None = None,
    source_payload_sha256: str | None = None,
) -> DagPlan:
    """Validate and compile ``tau.dag_contract.v1`` without dispatching it."""

    from tau_coding.project_dag import (
        validate_dag_contract,
        validate_project_dag_plan_semantics,
    )

    contract = validate_dag_contract(payload)
    validate_project_dag_plan_semantics(contract)
    source_dir = _source_dir(source_path)
    raw_nodes = _indexed_nodes(payload, id_key="id")
    incoming_edges = _incoming_edges(contract.edges, node_ids=set(contract.nodes))

    nodes: list[DagPlanNode] = []
    for node_id in sorted(contract.nodes):
        node = contract.nodes[node_id]
        raw = raw_nodes[node_id]
        adapter_kind, runtime_requirement = compile_project_node_runtime_requirement(
            raw, executor=node.executor
        )
        explicit_timeout = raw.get("timeout_seconds")
        source_default_timeout = contract.limits.get("default_timeout_seconds")
        timeout_value = explicit_timeout if explicit_timeout is not None else source_default_timeout
        timeout_kind = (
            "explicit"
            if explicit_timeout is not None
            else "source_default"
            if source_default_timeout is not None
            else "adapter_defined"
        )
        nodes.append(
            DagPlanNode(
                node_id=node_id,
                role=node.agent,
                executor=node.executor,
                adapter_kind=adapter_kind,
                adapter_config=FrozenJson.from_value(_project_adapter_config(raw)),
                max_attempts=node.max_attempts,
                timeout_kind=timeout_kind,
                timeout_seconds=_optional_positive_float(
                    timeout_value, label=f"node {node_id} timeout_seconds"
                ),
                required_evidence=tuple(node.required_evidence),
                static_context=FrozenJson.from_value(
                    {
                        "merge_policy": "project_handoff_context_v1",
                        "contract": contract.context,
                        "node": node.context,
                    }
                ),
                requested_capabilities=tuple(
                    FrozenJson.from_value(item) for item in node.requested_capabilities
                ),
                source_bindings=tuple(
                    FrozenJson.from_value(item)
                    for item in _project_source_bindings(
                        node_id=node_id,
                        raw=raw,
                        source_dir=source_dir,
                    )
                ),
                source_extensions=FrozenJson.from_value(_extensions(raw, PROJECT_NODE_KEYS)),
                runtime_requirement=FrozenJson.from_value(runtime_requirement.to_payload()),
            )
        )

    control_edges = tuple(
        DagPlanEdge(
            edge_id=(f"project-edge:{edge.edge_index}:{edge.source}:{edge.target}"),
            source_node_id=edge.source,
            target_id=edge.target,
            target_kind="node" if edge.target in contract.nodes else "terminal",
            condition=(
                FrozenJson.from_value(normalize_route_condition(edge.condition))
                if edge.condition not in (None, "")
                else None
            ),
            source_ordinal=edge.edge_index,
        )
        for edge in sorted(contract.edges, key=lambda item: item.edge_index)
    )
    edge_by_pair = {(edge.source_node_id, edge.target_id): edge for edge in control_edges}
    context_bindings = tuple(
        DagPlanContextBinding(
            binding_id=f"project-context:{edge.source}:{edge.target}",
            source_node_id=edge.source,
            target_node_id=edge.target,
            control_edge_id=edge_by_pair[(edge.source, edge.target)].edge_id,
            projection="activated_predecessor_evidence_and_artifacts",
            activation="after_route_activation",
            origin="project_handoff_default",
            accepted_source_schemas=("*",),
            selector_kind="accepted_output",
            materialization_mode="by_value",
            on_missing="omit",
            on_invalid="omit",
        )
        for edge in contract.edges
        if edge.target in contract.nodes
    )
    route_contracts = _project_route_contracts(contract, control_edges)
    join_contracts = _project_join_contracts(
        contract=contract,
        raw_nodes=raw_nodes,
        incoming_edges=incoming_edges,
        control_edges=control_edges,
    )
    profile_resolution = resolve_execution_profile(
        payload=payload,
        source_family="project_dag",
        source_schema=str(payload["schema"]),
        source_limits=contract.limits,
        source_required_evidence=tuple(contract.required_evidence),
        source_fail_closed_on=tuple(contract.fail_closed_on),
        node_count=len(nodes),
        edge_count=len(control_edges),
        max_concurrency=_optional_positive_int(contract.limits.get("max_concurrency")),
    )
    plan = DagPlan(
        schema=DAG_PLAN_SCHEMA,
        plan_id=f"project:{contract.dag_id}",
        source_family="project_dag",
        source_schema=str(payload["schema"]),
        source_logical_id=contract.dag_id,
        source_payload_sha256=source_payload_sha256 or canonical_sha256(payload),
        goal_binding=FrozenJson.from_value({"kind": "full", **contract.goal}),
        target_binding=FrozenJson.from_value(contract.target),
        entry_node_ids=_project_entry_node_ids(
            declared_entry=contract.entry_node,
            node_ids=tuple(sorted(contract.nodes)),
            incoming_edges=incoming_edges,
        ),
        terminal_endpoints=tuple(
            DagPlanTerminal(
                terminal_id=terminal,
                kind="declared_node" if terminal in contract.nodes else "external",
                origin="declared",
            )
            for terminal in contract.terminal_nodes
        ),
        completion_policy="declared_terminal_settlement",
        nodes=tuple(nodes),
        control_edges=control_edges,
        context_bindings=context_bindings,
        runtime_bindings=(),
        route_contracts=tuple(FrozenJson.from_value(item) for item in route_contracts),
        join_contracts=tuple(FrozenJson.from_value(item) for item in join_contracts),
        required_evidence=tuple(contract.required_evidence),
        fail_closed_on=tuple(contract.fail_closed_on),
        security_declarations=FrozenJson.from_value(
            _project_security_declarations(contract, source_dir=source_dir)
        ),
        execution_limits=FrozenJson.from_value(
            execution_limits_with_profile(contract.limits, profile_resolution)
        ),
        source_extensions=FrozenJson.from_value(
            source_extensions_with_profile(
                _extensions(payload, PROJECT_ROOT_KEYS),
                profile_resolution,
            )
        ),
    ).with_computed_hash()
    require_valid_dag_plan(plan)
    return plan


def compile_generic_dag_plan(payload: dict[str, Any], *, source_path: Path) -> DagPlan:
    """Validate and compile ``tau.generic_dag_spec.v1`` without dispatching it."""

    from tau_coding.generic_dag import validate_generic_dag_spec

    resolved_path = source_path.expanduser().resolve()
    source_dir = resolved_path.parent
    typed_nodes = validate_generic_dag_spec(payload, source_path=resolved_path)
    raw_nodes = _indexed_nodes(payload, id_key="node_id")
    edges = [
        (dependency, node.node_id)
        for node in typed_nodes.values()
        for dependency in node.depends_on
    ]
    _require_unique_edges(edges)
    outgoing: dict[str, set[str]] = {node_id: set() for node_id in typed_nodes}
    for source, target in edges:
        outgoing[source].add(target)

    control_edges = tuple(
        DagPlanEdge(
            edge_id=f"generic-dependency:{source}:{target}",
            source_node_id=source,
            target_id=target,
            target_kind="node",
            condition=None,
            source_ordinal=None,
        )
        for source, target in sorted(edges, key=lambda item: (item[1], item[0]))
    )
    edge_by_pair = {(edge.source_node_id, edge.target_id): edge for edge in control_edges}
    context_bindings = tuple(
        DagPlanContextBinding(
            binding_id=f"generic-context:{source}:{target}",
            source_node_id=source,
            target_node_id=target,
            control_edge_id=edge_by_pair[(source, target)].edge_id,
            projection="accepted_output_if_present",
            activation="after_source_pass",
            origin=(
                "explicit"
                if "accepted_context_from" in raw_nodes[target]
                else "default_all_dependencies"
            ),
            accepted_source_schemas=("*",),
            selector_kind="accepted_output",
            materialization_mode="by_value",
            on_missing="omit",
            on_invalid="omit",
        )
        for target in sorted(typed_nodes)
        for source in typed_nodes[target].accepted_context_from
    )
    nodes = tuple(
        DagPlanNode(
            node_id=node_id,
            role=typed_nodes[node_id].role,
            executor="local",
            adapter_kind=_generic_adapter_kind(raw_nodes[node_id]),
            adapter_config=FrozenJson.from_value(
                _generic_adapter_config(raw_nodes[node_id], source_dir=source_dir)
            ),
            max_attempts=(
                1
                if typed_nodes[node_id].transaction is not None
                else typed_nodes[node_id].max_attempts
            ),
            timeout_kind="explicit",
            timeout_seconds=typed_nodes[node_id].timeout_seconds,
            required_evidence=("tau.generic_dag_node_receipt.v1",),
            static_context=FrozenJson.from_value({}),
            requested_capabilities=(),
            source_bindings=tuple(
                FrozenJson.from_value(item)
                for item in _generic_source_bindings(
                    node_id=node_id,
                    raw=raw_nodes[node_id],
                    source_dir=source_dir,
                    run_dir=str(payload["run_dir"]),
                )
            ),
            source_extensions=FrozenJson.from_value(
                _extensions(raw_nodes[node_id], GENERIC_NODE_KEYS)
            ),
            runtime_requirement=FrozenJson.from_value(
                _generic_runtime_requirement(raw_nodes[node_id]).to_payload()
            ),
        )
        for node_id in sorted(typed_nodes)
    )
    goal = payload.get("goal")
    goal_hash = payload.get("goal_hash")
    full_goal_keys = {
        "goal_id",
        "goal_version",
        "goal_hash",
        "summary",
        "completion_criteria",
    }
    if isinstance(goal, Mapping) and any(key in goal for key in full_goal_keys):
        goal_binding = {"kind": "full", **dict(goal)}
    elif isinstance(goal_hash, str) and goal_hash:
        goal_binding = {"kind": "hash_only", "goal_hash": goal_hash}
    else:
        goal_binding = {"kind": "none"}
    raw_source_limits = payload.get("limits")
    source_limits: Mapping[str, Any] = (
        raw_source_limits if isinstance(raw_source_limits, Mapping) else {}
    )
    profile_resolution = resolve_execution_profile(
        payload=payload,
        source_family="generic_dag",
        source_schema=str(payload["schema"]),
        source_limits=source_limits,
        source_required_evidence=("tau.generic_dag_node_receipt.v1",),
        source_fail_closed_on=(),
        node_count=len(nodes),
        edge_count=len(control_edges),
        max_concurrency=_optional_positive_int(payload.get("max_concurrency")),
    )
    plan = DagPlan(
        schema=DAG_PLAN_SCHEMA,
        plan_id=f"generic:{payload['run_id']}",
        source_family="generic_dag",
        source_schema=str(payload["schema"]),
        source_logical_id=str(payload["run_id"]),
        source_payload_sha256=canonical_sha256(payload),
        goal_binding=FrozenJson.from_value(goal_binding),
        target_binding=FrozenJson.from_value({}),
        entry_node_ids=tuple(
            sorted(node_id for node_id, node in typed_nodes.items() if not node.depends_on)
        ),
        terminal_endpoints=tuple(
            DagPlanTerminal(node_id, "derived_leaf", "derived")
            for node_id in sorted(node_id for node_id in typed_nodes if not outgoing[node_id])
        ),
        completion_policy="all_nodes_pass_fail_fast",
        nodes=nodes,
        control_edges=control_edges,
        context_bindings=context_bindings,
        runtime_bindings=(FrozenJson.from_value(_generic_events_binding(payload)),),
        route_contracts=(),
        join_contracts=(),
        required_evidence=("tau.generic_dag_node_receipt.v1",),
        fail_closed_on=(),
        security_declarations=FrozenJson.from_value({"security_mode": None, "declarations": []}),
        execution_limits=FrozenJson.from_value(
            execution_limits_with_profile(source_limits, profile_resolution)
        ),
        source_extensions=FrozenJson.from_value(
            source_extensions_with_profile(
                _extensions(payload, GENERIC_ROOT_KEYS),
                profile_resolution,
            )
        ),
    ).with_computed_hash()
    require_valid_dag_plan(plan)
    return plan


def compile_dag_plan_file(path: Path) -> DagPlan:
    """Compile either supported public DAG contract from a local file."""

    from tau_coding.project_dag import load_dag_contract_payload

    resolved = path.expanduser().resolve()
    payload = load_dag_contract_payload(resolved)
    schema = payload.get("schema")
    if schema == "tau.dag_contract.v1":
        return compile_project_dag_plan(payload, source_path=resolved)
    if schema == "tau.generic_dag_spec.v1":
        return compile_generic_dag_plan(payload, source_path=resolved)
    raise RuntimeError(
        "DAG plan compiler supports only tau.dag_contract.v1 and tau.generic_dag_spec.v1"
    )


def write_dag_plan(path: Path, *, output_path: Path) -> dict[str, Any]:
    """Compile and write one deterministic canonical plan artifact."""

    resolved_source = path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    if resolved_output == resolved_source:
        raise RuntimeError("DAG plan output must not overwrite the source contract")
    plan = compile_dag_plan_file(resolved_source)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    payload = plan.to_payload()
    profile_resolution = payload["source_extensions"].get("execution_profile_resolution", {})
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "schema": "tau.dag_plan_compile_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "source_path": str(resolved_source),
        "source_schema": plan.source_schema,
        "source_payload_sha256": plan.source_payload_sha256,
        "plan_path": str(resolved_output),
        "plan_sha256": plan.plan_sha256,
        "node_count": len(plan.nodes),
        "edge_count": len(plan.control_edges),
        "execution_profile": {
            "profile_id": profile_resolution.get("profile_id"),
            "resolution_sha256": profile_resolution.get("resolution_sha256"),
            "compatibility_default": profile_resolution.get("compatibility_default"),
            "resolved_controls_sha256": profile_resolution.get("resolved_controls_sha256"),
        },
        "proof_scope": {
            "proves": [
                "Tau validated a supported public DAG contract and compiled it into DagPlan.",
                "The exported plan is bound to deterministic source and plan payload hashes.",
                "No DAG node or provider was dispatched by plan compilation.",
            ],
            "does_not_prove": [
                "The DAG will execute successfully.",
                "Worker or provider semantic quality.",
                "Scheduler convergence or durable restart behavior.",
            ],
        },
    }


def _project_route_contracts(contract: Any, edges: tuple[DagPlanEdge, ...]) -> list[dict[str, Any]]:
    by_source: dict[str, list[DagPlanEdge]] = {}
    for edge in edges:
        if edge.condition is not None:
            by_source.setdefault(edge.source_node_id, []).append(edge)
    contracts = []
    for source, source_edges in by_source.items():
        mode = contract.nodes[source].route_mode
        payload = {
            "source_node_id": source,
            "mode": mode,
            "ordered_edge_ids": [edge.edge_id for edge in source_edges],
        }
        payload["contract_sha256"] = canonical_sha256(payload)
        contracts.append(payload)
    return contracts


def _project_join_contracts(
    *,
    contract: Any,
    raw_nodes: Mapping[str, Mapping[str, Any]],
    incoming_edges: Mapping[str, tuple[Any, ...]],
    control_edges: tuple[DagPlanEdge, ...],
) -> list[dict[str, Any]]:
    edge_ids = {
        (edge.source_node_id, edge.target_id, edge.source_ordinal): edge.edge_id
        for edge in control_edges
    }
    contracts = []
    for node_id in sorted(contract.nodes):
        join = raw_nodes[node_id].get("join")
        if join is None:
            continue
        incoming = incoming_edges.get(node_id, ())
        policy = normalize_join_policy(join, incoming_count=len(incoming))
        payload = {
            "join_node_id": node_id,
            "incoming_edge_ids": [
                edge_ids[(edge.source, edge.target, edge.edge_index)] for edge in incoming
            ],
            "policy": policy,
            "policy_sha256": canonical_sha256(policy),
        }
        contracts.append(payload)
    return contracts


def _project_adapter_kind(raw: Mapping[str, Any], *, executor: str) -> str:
    if raw.get("agent") == "releaser" or raw.get("id") == "releaser":
        return "project_releaser"
    if raw.get("join") is not None:
        return "project_virtual"
    if (
        raw.get("command_spec") is None
        and isinstance(raw.get("persistent_subagent"), Mapping)
        and executor != "human"
        and raw.get("agent") != "human"
    ):
        return "project_persistent_declaration"
    if executor == "provider" or isinstance(raw.get("provider"), Mapping):
        return (
            "project_provider_handoff_command"
            if raw.get("command_spec") is not None
            else "project_provider"
        )
    if raw.get("command_spec") is not None:
        return "project_handoff_command"
    if executor == "human" or raw.get("agent") == "human":
        return "project_human"
    return "project_virtual"


def _project_adapter_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: raw[key]
        for key in (
            "provider",
            "model_policy",
            "prompt_contract",
            "reviewer",
            "persistent_subagent",
        )
        if key in raw
    }


def _generic_adapter_kind(raw: Mapping[str, Any]) -> str:
    if raw.get("tau_agent") is not None:
        return "tau_native_agent_loop"
    if raw.get("skill") is not None:
        return "generic_skill"
    if raw.get("transaction") is not None:
        return "generic_artifact_transaction"
    return "generic_command"


def _generic_adapter_config(raw: Mapping[str, Any], *, source_dir: Path) -> dict[str, Any]:
    if raw.get("tau_agent") is not None:
        config = _portable_config(raw["tau_agent"], source_dir=source_dir)
        if not isinstance(config, dict):
            raise RuntimeError("tau_agent adapter config must be an object")
        return config
    if raw.get("skill") is not None:
        config = _portable_config(raw["skill"], source_dir=source_dir)
        if not isinstance(config, dict):
            raise RuntimeError("generic skill adapter config must be an object")
        return config
    if raw.get("transaction") is not None:
        return {
            "producer_command": _portable_config(raw.get("command", []), source_dir=source_dir),
            "transaction": _portable_config(raw["transaction"], source_dir=source_dir),
            "transaction_max_attempts": int(raw.get("max_attempts", 1)),
        }
    return {"argv": list(raw.get("command", []))}


def _generic_runtime_requirement(raw: Mapping[str, Any]) -> RuntimeRequirement:
    declared = raw.get("runtime_requirement")
    if declared is not None:
        if not isinstance(declared, Mapping):
            raise RuntimeError("runtime_requirement must be an object when provided")
        return RuntimeRequirement.from_payload(dict(declared))
    backend = raw.get("runtime_backend")
    if backend is not None and (not isinstance(backend, str) or not backend.strip()):
        raise RuntimeError("runtime_backend must be a non-empty string when provided")
    if backend == "herdr" and raw.get("tau_agent") is not None:
        return RuntimeRequirement(
            backend="herdr",
            interaction_mode="interactive",
            required_capabilities=(
                "interactive",
                "stable_endpoint_id",
                "human_attach",
                "native_agent_state",
                "foreground_process_state",
                "supports_working_directory",
                "supports_owned_inventory",
                "supports_terminate",
            ),
            session_scope="node_attempt",
            observation_requirements=("PROCESS",),
        )
    return RuntimeRequirement(
        backend="local" if backend is None else backend,
        interaction_mode="one_shot",
        required_capabilities=("one_shot", "supports_working_directory"),
        session_scope="node_attempt",
        observation_requirements=("PROCESS",),
    )


def _project_runtime_requirement(
    raw: Mapping[str, Any], *, executor: str, adapter_kind: str
) -> RuntimeRequirement:
    declared_backend = raw.get("runtime_backend")
    if declared_backend is not None and (
        not isinstance(declared_backend, str) or not declared_backend.strip()
    ):
        raise RuntimeError("runtime_backend must be a non-empty string when provided")
    persistent = raw.get("persistent_subagent")
    if isinstance(persistent, Mapping) and adapter_kind in {
        "project_virtual",
        "project_human",
    }:
        raise RuntimeError("persistent_subagent_requires_executable_node")
    if isinstance(declared_backend, str):
        backend = declared_backend
    elif adapter_kind == "project_persistent_declaration" or adapter_kind in {
        "project_provider",
        "project_provider_handoff_command",
    }:
        backend = "herdr"
    elif executor == "local":
        backend = "local"
    else:
        backend = ""
    if isinstance(persistent, Mapping) and adapter_kind == "project_persistent_declaration":
        if not backend:
            raise RuntimeError(f"runtime_backend_required_for_executor:{executor}")
        return RuntimeRequirement(
            backend=backend,
            interaction_mode="interactive",
            required_capabilities=(
                "interactive",
                "stable_endpoint_id",
                "supports_owned_inventory",
                "supports_terminate",
            ),
            session_scope="persistent_subagent",
            observation_requirements=("PROCESS",),
        )
    if adapter_kind in {"project_virtual", "project_human"}:
        return RuntimeRequirement(
            backend="none",
            interaction_mode="none",
            required_capabilities=(),
            session_scope="dag_control",
            observation_requirements=(),
        )
    if adapter_kind == "project_releaser":
        return RuntimeRequirement(
            backend=backend or "local",
            interaction_mode="one_shot",
            required_capabilities=("one_shot", "supports_working_directory"),
            session_scope="ticket_repair_release",
            observation_requirements=("PROCESS",),
        )
    if not backend:
        raise RuntimeError(f"runtime_backend_required_for_executor:{executor}")
    return RuntimeRequirement(
        backend=backend,
        interaction_mode="one_shot",
        required_capabilities=("one_shot", "supports_working_directory"),
        session_scope="node_attempt",
        observation_requirements=("PROCESS",),
    )


def compile_project_node_runtime_requirement(
    raw: Mapping[str, Any], *, executor: str
) -> tuple[str, RuntimeRequirement]:
    """Compile one project node's adapter and runtime contract without graph checks."""

    adapter_kind = _project_adapter_kind(raw, executor=executor)
    return adapter_kind, _project_runtime_requirement(
        raw,
        executor=executor,
        adapter_kind=adapter_kind,
    )


def _project_source_bindings(
    *, node_id: str, raw: Mapping[str, Any], source_dir: Path
) -> list[dict[str, Any]]:
    bindings = []
    if isinstance(raw.get("command_spec"), str):
        try:
            binding = _file_binding(
                binding_id=f"node:{node_id}:command-spec",
                kind="input_file",
                declared_path=str(raw["command_spec"]),
                source_dir=source_dir,
                require_exists=True,
                directory_default="tau-dispatch-command.json",
            )
        except RuntimeError as exc:
            raise RuntimeError(f"command_spec for node {node_id} does not exist: {exc}") from exc
        bindings.append(binding)
    return bindings


def _generic_source_bindings(
    *, node_id: str, raw: Mapping[str, Any], source_dir: Path, run_dir: str
) -> list[dict[str, Any]]:
    bindings = [
        _working_directory_binding(
            binding_id=f"node:{node_id}:working-directory",
            declared_path=run_dir,
        ),
        _file_binding(
            binding_id=f"node:{node_id}:receipt",
            kind="output_path",
            declared_path=str(raw["receipt_path"]),
            source_dir=source_dir,
            require_exists=False,
        ),
    ]
    if isinstance(raw.get("work_order_path"), str):
        bindings.append(
            _file_binding(
                binding_id=f"node:{node_id}:work-order",
                kind="input_file",
                declared_path=str(raw["work_order_path"]),
                source_dir=source_dir,
                require_exists=True,
            )
        )
    skill = raw.get("skill")
    if isinstance(skill, Mapping) and isinstance(skill.get("output_dir"), str):
        bindings.append(
            _file_binding(
                binding_id=f"node:{node_id}:skill-output-directory",
                kind="output_directory",
                declared_path=str(skill["output_dir"]),
                source_dir=source_dir,
                require_exists=False,
            )
        )
    return bindings


def _project_security_declarations(contract: Any, *, source_dir: Path) -> dict[str, Any]:
    declarations = []
    for field in (
        "evidence_manifest",
        "command_policy",
        "policy_profile",
        "data_boundary",
        "actor_access_manifest",
        "environment_manifest",
        "memory_intent",
        "evidence_case",
        "research_query_safety_receipt",
        "itar_access_preflight_receipt",
        "sandbox_run_receipt",
        "compliance_package_validation_receipt",
    ):
        value = getattr(contract, field)
        if value is None:
            continue
        if isinstance(value, str):
            declarations.append(
                _file_binding(
                    binding_id=f"project:{field.replace('_', '-')}",
                    kind=(
                        "input_file"
                        if field
                        in {
                            "evidence_manifest",
                            "command_policy",
                            "policy_profile",
                            "data_boundary",
                            "actor_access_manifest",
                            "environment_manifest",
                            "memory_intent",
                            "evidence_case",
                        }
                        else "deferred_input"
                    ),
                    declared_path=value,
                    source_dir=source_dir,
                    require_exists=field
                    in {
                        "evidence_manifest",
                        "command_policy",
                        "policy_profile",
                        "data_boundary",
                        "actor_access_manifest",
                        "environment_manifest",
                        "memory_intent",
                        "evidence_case",
                    },
                )
            )
        else:
            inline = {
                "binding_id": f"project:{field.replace('_', '-')}",
                "kind": "inline_document",
                "value": value,
            }
            inline["content_sha256"] = canonical_sha256(value)
            declarations.append(inline)
    return {"security_mode": contract.security_mode, "declarations": declarations}


def _file_binding(
    *,
    binding_id: str,
    kind: str,
    declared_path: str,
    source_dir: Path,
    require_exists: bool,
    directory_default: str | None = None,
) -> dict[str, Any]:
    raw_path = Path(declared_path).expanduser()
    is_absolute = raw_path.is_absolute()
    resolved = raw_path if is_absolute else source_dir / raw_path
    resolved = resolved.resolve()
    content_path = resolved
    if content_path.is_dir() and directory_default is not None:
        content_path = content_path / directory_default
    if require_exists and not content_path.is_file():
        raise RuntimeError(f"required DAG plan input does not exist: {content_path}")
    portable_path = str(resolved) if is_absolute else os.path.relpath(resolved, source_dir)
    binding: dict[str, Any] = {
        "binding_id": binding_id,
        "kind": kind,
        "declared_path": portable_path,
        "anchor": "filesystem_root" if is_absolute else "source_document_directory",
        "portable": not is_absolute,
    }
    if kind == "input_file" and content_path.is_file():
        binding["content_sha256"] = _file_sha256(content_path)
    return binding


def _working_directory_binding(*, binding_id: str, declared_path: str) -> dict[str, Any]:
    path = Path(declared_path).expanduser()
    return {
        "binding_id": binding_id,
        "kind": "working_directory",
        "declared_path": str(path),
        "anchor": "filesystem_root" if path.is_absolute() else "process_invocation_directory",
        "portable": not path.is_absolute(),
    }


def _generic_events_binding(payload: Mapping[str, Any]) -> dict[str, Any]:
    explicit = payload.get("events_jsonl")
    if isinstance(explicit, str) and explicit:
        path = Path(explicit).expanduser()
        return {
            "binding_id": "generic:events-jsonl",
            "kind": "event_log",
            "declared_path": str(path),
            "anchor": "filesystem_root" if path.is_absolute() else "generic_run_directory",
            "portable": not path.is_absolute(),
            "origin": "explicit",
        }
    return {
        "binding_id": "generic:events-jsonl",
        "kind": "event_log",
        "declared_path": "events.jsonl",
        "anchor": "generic_run_directory",
        "portable": True,
        "origin": "derived_default",
    }


def _portable_config(value: Any, *, source_dir: Path, key: str = "") -> Any:
    if isinstance(value, Mapping):
        return {
            str(child_key): _portable_config(child_value, source_dir=source_dir, key=str(child_key))
            for child_key, child_value in sorted(value.items())
        }
    if isinstance(value, list):
        return [_portable_config(item, source_dir=source_dir, key=key) for item in value]
    if isinstance(value, str) and (
        key.endswith("_path") or key.endswith("_root") or key == "output_dir"
    ):
        path = Path(value).expanduser()
        resolved = path if path.is_absolute() else source_dir / path
        return os.path.relpath(resolved.resolve(), source_dir)
    return value



def _project_entry_node_ids(
    *,
    declared_entry: str,
    node_ids: tuple[str, ...],
    incoming_edges: Mapping[str, tuple[Any, ...]],
) -> tuple[str, ...]:
    """Return every root of a project DAG, not only the declared entry node.

    A DAG's entry set is its in-degree-zero set. A concurrent fan-out — the shape
    a roundtable *is* — has one root per participant, but `tau.dag_contract.v1`
    can express only a single scalar `entry_node`. Seeding plan reachability from
    that scalar alone made every other root unreachable, so
    `validate_dag_plan` rejected the plan pre-dispatch with `node_unreachable`
    and no participant was ever contacted.

    The generic_dag path already derives entries this way (all nodes with no
    `depends_on`); this brings the project_dag path into agreement.

    The declared entry is always retained and always first, because
    `_source_to_plan_diff` asserts `entry_preserved` by checking that
    `contract.entry_node` appears in `plan.entry_node_ids`.
    """

    roots = [
        node_id
        for node_id in node_ids
        if not incoming_edges.get(node_id, ()) and node_id != declared_entry
    ]
    return (declared_entry, *roots)


def _incoming_edges(edges: tuple[Any, ...], *, node_ids: set[str]) -> dict[str, tuple[Any, ...]]:
    incoming: dict[str, list[Any]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.target in incoming:
            incoming[edge.target].append(edge)
    return {key: tuple(value) for key, value in incoming.items()}


def _require_unique_edges(edges: list[tuple[str, str]]) -> None:
    if len(edges) != len(set(edges)):
        raise RuntimeError("DAG contains duplicate dependency edges")


def _indexed_nodes(payload: Mapping[str, Any], *, id_key: str) -> dict[str, dict[str, Any]]:
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raise RuntimeError("DAG nodes must be a list")
    return {
        str(item[id_key]): item
        for item in raw_nodes
        if isinstance(item, dict) and isinstance(item.get(id_key), str)
    }


def _extensions(payload: Mapping[str, Any], known: set[str]) -> dict[str, Any]:
    del known
    return explicit_extensions(payload, promoted_keys=frozenset({"workflow"}))


def _source_dir(source_path: Path | None) -> Path:
    return source_path.expanduser().resolve().parent if source_path else Path.cwd().resolve()


def _optional_positive_float(value: object, *, label: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        raise RuntimeError(f"{label} must be numeric")
    number = float(value)
    if number <= 0:
        raise RuntimeError(f"{label} must be positive")
    return number


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise RuntimeError("execution profile numeric source value must be a positive integer")
    return value


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
