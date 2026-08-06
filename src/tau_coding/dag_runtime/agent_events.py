"""Durable Tau-native agent events in the canonical run store (tau#313).

``DurableAgentEventSink`` persists every ``tau.agent_event.v1`` entry into
``dag-run.sqlite3`` as it occurs — the same canonical journal the scheduler
owns; no second event database. ``load_agent_events`` is the validated
read/cursor surface: it verifies per-attempt sequence contiguity, the hash
chain, payload hashes, and run/plan/goal binding before returning anything,
and fails closed on corruption or stale cursors.

``rebuild_agent_projection`` derives ``tau.agent_projection.v1`` purely from
persisted entries — no live ``AgentNodeRun`` required — so viewers, Herdr,
and operators can reattach after process loss. ``admitted_tool_effects``
recovers effect receipts for idempotent resume: an effect admitted before a
crash is replayed from its receipt, never re-executed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from tau_coding.dag_runtime.agent_node import (
    AgentNodeError,
    tool_request_idempotency_sha256,
)
from tau_coding.dag_runtime.agent_projection import (
    LIFECYCLE_FROM_EVENT,
    TERMINAL_LIFECYCLES,
    permitted_actions,
)
from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.run_store import (
    AGENT_EVENT_JOURNAL_ENTRY_SCHEMA,
    DagRunLease,
    SqliteDagRunStore,
)

AGENT_PROJECTION_SCHEMA = "tau.agent_projection.v1"


class DurableAgentEventSink:
    """Journal sink that persists each agent event into the canonical store.

    SQLite connections are thread-affine and agent loops run on scheduler
    worker threads, so the sink opens its own connection per thread against
    the same WAL store instead of borrowing the scheduler's connection.
    """

    def __init__(
        self,
        *,
        store: SqliteDagRunStore,
        lease: DagRunLease,
        plan_sha256: str,
        goal_hash: str,
        work_order_sha256: str,
        attempt_id: str,
        transport_correlation: Mapping[str, Any] | None = None,
    ) -> None:
        import threading

        self._store_path = store.path
        self._local = threading.local()
        self._lease = lease
        self._binding = {
            "plan_sha256": plan_sha256,
            "goal_hash": goal_hash,
            "work_order_sha256": work_order_sha256,
            "attempt_id": attempt_id,
            "transport_correlation": dict(transport_correlation or {}),
        }
        self.persisted: list[tuple[bool, int]] = []

    def _thread_store(self) -> SqliteDagRunStore:
        # Never reuse the injected store's connection: it belongs to whatever
        # thread constructed it (usually the scheduler's), not this one.
        store = getattr(self._local, "store", None)
        if store is None:
            store = SqliteDagRunStore(self._store_path)
            self._local.store = store
        return store

    def __call__(self, entry: Mapping[str, Any]) -> None:
        self.persisted.append(
            self._thread_store().append_agent_event(
                self._lease, entry=dict(entry), binding=self._binding
            )
        )


def load_agent_events(
    store: SqliteDagRunStore,
    run_id: str,
    *,
    node_id: str | None = None,
    after_agent_seq: int = 0,
) -> tuple[dict[str, Any], ...]:
    """Validated, ordered agent events with cursor catch-up.

    ``after_agent_seq`` is a per-(node, attempt) agent-sequence cursor: 0
    returns the full snapshot; N returns everything after N, gap-free. A
    cursor beyond the persisted head is stale and fails closed.
    """
    if after_agent_seq < 0:
        raise AgentNodeError("agent_event_cursor_invalid", str(after_agent_seq))
    rows = store.load_agent_event_rows(run_id, node_id=node_id)
    chains: dict[tuple[str, str], dict[str, Any]] = {}
    validated: list[dict[str, Any]] = []
    for row in rows:
        payload = row["payload"]
        if payload.get("schema") != AGENT_EVENT_JOURNAL_ENTRY_SCHEMA:
            raise AgentNodeError("agent_event_journal_schema_invalid", row["event_key"])
        entry = payload.get("agent_event")
        binding = payload.get("binding")
        if not isinstance(entry, Mapping) or not isinstance(binding, Mapping):
            raise AgentNodeError("agent_event_journal_malformed", row["event_key"])
        body = {key: value for key, value in entry.items() if key != "sha256"}
        if canonical_sha256(body) != entry.get("sha256"):
            raise AgentNodeError("agent_event_hash_mismatch", row["event_key"])
        if payload.get("agent_event_sha256") != entry.get("sha256"):
            raise AgentNodeError("agent_event_hash_mismatch", row["event_key"])
        for key in ("plan_sha256", "goal_hash", "work_order_sha256", "attempt_id"):
            if not binding.get(key):
                raise AgentNodeError("agent_event_binding_missing", key)
        chain_key = (str(entry["node_id"]), str(binding["attempt_id"]))
        state = chains.setdefault(chain_key, {"seq": 0, "prev_sha": "", "binding": None})
        if int(entry["seq"]) != state["seq"] + 1:
            raise AgentNodeError(
                "agent_event_sequence_gap", f"{chain_key}:{entry['seq']}!={state['seq'] + 1}"
            )
        if entry.get("prev_sha256") != state["prev_sha"]:
            raise AgentNodeError("agent_event_chain_broken", f"{chain_key}:{entry['seq']}")
        frozen_binding = canonical_sha256(dict(binding))
        if state["binding"] is None:
            state["binding"] = frozen_binding
        elif state["binding"] != frozen_binding:
            raise AgentNodeError("agent_event_binding_drift", str(chain_key))
        state["seq"] = int(entry["seq"])
        state["prev_sha"] = str(entry["sha256"])
        validated.append(
            {
                "journal_seq": row["journal_seq"],
                "agent_event": dict(entry),
                "binding": dict(binding),
            }
        )
    if after_agent_seq:
        if node_id is None:
            raise AgentNodeError("agent_event_cursor_requires_node")
        head = max(
            (int(item["agent_event"]["seq"]) for item in validated),
            default=0,
        )
        if after_agent_seq > head:
            raise AgentNodeError(
                "agent_event_cursor_stale", f"after_seq={after_agent_seq} head={head}"
            )
        validated = [
            item for item in validated if int(item["agent_event"]["seq"]) > after_agent_seq
        ]
    return tuple(validated)


def read_agent_events_surface(
    *,
    run_dir: Any,
    node_id: str | None,
    after_seq: int,
) -> dict[str, Any]:
    """Read-only CLI/API surface: snapshot or cursor catch-up from a run dir."""
    from pathlib import Path

    resolved = Path(run_dir).expanduser().resolve()
    database = resolved / "dag-run.sqlite3"
    if not database.is_file():
        database = resolved / "run" / "dag-run.sqlite3"
    if not database.is_file():
        raise RuntimeError(f"no dag-run.sqlite3 under {resolved}")
    store = SqliteDagRunStore(database)
    try:
        run_ids = sorted(
            {
                str(row[0])
                for row in store._connection.execute(  # noqa: SLF001 - read-only surface
                    "SELECT DISTINCT run_id FROM dag_run_events"
                )
            }
        )
        if len(run_ids) != 1:
            raise RuntimeError(f"expected exactly one run in store, found {run_ids}")
        run_id = run_ids[0]
        try:
            entries = load_agent_events(
                store, run_id, node_id=node_id, after_agent_seq=after_seq
            )
        except AgentNodeError as error:
            return {
                "schema": "tau.agent_events_readback.v1",
                "ok": False,
                "run_id": run_id,
                "error": error.code,
                "detail": error.detail,
                "classification": "authoritative",
            }
    finally:
        store.close()
    return {
        "schema": "tau.agent_events_readback.v1",
        "ok": True,
        "run_id": run_id,
        "node_id": node_id,
        "after_seq": after_seq,
        "count": len(entries),
        "classification": "authoritative",
        "events": list(entries),
    }


def admitted_tool_effects(
    entries: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Recover admitted tool-effect receipts keyed by idempotency hash."""
    effects: dict[str, dict[str, Any]] = {}
    for item in entries:
        event = item["agent_event"]
        if event["event_type"] != "tool_effect_recorded":
            continue
        receipt = event["payload"]
        if not receipt.get("ok"):
            continue
        request = receipt.get("tool_request", {})
        effects[tool_request_idempotency_sha256(request)] = dict(receipt)
    return effects


def rebuild_agent_projection(
    entries: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
    node_id: str,
) -> dict[str, Any]:
    """Derive ``tau.agent_projection.v1`` from persisted events alone."""
    lifecycle = "selected"
    turns = 0
    turn_hashes: list[str] = []
    effect_hashes: list[str] = []
    evidence_kinds: list[str] = []
    blocker: str | None = None
    last_seq = 0
    binding: dict[str, Any] | None = None
    work_order_meta: dict[str, Any] = {}
    for item in entries:
        event = item["agent_event"]
        if event["node_id"] != node_id or event.get("run_id") != run_id:
            continue
        binding = dict(item["binding"])
        last_seq = int(event["seq"])
        event_type = event["event_type"]
        lifecycle = LIFECYCLE_FROM_EVENT.get(event_type, lifecycle)
        payload = event.get("payload", {})
        if event_type == "agent_turn_recorded":
            turns += 1
            turn_hashes.append(str(payload.get("sha256")))
        elif event_type == "tool_effect_recorded":
            effect_hashes.append(str(payload.get("sha256")))
        elif event_type == "evidence_recorded":
            evidence_kinds.append(str(payload.get("kind")))
        elif event_type == "agent_node_settled":
            lifecycle = str(payload.get("state"))
    if binding is None:
        raise AgentNodeError("agent_event_projection_empty", f"{run_id}:{node_id}")
    if lifecycle not in TERMINAL_LIFECYCLES and lifecycle != "selected":
        blocker = None
    body = {
        "schema": AGENT_PROJECTION_SCHEMA,
        "run_id": run_id,
        "node_id": node_id,
        "attempt_id": binding["attempt_id"],
        "attempt": None,
        "goal_hash": binding["goal_hash"],
        "plan_sha256": binding["plan_sha256"],
        "journal_seq": last_seq,
        "journal_head_sha256": "",
        "role": work_order_meta.get("role"),
        "harness": "tau_native_agent_loop",
        "transport_profile": binding.get("transport_correlation") or None,
        "lifecycle": lifecycle,
        "turns": turns,
        "turn_receipt_sha256s": turn_hashes,
        "tool_effect_receipt_sha256s": effect_hashes,
        "evidence_kinds": sorted(set(evidence_kinds)),
        "current_blocker": blocker,
        "permitted_operator_actions": permitted_actions(lifecycle, {}),
        "proof_boundary": {
            "derived_from_journal_only": True,
            "rebuilt_from_persisted_events": True,
            "panes_and_transport_status_not_authoritative": True,
            "projection_is_not_semantic_quality_proof": True,
        },
    }
    return {**body, "sha256": canonical_sha256(body)}
