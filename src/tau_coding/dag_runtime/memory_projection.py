"""Outbox projection of accepted Tau outcomes into graph-memory (contract A3/220).

Transactional-outbox pattern (ratified in round 3): the projection *request*
commits in the SAME SQLite transaction as the accepted outcome, so SQLite
execution state stays authoritative and a graph-memory outage can never block
or redefine scheduler settlement. A separate relay drains the outbox into the
governed graph-memory-operator API; delivery is at-least-once, deduplicated by
a stable projection idempotency key so retries cannot create duplicate graph
facts.

Only accepted outcomes, human decisions, durable failure classifications,
provenance, and content-addressed evidence references are projected — never
raw scheduler state. Projection state (pending/projected/degraded/
retryable_failed/permanently_rejected) is viewer-visible but is explicitly not
settlement authority.

This module owns the outbox table and the relay contract. It never opens an
ArangoDB client; the relay is handed an injectable sender so the governed
graph-memory-operator API (or an explicit degraded stub) is the only backend.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib import error, request

from tau_coding.dag_runtime.run_store import DagRunLease, SqliteDagRunStore

TAU_ORCHESTRATION_EPISODE_SCHEMA = "memory.tau_orchestration_episode.v1"
TAU_ORCHESTRATION_EPISODE_KIND = "tau_orchestration_episode"
TAU_EPISODE_PROJECTION_VERSION = "tau.episode_projection.v1"
TAU_EPISODE_POLICY_VERSION = "tau.memory_projection_policy.v1"
TAU_EPISODE_DATA_BOUNDARY_VERSION = "tau.data_boundary.public_redacted.v1"
TAU_EPISODE_REDACTION_VERSION = "tau.redaction.public_receipt_refs.v1"
TAU_EPISODE_REQUIRED_FIELDS = (
    "_key",
    "run_id",
    "dag_id",
    "node_id",
    "attempt_id",
    "goal_hash",
    "journal_sequence",
    "journal_head_hash",
    "source_outbox_row",
    "source_receipt_refs",
    "source_receipt_hashes",
    "policy_version",
    "data_boundary_version",
    "redaction_version",
    "validity_state",
)
TAU_EPISODE_IMMUTABLE_FIELDS = (
    "_key",
    "schema",
    "run_id",
    "dag_id",
    "node_id",
    "attempt_id",
    "goal_hash",
    "journal_sequence",
    "journal_head_hash",
    "source_outbox_row",
    "source_receipt_refs",
    "source_receipt_hashes",
    "policy_version",
    "data_boundary_version",
    "redaction_version",
)
TAU_EPISODE_FORBIDDEN_FIELDS = frozenset(
    {
        "raw_prompt",
        "hidden_reasoning",
        "chain_of_thought",
        "secret",
        "secrets",
        "token",
        "api_key",
        "stdout",
        "stderr",
        "environment",
        "env",
        "terminal_text",
        "pane_text",
    }
)

PROJECTION_STATES = (
    "pending",
    "projected",
    "degraded",
    "retryable_failed",
    "permanently_rejected",
)

_OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS memory_projection_outbox (
    projection_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    fact_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending','projected','degraded','retryable_failed','permanently_rejected')
    ),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class MemoryProjectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectionResult:
    projection_key: str
    state: str
    attempts: int


def _now() -> str:
    return datetime.now(UTC).isoformat()


def projection_key_for(run_id: str, node_id: str, attempt_id: str, fact_kind: str) -> str:
    basis = json.dumps(
        {"run_id": run_id, "node_id": node_id, "attempt_id": attempt_id, "fact_kind": fact_kind},
        sort_keys=True,
    )
    return "mp-" + hashlib.sha256(basis.encode()).hexdigest()[:32]


def _projection_key(run_id: str, node_id: str, attempt_id: str, fact_kind: str) -> str:
    return projection_key_for(run_id, node_id, attempt_id, fact_kind)


def build_tau_orchestration_episode(
    *,
    projection_key: str,
    source_outbox_row: str,
    run_id: str,
    dag_id: str,
    dag_plan_hash: str,
    node_id: str,
    attempt_id: str,
    attempt_number: int,
    goal_hash: str,
    work_order_hash: str,
    journal_sequence: int,
    journal_head_hash: str,
    source_event_refs: list[str],
    source_receipt_refs: list[str],
    source_receipt_hashes: list[str],
    fact_kind: str,
    summary: str,
    outcome: str,
    project: str,
    live: bool,
    mocked: bool,
    provider_live: bool,
    route_key: str | None = None,
    joined_from: list[str] | None = None,
    repair_refs: list[str] | None = None,
    child_lineage: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a governed Memory Tau orchestration episode payload.

    The payload contains only accepted, bounded receipt and lineage facts.  It is
    accepted by graph-memory-operator's ``memory.tau_orchestration_episode.v1``
    normalizer and intentionally omits raw prompts, hidden reasoning, terminal
    text, stdout/stderr, secrets, and diagnostic-only provider events.
    """

    if attempt_number < 1:
        raise MemoryProjectionError("tau_episode_attempt_number_invalid")
    if journal_sequence < 1:
        raise MemoryProjectionError("tau_episode_journal_sequence_invalid")
    key_basis = {
        "projection_key": projection_key,
        "source_outbox_row": source_outbox_row,
        "run_id": run_id,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "fact_kind": fact_kind,
    }
    doc = {
        "_key": "tau_episode_" + hashlib.sha256(
            json.dumps(key_basis, sort_keys=True).encode("utf-8")
        ).hexdigest()[:40],
        "schema": TAU_ORCHESTRATION_EPISODE_SCHEMA,
        "kind": TAU_ORCHESTRATION_EPISODE_KIND,
        "record_kind": TAU_ORCHESTRATION_EPISODE_KIND,
        "projection_schema_version": TAU_EPISODE_PROJECTION_VERSION,
        "projection_idempotency_key": projection_key,
        "source_outbox_row": source_outbox_row,
        "scope": "tau_orchestration",
        "project": project,
        "run_id": run_id,
        "dag_id": dag_id,
        "dag_plan_hash": dag_plan_hash,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "goal_hash": goal_hash,
        "work_order_hash": work_order_hash,
        "journal_sequence": journal_sequence,
        "journal_head_hash": journal_head_hash,
        "source_event_refs": list(source_event_refs),
        "source_receipt_refs": list(source_receipt_refs),
        "source_receipt_hashes": list(source_receipt_hashes),
        "policy_version": TAU_EPISODE_POLICY_VERSION,
        "data_boundary_version": TAU_EPISODE_DATA_BOUNDARY_VERSION,
        "redaction_version": TAU_EPISODE_REDACTION_VERSION,
        "fact_kind": fact_kind,
        "summary": summary,
        "outcome": outcome,
        "validity_state": "accepted",
        "supersedes_refs": [],
        "mocked": mocked,
        "live": live,
        "provider_live": provider_live,
        "observed_at": _now(),
        "tags": ["tau", "orchestration", "episode", fact_kind],
        "artifact_refs": list(source_receipt_refs),
    }
    if route_key:
        doc["route_key"] = route_key
        doc["selected_route"] = route_key
    if joined_from:
        doc["joined_from"] = list(joined_from)
    if repair_refs:
        doc["repair_refs"] = list(repair_refs)
    if child_lineage:
        doc["child_lineage"] = list(child_lineage)
    validate_tau_orchestration_episode(doc)
    return doc


def validate_tau_orchestration_episode(
    doc: dict[str, Any], *, existing: dict[str, Any] | None = None
) -> None:
    missing = [
        field
        for field in TAU_EPISODE_REQUIRED_FIELDS
        if doc.get(field) is None
        or (isinstance(doc.get(field), str) and not str(doc.get(field)).strip())
        or (isinstance(doc.get(field), list) and not doc.get(field))
    ]
    if missing:
        raise MemoryProjectionError("tau_episode_missing_lineage:" + ",".join(missing))
    forbidden = [field for field in TAU_EPISODE_FORBIDDEN_FIELDS if field in doc]
    if forbidden:
        raise MemoryProjectionError("tau_episode_forbidden_field:" + ",".join(forbidden))
    if existing:
        changed = [
            field
            for field in TAU_EPISODE_IMMUTABLE_FIELDS
            if existing.get(field) is not None
            and doc.get(field) is not None
            and existing.get(field) != doc.get(field)
        ]
        if changed:
            raise MemoryProjectionError("tau_episode_immutable_lineage_changed:" + ",".join(changed))


def governed_memory_store_sender(
    *, memory_url: str = "http://127.0.0.1:8601", timeout_seconds: float = 10.0
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return a sender that writes only through Memory's governed ``/store`` API."""

    endpoint = memory_url.rstrip("/") + "/store"

    def send(payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"collection": "agent_conversations", "document": payload}).encode("utf-8")
        req = request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:  # nosec B310
                loaded = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return {
                "ok": False,
                "retryable": 500 <= exc.code < 600,
                "error": f"memory_http_{exc.code}:{detail}",
            }
        except Exception as exc:  # noqa: BLE001 - projection outage must not block Tau
            return {"ok": False, "retryable": True, "error": str(exc)}
        return {"ok": bool(loaded.get("stored")), "retryable": False, "response": loaded}

    return send


class MemoryProjectionOutbox:
    """SQLite-backed outbox and relay. One instance per run store."""

    def __init__(self, store: SqliteDagRunStore) -> None:
        self._store = store
        store._connection.executescript(_OUTBOX_DDL)

    def enqueue_within_transaction(
        self,
        lease: DagRunLease,
        *,
        node_id: str,
        attempt_id: str,
        fact_kind: str,
        payload: dict[str, Any],
    ) -> str:
        """Insert a pending projection row. MUST be called inside the same
        transaction as the accepted-outcome admission (the caller holds it).

        Idempotent on the projection key: an accepted outcome projected twice
        is one graph fact, never two.
        """

        key = _projection_key(lease.run_id, node_id, attempt_id, fact_kind)
        self._store._connection.execute(
            """INSERT INTO memory_projection_outbox(
                projection_key, run_id, node_id, attempt_id, fact_kind,
                payload_json, state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(projection_key) DO NOTHING""",
            (key, lease.run_id, node_id, attempt_id, fact_kind,
             json.dumps(payload, sort_keys=True), _now(), _now()),
        )
        return key

    def pending(self) -> list[dict[str, Any]]:
        rows = self._store._connection.execute(
            "SELECT * FROM memory_projection_outbox WHERE state IN ('pending','retryable_failed')"
        ).fetchall()
        return [dict(r) for r in rows]

    def relay(
        self,
        sender: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        max_attempts: int = 3,
    ) -> list[ProjectionResult]:
        """Drain pending/retryable rows through ``sender`` (the governed
        graph-memory API or a degraded stub). Never raises on delivery
        failure: a graph-memory outage degrades the outbox, never the run.

        ``sender`` returns ``{"ok": True}`` on projection,
        ``{"ok": False, "retryable": bool, "error": str}`` otherwise.
        """

        results: list[ProjectionResult] = []
        for row in self.pending():
            key = row["projection_key"]
            attempts = int(row["attempts"]) + 1
            try:
                verdict = sender(json.loads(row["payload_json"]))
            except Exception as exc:  # noqa: BLE001 - outage must not raise
                verdict = {"ok": False, "retryable": True, "error": str(exc)}
            if verdict.get("ok"):
                state = "projected"
                error = None
            elif not verdict.get("retryable", False):
                state = "permanently_rejected"
                error = str(verdict.get("error", "rejected"))
            elif attempts >= max_attempts:
                state = "degraded"
                error = str(verdict.get("error", "max attempts"))
            else:
                state = "retryable_failed"
                error = str(verdict.get("error", "retry"))
            with self._store._transaction():
                self._store._connection.execute(
                    """UPDATE memory_projection_outbox
                       SET state = ?, attempts = ?, last_error = ?, updated_at = ?
                       WHERE projection_key = ?""",
                    (state, attempts, error, _now(), key),
                )
            results.append(ProjectionResult(key, state, attempts))
        return results

    def state_of(self, projection_key: str) -> str | None:
        row = self._store._connection.execute(
            "SELECT state FROM memory_projection_outbox WHERE projection_key = ?",
            (projection_key,),
        ).fetchone()
        return str(row["state"]) if row is not None else None

    def all_rows(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._store._connection.execute(
                "SELECT * FROM memory_projection_outbox ORDER BY created_at, projection_key"
            ).fetchall()
        ]


__all__ = [
    "PROJECTION_STATES",
    "TAU_ORCHESTRATION_EPISODE_SCHEMA",
    "MemoryProjectionError",
    "MemoryProjectionOutbox",
    "ProjectionResult",
    "build_tau_orchestration_episode",
    "governed_memory_store_sender",
    "projection_key_for",
    "validate_tau_orchestration_episode",
]
