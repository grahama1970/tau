"""Durable bounded goal-run controller for Tau handoff loops."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.handoff_dispatch import write_agent_handoff_command_loop_receipt

TAU_GOAL_RUN_RECEIPT_SCHEMA = "tau.goal_run_receipt.v1"

PASS_STATUSES = frozenset({"PASS", "COMPLETE", "COMPLETED", "DONE"})


@dataclass(frozen=True, slots=True)
class GoalCompletionEvaluation:
    """Deterministic evaluation of a latest handoff against explicit criteria."""

    solved: bool
    reason: str
    result_status: str | None
    terminal_human: bool
    evidence_count: int
    required_criteria: tuple[str, ...] = ()
    completed_criteria: tuple[str, ...] = ()
    missing_criteria: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "tau.goal_completion_evaluation.v1",
            "solved": self.solved,
            "reason": self.reason,
            "result_status": self.result_status,
            "terminal_human": self.terminal_human,
            "evidence_count": self.evidence_count,
            "required_criteria": list(self.required_criteria),
            "completed_criteria": list(self.completed_criteria),
            "missing_criteria": list(self.missing_criteria),
            "semantic_truth": "NOT_CLAIMED",
        }


def run_goal_until_complete(
    *,
    start_path: Path,
    receipt_dir: Path,
    agent_registry_root: Path,
    timeout_s: float,
    active_goal_hash: str | None = None,
    command_spec_root: Path | None = None,
    command_policy_path: Path | None = None,
    goal_helper_path: Path | None = None,
    max_steps_per_tick: int = 1,
    max_ticks: int | None = None,
    poll_interval_s: float = 0.0,
) -> dict[str, Any]:
    """Run bounded handoff loop ticks until explicit criteria pass or deadline expires."""

    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if max_steps_per_tick < 1:
        raise ValueError("max_steps_per_tick must be at least 1")
    if max_ticks is not None and max_ticks < 1:
        raise ValueError("max_ticks must be at least 1 when supplied")
    if poll_interval_s < 0:
        raise ValueError("poll_interval_s must be non-negative")

    resolved_receipt_dir = receipt_dir.expanduser().resolve()
    resolved_receipt_dir.mkdir(parents=True, exist_ok=True)
    start_payload = _load_json_object(start_path, label="start handoff")
    goal_helper = (
        _load_json_object(goal_helper_path, label="goal helper") if goal_helper_path else {}
    )
    required_criteria = _completion_criteria(goal_helper)

    current_payload = start_payload
    started_monotonic = time.monotonic()
    deadline_monotonic = started_monotonic + timeout_s
    ticks: list[dict[str, Any]] = []
    final_evaluation = evaluate_goal_completion(
        current_payload,
        required_criteria=required_criteria,
    )
    stop_reason = final_evaluation.reason

    tick_index = 0
    while not final_evaluation.solved:
        now = time.monotonic()
        if now >= deadline_monotonic:
            stop_reason = "deadline_expired"
            break
        if max_ticks is not None and tick_index >= max_ticks:
            stop_reason = "max_ticks_exhausted"
            break

        tick_index += 1
        tick_dir = resolved_receipt_dir / f"tick-{tick_index:03d}"
        loop = write_agent_handoff_command_loop_receipt(
            current_payload,
            tick_dir,
            agent_registry_root=agent_registry_root,
            command_spec_root=command_spec_root,
            command_policy_path=command_policy_path,
            active_goal_hash=active_goal_hash,
            max_steps=max_steps_per_tick,
            deadline_monotonic=deadline_monotonic,
        )
        loop_payload = loop.as_dict()
        loop_receipt_path = tick_dir / "command-loop-receipt.json"
        latest_payload = _latest_response_payload(loop_payload)
        if latest_payload is not None:
            current_payload = latest_payload

        final_evaluation = evaluate_goal_completion(
            current_payload,
            required_criteria=required_criteria,
        )
        tick_summary = {
            "tick": tick_index,
            "loop_receipt": str(loop_receipt_path),
            "loop_status": loop_payload.get("status"),
            "loop_ok": loop_payload.get("ok"),
            "loop_stop_reason": loop_payload.get("stop_reason"),
            "step_count": loop_payload.get("step_count"),
            "latest_handoff_updated": latest_payload is not None,
            "completion_evaluation": final_evaluation.as_dict(),
        }
        ticks.append(tick_summary)

        if not final_evaluation.solved and time.monotonic() >= deadline_monotonic:
            stop_reason = "deadline_expired"
            break
        if final_evaluation.solved:
            stop_reason = "completion_criteria_satisfied"
            break
        if (
            loop_payload.get("ok") is not True
            and loop_payload.get("stop_reason") != "max_steps_exhausted"
        ):
            stop_reason = str(loop_payload.get("stop_reason") or "tick_blocked")
            break
        if _next_agent_name(current_payload) == "human":
            stop_reason = "terminal_human_without_completion"
            break
        if poll_interval_s:
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                stop_reason = "deadline_expired"
                break
            time.sleep(min(poll_interval_s, remaining))

    duration_seconds = time.monotonic() - started_monotonic
    status = "PASS" if final_evaluation.solved else (
        "TIMEOUT" if stop_reason == "deadline_expired" else "BLOCKED"
    )
    receipt = {
        "schema": TAU_GOAL_RUN_RECEIPT_SCHEMA,
        "ok": final_evaluation.solved,
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "started_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(duration_seconds, 6),
        "timeout_s": timeout_s,
        "deadline_exceeded": stop_reason == "deadline_expired",
        "tick_count": len(ticks),
        "max_steps_per_tick": max_steps_per_tick,
        "max_ticks": max_ticks,
        "stop_reason": stop_reason,
        "active_goal_hash": active_goal_hash,
        "start_path": str(start_path.expanduser().resolve()),
        "goal_helper_path": (
            str(goal_helper_path.expanduser().resolve()) if goal_helper_path else None
        ),
        "receipt_dir": str(resolved_receipt_dir),
        "ticks": ticks,
        "completion_evaluation": final_evaluation.as_dict(),
        "current_handoff": current_payload,
        "proof_scope": {
            "proves": [
                "Tau repeatedly invoked bounded command-loop ticks.",
                (
                    "Tau enforced a wall-clock deadline across repeated ticks "
                    "and capped each dispatched command timeout by the remaining deadline."
                ),
                "Tau evaluated explicit completion criteria from the latest handoff.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "The task is truly solved outside the explicit completion criteria.",
                "Unbounded autonomous operation.",
                "Human acceptance.",
            ],
        },
    }
    _write_json(resolved_receipt_dir / "goal-run-receipt.json", receipt)
    return receipt


def evaluate_goal_completion(
    handoff: Mapping[str, Any],
    *,
    required_criteria: tuple[str, ...] = (),
) -> GoalCompletionEvaluation:
    """Evaluate completion from handoff content, not from a loop receipt status."""

    result = handoff.get("result")
    result_status = None
    evidence_count = 0
    completed_criteria: tuple[str, ...] = ()
    if isinstance(result, Mapping):
        raw_status = result.get("status")
        if isinstance(raw_status, str):
            result_status = raw_status.strip().upper()
        evidence = result.get("evidence")
        if isinstance(evidence, list):
            evidence_count = len(
                [item for item in evidence if isinstance(item, str) and item.strip()]
            )
        completed_criteria = _string_tuple(result.get("completed_criteria"))

    context = handoff.get("context")
    if not completed_criteria and isinstance(context, Mapping):
        completed_criteria = _string_tuple(context.get("completed_criteria"))

    terminal_human = _next_agent_name(handoff) == "human"
    missing = tuple(item for item in required_criteria if item not in set(completed_criteria))
    if not terminal_human:
        reason = "next_agent_not_human"
    elif result_status not in PASS_STATUSES:
        reason = "result_status_not_pass"
    elif evidence_count < 1:
        reason = "missing_result_evidence"
    elif missing:
        reason = "missing_completion_criteria"
    else:
        reason = "completion_criteria_satisfied"

    return GoalCompletionEvaluation(
        solved=reason == "completion_criteria_satisfied",
        reason=reason,
        result_status=result_status,
        terminal_human=terminal_human,
        evidence_count=evidence_count,
        required_criteria=required_criteria,
        completed_criteria=completed_criteria,
        missing_criteria=missing,
    )


def _latest_response_payload(loop_payload: Mapping[str, Any]) -> dict[str, Any] | None:
    dispatches = loop_payload.get("dispatches")
    if not isinstance(dispatches, list):
        return None
    for dispatch in reversed(dispatches):
        if not isinstance(dispatch, Mapping):
            continue
        command_results = dispatch.get("command_results")
        if not isinstance(command_results, list) or not command_results:
            continue
        first = command_results[0]
        if not isinstance(first, Mapping):
            continue
        stdout = first.get("stdout")
        if not isinstance(stdout, str) or not stdout.strip():
            continue
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _completion_criteria(goal_helper: Mapping[str, Any]) -> tuple[str, ...]:
    return _string_tuple(goal_helper.get("completion_criteria"))


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _next_agent_name(payload: Mapping[str, Any]) -> str | None:
    next_agent = payload.get("next_agent")
    if isinstance(next_agent, Mapping):
        name = next_agent.get("name")
        return name if isinstance(name, str) else None
    return next_agent if isinstance(next_agent, str) else None


def _load_json_object(path: Path | None, *, label: str) -> dict[str, Any]:
    if path is None:
        return {}
    resolved = path.expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable: {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be a JSON object: {resolved}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
