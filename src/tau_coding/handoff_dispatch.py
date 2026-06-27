"""One-step handoff dispatch receipts for Tau agent orchestration."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.generated_ticket import ROUTABLE_AGENTS, project_agent_handoff

TAU_AGENT_HANDOFF_DISPATCH_RECEIPT_SCHEMA = "tau.agent_handoff_dispatch_receipt.v1"
TAU_AGENT_HANDOFF_COMMAND_LOOP_RECEIPT_SCHEMA = "tau.agent_handoff_command_loop_receipt.v1"
TAU_AGENT_DISPATCH_COMMAND_FILENAME = "tau-dispatch-command.json"


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

    stdin = json.dumps(start_payload, sort_keys=True) + "\n"
    env = os.environ.copy()
    env["TAU_HANDOFF_SELECTED_AGENT"] = str(selected_agent)
    if active_goal_hash:
        env["TAU_HANDOFF_ACTIVE_GOAL_HASH"] = active_goal_hash
    if agent_registry_root is not None:
        env["TAU_HANDOFF_AGENT_REGISTRY_ROOT"] = str(agent_registry_root.expanduser().resolve())
    try:
        completed = subprocess.run(
            command,
            input=stdin,
            cwd=str(cwd) if cwd else None,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
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
    if completed.returncode != 0:
        return AgentHandoffDispatchResult(
            ok=False,
            status="BLOCKED",
            selected_agent=str(selected_agent),
            stop_reason="command_failed",
            runner="command",
            start_projection=start_projection,
            command_results=(command_result,),
            errors=(f"command exited {completed.returncode}",),
        )

    try:
        response_payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
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
        start_payload,
        {str(selected_agent): response_payload},
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    )
    return AgentHandoffDispatchResult(
        ok=dispatch.ok,
        status=dispatch.status,
        selected_agent=dispatch.selected_agent,
        stop_reason=dispatch.stop_reason,
        mocked=False,
        live=True,
        runner="command",
        start_projection=dispatch.start_projection,
        response_projection=dispatch.response_projection,
        command_results=(command_result,),
        errors=dispatch.errors,
    )


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
    max_steps: int = 5,
) -> AgentHandoffCommandLoopResult:
    """Run selected agent commands until the route reaches human or fails closed."""

    if max_steps < 1:
        return AgentHandoffCommandLoopResult(
            ok=False,
            status="BLOCKED",
            stop_reason="invalid_max_steps",
            errors=("max_steps must be at least 1",),
        )

    current_payload = start_payload
    dispatches: list[dict[str, Any]] = []
    for step in range(1, max_steps + 1):
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
            return AgentHandoffCommandLoopResult(
                ok=True,
                status="WAITING",
                step_count=step - 1,
                terminal_agent=selected_agent,
                stop_reason="next_agent_is_human",
                dispatches=tuple(dispatches),
            )
        if selected_agent is None:
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
            )
        except ValueError as exc:
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=selected_agent,
                stop_reason="missing_agent_command_spec",
                dispatches=tuple(dispatches),
                errors=(str(exc),),
            )

        dispatch = dispatch_agent_handoff_command_once(
            current_payload,
            spec["command"],
            timeout_s=spec["timeout_s"],
            cwd=spec["cwd"],
            active_goal_hash=active_goal_hash,
            agent_registry_root=agent_registry_root,
        )
        dispatch_payload = dispatch.as_dict()
        dispatch_payload["loop_step"] = step
        dispatches.append(dispatch_payload)
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
            return AgentHandoffCommandLoopResult(
                ok=False,
                status="BLOCKED",
                step_count=step,
                terminal_agent=dispatch.selected_agent,
                stop_reason="invalid_command_json",
                dispatches=tuple(dispatches),
                errors=(f"step[{step}]: command stdout JSON root must be an object",),
            )
        current_payload = next_payload
        next_projection = project_agent_handoff(
            current_payload,
            active_goal_hash=active_goal_hash,
            agent_registry_root=agent_registry_root,
        )
        if not next_projection.ok:
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
            return AgentHandoffCommandLoopResult(
                ok=True,
                status="WAITING",
                step_count=step,
                terminal_agent="human",
                stop_reason="next_agent_is_human",
                dispatches=tuple(dispatches),
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
    max_steps: int = 5,
) -> AgentHandoffCommandLoopResult:
    """Write one command-backed loop receipt plus per-step dispatch artifacts."""

    receipt_dir.mkdir(parents=True, exist_ok=True)
    loop = run_agent_handoff_command_loop(
        start_payload,
        agent_registry_root=agent_registry_root,
        command_spec_root=command_spec_root,
        active_goal_hash=active_goal_hash,
        max_steps=max_steps,
    )
    artifacts: list[str] = []
    for dispatch in loop.dispatches:
        step = int(dispatch["loop_step"])
        step_path = receipt_dir / f"command-loop-step-{step:03d}.receipt.json"
        _write_json(step_path, dispatch)
        artifacts.append(str(step_path))
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


def load_agent_dispatch_command_spec(
    root: Path,
    agent_id: str,
    *,
    command_spec_root: Path | None = None,
) -> dict[str, object]:
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
        command_spec_root.expanduser().resolve() / agent_id
        if command_spec_root
        else agent_dir
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
    return validate_command_dispatch_spec(payload, label=f"agent dispatch command spec {spec_path}")


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
    timeout_value = payload.get("timeout_s", 30.0)
    if isinstance(timeout_value, bool) or not isinstance(timeout_value, (int, float)):
        raise ValueError(f"{label} timeout_s must be a positive number")
    timeout_s = float(timeout_value)
    if timeout_s <= 0:
        raise ValueError(f"{label} timeout_s must be a positive number")
    cwd_value = payload.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else None
    return {"command": command, "timeout_s": timeout_s, "cwd": cwd}


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
            f"response.goal must match start handoff goal {start_goal!r}; "
            f"got {response_goal!r}"
        )
    return errors


def _goal_key(value: object) -> tuple[Any, Any, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return (value.get("goal_id"), value.get("goal_version"), value.get("goal_hash"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
