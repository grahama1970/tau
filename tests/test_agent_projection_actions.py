"""Deterministic tests for tau#309 run projections and operator actions."""

from __future__ import annotations

from typing import Any

import pytest

from tau_agent import AssistantMessage
from tau_ai import FakeProvider, ProviderResponseEndEvent, ProviderResponseStartEvent
from tau_coding.dag_runtime.agent_node import AgentNodeError, AgentNodeRun, ToolPolicy
from tau_coding.dag_runtime.agent_projection import (
    OperatorActionError,
    apply_operator_action,
    project_agent_node,
    project_run,
    validate_projection_readback,
)

GOAL = "g" * 64


def _work_order(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": "tau.agent_node.v1",
        "run_id": "run-1",
        "node_id": "node-a",
        "attempt_id": "attempt-1",
        "attempt": 1,
        "goal_hash": GOAL,
        "plan_sha256": "p" * 64,
        "model": "fake",
        "harness": "tau_native_agent_loop",
        "role": "frontend",
        "required_evidence": [],
        "transport_profile_selection": {
            "selected_profile": {"profile_id": "claude-model-turn", "provider": "anthropic-oauth"}
        },
    }
    base.update(overrides)
    return base


def _text_stream(text: str) -> list[Any]:
    return [
        ProviderResponseStartEvent(model="fake"),
        ProviderResponseEndEvent(message=AssistantMessage(content=text), finish_reason="stop"),
    ]


def _fresh_run(**overrides: Any) -> AgentNodeRun:
    return AgentNodeRun(
        work_order=_work_order(**overrides),
        policy=ToolPolicy(goal_hash=GOAL, allowed_tools=()),
        provider=FakeProvider([_text_stream("output")]),
        tools=[],
    )


async def _completed_run(**overrides: Any) -> AgentNodeRun:
    run = _fresh_run(**overrides)
    await run.run("Do the work.")
    return run


def _action(run: AgentNodeRun, action: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": "tau.operator_action_request.v1",
        "action": action,
        "actor": "human_operator",
        "run_id": run.work_order["run_id"],
        "node_id": run.work_order["node_id"],
        "goal_hash": run.work_order["goal_hash"],
        "observed_journal_seq": run.journal.entries[-1]["seq"] if run.journal.entries else 0,
    }
    base.update(overrides)
    return base


@pytest.mark.anyio
async def test_projection_covers_lifecycle_and_transport() -> None:
    run = await _completed_run()
    settlement = run.settle()
    projection = project_agent_node(run, settlement=settlement)
    assert projection["lifecycle"] == "completed"
    assert projection["transport_profile"]["profile_id"] == "claude-model-turn"
    assert projection["journal_seq"] == run.journal.entries[-1]["seq"]
    assert projection["permitted_operator_actions"] == []
    validate_projection_readback(projection, run=run)


@pytest.mark.anyio
async def test_projection_terminal_failed_permits_retry() -> None:
    run = _fresh_run()
    run.provider = FakeProvider([_text_stream("")])
    await run.run("Do the work.")
    settlement = run.settle()
    assert settlement["state"] == "failed"
    projection = project_agent_node(run, settlement=settlement)
    assert projection["lifecycle"] == "failed"
    assert projection["permitted_operator_actions"] == ["retry_requested"]
    assert projection["current_blocker"] == "empty_terminal_output"


@pytest.mark.anyio
async def test_run_projection_binds_goal_and_run_ids() -> None:
    run = await _completed_run()
    projection = project_agent_node(run, settlement=run.settle())
    run_projection = project_run(
        run_id="run-1", dag_id="dag-1", goal_hash=GOAL, node_projections=[projection]
    )
    assert run_projection["schema"] == "tau.run_projection.v1"
    with pytest.raises(AgentNodeError) as excinfo:
        project_run(
            run_id="other-run", dag_id="dag-1", goal_hash=GOAL, node_projections=[projection]
        )
    assert excinfo.value.code == "run_projection_run_id_mismatch"
    with pytest.raises(AgentNodeError) as excinfo:
        project_run(
            run_id="run-1", dag_id="dag-1", goal_hash="x" * 64, node_projections=[projection]
        )
    assert excinfo.value.code == "run_projection_goal_mismatch"


@pytest.mark.anyio
async def test_projection_readback_rejects_stale_and_mismatched() -> None:
    run = await _completed_run()
    projection = project_agent_node(run)
    run.journal.append("evidence_recorded", {"kind": "late", "sha256": "0" * 64})
    with pytest.raises(AgentNodeError) as excinfo:
        validate_projection_readback(projection, run=run)
    assert excinfo.value.code == "projection_stale_journal_seq"

    fresh = await _completed_run()
    projection = project_agent_node(fresh)
    forged = dict(projection)
    forged["node_id"] = "node-z"
    with pytest.raises(AgentNodeError) as excinfo:
        validate_projection_readback(forged, run=fresh)
    assert excinfo.value.code == "projection_identity_mismatch"
    unbound = dict(projection)
    unbound["goal_hash"] = ""
    with pytest.raises(AgentNodeError) as excinfo:
        validate_projection_readback(unbound, run=fresh)
    assert excinfo.value.code == "projection_goal_binding_missing"


@pytest.mark.anyio
async def test_cancel_action_changes_journal_and_writes_receipt() -> None:
    run = _fresh_run()
    receipt = apply_operator_action(run=run, request=_action(run, "cancel"))
    assert receipt["outcome"] == "applied"
    assert receipt["journal_transition"]["journal_changed"] is True
    assert run.cancellation.is_cancelled()
    events = [entry["event_type"] for entry in run.journal.entries]
    assert "agent_cancelled" in events


@pytest.mark.anyio
async def test_steer_action_queued_for_next_turn() -> None:
    run = _fresh_run()
    receipt = apply_operator_action(
        run=run,
        request=_action(run, "add_next_turn_instruction", instruction="Cover the edge case."),
    )
    assert receipt["outcome"] == "queued_for_next_turn"
    assert run.steering_queue == ["Cover the edge case."]


@pytest.mark.anyio
async def test_opaque_compat_steer_requires_fork() -> None:
    run = _fresh_run(harness="opaque_agent_compat")
    receipt = apply_operator_action(
        run=run, request=_action(run, "add_next_turn_instruction", instruction="x")
    )
    assert receipt["outcome"] == "fork_required"
    assert run.steering_queue == []


@pytest.mark.anyio
async def test_pause_is_typed_unsupported() -> None:
    run = _fresh_run()
    receipt = apply_operator_action(run=run, request=_action(run, "pause"))
    assert receipt["outcome"] == "unsupported"


@pytest.mark.anyio
async def test_retry_on_failed_node_authorized_and_exhaustion_rejected() -> None:
    run = _fresh_run()
    run.provider = FakeProvider([_text_stream("")])
    await run.run("Do the work.")
    run.settle()
    receipt = apply_operator_action(run=run, request=_action(run, "retry_requested"))
    assert receipt["journal_transition"]["journal_changed"] is True
    events = [entry["event_type"] for entry in run.journal.entries]
    assert "retry_authorized" in events

    exhausted = _fresh_run(attempt=3, attempt_id="attempt-3")
    exhausted.provider = FakeProvider([_text_stream("")])
    await exhausted.run("Do the work.")
    exhausted.settle()
    with pytest.raises(OperatorActionError) as excinfo:
        apply_operator_action(run=exhausted, request=_action(exhausted, "retry_requested"))
    assert excinfo.value.code == "operator_action_retry_exhausted"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda run: {"schema": "tau.pane_input.v1"}, "operator_action_schema_invalid"),
        (lambda run: {"action": "rewrite_goal"}, "operator_action_unknown"),
        (lambda run: {"actor": "random_pane_writer"}, "operator_action_unauthorized_actor"),
        (lambda run: {"node_id": "node-z"}, "operator_action_identity_mismatch"),
        (lambda run: {"goal_hash": "x" * 64}, "operator_action_goal_mismatch"),
        (lambda run: {"observed_journal_seq": 0}, "operator_action_stale_journal_seq"),
    ],
)
async def test_action_negative_paths(mutator: Any, code: str) -> None:
    run = await _completed_run()
    request = _action(run, "cancel")
    request.update(mutator(run))
    with pytest.raises(OperatorActionError) as excinfo:
        apply_operator_action(run=run, request=request)
    assert excinfo.value.code == code


@pytest.mark.anyio
async def test_terminal_node_rejects_non_retry_actions() -> None:
    run = await _completed_run()
    run.settle()
    with pytest.raises(OperatorActionError) as excinfo:
        apply_operator_action(run=run, request=_action(run, "cancel"))
    assert excinfo.value.code == "operator_action_node_terminal"
    with pytest.raises(OperatorActionError) as excinfo:
        apply_operator_action(run=run, request=_action(run, "retry_requested"))
    assert excinfo.value.code == "operator_action_retry_not_applicable"


@pytest.mark.anyio
async def test_pane_only_mutation_does_not_count_as_action() -> None:
    """Changing anything outside the journal leaves projections unchanged."""
    run = await _completed_run()
    before = project_agent_node(run)
    run.work_order["pane_text"] = "FAKE PANE SAYS DONE"  # side channel, not journal
    after = project_agent_node(run)
    assert before["journal_seq"] == after["journal_seq"]
    assert before["lifecycle"] == after["lifecycle"]
