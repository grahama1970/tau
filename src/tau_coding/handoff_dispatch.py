"""One-step handoff dispatch receipts for Tau agent orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tau_coding.generated_ticket import ROUTABLE_AGENTS, project_agent_handoff
from tau_coding.secure_executor import execute_secure_command

TAU_AGENT_HANDOFF_DISPATCH_RECEIPT_SCHEMA = "tau.agent_handoff_dispatch_receipt.v1"
TAU_AGENT_HANDOFF_COMMAND_LOOP_RECEIPT_SCHEMA = "tau.agent_handoff_command_loop_receipt.v1"
TAU_AGENT_DISPATCH_COMMAND_FILENAME = "tau-dispatch-command.json"
TAU_COMMAND_SPEC_POLICY_SCHEMA = "tau.command_spec_policy.v1"
DURABLE_HANDOFF_CONTEXT_KEYS = (
    "identity_review_model_policy",
    "image_model_policy",
    "persona_dream_phase07_storyboard",
    "persona_dream_panel",
)


@dataclass(frozen=True, slots=True)
class AgentHandoffDispatchResult:
    """Receipt for one selected subagent response consumed from a file runner."""

    ok: bool
    status: str
    selected_agent: str | None = None
    stop_reason: str | None = None
    mocked: bool = True
    live: bool = False
    runner: str = "file-response"
    start_projection: dict[str, Any] | None = None
    response_projection: dict[str, Any] | None = None
    command_results: tuple[dict[str, Any], ...] = ()
    receipt_dir: str | None = None
    artifacts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dispatch receipt."""

        return {
            "schema": TAU_AGENT_HANDOFF_DISPATCH_RECEIPT_SCHEMA,
            "ok": self.ok,
            "status": self.status,
            "selected_agent": self.selected_agent,
            "stop_reason": self.stop_reason,
            "mocked": self.mocked,
            "live": self.live,
            "runner": self.runner,
            "start_projection": self.start_projection,
            "response_projection": self.response_projection,
            "command_results": list(self.command_results),
            "receipt_dir": self.receipt_dir,
            "artifacts": list(self.artifacts),
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class AgentHandoffCommandLoopResult:
    """Receipt for a bounded command-backed handoff loop."""

    ok: bool
    status: str
    step_count: int = 0
    terminal_agent: str | None = None
    stop_reason: str | None = None
    mocked: bool = False
    live: bool = True
    runner: str = "agent-registry-command-loop"
    dispatches: tuple[dict[str, Any], ...] = ()
    receipt_dir: str | None = None
    artifacts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable command loop receipt."""

        return {
            "schema": TAU_AGENT_HANDOFF_COMMAND_LOOP_RECEIPT_SCHEMA,
            "ok": self.ok,
            "status": self.status,
            "step_count": self.step_count,
            "terminal_agent": self.terminal_agent,
            "stop_reason": self.stop_reason,
            "mocked": self.mocked,
            "live": self.live,
            "runner": self.runner,
            "dispatches": list(self.dispatches),
            "receipt_dir": self.receipt_dir,
            "artifacts": list(self.artifacts),
            "errors": list(self.errors),
        }


def dispatch_agent_handoff_once(
    start_payload: Mapping[str, Any],
    response_by_agent: Mapping[str, Mapping[str, Any]],
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> AgentHandoffDispatchResult:
    """Consume one handoff route and validate exactly one selected agent response."""

    start_projection = project_agent_handoff(
        start_payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    ).as_dict()
    if start_projection.get("ok") is not True:
        return AgentHandoffDispatchResult(
            ok=False,
            status="BLOCKED",
            selected_agent=start_projection.get("next_agent"),
            stop_reason="invalid_start_handoff",
            start_projection=start_projection,
            errors=tuple(f"start: {error}" for error in start_projection.get("errors", [])),
        )

    selected_agent = str(start_projection.get("next_agent") or "")
    if selected_agent == "human":
        return AgentHandoffDispatchResult(
            ok=True,
            status="WAITING",
            selected_agent=selected_agent,
            stop_reason="next_agent_is_human",
            start_projection=start_projection,
        )

    response_payload = response_by_agent.get(selected_agent)
    if response_payload is None:
        return AgentHandoffDispatchResult(
            ok=True,
            status="WAITING",
            selected_agent=selected_agent,
            stop_reason="missing_agent_response",
            start_projection=start_projection,
        )

    response_projection = project_agent_handoff(
        response_payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    ).as_dict()
    errors = _response_continuity_errors(
        start_payload=start_payload,
        start_projection=start_projection,
        response_payload=response_payload,
        response_projection=response_projection,
        selected_agent=selected_agent,
    )
    if response_projection.get("ok") is not True:
        errors.extend(f"response: {error}" for error in response_projection.get("errors", []))

    if errors:
        return AgentHandoffDispatchResult(
            ok=False,
            status="BLOCKED",
            selected_agent=selected_agent,
            stop_reason="invalid_agent_response",
            start_projection=start_projection,
            response_projection=response_projection,
            errors=tuple(errors),
        )

    return AgentHandoffDispatchResult(
        ok=True,
        status="COMPLETED",
        selected_agent=selected_agent,
        stop_reason="response_consumed",
        start_projection=start_projection,
        response_projection=response_projection,
    )


def write_agent_handoff_dispatch_receipt(
    start_payload: Mapping[str, Any],
    response_by_agent: Mapping[str, Mapping[str, Any]],
    receipt_dir: Path,
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> AgentHandoffDispatchResult:
    """Write one dispatch receipt plus per-step projection artifacts."""

    receipt_dir.mkdir(parents=True, exist_ok=True)
    dispatch = dispatch_agent_handoff_once(
        start_payload,
        response_by_agent,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    )
    artifacts: list[str] = []
    artifacts.extend(dispatch.artifacts)
    if dispatch.start_projection is not None:
        start_path = receipt_dir / "start-handoff.receipt.json"
        _write_json(start_path, dispatch.start_projection)
        artifacts.append(str(start_path))
    if dispatch.response_projection is not None:
        response_path = receipt_dir / f"{dispatch.selected_agent}-response.receipt.json"
        _write_json(response_path, dispatch.response_projection)
        artifacts.append(str(response_path))

    receipt_payload = {
        **dispatch.as_dict(),
        "receipt_dir": str(receipt_dir),
        "artifacts": artifacts,
    }
    _write_json(receipt_dir / "dispatch-receipt.json", receipt_payload)
    return AgentHandoffDispatchResult(
        ok=dispatch.ok,
        status=dispatch.status,
        selected_agent=dispatch.selected_agent,
        stop_reason=dispatch.stop_reason,
        start_projection=dispatch.start_projection,
        response_projection=dispatch.response_projection,
        command_results=dispatch.command_results,
        receipt_dir=str(receipt_dir),
        artifacts=tuple(artifacts),
        errors=dispatch.errors,
    )


def dispatch_agent_handoff_command_once(
    start_payload: Mapping[str, Any],
    command: list[str],
    *,
    timeout_s: float = 30.0,
    cwd: Path | None = None,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
    artifact_dir: Path | None = None,
    command_spec_metadata: Mapping[str, Any] | None = None,
    secure_execution: Mapping[str, Any] | None = None,
    attempt: int = 1,
    cancel_event: threading.Event | None = None,
) -> AgentHandoffDispatchResult:
    """Run one bounded local command and validate its stdout handoff response."""

    start_projection = project_agent_handoff(
        start_payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    ).as_dict()
    selected_agent = start_projection.get("next_agent")
    if start_projection.get("ok") is not True:
        return AgentHandoffDispatchResult(
            ok=False,
            status="BLOCKED",
            selected_agent=selected_agent,
            stop_reason="invalid_start_handoff",
            runner="command",
            start_projection=start_projection,
            errors=tuple(f"start: {error}" for error in start_projection.get("errors", [])),
        )
    if selected_agent == "human":
        return AgentHandoffDispatchResult(
            ok=True,
            status="WAITING",
            selected_agent=str(selected_agent),
            stop_reason="next_agent_is_human",
            runner="command",
            start_projection=start_projection,
        )
    if not command:
        return AgentHandoffDispatchResult(
            ok=False,
            status="BLOCKED",
            selected_agent=str(selected_agent),
            stop_reason="missing_command",
            runner="command",
            start_projection=start_projection,
            errors=("command must not be empty",),
        )

    command_start_payload = _payload_with_command_spec_dispatch_metadata(
        start_payload,
        command_spec_metadata=command_spec_metadata,
    )
    stdin = json.dumps(command_start_payload, sort_keys=True) + "\n"
    resolved_artifact_dir: Path | None = None
    if artifact_dir is not None:
        resolved_artifact_dir = artifact_dir.expanduser().resolve()
        resolved_artifact_dir.mkdir(parents=True, exist_ok=True)
    if secure_execution is not None:
        node_metadata = (
            command_spec_metadata.get("tau_dag_node")
            if isinstance(command_spec_metadata, Mapping)
            else None
        )
        node_id = (
            str(node_metadata.get("node_id"))
            if isinstance(node_metadata, Mapping) and node_metadata.get("node_id")
            else str(selected_agent)
        )
        grants_by_node = secure_execution.get("grants_by_node")
        grants = grants_by_node.get(node_id, []) if isinstance(grants_by_node, Mapping) else []
        secure_result = execute_secure_command(
            command=command,
            stdin_text=stdin,
            timeout_seconds=timeout_s,
            backend=str(secure_execution.get("backend") or "bwrap"),
            receipt_dir=(
                Path(str(secure_execution["receipt_root"])) / node_id / f"attempt-{attempt:03d}"
            ),
            policy_profile_path=Path(str(secure_execution["policy_profile_path"])),
            data_boundary_path=Path(str(secure_execution["data_boundary_path"])),
            grants=grants if isinstance(grants, list) else [],
            run_id=str(secure_execution["run_id"]),
            dag_id=str(secure_execution["dag_id"]),
            node_id=node_id,
            attempt=attempt,
            goal_hash=str(secure_execution["goal_hash"]),
            security_context_sha256=str(secure_execution["security_context_sha256"]),
            policy_profile_sha256=str(secure_execution["policy_profile_sha256"]),
            data_boundary_sha256=str(secure_execution["data_boundary_sha256"]),
            child_environment={"TAU_HANDOFF_SELECTED_AGENT": str(selected_agent)},
        )
        command_result = {
            "command": command,
            "exit_code": secure_result.returncode,
            "timeout_s": timeout_s,
            "timed_out": secure_result.receipt.get("timed_out") is True,
            "stdout": secure_result.stdout,
            "stderr": secure_result.stderr,
            "secure_execution_receipt": secure_result.receipt.get("receipt_path"),
            "secure_execution_status": secure_result.receipt.get("status"),
        }
        if secure_result.receipt.get("status") != "PASS":
            return AgentHandoffDispatchResult(
                ok=False,
                status="BLOCKED",
                selected_agent=str(selected_agent),
                stop_reason="secure_execution_blocked",
                runner="secure-command",
                start_projection=start_projection,
                command_results=(command_result,),
                artifacts=tuple(_artifact_paths(Path(str(secure_execution["receipt_root"])))),
                errors=tuple(
                    str(alert.get("message") or alert.get("code"))
                    for alert in secure_result.receipt.get("alerts", [])
                    if isinstance(alert, Mapping)
                ),
            )
    else:
        env = os.environ.copy()
        env["TAU_HANDOFF_SELECTED_AGENT"] = str(selected_agent)
        if active_goal_hash:
            env["TAU_HANDOFF_ACTIVE_GOAL_HASH"] = active_goal_hash
        if agent_registry_root is not None:
            env["TAU_HANDOFF_AGENT_REGISTRY_ROOT"] = str(agent_registry_root.expanduser().resolve())
        if resolved_artifact_dir is not None:
            env["TAU_HANDOFF_COMMAND_ARTIFACT_DIR"] = str(resolved_artifact_dir)
        try:
            completed = _run_command_with_optional_cancellation(
                command,
                stdin=stdin,
                cwd=cwd,
                env=env,
                timeout_s=timeout_s,
                cancel_event=cancel_event,
            )
        except _CommandCancelled as exc:
            return AgentHandoffDispatchResult(
                ok=False,
                status="BLOCKED",
                selected_agent=str(selected_agent),
                stop_reason="command_cancelled",
                runner="command",
                start_projection=start_projection,
                command_results=(
                    {
                        "command": command,
                        "exit_code": exc.returncode,
                        "timeout_s": timeout_s,
                        "timed_out": False,
                        "cancelled": True,
                        "stdout": exc.stdout,
                        "stderr": exc.stderr,
                    },
                ),
                errors=("command cancelled by DAG join decision",),
            )
        except subprocess.TimeoutExpired as exc:
            timeout_metadata: dict[str, object] = {}
            if command_spec_metadata is not None:
                timeout_source = command_spec_metadata.get("timeout_s_source")
                if isinstance(timeout_source, str):
                    timeout_metadata["timeout_s_source"] = timeout_source
                timeout_policy = command_spec_metadata.get("timeout_policy")
                if isinstance(timeout_policy, Mapping):
                    timeout_metadata["timeout_policy"] = dict(timeout_policy)
            return AgentHandoffDispatchResult(
                ok=False,
                status="BLOCKED",
                selected_agent=str(selected_agent),
                stop_reason="command_timeout",
                runner="command",
                start_projection=start_projection,
                command_results=(
                    {
                        "command": command,
                        "exit_code": None,
                        "timeout_s": timeout_s,
                        "timed_out": True,
                        "stdout": exc.stdout or "",
                        "stderr": exc.stderr or "",
                        **timeout_metadata,
                    },
                ),
                errors=(f"command timed out after {timeout_s:g}s",),
            )
        command_result = {
            "command": command,
            "exit_code": completed.returncode,
            "timeout_s": timeout_s,
            "timed_out": False,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    if command_spec_metadata is not None:
        command_result.update(
            {
                key: value
                for key, value in command_spec_metadata.items()
                if key
                in {
                    "command_spec_path",
                    "command_spec_sha256",
                    "command_policy_path",
                    "command_policy_sha256",
                }
                and isinstance(value, str)
            }
        )
        timeout_source = command_spec_metadata.get("timeout_s_source")
        if isinstance(timeout_source, str):
            command_result["timeout_s_source"] = timeout_source
        timeout_policy = command_spec_metadata.get("timeout_policy")
        if isinstance(timeout_policy, Mapping):
            command_result["timeout_policy"] = dict(timeout_policy)
    try:
        response_payload = json.loads(str(command_result["stdout"]))
    except json.JSONDecodeError as exc:
        if command_result["exit_code"] != 0:
            return AgentHandoffDispatchResult(
                ok=False,
                status="BLOCKED",
                selected_agent=str(selected_agent),
                stop_reason="command_failed",
                runner="secure-command" if secure_execution is not None else "command",
                start_projection=start_projection,
                command_results=(command_result,),
                errors=(f"command exited {command_result['exit_code']}",),
            )
        return AgentHandoffDispatchResult(
            ok=False,
            status="BLOCKED",
            selected_agent=str(selected_agent),
            stop_reason="invalid_command_json",
            runner="command",
            start_projection=start_projection,
            command_results=(command_result,),
            errors=(f"command stdout was not JSON: {exc}",),
        )
    if not isinstance(response_payload, dict):
        return AgentHandoffDispatchResult(
            ok=False,
            status="BLOCKED",
            selected_agent=str(selected_agent),
            stop_reason="invalid_command_json",
            runner="command",
            start_projection=start_projection,
            command_results=(command_result,),
            errors=("command stdout JSON root must be an object",),
        )

    dispatch = dispatch_agent_handoff_once(
        command_start_payload,
        {str(selected_agent): response_payload},
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    )
    result_payload = response_payload.get("result")
    semantic_blocked = (
        command_result["exit_code"] != 0
        and isinstance(result_payload, Mapping)
        and result_payload.get("status") == "BLOCKED"
        and dispatch.ok
    )
    if command_result["exit_code"] != 0 and not semantic_blocked:
        return AgentHandoffDispatchResult(
            ok=False,
            status="BLOCKED",
            selected_agent=str(selected_agent),
            stop_reason="command_failed",
            runner="secure-command" if secure_execution is not None else "command",
            start_projection=start_projection,
            response_projection=dispatch.response_projection,
            command_results=(command_result,),
            errors=(f"command exited {command_result['exit_code']}", *dispatch.errors),
        )
    artifacts = _artifact_paths(resolved_artifact_dir)
    if secure_execution is not None:
        artifacts.extend(_artifact_paths(Path(str(secure_execution["receipt_root"]))))
    return AgentHandoffDispatchResult(
        ok=False if semantic_blocked else dispatch.ok,
        status="BLOCKED" if semantic_blocked else dispatch.status,
        selected_agent=dispatch.selected_agent,
        stop_reason="node_blocked" if semantic_blocked else dispatch.stop_reason,
        mocked=False,
        live=True,
        runner="secure-command" if secure_execution is not None else "command",
        start_projection=dispatch.start_projection,
        response_projection=dispatch.response_projection,
        command_results=(command_result,),
        artifacts=tuple(artifacts),
        errors=(
            (f"node command exited {command_result['exit_code']} after a valid BLOCKED handoff",)
            if semantic_blocked
            else dispatch.errors
        ),
    )


def _payload_with_command_spec_dispatch_metadata(
    start_payload: Mapping[str, Any],
    *,
    command_spec_metadata: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if command_spec_metadata is None:
        return start_payload
    tau_dag_node = command_spec_metadata.get("tau_dag_node")
    if not isinstance(tau_dag_node, Mapping):
        return start_payload

    payload = cast(dict[str, Any], json.loads(json.dumps(start_payload)))
    context = payload.get("context")
    if not isinstance(context, dict):
        context = {}
        payload["context"] = context

    context["tau_dag_node"] = tau_dag_node
    node_id = tau_dag_node.get("node_id")
    if isinstance(node_id, str):
        context["dag_node_id"] = node_id
    agent = tau_dag_node.get("agent")
    if isinstance(agent, str):
        context["dag_agent_role"] = agent

    for key in (
        "provider",
        "model_policy",
        "prompt_contract",
        "provider_route",
        "requires_provider_route",
    ):
        if key in tau_dag_node:
            context[key] = tau_dag_node[key]

    return payload


def _payload_with_durable_handoff_context(
    previous_payload: Mapping[str, Any],
    response_payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    previous_context = previous_payload.get("context")
    if not isinstance(previous_context, Mapping):
        return response_payload

    durable_context = {
        key: previous_context[key]
        for key in DURABLE_HANDOFF_CONTEXT_KEYS
        if key in previous_context
    }
    if not durable_context:
        return response_payload

    response_context = response_payload.get("context")
    if isinstance(response_context, Mapping) and all(
        key in response_context for key in durable_context
    ):
        return response_payload

    payload = cast(dict[str, Any], json.loads(json.dumps(response_payload)))
    context = payload.get("context")
    if not isinstance(context, dict):
        context = {}
        payload["context"] = context

    for key, value in durable_context.items():
        context.setdefault(key, value)

    return payload


def _command_spec_node_id(
    command_spec_metadata: Mapping[str, Any],
    selected_agent: str,
) -> str:
    node_metadata = command_spec_metadata.get("tau_dag_node")
    if isinstance(node_metadata, Mapping):
        node_id = node_metadata.get("node_id")
        if isinstance(node_id, str) and node_id:
            return node_id
    return selected_agent


def write_agent_handoff_command_dispatch_receipt(
    start_payload: Mapping[str, Any],
    command: list[str],
    receipt_dir: Path,
    *,
    timeout_s: float = 30.0,
    cwd: Path | None = None,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> AgentHandoffDispatchResult:
    """Write one command-backed dispatch receipt plus projection artifacts."""

    receipt_dir.mkdir(parents=True, exist_ok=True)
    dispatch = dispatch_agent_handoff_command_once(
        start_payload,
        command,
        timeout_s=timeout_s,
        cwd=cwd,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
        artifact_dir=receipt_dir / "command-artifacts",
    )
    artifacts: list[str] = []
    if dispatch.start_projection is not None:
        start_path = receipt_dir / "start-handoff.receipt.json"
        _write_json(start_path, dispatch.start_projection)
        artifacts.append(str(start_path))
    if dispatch.response_projection is not None:
        response_path = receipt_dir / f"{dispatch.selected_agent}-response.receipt.json"
        _write_json(response_path, dispatch.response_projection)
        artifacts.append(str(response_path))
    receipt_payload = {
        **dispatch.as_dict(),
        "receipt_dir": str(receipt_dir),
        "artifacts": artifacts,
    }
    _write_json(receipt_dir / "dispatch-receipt.json", receipt_payload)
    return AgentHandoffDispatchResult(
        ok=dispatch.ok,
        status=dispatch.status,
        selected_agent=dispatch.selected_agent,
        stop_reason=dispatch.stop_reason,
        mocked=dispatch.mocked,
        live=dispatch.live,
        runner=dispatch.runner,
        start_projection=dispatch.start_projection,
        response_projection=dispatch.response_projection,
        command_results=dispatch.command_results,
        receipt_dir=str(receipt_dir),
        artifacts=tuple(artifacts),
        errors=dispatch.errors,
    )


def run_agent_handoff_command_loop(
    start_payload: Mapping[str, Any],
    *,
    agent_registry_root: Path,
    command_spec_root: Path | None = None,
    active_goal_hash: str | None = None,
    goal_guardian_ticket_source: Path | None = None,
    max_steps: int = 5,
    artifact_root: Path | None = None,
    command_policy_path: Path | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    deadline_monotonic: float | None = None,
    secure_execution: Mapping[str, Any] | None = None,
) -> AgentHandoffCommandLoopResult:
    """Run selected agent commands until the route reaches human or fails closed."""

    def emit_progress(event: dict[str, Any]) -> None:
        if progress_callback is not None:
            progress_callback({"ts": _utc_stamp(), **event})

    if max_steps < 1:
        return AgentHandoffCommandLoopResult(
            ok=False,
            status="BLOCKED",
            stop_reason="invalid_max_steps",
            errors=("max_steps must be at least 1",),
        )

    current_payload = start_payload
    dispatches: list[dict[str, Any]] = []
    node_attempts: dict[str, int] = {}
    for step in range(1, max_steps + 1):
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step - 1,
                terminal_agent=None,
                stop_reason="deadline_expired",
                dispatches=tuple(dispatches),
                errors=("command handoff loop deadline expired",),
            )
        current_projection = project_agent_handoff(
            current_payload,
            active_goal_hash=active_goal_hash,
            agent_registry_root=agent_registry_root,
        )
        if not current_projection.ok:
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=current_projection.next_agent,
                stop_reason="invalid_handoff",
                dispatches=tuple(dispatches),
                errors=tuple(f"step[{step}]: {error}" for error in current_projection.errors),
            )
        selected_agent = current_projection.next_agent
        if selected_agent == "human":
            emit_progress(
                {
                    "event": "loop_waiting",
                    "loop_step": step - 1,
                    "selected_agent": selected_agent,
                    "status": "WAITING",
                    "stop_reason": "next_agent_is_human",
                }
            )
            return AgentHandoffCommandLoopResult(
                ok=True,
                status="WAITING",
                step_count=step - 1,
                terminal_agent=selected_agent,
                stop_reason="next_agent_is_human",
                dispatches=tuple(dispatches),
            )
        if selected_agent is None:
            emit_progress(
                {
                    "event": "loop_blocked",
                    "loop_step": step,
                    "selected_agent": None,
                    "status": "BLOCKED",
                    "stop_reason": "missing_next_agent",
                }
            )
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=None,
                stop_reason="missing_next_agent",
                dispatches=tuple(dispatches),
                errors=(f"step[{step}]: next_agent is missing",),
            )
        try:
            spec = load_agent_dispatch_command_spec(
                agent_registry_root,
                selected_agent,
                command_spec_root=command_spec_root,
                command_policy_path=command_policy_path,
            )
        except ValueError as exc:
            emit_progress(
                {
                    "event": "step_blocked",
                    "loop_step": step,
                    "selected_agent": selected_agent,
                    "status": "BLOCKED",
                    "stop_reason": _command_spec_load_stop_reason(str(exc)),
                }
            )
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=selected_agent,
                stop_reason=_command_spec_load_stop_reason(str(exc)),
                dispatches=tuple(dispatches),
                errors=(str(exc),),
            )

        emit_progress(
            {
                "event": "step_started",
                "loop_step": step,
                "selected_agent": selected_agent,
                "status": "RUNNING",
                "command_spec_path": spec.get("command_spec_path"),
            }
        )
        timeout_s = spec["timeout_s"]
        command_spec_metadata = spec
        if deadline_monotonic is not None:
            remaining_s = deadline_monotonic - time.monotonic()
            if remaining_s <= 0:
                return AgentHandoffCommandLoopResult(
                    ok=False,
                    status="BLOCKED",
                    step_count=step - 1,
                    terminal_agent=selected_agent,
                    stop_reason="deadline_expired",
                    dispatches=tuple(dispatches),
                    errors=("command handoff loop deadline expired before dispatch",),
                )
            if remaining_s < timeout_s:
                timeout_s = remaining_s
                command_spec_metadata = {
                    **spec,
                    "timeout_s": timeout_s,
                    "timeout_s_source": "goal_run_deadline_remaining",
                    "original_timeout_s": spec["timeout_s"],
                }
        node_key = _command_spec_node_id(command_spec_metadata, selected_agent)
        attempt = node_attempts.get(node_key, 0) + 1
        dispatch = dispatch_agent_handoff_command_once(
            current_payload,
            _command_with_goal_guardian_ticket_source(
                spec["command"],
                selected_agent=selected_agent,
                ticket_source=goal_guardian_ticket_source,
            ),
            timeout_s=timeout_s,
            cwd=spec["cwd"],
            active_goal_hash=active_goal_hash,
            agent_registry_root=agent_registry_root,
            artifact_dir=(
                artifact_root / f"command-loop-step-{step:03d}"
                if artifact_root is not None
                else None
            ),
            command_spec_metadata=command_spec_metadata,
            secure_execution=secure_execution,
            attempt=attempt,
        )
        node_attempts[node_key] = attempt
        dispatch_payload = dispatch.as_dict()
        dispatch_payload["loop_step"] = step
        dispatches.append(dispatch_payload)
        emit_progress(
            {
                "event": "step_completed",
                "loop_step": step,
                "selected_agent": dispatch.selected_agent,
                "status": dispatch.status,
                "ok": dispatch.ok,
                "stop_reason": dispatch.stop_reason,
            }
        )
        if not dispatch.ok:
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=dispatch.selected_agent,
                stop_reason=dispatch.stop_reason or "dispatch_failed",
                dispatches=tuple(dispatches),
                errors=dispatch.errors,
            )
        if dispatch.response_projection is None:
            return AgentHandoffCommandLoopResult(
                ok=True,
                status=dispatch.status,
                step_count=step,
                terminal_agent=dispatch.selected_agent,
                stop_reason=dispatch.stop_reason,
                dispatches=tuple(dispatches),
            )
        command_results = dispatch.command_results
        stdout = command_results[0].get("stdout") if command_results else None
        if not isinstance(stdout, str):
            emit_progress(
                {
                    "event": "loop_blocked",
                    "loop_step": step,
                    "selected_agent": dispatch.selected_agent,
                    "status": "BLOCKED",
                    "stop_reason": "missing_command_stdout",
                }
            )
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=dispatch.selected_agent,
                stop_reason="missing_command_stdout",
                dispatches=tuple(dispatches),
                errors=(f"step[{step}]: command stdout missing",),
            )
        try:
            next_payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            emit_progress(
                {
                    "event": "loop_blocked",
                    "loop_step": step,
                    "selected_agent": dispatch.selected_agent,
                    "status": "BLOCKED",
                    "stop_reason": "invalid_command_json",
                }
            )
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=dispatch.selected_agent,
                stop_reason="invalid_command_json",
                dispatches=tuple(dispatches),
                errors=(f"step[{step}]: command stdout was not JSON: {exc}",),
            )
        if not isinstance(next_payload, dict):
            emit_progress(
                {
                    "event": "loop_blocked",
                    "loop_step": step,
                    "selected_agent": dispatch.selected_agent,
                    "status": "BLOCKED",
                    "stop_reason": "invalid_command_json",
                }
            )
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=dispatch.selected_agent,
                stop_reason="invalid_command_json",
                dispatches=tuple(dispatches),
                errors=(f"step[{step}]: command stdout JSON root must be an object",),
            )
        current_payload = _payload_with_durable_handoff_context(current_payload, next_payload)
        next_projection = project_agent_handoff(
            current_payload,
            active_goal_hash=active_goal_hash,
            agent_registry_root=agent_registry_root,
        )
        if not next_projection.ok:
            emit_progress(
                {
                    "event": "loop_blocked",
                    "loop_step": step,
                    "selected_agent": next_projection.next_agent,
                    "status": "BLOCKED",
                    "stop_reason": "invalid_handoff",
                }
            )
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=next_projection.next_agent,
                stop_reason="invalid_handoff",
                dispatches=tuple(dispatches),
                errors=tuple(f"step[{step}]: {error}" for error in next_projection.errors),
            )
        if next_projection.next_agent == "human":
            emit_progress(
                {
                    "event": "loop_waiting",
                    "loop_step": step,
                    "selected_agent": "human",
                    "status": "WAITING",
                    "stop_reason": "next_agent_is_human",
                }
            )
            return AgentHandoffCommandLoopResult(
                ok=True,
                status="WAITING",
                step_count=step,
                terminal_agent="human",
                stop_reason="next_agent_is_human",
                dispatches=tuple(dispatches),
            )

    emit_progress(
        {
            "event": "loop_blocked",
            "loop_step": max_steps,
            "selected_agent": dispatches[-1].get("selected_agent") if dispatches else None,
            "status": "BLOCKED",
            "stop_reason": "max_steps_exhausted",
        }
    )
    return AgentHandoffCommandLoopResult(
        ok=False,
        status="BLOCKED",
        step_count=max_steps,
        terminal_agent=dispatches[-1].get("selected_agent") if dispatches else None,
        stop_reason="max_steps_exhausted",
        dispatches=tuple(dispatches),
        errors=(f"command handoff loop exceeded max_steps={max_steps}",),
    )


def write_agent_handoff_command_loop_receipt(
    start_payload: Mapping[str, Any],
    receipt_dir: Path,
    *,
    agent_registry_root: Path,
    command_spec_root: Path | None = None,
    active_goal_hash: str | None = None,
    goal_guardian_ticket_source: Path | None = None,
    max_steps: int = 5,
    command_policy_path: Path | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    deadline_monotonic: float | None = None,
    secure_execution: Mapping[str, Any] | None = None,
) -> AgentHandoffCommandLoopResult:
    """Write one command-backed loop receipt plus per-step dispatch artifacts."""

    receipt_dir.mkdir(parents=True, exist_ok=True)
    loop = run_agent_handoff_command_loop(
        start_payload,
        agent_registry_root=agent_registry_root,
        command_spec_root=command_spec_root,
        active_goal_hash=active_goal_hash,
        goal_guardian_ticket_source=goal_guardian_ticket_source,
        max_steps=max_steps,
        artifact_root=receipt_dir / "command-artifacts",
        command_policy_path=command_policy_path,
        progress_callback=progress_callback,
        deadline_monotonic=deadline_monotonic,
        secure_execution=secure_execution,
    )
    artifacts: list[str] = []
    for dispatch in loop.dispatches:
        step = int(dispatch["loop_step"])
        step_path = receipt_dir / f"command-loop-step-{step:03d}.receipt.json"
        _write_json(step_path, dispatch)
        artifacts.append(str(step_path))
        artifacts.extend(str(path) for path in dispatch.get("artifacts", []))
    loop_payload = {
        **loop.as_dict(),
        "receipt_dir": str(receipt_dir),
        "artifacts": artifacts,
    }
    _write_json(receipt_dir / "command-loop-receipt.json", loop_payload)
    return AgentHandoffCommandLoopResult(
        ok=loop.ok,
        status=loop.status,
        step_count=loop.step_count,
        terminal_agent=loop.terminal_agent,
        stop_reason=loop.stop_reason,
        dispatches=loop.dispatches,
        receipt_dir=str(receipt_dir),
        artifacts=tuple(artifacts),
        errors=loop.errors,
    )


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_agent_dispatch_command_spec(
    root: Path,
    agent_id: str,
    *,
    command_spec_root: Path | None = None,
    command_policy_path: Path | None = None,
) -> dict[str, Any]:
    """Load an opt-in Tau command dispatch spec for one validated registry entry."""

    if not agent_id or "/" in agent_id or agent_id in {".", ".."}:
        raise ValueError(f"invalid agent id for command dispatch: {agent_id!r}")
    agent_dir = root.expanduser().resolve() / agent_id
    if agent_id not in ROUTABLE_AGENTS or command_spec_root is None:
        if not agent_dir.is_dir():
            raise ValueError(f"agent registry entry does not exist: {agent_dir}")
        if not (agent_dir / "AGENTS.md").is_file():
            raise ValueError(f"agent registry entry lacks AGENTS.md: {agent_dir}")
    spec_dir = (
        command_spec_root.expanduser().resolve() / agent_id if command_spec_root else agent_dir
    )
    spec_path = spec_dir / TAU_AGENT_DISPATCH_COMMAND_FILENAME
    if not spec_path.is_file():
        raise ValueError(f"agent dispatch command spec missing: {spec_path}")
    try:
        payload = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"agent dispatch command spec unreadable: {spec_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"agent dispatch command spec root must be an object: {spec_path}")
    spec = validate_command_dispatch_spec(payload, label=f"agent dispatch command spec {spec_path}")
    spec["command_spec_path"] = str(spec_path)
    spec["command_spec_sha256"] = f"sha256:{_sha256(spec_path)}"
    if command_policy_path is not None:
        policy_path = command_policy_path.expanduser().resolve()
        policy = load_command_spec_policy(policy_path)
        validate_command_dispatch_spec_policy(
            spec,
            policy,
            policy_dir=policy_path.parent,
            label=f"agent dispatch command spec {spec_path}",
        )
        spec["command_policy_path"] = str(policy_path)
        spec["command_policy_sha256"] = f"sha256:{_sha256(policy_path)}"
    return spec


def validate_command_dispatch_spec(
    payload: Mapping[str, Any],
    *,
    label: str = "handoff command spec",
) -> dict[str, object]:
    """Validate the minimal command spec used by command-backed dispatch."""

    command = payload.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item for item in command)
    ):
        raise ValueError(f"{label} requires non-empty string list field: command")
    tau_dag_node = payload.get("tau_dag_node")
    timeout_policy = tau_dag_node.get("timeout_policy") if isinstance(tau_dag_node, dict) else None
    if "timeout_s" in payload:
        timeout_s_source = "command_spec"
        timeout_value = payload["timeout_s"]
    elif isinstance(timeout_policy, dict) and isinstance(
        timeout_policy.get("timeout_s"), (int, float)
    ):
        timeout_s_source = str(
            timeout_policy.get("source") or "tau_provider_command_timeout_policy"
        )
        timeout_value = timeout_policy["timeout_s"]
    else:
        timeout_s_source = "default_command_timeout"
        timeout_value = 30.0
    if isinstance(timeout_value, bool) or not isinstance(timeout_value, (int, float)):
        raise ValueError(f"{label} timeout_s must be a positive number")
    timeout_s = float(timeout_value)
    if timeout_s <= 0:
        raise ValueError(f"{label} timeout_s must be a positive number")
    cwd_value = payload.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else None
    spec: dict[str, object] = {
        "command": command,
        "timeout_s": timeout_s,
        "timeout_s_source": timeout_s_source,
        "cwd": cwd,
        "requires_network": _bool(payload.get("requires_network")),
        "mutates": _bool(payload.get("mutates")),
        "requires_clean_worktree": _bool(payload.get("requires_clean_worktree")),
    }
    if isinstance(tau_dag_node, dict):
        spec["tau_dag_node"] = tau_dag_node
    if isinstance(timeout_policy, dict) and timeout_s_source != "command_spec":
        spec["timeout_policy"] = timeout_policy
    return spec


def load_command_spec_policy(path: Path) -> dict[str, object]:
    """Load a command-spec trust policy."""

    try:
        payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"command spec policy unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"command spec policy root must be an object: {path}")
    if payload.get("schema") != TAU_COMMAND_SPEC_POLICY_SCHEMA:
        raise ValueError(f"command spec policy schema must be {TAU_COMMAND_SPEC_POLICY_SCHEMA}")
    return payload


def validate_command_dispatch_spec_policy(
    spec: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    policy_dir: Path,
    label: str = "handoff command spec",
) -> None:
    """Validate a command spec against an opt-in trust policy."""

    command = spec.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) for item in command)
    ):
        raise ValueError(f"{label} command must be validated before policy checks")
    command_root = Path(command[0]).name
    command_line = " ".join(command)
    allowed_roots = _string_list(policy.get("allowed_command_roots"))
    if allowed_roots and command[0] not in allowed_roots and command_root not in allowed_roots:
        raise ValueError(f"{label} command root {command[0]!r} is not allowed by command policy")
    for denied in _string_list(policy.get("denied_commands")):
        if (
            command_root == denied
            or command_line == denied
            or command_line.startswith(f"{denied} ")
            or f" {denied} " in command_line
        ):
            raise ValueError(f"{label} command is denied by command policy: {denied}")
    cwd = spec.get("cwd")
    allowed_cwd_roots = _string_list(policy.get("allowed_cwd_roots"))
    if allowed_cwd_roots and cwd is None:
        raise ValueError(
            f"{label} cwd must be explicit when command policy allowed_cwd_roots is set"
        )
    if cwd is not None and allowed_cwd_roots:
        resolved_cwd = _resolve_policy_path(Path(cwd), policy_dir)
        allowed = [_resolve_policy_path(Path(root), policy_dir) for root in allowed_cwd_roots]
        if not any(_is_relative_to(resolved_cwd, root) for root in allowed):
            raise ValueError(
                f"{label} cwd {resolved_cwd} is outside allowed command policy cwd roots"
            )
    if spec.get("requires_network") is True and policy.get("allows_network") is not True:
        raise ValueError(f"{label} requires network but command policy does not allow network")
    if spec.get("mutates") is True and policy.get("allows_mutation") is not True:
        raise ValueError(f"{label} mutates state but command policy does not allow mutation")
    if policy.get("requires_clean_worktree") is True or spec.get("requires_clean_worktree") is True:
        _validate_clean_worktree(policy_dir=policy_dir, label=label)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _bool(value: object) -> bool:
    return value is True


def _validate_clean_worktree(*, policy_dir: Path, label: str) -> None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(policy_dir), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"{label} clean worktree check failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"{label} clean worktree check failed: {detail}")
    if completed.stdout.strip():
        raise ValueError(f"{label} requires a clean git worktree")


def _resolve_policy_path(path: Path, policy_dir: Path) -> Path:
    if path.is_absolute():
        return path.expanduser().resolve()
    return (policy_dir / path).expanduser().resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.expanduser().resolve().read_bytes()).hexdigest()


def _command_with_goal_guardian_ticket_source(
    command: object,
    *,
    selected_agent: str | None,
    ticket_source: Path | None,
) -> list[str]:
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        return []
    resolved_command = list(command)
    if (
        selected_agent != "goal-guardian"
        or ticket_source is None
        or "handoff-goal-guardian-adapter" not in resolved_command
        or "--ticket-source" in resolved_command
    ):
        return resolved_command
    return [
        *resolved_command,
        "--ticket-source",
        str(ticket_source.expanduser().resolve()),
    ]


def _command_spec_load_stop_reason(error: str) -> str:
    """Classify command-spec load failures for project-agent course correction."""

    lower = error.lower()
    if any(
        marker in lower
        for marker in (
            "command policy",
            "command spec policy",
            "requires network",
            "mutates state",
            "clean worktree",
        )
    ):
        return "command_policy_rejected"
    return "missing_agent_command_spec"


def _artifact_paths(root: Path | None) -> list[str]:
    if root is None or not root.exists():
        return []
    return [str(path) for path in sorted(root.rglob("*")) if path.is_file()]


class _CommandCancelled(RuntimeError):
    def __init__(self, *, returncode: int | None, stdout: str, stderr: str) -> None:
        super().__init__("command cancelled")
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_command_with_optional_cancellation(
    command: list[str],
    *,
    stdin: str,
    cwd: Path | None,
    env: Mapping[str, str],
    timeout_s: float,
    cancel_event: threading.Event | None,
) -> subprocess.CompletedProcess[str]:
    if cancel_event is None:
        return subprocess.run(
            command,
            input=stdin,
            cwd=str(cwd) if cwd else None,
            env=dict(env),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=dict(env),
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout_s
    pending_input: str | None = stdin
    while True:
        if cancel_event.is_set():
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            raise _CommandCancelled(
                returncode=process.returncode,
                stdout=stdout or "",
                stderr=stderr or "",
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                command,
                timeout_s,
                output=stdout or "",
                stderr=stderr or "",
            )
        try:
            stdout, stderr = process.communicate(
                input=pending_input,
                timeout=min(remaining, 0.05),
            )
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            pending_input = None


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=0.5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)


def _response_continuity_errors(
    *,
    start_payload: Mapping[str, Any],
    start_projection: Mapping[str, Any],
    response_payload: Mapping[str, Any],
    response_projection: Mapping[str, Any],
    selected_agent: str,
) -> list[str]:
    errors: list[str] = []
    if response_payload.get("previous_subagent") != selected_agent:
        errors.append(
            "response.previous_subagent must equal selected_agent "
            f"{selected_agent!r}; got {response_payload.get('previous_subagent')!r}"
        )
    if response_projection.get("target") != start_projection.get("target"):
        errors.append(
            "response.github target must match start handoff target "
            f"{start_projection.get('target')!r}; got {response_projection.get('target')!r}"
        )
    start_goal = _goal_key(start_payload.get("goal"))
    response_goal = _goal_key(response_payload.get("goal"))
    if response_goal != start_goal:
        errors.append(
            f"response.goal must match start handoff goal {start_goal!r}; got {response_goal!r}"
        )
    return errors


def _goal_key(value: object) -> tuple[Any, Any, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return (value.get("goal_id"), value.get("goal_version"), value.get("goal_hash"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
