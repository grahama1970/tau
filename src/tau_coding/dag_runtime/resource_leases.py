"""Durable resource leases for DAG scheduler dispatch gates."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import DagPlanNode, canonical_json, canonical_sha256
from tau_coding.dag_runtime.run_store import DagRunLease, SqliteDagRunStore

RESOURCE_LEASE_EVENT_SCHEMA = "tau.resource_lease_event.v1"
RESOURCE_LEASE_DIAGNOSTIC_SCHEMA = "tau.dag_diagnostic_event.v1"
RESOURCE_LEASE_STORE_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_lease_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_leases (
    token TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('shared', 'exclusive')),
    mutation_key TEXT,
    expires_at_ms INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED')),
    created_at TEXT NOT NULL,
    released_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_resource_leases_active
ON resource_leases(resource_kind, resource_id, status, expires_at_ms);

CREATE INDEX IF NOT EXISTS idx_resource_leases_mutation
ON resource_leases(mutation_key, status, expires_at_ms);

CREATE TABLE IF NOT EXISTS resource_lease_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    token TEXT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class ResourceLeaseError(RuntimeError):
    """Fail-closed resource lease error with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


class ResourceLeaseDenied(ResourceLeaseError):
    """Raised when a scheduler dispatch cannot acquire a required lease."""


@dataclass(frozen=True, slots=True)
class ResourceLeaseToken:
    token: str
    run_id: str
    node_id: str
    attempt_id: str
    resource_kind: str
    resource_id: str
    mode: str
    mutation_key: str | None
    expires_at_ms: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "resource_kind": self.resource_kind,
            "resource_id": self.resource_id,
            "mode": self.mode,
            "mutation_key": self.mutation_key,
            "expires_at_ms": self.expires_at_ms,
        }


class ResourceLeaseManager:
    """SQLite-backed cooperative lease store for scheduler resource admission."""

    def __init__(self, path: Path, *, owner_id: str) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.owner_id = owner_id
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.executescript(_SCHEMA)
        self._connection.execute(
            """
            INSERT INTO resource_lease_meta(key, value) VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(RESOURCE_LEASE_STORE_SCHEMA_VERSION),),
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ResourceLeaseManager:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def acquire_for_attempt(
        self,
        *,
        node: DagPlanNode,
        run_id: str,
        attempt_id: str,
        ttl_seconds: float,
        run_store: SqliteDagRunStore | None = None,
        scheduler_lease: DagRunLease | None = None,
    ) -> tuple[ResourceLeaseToken, ...]:
        requirements = _resource_requirements(node)
        if not requirements:
            return ()
        if ttl_seconds <= 0:
            raise ResourceLeaseDenied("resource_lease_invalid_ttl", node.node_id)
        acquired: list[ResourceLeaseToken] = []
        with self._lock:
            self._expire_stale_locked(reason="pre_acquire")
            try:
                for requirement in requirements:
                    token = self._acquire_one_locked(
                        requirement=requirement,
                        run_id=run_id,
                        node_id=node.node_id,
                        attempt_id=attempt_id,
                        ttl_seconds=ttl_seconds,
                    )
                    acquired.append(token)
                    _append_run_diagnostic(
                        run_store=run_store,
                        scheduler_lease=scheduler_lease,
                        event_key=f"resource-lease:{token.token}:acquired",
                        node_id=node.node_id,
                        payload=_event_payload("resource_lease_acquired", token.to_payload()),
                        attempt_id=attempt_id,
                    )
            except Exception:
                for token in reversed(acquired):
                    self._release_one_locked(token, reason="partial_acquire_rollback")
                raise
        return tuple(acquired)

    def release(
        self,
        tokens: tuple[ResourceLeaseToken, ...],
        *,
        run_store: SqliteDagRunStore | None = None,
        scheduler_lease: DagRunLease | None = None,
        reason: str = "node_finished",
    ) -> None:
        with self._lock:
            for token in tokens:
                self._release_one_locked(token, reason=reason)
                _append_run_diagnostic(
                    run_store=run_store,
                    scheduler_lease=scheduler_lease,
                    event_key=f"resource-lease:{token.token}:released:{reason}",
                    node_id=token.node_id,
                    payload=_event_payload(
                        "resource_lease_released",
                        {**token.to_payload(), "reason": reason},
                    ),
                    attempt_id=token.attempt_id,
                )

    def recover_expired(self, *, reason: str = "explicit_recovery") -> int:
        with self._lock:
            return self._expire_stale_locked(reason=reason)

    def active_count(self, *, resource_kind: str, resource_id: str) -> int:
        row = self._connection.execute(
            """SELECT COUNT(*) FROM resource_leases
               WHERE resource_kind = ? AND resource_id = ? AND status = 'ACTIVE'""",
            (resource_kind, resource_id),
        ).fetchone()
        return int(row[0])

    def event_counts(self) -> dict[str, int]:
        rows = self._connection.execute(
            "SELECT event_type, COUNT(*) FROM resource_lease_events GROUP BY event_type"
        ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def events(self) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute(
            "SELECT * FROM resource_lease_events ORDER BY seq"
        ).fetchall()
        return tuple(_event_row(row) for row in rows)

    def _acquire_one_locked(
        self,
        *,
        requirement: Mapping[str, Any],
        run_id: str,
        node_id: str,
        attempt_id: str,
        ttl_seconds: float,
    ) -> ResourceLeaseToken:
        resource_kind = _required_string(requirement, "resource_kind")
        resource_id = _required_string(requirement, "resource_id")
        mode = str(requirement.get("mode") or "exclusive")
        if mode not in {"shared", "exclusive"}:
            raise ResourceLeaseDenied("resource_lease_mode_invalid", mode)
        mutation_key = _optional_string(requirement.get("mutation_key"))
        if _conflicting_active_rows(
            self._connection,
            resource_kind=resource_kind,
            resource_id=resource_id,
            mode=mode,
            mutation_key=mutation_key,
        ):
            token = f"denied-{uuid.uuid4().hex}"
            payload = {
                "run_id": run_id,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "resource_kind": resource_kind,
                "resource_id": resource_id,
                "mode": mode,
                "mutation_key": mutation_key,
                "reason": "resource_lease_conflict",
            }
            self._append_event_locked("resource_lease_denied", token, payload)
            raise ResourceLeaseDenied("resource_lease_conflict", canonical_json(payload))
        now = _now_ms()
        expires_at_ms = now + max(1, int(ttl_seconds * 1000))
        token = f"lease-{uuid.uuid4().hex}"
        created_at = _now_iso()
        self._connection.execute(
            """INSERT INTO resource_leases(
                token, run_id, node_id, attempt_id, owner_id, resource_kind,
                resource_id, mode, mutation_key, expires_at_ms, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)""",
            (
                token,
                run_id,
                node_id,
                attempt_id,
                self.owner_id,
                resource_kind,
                resource_id,
                mode,
                mutation_key,
                expires_at_ms,
                created_at,
            ),
        )
        lease = ResourceLeaseToken(
            token=token,
            run_id=run_id,
            node_id=node_id,
            attempt_id=attempt_id,
            resource_kind=resource_kind,
            resource_id=resource_id,
            mode=mode,
            mutation_key=mutation_key,
            expires_at_ms=expires_at_ms,
        )
        self._append_event_locked("resource_lease_acquired", token, lease.to_payload())
        return lease

    def _release_one_locked(self, token: ResourceLeaseToken, *, reason: str) -> None:
        row = self._connection.execute(
            "SELECT status FROM resource_leases WHERE token = ?", (token.token,)
        ).fetchone()
        if row is None:
            raise ResourceLeaseError("resource_lease_missing", token.token)
        if str(row["status"]) != "ACTIVE":
            return
        self._connection.execute(
            """UPDATE resource_leases SET status = 'RELEASED', released_at = ?
               WHERE token = ?""",
            (_now_iso(), token.token),
        )
        self._append_event_locked(
            "resource_lease_released",
            token.token,
            {**token.to_payload(), "reason": reason},
        )

    def _expire_stale_locked(self, *, reason: str) -> int:
        now = _now_ms()
        rows = self._connection.execute(
            """SELECT * FROM resource_leases
               WHERE status = 'ACTIVE' AND expires_at_ms <= ?
               ORDER BY expires_at_ms, token""",
            (now,),
        ).fetchall()
        for row in rows:
            token = str(row["token"])
            payload = _lease_payload_from_row(row)
            self._connection.execute(
                "UPDATE resource_leases SET status = 'EXPIRED', released_at = ? WHERE token = ?",
                (_now_iso(), token),
            )
            self._append_event_locked(
                "resource_lease_expired",
                token,
                {**payload, "reason": reason},
            )
        return len(rows)

    def _append_event_locked(
        self,
        event_type: str,
        token: str | None,
        payload: Mapping[str, Any],
    ) -> int:
        value = _event_payload(event_type, payload)
        self._connection.execute(
            """INSERT INTO resource_lease_events(
                event_type, token, payload_json, payload_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?)""",
            (event_type, token, canonical_json(value), canonical_sha256(value), _now_iso()),
        )
        row = self._connection.execute("SELECT last_insert_rowid()").fetchone()
        return int(row[0])


def _resource_requirements(node: DagPlanNode) -> tuple[Mapping[str, Any], ...]:
    runtime = node.runtime_requirement.to_value()
    if not isinstance(runtime, dict):
        return ()
    value = runtime.get("resource_leases")
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _conflicting_active_rows(
    connection: sqlite3.Connection,
    *,
    resource_kind: str,
    resource_id: str,
    mode: str,
    mutation_key: str | None,
) -> bool:
    rows = connection.execute(
        """SELECT mode, mutation_key FROM resource_leases
           WHERE status = 'ACTIVE' AND (
             (resource_kind = ? AND resource_id = ?)
             OR (? IS NOT NULL AND mutation_key = ?)
           )""",
        (resource_kind, resource_id, mutation_key, mutation_key),
    ).fetchall()
    for row in rows:
        if mutation_key is not None and row["mutation_key"] == mutation_key:
            return True
        if mode == "exclusive" or row["mode"] == "exclusive":
            return True
    return False


def _append_run_diagnostic(
    *,
    run_store: SqliteDagRunStore | None,
    scheduler_lease: DagRunLease | None,
    event_key: str,
    node_id: str,
    payload: Mapping[str, Any],
    attempt_id: str,
) -> None:
    if run_store is None or scheduler_lease is None:
        return
    run_store.append_diagnostic_event(
        scheduler_lease,
        event_key=event_key,
        node_id=node_id,
        attempt_id=attempt_id,
        payload={
            "schema": RESOURCE_LEASE_DIAGNOSTIC_SCHEMA,
            "source_schema": RESOURCE_LEASE_EVENT_SCHEMA,
            "resource_lease_event": dict(payload),
        },
    )


def _event_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": RESOURCE_LEASE_EVENT_SCHEMA,
        "event_type": event_type,
        "payload": dict(payload),
        "created_at": _now_iso(),
    }


def _event_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(str(row["payload_json"]))
    return {
        "seq": int(row["seq"]),
        "event_type": str(row["event_type"]),
        "token": str(row["token"]) if row["token"] is not None else None,
        "payload": payload,
        "payload_sha256": str(row["payload_sha256"]),
        "created_at": str(row["created_at"]),
    }


def _lease_payload_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "token": str(row["token"]),
        "run_id": str(row["run_id"]),
        "node_id": str(row["node_id"]),
        "attempt_id": str(row["attempt_id"]),
        "resource_kind": str(row["resource_kind"]),
        "resource_id": str(row["resource_id"]),
        "mode": str(row["mode"]),
        "mutation_key": str(row["mutation_key"]) if row["mutation_key"] is not None else None,
        "expires_at_ms": int(row["expires_at_ms"]),
    }


def _required_string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ResourceLeaseDenied("resource_lease_requirement_invalid", key)
    return item


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

