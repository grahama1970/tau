"""Deterministic crash/replay/cursor tests for tau#313 durable agent events."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tau_agent import AgentTool, AgentToolResult, AssistantMessage, ToolCall
from tau_ai import FakeProvider, ProviderResponseEndEvent, ProviderResponseStartEvent
from tau_coding.dag_runtime.agent_events import (
    DurableAgentEventSink,
    admitted_tool_effects,
    load_agent_events,
    read_agent_events_surface,
    rebuild_agent_projection,
)
from tau_coding.dag_runtime.agent_node import AgentNodeError, AgentNodeRun, ToolPolicy
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.run_store import SqliteDagRunStore

GOAL = "g" * 64
RUN_ID = "durable-run"


@pytest.fixture
def store(tmp_path: Path) -> Any:
    s = SqliteDagRunStore(tmp_path / "dag-run.sqlite3")
    yield s
    s.close()


def _lease(store: SqliteDagRunStore, tmp_path: Path | None = None) -> Any:
    base = Path(store.path).parent
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": RUN_ID,
            "run_dir": str(base / "run"),
            "nodes": [
                {
                    "node_id": "node-a",
                    "role": "node-a",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(base / "receipts" / "node-a.json"),
                    "timeout_seconds": 10,
                    "max_attempts": 3,
                }
            ],
        },
        source_path=base / "dag.json",
    )
    return store.acquire_run(plan=plan, run_id=RUN_ID, owner_id="test", ttl_seconds=60)


def _work_order(attempt: int = 1) -> dict[str, Any]:
    return {
        "schema": "tau.agent_node.v1",
        "run_id": RUN_ID,
        "node_id": "node-a",
        "attempt_id": f"attempt-{attempt}",
        "attempt": attempt,
        "goal_hash": GOAL,
        "plan_sha256": "p" * 64,
        "model": "fake",
    }


def _sink(store: SqliteDagRunStore, lease: Any, attempt: int = 1) -> DurableAgentEventSink:
    return DurableAgentEventSink(
        store=store,
        lease=lease,
        plan_sha256="p" * 64,
        goal_hash=GOAL,
        work_order_sha256=canonical_sha256(_work_order(attempt)),
        attempt_id=f"attempt-{attempt}",
    )


def _tool_stream(arguments: dict[str, Any]) -> list[Any]:
    return [
        ProviderResponseStartEvent(model="fake"),
        ProviderResponseEndEvent(
            message=AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call-1", name="write_file", arguments=arguments)],
            ),
            finish_reason="tool_calls",
        ),
    ]


def _text_stream(text: str) -> list[Any]:
    return [
        ProviderResponseStartEvent(model="fake"),
        ProviderResponseEndEvent(message=AssistantMessage(content=text), finish_reason="stop"),
    ]


def _counting_tool(counter: dict[str, int]) -> AgentTool:
    async def _executor(arguments: Any, signal: Any = None) -> AgentToolResult:
        counter["calls"] = counter.get("calls", 0) + 1
        return AgentToolResult(
            tool_call_id="", name="write_file", ok=True, content=f"wrote {arguments['path']}"
        )

    return AgentTool(
        name="write_file",
        description="Write a file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        executor=_executor,
    )


def _run(
    store: SqliteDagRunStore,
    lease: Any,
    streams: list[list[Any]],
    *,
    attempt: int = 1,
    counter: dict[str, int] | None = None,
    prior_effects: dict[str, Any] | None = None,
) -> AgentNodeRun:
    return AgentNodeRun(
        work_order=_work_order(attempt),
        policy=ToolPolicy(goal_hash=GOAL, allowed_tools=("write_file",)),
        provider=FakeProvider(streams),
        tools=[_counting_tool(counter if counter is not None else {})],
        event_sink=_sink(store, lease, attempt),
        prior_tool_effects=dict(prior_effects or {}),
    )


@pytest.mark.anyio
async def test_events_persisted_as_they_occur_and_effect_before_next_turn(store: Any) -> None:
    lease = _lease(store)
    run = _run(store, lease, [_tool_stream({"path": "a.txt"}), _text_stream("done writing")])
    await run.run("Write the file.")
    run.settle()
    entries = load_agent_events(store, RUN_ID, node_id="node-a")
    types = [item["agent_event"]["event_type"] for item in entries]
    assert types[0] == "agent_node_started"
    assert "agent_node_settled" in types
    # Crash requirement: the tool effect is durable BEFORE the next model
    # turn is recorded.
    assert types.index("tool_effect_recorded") < types.index("agent_turn_recorded", 1)
    # In-store representation binds lineage.
    binding = entries[0]["binding"]
    assert binding["goal_hash"] == GOAL and binding["plan_sha256"] == "p" * 64


@pytest.mark.anyio
async def test_crash_after_effect_resume_does_not_repeat_effect(store: Any) -> None:
    lease = _lease(store)
    counter: dict[str, int] = {}

    class _CrashAfterTool(Exception):
        pass

    run = _run(store, lease, [_tool_stream({"path": "a.txt"})], counter=counter)
    # Only one stream: the loop errors after the tool turn — simulating a
    # worker death after the admitted effect, before the next model turn.
    await run.run("Write the file.")
    assert counter["calls"] == 1
    persisted = load_agent_events(store, RUN_ID, node_id="node-a")
    effects = admitted_tool_effects(persisted)
    assert len(effects) == 1

    # Restart: attempt 2 with the recovered prior effects. Same tool request
    # must be replayed from the receipt, not executed again.
    resumed = _run(
        store,
        lease,
        [_tool_stream({"path": "a.txt"}), _text_stream("done after resume")],
        attempt=2,
        counter=counter,
        prior_effects=effects,
    )
    await resumed.run("Write the file.")
    settlement = resumed.settle()
    assert settlement["state"] == "completed"
    assert counter["calls"] == 1  # NOT re-executed
    resumed_types = [
        item["agent_event"]["event_type"]
        for item in load_agent_events(store, RUN_ID, node_id="node-a")
        if item["binding"]["attempt_id"] == "attempt-2"
    ]
    assert "tool_effect_replayed" in resumed_types
    assert "tool_effect_recorded" not in resumed_types


@pytest.mark.anyio
async def test_projection_rebuilds_without_live_run(store: Any) -> None:
    lease = _lease(store)
    run = _run(store, lease, [_tool_stream({"path": "a.txt"}), _text_stream("finished")])
    await run.run("Write the file.")
    # Crash BEFORE settlement: rebuild purely from the store.
    entries = load_agent_events(store, RUN_ID, node_id="node-a")
    projection = rebuild_agent_projection(entries, run_id=RUN_ID, node_id="node-a")
    assert projection["schema"] == "tau.agent_projection.v1"
    assert projection["lifecycle"] not in ("completed", "failed", "cancelled")
    assert projection["turns"] == 2
    assert len(projection["tool_effect_receipt_sha256s"]) == 1
    assert projection["proof_boundary"]["rebuilt_from_persisted_events"] is True


@pytest.mark.anyio
async def test_duplicate_event_delivery_is_idempotent(store: Any) -> None:
    lease = _lease(store)
    run = _run(store, lease, [_text_stream("answer")])
    await run.run("Answer.")
    entry = run.journal.entries[0]
    before = len(load_agent_events(store, RUN_ID, node_id="node-a"))
    appended, seq = store.append_agent_event(
        lease,
        entry=entry,
        binding={
            "plan_sha256": "p" * 64,
            "goal_hash": GOAL,
            "work_order_sha256": canonical_sha256(_work_order()),
            "attempt_id": "attempt-1",
            "transport_correlation": {},
        },
    )
    assert appended is False  # byte-identical duplicate: same canonical seq
    after = len(load_agent_events(store, RUN_ID, node_id="node-a"))
    assert after == before


@pytest.mark.anyio
async def test_corrupted_chain_fails_closed(store: Any) -> None:
    lease = _lease(store)
    run = _run(store, lease, [_text_stream("answer")])
    await run.run("Answer.")
    # Forge a conflicting event for an existing (node, attempt, seq) key.
    forged = dict(run.journal.entries[0])
    forged["payload"] = {"tampered": True}
    body = {k: v for k, v in forged.items() if k != "sha256"}
    forged["sha256"] = canonical_sha256(body)
    with pytest.raises(Exception, match="dag_run_event_conflict"):
        store.append_agent_event(
            lease,
            entry=forged,
            binding={
                "plan_sha256": "p" * 64,
                "goal_hash": GOAL,
                "work_order_sha256": canonical_sha256(_work_order()),
                "attempt_id": "attempt-1",
                "transport_correlation": {},
            },
        )
    # An out-of-sequence append (gap) fails replay validation.
    gap_entry = dict(run.journal.entries[-1])
    gap_entry["seq"] = int(gap_entry["seq"]) + 5
    gap_body = {k: v for k, v in gap_entry.items() if k != "sha256"}
    gap_entry["sha256"] = canonical_sha256(gap_body)
    store.append_agent_event(
        lease,
        entry=gap_entry,
        binding={
            "plan_sha256": "p" * 64,
            "goal_hash": GOAL,
            "work_order_sha256": canonical_sha256(_work_order()),
            "attempt_id": "attempt-1",
            "transport_correlation": {},
        },
    )
    with pytest.raises(AgentNodeError) as excinfo:
        load_agent_events(store, RUN_ID, node_id="node-a")
    assert excinfo.value.code == "agent_event_sequence_gap"


@pytest.mark.anyio
async def test_cursor_catchup_is_ordered_and_stale_cursor_rejected(store: Any) -> None:
    lease = _lease(store)
    run = _run(store, lease, [_tool_stream({"path": "a.txt"}), _text_stream("done")])
    await run.run("Write the file.")
    run.settle()
    snapshot = load_agent_events(store, RUN_ID, node_id="node-a")
    head = int(snapshot[-1]["agent_event"]["seq"])
    cursor = head // 2
    catchup = load_agent_events(store, RUN_ID, node_id="node-a", after_agent_seq=cursor)
    seqs = [int(item["agent_event"]["seq"]) for item in catchup]
    assert seqs == list(range(cursor + 1, head + 1))  # ordered, gap-free
    with pytest.raises(AgentNodeError) as excinfo:
        load_agent_events(store, RUN_ID, node_id="node-a", after_agent_seq=head + 10)
    assert excinfo.value.code == "agent_event_cursor_stale"


@pytest.mark.anyio
async def test_read_surface_reports_from_run_dir(store: Any, tmp_path: Path) -> None:
    lease = _lease(store)
    run = _run(store, lease, [_text_stream("answer")])
    await run.run("Answer.")
    run.settle()
    payload = read_agent_events_surface(
        run_dir=tmp_path, node_id="node-a", after_seq=0
    )
    assert payload["ok"] is True
    assert payload["classification"] == "authoritative"
    assert payload["count"] == len(run.journal.entries)


@pytest.mark.anyio
async def test_settlement_still_gated_on_required_evidence(store: Any) -> None:
    lease = _lease(store)
    work_order = _work_order()
    work_order["required_evidence"] = ["test_run_receipt"]
    run = AgentNodeRun(
        work_order=work_order,
        policy=ToolPolicy(goal_hash=GOAL, allowed_tools=()),
        provider=FakeProvider([_text_stream("provider says complete!")]),
        tools=[],
        event_sink=_sink(store, lease),
    )
    await run.run("Do it.")
    settlement = run.settle()
    assert settlement["state"] == "failed"
    entries = load_agent_events(store, RUN_ID, node_id="node-a")
    settled = [
        item for item in entries if item["agent_event"]["event_type"] == "agent_node_settled"
    ]
    assert settled and settled[-1]["agent_event"]["payload"]["state"] == "failed"
