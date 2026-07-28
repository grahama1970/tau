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

from tau_coding.dag_runtime.run_store import DagRunLease, SqliteDagRunStore

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


def _projection_key(run_id: str, node_id: str, attempt_id: str, fact_kind: str) -> str:
    basis = json.dumps(
        {"run_id": run_id, "node_id": node_id, "attempt_id": attempt_id, "fact_kind": fact_kind},
        sort_keys=True,
    )
    return "mp-" + hashlib.sha256(basis.encode()).hexdigest()[:32]


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
    "MemoryProjectionError",
    "MemoryProjectionOutbox",
    "ProjectionResult",
]
