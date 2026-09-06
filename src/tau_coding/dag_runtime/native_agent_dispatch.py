"""CLI/scheduler dispatch for ``tau_agent`` nodes (tau#340).

``tau run`` compiles a ``tau_agent`` node to adapter kind
``tau_native_agent_loop`` (tau#310) but, before this module, ``generic_dag``
fell into the legacy command runner with an empty command. This module is the
missing join: it preflights the native node (profile, tools, paths, shape,
cancellation) *before* any provider or tool executes, freezes the SciLLM
transport-profile selection (tau#308), and hands the node to
``execute_tau_agent_node`` with a ``ScillmTransportProvider`` and Tau's own
read-only ``AgentTool`` implementations.

Nothing here talks to a model directly; SciLLM is the only transport.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.agent_node_adapter import execute_tau_agent_node
from tau_coding.dag_runtime.agent_requirement import (
    AgentRequirementError,
    select_transport_profile,
    validate_agent_requirement,
    validate_selection_receipt,
)
from tau_coding.dag_runtime.model import DagPlanNode, FrozenJson, canonical_sha256

SCILLM_BASE_URL_ENV = "SCILLM_BASE_URL"
DEFAULT_SCILLM_BASE_URL = "http://localhost:4001"
CALLER_SKILL = "tau"

# Read-only native tools a CLI-dispatched reviewer may request. Write/edit/bash
# are deliberately absent: tau#340 requires a tool-capable *reviewer*, and a
# writing worker needs the worktree lease path, which is not this ticket.
READ_ONLY_TOOLS = ("read", "ls", "grep", "find")


class NativeNodePreflightError(RuntimeError):
    def __init__(self, verdict: str, detail: str) -> None:
        super().__init__(f"{verdict}: {detail}")
        self.verdict = verdict
        self.detail = detail


# ----------------------------------------------------------------- transport


def scillm_base_url() -> str:
    return (
        os.environ.get(SCILLM_BASE_URL_ENV)
        or os.environ.get("SCILLM_API_BASE")
        or DEFAULT_SCILLM_BASE_URL
    )


def scillm_api_key() -> str:
    from tau_coding.battle_scillm import _resolve_api_key

    key, _source, _errors = _resolve_api_key()
    return key


def _string_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _target_profile_ids(
    requirement: Mapping[str, Any] | None, profiles: list[Mapping[str, Any]]
) -> list[str]:
    """Profiles that need authoritative live readiness for this node.

    SciLLM's all-profile live-readiness endpoint probes every provider profile
    sequentially. A slow or rate-limited unrelated provider can then block a Tau
    node that only needs one profile. Tau still requires live readiness; it just
    asks SciLLM for the node's preference/fallback chain instead of the whole
    catalog.
    """
    if requirement is None:
        return [str(item.get("id")) for item in profiles if item.get("id")]
    normalized = validate_agent_requirement(requirement)
    by_id = {str(item.get("id")): item for item in profiles if item.get("id")}
    targets: list[str] = []
    for preference in normalized["profile_preferences"]:
        if preference not in targets:
            targets.append(preference)
        if not normalized["fallback_policy"]["allowed"]:
            continue
        for fallback_id in _string_list(by_id.get(preference, {}).get("fallbacks")):
            if fallback_id not in targets:
                targets.append(fallback_id)
    return targets


def discover_transport_profiles(requirement: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Live scillm#27 discovery: profiles plus scoped live readiness states."""
    import httpx

    headers = {"Authorization": f"Bearer {scillm_api_key()}", "X-Caller-Skill": CALLER_SKILL}
    base = scillm_base_url()
    timeout = httpx.Timeout(90.0, connect=10.0)
    with httpx.Client(timeout=timeout) as client:
        profiles_response = client.get(f"{base}/v1/scillm/profiles", headers=headers)
        profiles_response.raise_for_status()
        profiles = profiles_response.json()["profiles"]
        readiness: dict[str, str] = {}
        for profile_id in _target_profile_ids(requirement, profiles):
            readiness_response = client.get(
                f"{base}/v1/scillm/profiles/readiness",
                headers=headers,
                params={"live": "true", "profile": profile_id},
            )
            readiness_response.raise_for_status()
            readiness_payload = readiness_response.json()
            for item in readiness_payload.get("readiness", []):
                readiness[str(item["profile"])] = str(item["state"])
    return {"profiles": profiles, "readiness": readiness}


def build_transport_provider(selection: dict[str, Any], correlation: dict[str, Any]) -> Any:
    from tau_ai.scillm_transport import ScillmTransportProvider

    return ScillmTransportProvider(
        base_url=scillm_base_url(),
        api_key=scillm_api_key(),
        profile_id=str(selection["selected_profile"]["profile_id"]),
        correlation=correlation,
        required_capabilities=list(
            selection["agent_requirement"].get("required_transport_capabilities", [])
        ),
        timeout_seconds=float(selection.get("timeout_seconds") or 300.0),
        source=CALLER_SKILL,
    )


# ----------------------------------------------------------------- preflight


def _precancelled(execution: Any) -> bool:
    event = getattr(execution, "cancel_event", None)
    return bool(event is not None and event.is_set())


def _int(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise NativeNodePreflightError("NATIVE_NODE_INVALID", f"{key} must be a positive integer")
    return value


def _str_list(config: Mapping[str, Any], key: str) -> list[str]:
    value = config.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise NativeNodePreflightError(
            "NATIVE_NODE_INVALID", f"{key} must be a list of non-empty strings"
        )
    return list(value)


def preflight_native_node(
    plan_node: DagPlanNode, execution: Any, *, goal_hash: str, plan_sha256: str | None = None
) -> dict[str, Any]:
    """Validate node shape, tools, paths, and profile before anything executes.

    Returns the normalized config with the frozen ``transport_profile_selection``
    injected. Raises ``NativeNodePreflightError`` with a stable verdict.
    """
    if _precancelled(execution):
        raise NativeNodePreflightError("CANCELLED", "cancel requested before dispatch")
    config = dict(plan_node.adapter_config.to_value() or {})
    prompt = config.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise NativeNodePreflightError("NATIVE_NODE_INVALID", "prompt must be a non-empty string")
    _int(config, "max_turns", 8)
    _int(config, "max_tool_calls", 16)
    cwd = Path(str(config.get("cwd") or ".")).expanduser().resolve()
    if not cwd.is_dir():
        raise NativeNodePreflightError("NATIVE_NODE_INVALID", f"cwd is not a directory: {cwd}")
    tools = _str_list(config, "allowed_tools")
    unsupported = sorted(set(tools) - set(READ_ONLY_TOOLS))
    if unsupported:
        raise NativeNodePreflightError(
            "NATIVE_TOOL_UNSUPPORTED",
            f"tools {unsupported} are not native read-only tools {list(READ_ONLY_TOOLS)}",
        )
    paths = _str_list(config, "allowed_paths")
    if tools and not paths:
        raise NativeNodePreflightError("NATIVE_PATH_POLICY_INVALID", "allowed_paths is empty")
    for pattern in paths:
        if pattern.startswith("/") or ".." in pattern.split("/"):
            raise NativeNodePreflightError(
                "NATIVE_PATH_POLICY_INVALID", f"allowed_paths pattern escapes cwd: {pattern}"
            )
    requirement = config.get("agent_requirement")
    if not isinstance(requirement, Mapping):
        raise NativeNodePreflightError(
            "NATIVE_NODE_INVALID", "agent_requirement (tau.agent_requirement.v1) is required"
        )
    try:
        discovery = discover_transport_profiles(requirement=requirement)
        selection = select_transport_profile(requirement=requirement, discovery=discovery)
    except AgentRequirementError as exc:
        verdict = (
            "TRANSPORT_PROFILE_UNAVAILABLE"
            if exc.code in {"no_eligible_transport_profile", "scillm_discovery_missing_profiles"}
            else "NATIVE_NODE_INVALID"
        )
        raise NativeNodePreflightError(verdict, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - discovery transport failure is a preflight block
        raise NativeNodePreflightError(
            "TRANSPORT_PROFILE_UNAVAILABLE", f"{type(exc).__name__}:{exc}"
        ) from exc
    policy_hash = canonical_sha256(
        {
            "goal_hash": goal_hash,
            "allowed_tools": tools,
            "allowed_paths": paths,
            "max_tool_calls": config.get("max_tool_calls", 16),
        }
    )
    receipt = selection.receipt_payload(
        run_id=execution.run_id,
        node_id=plan_node.node_id,
        attempt_id=execution.attempt_id,
        attempt=execution.attempt,
        plan_sha256=plan_sha256 or "0" * 64,
        goal_hash=goal_hash,
        policy_hash=policy_hash,
        data_boundary_hash=canonical_sha256({"cwd": str(cwd), "allowed_paths": paths}),
    )
    try:
        validate_selection_receipt(receipt)
    except AgentRequirementError as exc:
        raise NativeNodePreflightError("TRANSPORT_PROFILE_UNAVAILABLE", str(exc)) from exc
    config["transport_profile_selection"] = receipt
    config["policy_hash"] = policy_hash
    config["model"] = str(selection.selected.get("model") or config.get("model") or "profile-owned")
    config["harness"] = "tau_native_agent_loop"
    config["cwd"] = str(cwd)
    config["allowed_tools"] = tools
    config["allowed_paths"] = paths
    return config


# ----------------------------------------------------------------- dispatch


def _native_tools(config: Mapping[str, Any]) -> list[Any]:
    from tau_coding import tools as native

    factories = {
        "read": native.create_read_tool,
        "ls": native.create_ls_tool,
        "grep": native.create_grep_tool,
        "find": native.create_find_tool,
    }
    cwd = config["cwd"]
    return [factories[name](cwd=cwd) for name in config["allowed_tools"]]


def execute_native_agent_node(
    plan_node: DagPlanNode,
    accepted_inputs: tuple[dict[str, Any], ...],
    execution: Any,
    *,
    goal_hash: str,
    plan_sha256: str | None = None,
    run_store: Any | None,
    lease: Any | None,
) -> dict[str, Any]:
    """Preflight, then run the node through the tau#310 adapter over SciLLM."""
    try:
        config = preflight_native_node(
            plan_node, execution, goal_hash=goal_hash, plan_sha256=plan_sha256
        )
    except NativeNodePreflightError as exc:
        return {
            "node_id": plan_node.node_id,
            "status": "BLOCKED",
            "verdict": exc.verdict,
            "accepted_output": None,
            "errors": [exc.detail],
            "provider_invoked": False,
        }
    frozen_node = dataclasses.replace(plan_node, adapter_config=FrozenJson.from_value(config))
    selection = config["transport_profile_selection"]
    correlation = {
        "tau_run_id": execution.run_id,
        "node_id": plan_node.node_id,
        "attempt": execution.attempt,
        "attempt_id": execution.attempt_id,
        "goal_hash": goal_hash,
    }
    provider_holder: dict[str, Any] = {}

    def provider_factory(node: DagPlanNode, node_config: Mapping[str, Any]) -> Any:
        provider = build_transport_provider(selection, correlation)
        provider_holder["provider"] = provider
        return provider

    result = execute_tau_agent_node(
        frozen_node,
        accepted_inputs,
        execution,
        goal_hash=goal_hash,
        provider_factory=provider_factory,
        tools_factory=lambda node, node_config: _native_tools(config),
        run_store=run_store,
        lease=lease,
        cancel_event=getattr(execution, "cancel_event", None),
        plan_sha256=plan_sha256,
    )
    result["provider_invoked"] = "provider" in provider_holder
    result["transport_profile"] = dict(selection["selected_profile"])
    result["policy_hash"] = config["policy_hash"]
    provider = provider_holder.get("provider")
    turn_results = getattr(provider, "turn_results", None)
    if isinstance(turn_results, list):
        result["transport_turn_results"] = list(turn_results)
    return result


__all__ = [
    "READ_ONLY_TOOLS",
    "NativeNodePreflightError",
    "build_transport_provider",
    "discover_transport_profiles",
    "execute_native_agent_node",
    "preflight_native_node",
]
