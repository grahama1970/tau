"""Transactional SQLite journal for the canonical DAG scheduler."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tau_coding.dag_runtime.model import DagPlan, canonical_json, canonical_sha256
from tau_coding.runtime_backends.contracts import RuntimeEvent, RuntimeStateProjection

EVENT_SCHEMA = "tau.dag_run_event.v1"
RUNTIME_EVENT_JOURNAL_ENTRY_SCHEMA = "tau.runtime_event_journal_entry.v1"
STORE_SCHEMA_VERSION = 1


class DagRunStoreError(RuntimeError):
    """Fail-closed run-store error with a stable failure code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class DagRunLease:
    run_id: str
    owner_id: str
    epoch: int
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class DagAttemptIdentity:
    run_id: str
    node_id: str
    attempt: int
    attempt_id: str
    idempotency_key: str
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class StoredAttempt:
    identity: DagAttemptIdentity
    state: str
    effect_state: str
    staged_result: dict[str, Any] | None
    committed_result: dict[str, Any] | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dag_store_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dag_runs (
    run_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('RUNNING', 'PASS', 'BLOCKED', 'RECONCILIATION_REQUIRED')
    ),
    verdict TEXT,
    lease_owner TEXT,
    lease_epoch INTEGER NOT NULL DEFAULT 0,
    lease_expires_at_ms INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dag_run_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES dag_runs(run_id),
    event_key TEXT NOT NULL,
    event_schema TEXT NOT NULL CHECK (event_schema = 'tau.dag_run_event.v1'),
    event_version INTEGER NOT NULL CHECK (event_version = 1),
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    attempt_id TEXT,
    lease_epoch INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, event_key)
);

CREATE INDEX IF NOT EXISTS idx_dag_run_events_run_seq
ON dag_run_events(run_id, seq);

CREATE INDEX IF NOT EXISTS idx_dag_run_events_attempt
ON dag_run_events(run_id, attempt_id, seq);

CREATE TABLE IF NOT EXISTS dag_node_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES dag_runs(run_id),
    node_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'RESERVED', 'DISPATCHED', 'STAGED', 'VALIDATED',
            'OUTPUT_COMMITTED', 'SETTLED', 'RETRY_SCHEDULED',
            'UNCERTAIN', 'RECONCILED'
        )
    ),
    effect_state TEXT NOT NULL CHECK (effect_state IN ('NONE', 'UNCERTAIN', 'RECONCILED')),
    lease_epoch INTEGER NOT NULL,
    dispatch_event_seq INTEGER,
    final_event_seq INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, node_id, attempt_no),
    UNIQUE(run_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS dag_attempt_outputs (
    attempt_id TEXT PRIMARY KEY REFERENCES dag_node_attempts(attempt_id),
    staged_json TEXT NOT NULL,
    staged_sha256 TEXT NOT NULL,
    validation_json TEXT,
    validation_sha256 TEXT,
    committed_json TEXT,
    committed_sha256 TEXT,
    CHECK (
        (committed_json IS NULL AND committed_sha256 IS NULL)
        OR (committed_json IS NOT NULL AND committed_sha256 IS NOT NULL)
    )
);

CREATE TRIGGER IF NOT EXISTS dag_run_events_no_update
BEFORE UPDATE ON dag_run_events
BEGIN
    SELECT RAISE(ABORT, 'dag_run_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS dag_run_events_no_delete
BEFORE DELETE ON dag_run_events
BEGIN
    SELECT RAISE(ABORT, 'dag_run_events is append-only');
END;
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _runtime_transport_mode(event: RuntimeEvent) -> str:
    observation = event.observation.to_value()
    transport = observation.get("transport")
    if not isinstance(transport, dict):
        return "unknown"
    mode = transport.get("mode")
    return mode if isinstance(mode, str) and mode else "unknown"


def _decoded_runtime_journal_payload(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise DagRunStoreError("runtime_event_journal_invalid", str(row["seq"])) from exc
    if not isinstance(payload, dict) or canonical_sha256(payload) != row["payload_sha256"]:
        raise DagRunStoreError("runtime_event_hash_mismatch", str(row["seq"]))
    if payload.get("schema") != RUNTIME_EVENT_JOURNAL_ENTRY_SCHEMA:
        raise DagRunStoreError("runtime_event_journal_schema_invalid", str(row["seq"]))
    runtime_payload = payload.get("runtime_event")
    if not isinstance(runtime_payload, dict):
        raise DagRunStoreError("runtime_event_journal_invalid", str(row["seq"]))
    if canonical_sha256(runtime_payload) != payload.get("runtime_event_sha256"):
        raise DagRunStoreError("runtime_event_hash_mismatch", str(row["seq"]))
    identity_payload = dict(runtime_payload)
    identity_payload.pop("observed_at", None)
    if canonical_sha256(identity_payload) != payload.get(
        "runtime_event_identity_sha256"
    ):
        raise DagRunStoreError("runtime_event_identity_hash_mismatch", str(row["seq"]))
    return payload


class SqliteDagRunStore:
    """File-backed append-only event journal with transactional projections."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        journal_mode = str(self._connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
        if journal_mode.lower() != "wal":
            raise DagRunStoreError("dag_run_store_wal_unavailable", journal_mode)
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.executescript(_SCHEMA)
        self._connection.execute(
            "INSERT OR IGNORE INTO dag_store_meta(key, value) VALUES ('schema_version', ?)",
            (str(STORE_SCHEMA_VERSION),),
        )
        stored_version = self._connection.execute(
            "SELECT value FROM dag_store_meta WHERE key = 'schema_version'"
        ).fetchone()
        if stored_version is None or stored_version[0] != str(STORE_SCHEMA_VERSION):
            raise DagRunStoreError("dag_run_store_schema_mismatch")

    def execution_run_id(self, base_run_id: str) -> str:
        """Return an unfinished generation or allocate a clean invocation."""

        prefix = f"{base_run_id}:generation:"
        rows = self._connection.execute("SELECT run_id, status FROM dag_runs").fetchall()
        generations: list[tuple[int, str, str]] = []
        for row in rows:
            stored_run_id = str(row["run_id"])
            if stored_run_id == base_run_id:
                generations.append((0, stored_run_id, str(row["status"])))
                continue
            if not stored_run_id.startswith(prefix):
                continue
            suffix = stored_run_id.removeprefix(prefix)
            if suffix.isdigit():
                generations.append((int(suffix), stored_run_id, str(row["status"])))
        if not generations:
            return base_run_id
        generation, stored_run_id, status = max(generations)
        if status in {"RUNNING", "RECONCILIATION_REQUIRED"}:
            return stored_run_id
        return f"{base_run_id}:generation:{generation + 1}"

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SqliteDagRunStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def integrity_check(self) -> dict[str, Any]:
        integrity = [str(row[0]) for row in self._connection.execute("PRAGMA integrity_check")]
        foreign_keys = [tuple(row) for row in self._connection.execute("PRAGMA foreign_key_check")]
        return {
            "ok": integrity == ["ok"] and not foreign_keys,
            "integrity_check": integrity,
            "foreign_key_check": foreign_keys,
            "journal_mode": str(
                self._connection.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower(),
        }

    def run_outcome(self, run_id: str) -> tuple[str, str | None] | None:
        row = self._connection.execute(
            "SELECT status, verdict FROM dag_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return str(row["status"]), (
            str(row["verdict"]) if row["verdict"] is not None else None
        )

    def max_observed_concurrency(self, run_id: str) -> int:
        """Return the highest scheduler concurrency recorded in the journal."""

        maximum = 0
        for event in self.load_events(run_id):
            if event["event_type"] != "scheduler_concurrency_observed":
                continue
            value = event["payload"].get("concurrency")
            if isinstance(value, int) and not isinstance(value, bool):
                maximum = max(maximum, value)
        return maximum

    def record_observed_concurrency(self, lease: DagRunLease, concurrency: int) -> None:
        """Append a durable high-water mark when scheduler concurrency increases."""

        if concurrency < 0:
            raise DagRunStoreError("dag_run_concurrency_invalid", str(concurrency))
        current = self.max_observed_concurrency(lease.run_id)
        if concurrency <= current:
            return
        with self._transaction():
            self._assert_lease(lease)
            self._append_event(
                lease,
                event_key=f"scheduler:concurrency:{concurrency}",
                event_type="scheduler_concurrency_observed",
                entity_type="run",
                entity_id=lease.run_id,
                payload={"concurrency": concurrency},
            )

    def acquire_run(
        self,
        *,
        plan: DagPlan,
        run_id: str,
        owner_id: str,
        ttl_seconds: float = 15.0,
        allow_takeover: bool = False,
    ) -> DagRunLease:
        now_ms = _now_ms()
        expires_at_ms = now_ms + max(1, int(ttl_seconds * 1000))
        plan_json = canonical_json(plan.to_payload())
        with self._transaction():
            row = self._connection.execute(
                "SELECT * FROM dag_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                now = _now_iso()
                self._connection.execute(
                    """INSERT INTO dag_runs(
                        run_id, plan_id, plan_sha256, plan_json, status, verdict,
                        lease_owner, lease_epoch, lease_expires_at_ms, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'RUNNING', NULL, ?, 1, ?, ?, ?)""",
                    (
                        run_id,
                        plan.plan_id,
                        plan.plan_sha256,
                        plan_json,
                        owner_id,
                        expires_at_ms,
                        now,
                        now,
                    ),
                )
                lease = DagRunLease(run_id, owner_id, 1, expires_at_ms)
                self._append_event(
                    lease,
                    event_key="run:created",
                    event_type="run_created",
                    entity_type="run",
                    entity_id=run_id,
                    payload={"plan_id": plan.plan_id, "plan_sha256": plan.plan_sha256},
                    check_lease=False,
                )
                self._append_event(
                    lease,
                    event_key="lease:1:acquired",
                    event_type="run_lease_acquired",
                    entity_type="run",
                    entity_id=run_id,
                    payload={"owner_id": owner_id, "expires_at_ms": expires_at_ms},
                    check_lease=False,
                )
                return lease
            if (
                row["plan_id"] != plan.plan_id
                or row["plan_sha256"] != plan.plan_sha256
                or row["plan_json"] != plan_json
            ):
                raise DagRunStoreError("dag_run_plan_mismatch", run_id)
            if row["status"] == "RECONCILIATION_REQUIRED":
                raise DagRunStoreError("dag_run_reconciliation_required", run_id)
            current_owner = row["lease_owner"]
            current_expiry = int(row["lease_expires_at_ms"] or 0)
            epoch = int(row["lease_epoch"])
            if current_owner == owner_id:
                self._connection.execute(
                    "UPDATE dag_runs SET lease_expires_at_ms = ?, updated_at = ? WHERE run_id = ?",
                    (expires_at_ms, _now_iso(), run_id),
                )
                lease = DagRunLease(run_id, owner_id, epoch, expires_at_ms)
                self._append_event(
                    lease,
                    event_key=f"lease:{epoch}:renewed:{expires_at_ms}",
                    event_type="run_lease_renewed",
                    entity_type="run",
                    entity_id=run_id,
                    payload={"owner_id": owner_id, "expires_at_ms": expires_at_ms},
                    check_lease=False,
                )
                return lease
            if current_owner is not None and current_expiry > now_ms:
                raise DagRunStoreError("dag_run_lease_held", str(current_owner))
            if current_owner is not None and not allow_takeover:
                raise DagRunStoreError("dag_run_lease_takeover_required", str(current_owner))
            epoch += 1
            self._connection.execute(
                """UPDATE dag_runs
                   SET lease_owner = ?, lease_epoch = ?, lease_expires_at_ms = ?, updated_at = ?
                   WHERE run_id = ?""",
                (owner_id, epoch, expires_at_ms, _now_iso(), run_id),
            )
            lease = DagRunLease(run_id, owner_id, epoch, expires_at_ms)
            self._append_event(
                lease,
                event_key=f"lease:{epoch}:taken-over",
                event_type="run_lease_taken_over",
                entity_type="run",
                entity_id=run_id,
                payload={
                    "owner_id": owner_id,
                    "prior_owner_id": current_owner,
                    "expires_at_ms": expires_at_ms,
                },
                check_lease=False,
            )
            return lease

    def renew_lease(self, lease: DagRunLease, *, ttl_seconds: float = 15.0) -> DagRunLease:
        expires_at_ms = _now_ms() + max(1, int(ttl_seconds * 1000))
        with self._transaction():
            self._assert_lease(lease)
            self._connection.execute(
                "UPDATE dag_runs SET lease_expires_at_ms = ?, updated_at = ? WHERE run_id = ?",
                (expires_at_ms, _now_iso(), lease.run_id),
            )
            renewed = DagRunLease(lease.run_id, lease.owner_id, lease.epoch, expires_at_ms)
            self._append_event(
                renewed,
                event_key=f"lease:{lease.epoch}:renewed:{expires_at_ms}",
                event_type="run_lease_renewed",
                entity_type="run",
                entity_id=lease.run_id,
                payload={"owner_id": lease.owner_id, "expires_at_ms": expires_at_ms},
                check_lease=False,
            )
            return renewed

    def release_lease(self, lease: DagRunLease) -> None:
        with self._transaction():
            self._assert_lease(lease, allow_expired=True)
            self._append_event(
                lease,
                event_key=f"lease:{lease.epoch}:released",
                event_type="run_lease_released",
                entity_type="run",
                entity_id=lease.run_id,
                payload={"owner_id": lease.owner_id},
                check_lease=False,
            )
            self._connection.execute(
                """UPDATE dag_runs SET lease_owner = NULL, lease_expires_at_ms = NULL,
                   updated_at = ? WHERE run_id = ?""",
                (_now_iso(), lease.run_id),
            )

    def reserve_attempt(
        self,
        lease: DagRunLease,
        *,
        plan_sha256: str,
        node_id: str,
        attempt: int,
    ) -> DagAttemptIdentity:
        basis = {
            "schema": "tau.dag_attempt_identity.v1",
            "run_id": lease.run_id,
            "plan_sha256": plan_sha256,
            "node_id": node_id,
            "attempt": attempt,
        }
        digest = canonical_sha256(basis).removeprefix("sha256:")
        identity = DagAttemptIdentity(
            run_id=lease.run_id,
            node_id=node_id,
            attempt=attempt,
            attempt_id=f"attempt-{digest[:32]}",
            idempotency_key=canonical_sha256({**basis, "purpose": "adapter_effect"}),
        )
        with self._transaction():
            self._assert_lease(lease)
            row = self._connection.execute(
                """SELECT * FROM dag_node_attempts
                   WHERE run_id = ? AND node_id = ? AND attempt_no = ?""",
                (lease.run_id, node_id, attempt),
            ).fetchone()
            if row is not None:
                if (
                    row["attempt_id"] != identity.attempt_id
                    or row["idempotency_key"] != identity.idempotency_key
                ):
                    raise DagRunStoreError("dag_attempt_identity_conflict", identity.attempt_id)
                return replace(identity, recovered=True)
            now = _now_iso()
            self._connection.execute(
                """INSERT INTO dag_node_attempts(
                    attempt_id, run_id, node_id, attempt_no, idempotency_key,
                    state, effect_state, lease_epoch, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'RESERVED', 'NONE', ?, ?, ?)""",
                (
                    identity.attempt_id,
                    lease.run_id,
                    node_id,
                    attempt,
                    identity.idempotency_key,
                    lease.epoch,
                    now,
                    now,
                ),
            )
            self._append_event(
                lease,
                event_key=f"attempt:{identity.attempt_id}:reserved",
                event_type="attempt_reserved",
                entity_type="attempt",
                entity_id=identity.attempt_id,
                attempt_id=identity.attempt_id,
                payload={
                    "node_id": node_id,
                    "attempt": attempt,
                    "idempotency_key": identity.idempotency_key,
                },
            )
        return identity

    def mark_dispatched(self, lease: DagRunLease, attempt_id: str) -> None:
        self._change_attempt_state(
            lease,
            attempt_id,
            allowed={"RESERVED", "RECONCILED"},
            target="DISPATCHED",
            event_type="attempt_dispatched",
            event_key="dispatched",
        )

    def stage_result(
        self,
        lease: DagRunLease,
        attempt_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        canonical = canonical_json(dict(result))
        digest = canonical_sha256(dict(result))
        with self._transaction():
            self._assert_lease(lease)
            attempt = self._attempt_row(attempt_id)
            if attempt["state"] not in {"DISPATCHED", "STAGED"}:
                raise DagRunStoreError("dag_attempt_state_invalid", str(attempt["state"]))
            row = self._connection.execute(
                "SELECT staged_json, staged_sha256 FROM dag_attempt_outputs WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is not None:
                if row["staged_sha256"] != digest or row["staged_json"] != canonical:
                    raise DagRunStoreError("dag_attempt_result_conflict", attempt_id)
                return cast(dict[str, Any], json.loads(canonical))
            self._connection.execute(
                """INSERT INTO dag_attempt_outputs(
                    attempt_id, staged_json, staged_sha256
                ) VALUES (?, ?, ?)""",
                (attempt_id, canonical, digest),
            )
            self._connection.execute(
                """UPDATE dag_node_attempts
                   SET state = 'STAGED', updated_at = ? WHERE attempt_id = ?""",
                (_now_iso(), attempt_id),
            )
            self._append_event(
                lease,
                event_key=f"attempt:{attempt_id}:result-staged",
                event_type="attempt_result_staged",
                entity_type="attempt",
                entity_id=attempt_id,
                attempt_id=attempt_id,
                payload={"result": json.loads(canonical), "result_sha256": digest},
            )
        return cast(dict[str, Any], json.loads(canonical))

    def validate_result(
        self,
        lease: DagRunLease,
        attempt_id: str,
        validation: Mapping[str, Any],
    ) -> None:
        canonical = canonical_json(dict(validation))
        digest = canonical_sha256(dict(validation))
        with self._transaction():
            self._assert_lease(lease)
            attempt = self._attempt_row(attempt_id)
            if attempt["state"] == "VALIDATED":
                row = self._output_row(attempt_id)
                if row["validation_sha256"] != digest:
                    raise DagRunStoreError("dag_attempt_result_conflict", attempt_id)
                return
            if attempt["state"] != "STAGED":
                raise DagRunStoreError("dag_attempt_state_invalid", str(attempt["state"]))
            self._connection.execute(
                """UPDATE dag_attempt_outputs
                   SET validation_json = ?, validation_sha256 = ? WHERE attempt_id = ?""",
                (canonical, digest, attempt_id),
            )
            self._connection.execute(
                """UPDATE dag_node_attempts
                   SET state = 'VALIDATED', updated_at = ? WHERE attempt_id = ?""",
                (_now_iso(), attempt_id),
            )
            self._append_event(
                lease,
                event_key=f"attempt:{attempt_id}:result-validated",
                event_type="attempt_result_validated",
                entity_type="attempt",
                entity_id=attempt_id,
                attempt_id=attempt_id,
                payload=dict(validation),
            )

    def schedule_retry(self, lease: DagRunLease, attempt_id: str, *, next_attempt: int) -> None:
        self._change_attempt_state(
            lease,
            attempt_id,
            allowed={"VALIDATED", "RETRY_SCHEDULED"},
            target="RETRY_SCHEDULED",
            event_type="attempt_retry_scheduled",
            event_key="retry-scheduled",
            payload={"next_attempt": next_attempt},
        )

    def commit_output(self, lease: DagRunLease, attempt_id: str) -> dict[str, Any]:
        with self._transaction():
            self._assert_lease(lease)
            attempt = self._attempt_row(attempt_id)
            row = self._output_row(attempt_id)
            if attempt["state"] in {"OUTPUT_COMMITTED", "SETTLED"}:
                if row["committed_json"] is None:
                    raise DagRunStoreError("dag_attempt_output_not_committed", attempt_id)
                return cast(dict[str, Any], json.loads(row["committed_json"]))
            if attempt["state"] != "VALIDATED":
                raise DagRunStoreError("dag_attempt_state_invalid", str(attempt["state"]))
            self._connection.execute(
                """UPDATE dag_attempt_outputs SET committed_json = staged_json,
                   committed_sha256 = staged_sha256 WHERE attempt_id = ?""",
                (attempt_id,),
            )
            self._connection.execute(
                """UPDATE dag_node_attempts SET state = 'OUTPUT_COMMITTED', updated_at = ?
                   WHERE attempt_id = ?""",
                (_now_iso(), attempt_id),
            )
            self._append_event(
                lease,
                event_key=f"attempt:{attempt_id}:output-committed",
                event_type="attempt_output_committed",
                entity_type="attempt",
                entity_id=attempt_id,
                attempt_id=attempt_id,
                payload={"result_sha256": row["staged_sha256"]},
            )
            return cast(dict[str, Any], json.loads(row["staged_json"]))

    def commit_transition(
        self,
        lease: DagRunLease,
        attempt_id: str,
        *,
        completion: Mapping[str, Any],
        result: Mapping[str, Any],
        transition: Mapping[str, Any],
    ) -> None:
        payload = {
            "completion": dict(completion),
            "result": dict(result),
            "transition": dict(transition),
        }
        with self._transaction():
            self._assert_lease(lease)
            attempt = self._attempt_row(attempt_id)
            if attempt["state"] == "SETTLED":
                existing = self._event_by_key(
                    lease.run_id, f"attempt:{attempt_id}:transition-committed"
                )
                if existing is None or existing["payload_sha256"] != canonical_sha256(payload):
                    raise DagRunStoreError("dag_transition_replay_mismatch", attempt_id)
                return
            if attempt["state"] != "OUTPUT_COMMITTED":
                raise DagRunStoreError("dag_attempt_output_not_committed", attempt_id)
            event_seq = self._append_event(
                lease,
                event_key=f"attempt:{attempt_id}:transition-committed",
                event_type="scheduler_transition_committed",
                entity_type="attempt",
                entity_id=attempt_id,
                attempt_id=attempt_id,
                payload=payload,
            )
            self._connection.execute(
                """UPDATE dag_node_attempts SET state = 'SETTLED', final_event_seq = ?,
                   updated_at = ? WHERE attempt_id = ?""",
                (event_seq, _now_iso(), attempt_id),
            )

    def commit_control_transition(
        self,
        lease: DagRunLease,
        *,
        event_key: str,
        transition: Mapping[str, Any],
    ) -> None:
        with self._transaction():
            self._assert_lease(lease)
            self._append_event(
                lease,
                event_key=f"transition:{event_key}",
                event_type="scheduler_control_transition_committed",
                entity_type="scheduler",
                entity_id=lease.run_id,
                payload={"transition": dict(transition)},
            )

    def mark_run_finished(self, lease: DagRunLease, *, status: str, verdict: str) -> None:
        if status not in {"PASS", "BLOCKED"}:
            raise DagRunStoreError("dag_run_replay_invalid", status)
        with self._transaction():
            self._assert_lease(lease, allow_expired=True)
            self._connection.execute(
                "UPDATE dag_runs SET status = ?, verdict = ?, updated_at = ? WHERE run_id = ?",
                (status, verdict, _now_iso(), lease.run_id),
            )
            self._append_event(
                lease,
                event_key=f"run:finished:{status}:{verdict}",
                event_type="run_completed" if status == "PASS" else "run_blocked",
                entity_type="run",
                entity_id=lease.run_id,
                payload={"status": status, "verdict": verdict},
                check_lease=False,
            )

    def mark_dispatched_attempts_uncertain(self, lease: DagRunLease) -> tuple[StoredAttempt, ...]:
        with self._transaction():
            self._assert_lease(lease)
            rows = self._connection.execute(
                """SELECT attempt_id FROM dag_node_attempts
                   WHERE run_id = ? AND state = 'DISPATCHED' ORDER BY attempt_no, node_id""",
                (lease.run_id,),
            ).fetchall()
            for row in rows:
                attempt_id = str(row["attempt_id"])
                self._connection.execute(
                    """UPDATE dag_node_attempts SET state = 'UNCERTAIN',
                       effect_state = 'UNCERTAIN', updated_at = ? WHERE attempt_id = ?""",
                    (_now_iso(), attempt_id),
                )
                self._append_event(
                    lease,
                    event_key=f"attempt:{attempt_id}:effect-uncertain",
                    event_type="attempt_effect_uncertain",
                    entity_type="attempt",
                    entity_id=attempt_id,
                    attempt_id=attempt_id,
                    payload={"reason": "dispatched_without_staged_result"},
                )
            if rows:
                self._connection.execute(
                    """UPDATE dag_runs SET status = 'RECONCILIATION_REQUIRED',
                       verdict = 'DAG_ATTEMPT_EFFECT_UNCERTAIN', updated_at = ? WHERE run_id = ?""",
                    (_now_iso(), lease.run_id),
                )
        return tuple(
            attempt
            for attempt in self.list_attempts(lease.run_id)
            if attempt.state == "UNCERTAIN"
        )

    def list_attempts(self, run_id: str) -> tuple[StoredAttempt, ...]:
        rows = self._connection.execute(
            """SELECT a.*, o.staged_json, o.staged_sha256,
                      o.validation_json, o.validation_sha256,
                      o.committed_json, o.committed_sha256
               FROM dag_node_attempts a
               LEFT JOIN dag_attempt_outputs o ON o.attempt_id = a.attempt_id
               WHERE a.run_id = ? ORDER BY a.attempt_no, a.node_id""",
            (run_id,),
        ).fetchall()
        return tuple(self._stored_attempt(row) for row in rows)

    def load_events(self, run_id: str) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute(
            "SELECT * FROM dag_run_events WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            if canonical_sha256(payload) != row["payload_sha256"]:
                raise DagRunStoreError("dag_run_event_hash_mismatch", str(row["seq"]))
            events.append(
                {
                    "seq": int(row["seq"]),
                    "event_key": row["event_key"],
                    "event_type": row["event_type"],
                    "entity_type": row["entity_type"],
                    "entity_id": row["entity_id"],
                    "attempt_id": row["attempt_id"],
                    "lease_epoch": int(row["lease_epoch"]),
                    "payload": payload,
                }
            )
        return tuple(events)

    def append_runtime_event(
        self,
        lease: DagRunLease,
        event: RuntimeEvent,
    ) -> tuple[bool, int, RuntimeStateProjection]:
        """Append one normalized runtime observation without changing DAG authority."""

        if event.run_id != lease.run_id:
            raise DagRunStoreError("runtime_event_run_mismatch", event.event_id)
        event_key = f"runtime:{event.endpoint_lease_sha256}:{event.event_id}"
        event_payload = event.to_payload()
        identity_payload = dict(event_payload)
        identity_payload.pop("observed_at")
        identity_sha256 = canonical_sha256(identity_payload)
        transport_mode = _runtime_transport_mode(event)
        if transport_mode not in {"poll", "native"}:
            raise DagRunStoreError("runtime_event_transport_mode_invalid", event.event_id)
        journal_payload = {
            "schema": RUNTIME_EVENT_JOURNAL_ENTRY_SCHEMA,
            "runtime_event": event_payload,
            "runtime_event_sha256": canonical_sha256(event_payload),
            "runtime_event_identity_sha256": identity_sha256,
            "endpoint_lease_sha256": event.endpoint_lease_sha256,
            "transport_mode": transport_mode,
        }
        with self._transaction():
            self._assert_lease(lease)
            existing = self._event_by_key(lease.run_id, event_key)
            if existing is not None:
                existing_payload = _decoded_runtime_journal_payload(existing)
                if existing_payload.get("runtime_event_identity_sha256") != identity_sha256:
                    raise DagRunStoreError("runtime_event_conflict", event.event_id)
                sequence = int(existing["seq"])
                appended = False
            else:
                sequence = self._append_event(
                    lease,
                    event_key=event_key,
                    event_type="runtime_event_appended",
                    entity_type="runtime_endpoint",
                    entity_id=event.endpoint_lease_sha256,
                    payload=journal_payload,
                )
                appended = True
            projection = self.runtime_state_projection(
                lease.run_id, event.endpoint_lease_sha256
            )
            if projection is None:
                raise DagRunStoreError("runtime_event_projection_missing", event.event_id)
        return appended, sequence, projection

    def load_runtime_events(
        self,
        run_id: str,
        endpoint_lease_sha256: str | None = None,
    ) -> tuple[tuple[int, RuntimeEvent], ...]:
        runtime_events: list[tuple[int, RuntimeEvent]] = []
        for journal_event in self.load_events(run_id):
            if journal_event["event_type"] != "runtime_event_appended":
                continue
            payload = journal_event["payload"]
            if payload.get("schema") != RUNTIME_EVENT_JOURNAL_ENTRY_SCHEMA:
                raise DagRunStoreError(
                    "runtime_event_journal_schema_invalid", str(journal_event["seq"])
                )
            runtime_payload = payload.get("runtime_event")
            if not isinstance(runtime_payload, dict):
                raise DagRunStoreError(
                    "runtime_event_journal_invalid", str(journal_event["seq"])
                )
            if canonical_sha256(runtime_payload) != payload.get("runtime_event_sha256"):
                raise DagRunStoreError(
                    "runtime_event_hash_mismatch", str(journal_event["seq"])
                )
            identity_payload = dict(runtime_payload)
            identity_payload.pop("observed_at", None)
            if canonical_sha256(identity_payload) != payload.get(
                "runtime_event_identity_sha256"
            ):
                raise DagRunStoreError(
                    "runtime_event_identity_hash_mismatch", str(journal_event["seq"])
                )
            try:
                runtime_event = RuntimeEvent.from_payload(runtime_payload)
            except (TypeError, ValueError) as exc:
                raise DagRunStoreError(
                    "runtime_event_schema_invalid", str(journal_event["seq"])
                ) from exc
            if payload.get("endpoint_lease_sha256") != runtime_event.endpoint_lease_sha256:
                raise DagRunStoreError(
                    "runtime_event_endpoint_mismatch", runtime_event.event_id
                )
            if runtime_event.run_id != run_id:
                raise DagRunStoreError("runtime_event_run_mismatch", runtime_event.event_id)
            expected_key = (
                f"runtime:{runtime_event.endpoint_lease_sha256}:{runtime_event.event_id}"
            )
            if journal_event["event_key"] != expected_key:
                raise DagRunStoreError("runtime_event_key_mismatch", runtime_event.event_id)
            if payload.get("transport_mode") != _runtime_transport_mode(runtime_event):
                raise DagRunStoreError(
                    "runtime_event_transport_mode_mismatch", runtime_event.event_id
                )
            if (
                endpoint_lease_sha256 is None
                or runtime_event.endpoint_lease_sha256 == endpoint_lease_sha256
            ):
                runtime_events.append((int(journal_event["seq"]), runtime_event))
        return tuple(runtime_events)

    def runtime_state_projection(
        self,
        run_id: str,
        endpoint_lease_sha256: str,
    ) -> RuntimeStateProjection | None:
        events = self.load_runtime_events(run_id, endpoint_lease_sha256)
        if not events:
            return None
        latest = events[-1][1]
        return RuntimeStateProjection(
            run_id=run_id,
            endpoint_lease_sha256=endpoint_lease_sha256,
            state=latest.state,
            liveness=latest.liveness,
            confidence=latest.confidence,
            last_event_id=latest.event_id,
            event_count=len(events),
        )

    def runtime_event_cursor(
        self,
        run_id: str,
        endpoint_lease_sha256: str,
    ) -> str | None:
        events = self.load_runtime_events(run_id, endpoint_lease_sha256)
        if not events:
            return None
        latest = events[-1][1]
        observation = latest.observation.to_value()
        transport = observation.get("transport")
        if isinstance(transport, dict):
            cursor = transport.get("backend_cursor")
            if isinstance(cursor, str) and cursor:
                return cursor
        return latest.event_id

    def _change_attempt_state(
        self,
        lease: DagRunLease,
        attempt_id: str,
        *,
        allowed: set[str],
        target: str,
        event_type: str,
        event_key: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        with self._transaction():
            self._assert_lease(lease)
            row = self._attempt_row(attempt_id)
            if row["state"] == target:
                return
            if row["state"] not in allowed:
                raise DagRunStoreError("dag_attempt_state_invalid", str(row["state"]))
            event_seq = self._append_event(
                lease,
                event_key=f"attempt:{attempt_id}:{event_key}",
                event_type=event_type,
                entity_type="attempt",
                entity_id=attempt_id,
                attempt_id=attempt_id,
                payload=dict(payload or {}),
            )
            fields = "state = ?, updated_at = ?"
            values: list[Any] = [target, _now_iso()]
            if target == "DISPATCHED":
                fields += ", dispatch_event_seq = ?"
                values.append(event_seq)
            values.append(attempt_id)
            self._connection.execute(
                f"UPDATE dag_node_attempts SET {fields} WHERE attempt_id = ?", values
            )

    def _append_event(
        self,
        lease: DagRunLease,
        *,
        event_key: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: Mapping[str, Any],
        attempt_id: str | None = None,
        check_lease: bool = True,
    ) -> int:
        if check_lease:
            self._assert_lease(lease)
        payload_dict = dict(payload)
        payload_json = canonical_json(payload_dict)
        payload_sha256 = canonical_sha256(payload_dict)
        existing = self._event_by_key(lease.run_id, event_key)
        if existing is not None:
            if existing["payload_sha256"] != payload_sha256:
                raise DagRunStoreError("dag_run_event_conflict", event_key)
            return int(existing["seq"])
        cursor = self._connection.execute(
            """INSERT INTO dag_run_events(
                run_id, event_key, event_schema, event_version, event_type,
                entity_type, entity_id, attempt_id, lease_epoch,
                payload_json, payload_sha256, created_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lease.run_id,
                event_key,
                EVENT_SCHEMA,
                event_type,
                entity_type,
                entity_id,
                attempt_id,
                lease.epoch,
                payload_json,
                payload_sha256,
                _now_iso(),
            ),
        )
        if cursor.lastrowid is None:
            raise DagRunStoreError("dag_run_replay_invalid", event_key)
        return int(cursor.lastrowid)

    def _assert_lease(self, lease: DagRunLease, *, allow_expired: bool = False) -> None:
        row = self._connection.execute(
            """SELECT lease_owner, lease_epoch, lease_expires_at_ms
               FROM dag_runs WHERE run_id = ?""",
            (lease.run_id,),
        ).fetchone()
        if row is None:
            raise DagRunStoreError("dag_run_replay_invalid", lease.run_id)
        if row["lease_owner"] != lease.owner_id or int(row["lease_epoch"]) != lease.epoch:
            raise DagRunStoreError("dag_run_lease_lost", lease.run_id)
        if not allow_expired and int(row["lease_expires_at_ms"] or 0) <= _now_ms():
            raise DagRunStoreError("dag_run_lease_lost", "expired")

    def _attempt_row(self, attempt_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM dag_node_attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise DagRunStoreError("dag_attempt_identity_conflict", attempt_id)
        return cast(sqlite3.Row, row)

    def _output_row(self, attempt_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM dag_attempt_outputs WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise DagRunStoreError("dag_attempt_output_not_committed", attempt_id)
        return cast(sqlite3.Row, row)

    def _event_by_key(self, run_id: str, event_key: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._connection.execute(
            "SELECT * FROM dag_run_events WHERE run_id = ? AND event_key = ?",
            (run_id, event_key),
            ).fetchone(),
        )

    @staticmethod
    def _stored_attempt(row: sqlite3.Row) -> StoredAttempt:
        staged_result = SqliteDagRunStore._verified_output_projection(
            row["staged_json"], row["staged_sha256"], attempt_id=str(row["attempt_id"])
        )
        validation = SqliteDagRunStore._verified_output_projection(
            row["validation_json"],
            row["validation_sha256"],
            attempt_id=str(row["attempt_id"]),
        )
        committed_result = SqliteDagRunStore._verified_output_projection(
            row["committed_json"],
            row["committed_sha256"],
            attempt_id=str(row["attempt_id"]),
        )
        if validation is not None and staged_result is None:
            raise DagRunStoreError("dag_attempt_output_hash_mismatch", str(row["attempt_id"]))
        if committed_result is not None and committed_result != staged_result:
            raise DagRunStoreError("dag_attempt_output_hash_mismatch", str(row["attempt_id"]))
        identity = DagAttemptIdentity(
            run_id=str(row["run_id"]),
            node_id=str(row["node_id"]),
            attempt=int(row["attempt_no"]),
            attempt_id=str(row["attempt_id"]),
            idempotency_key=str(row["idempotency_key"]),
            recovered=True,
        )
        return StoredAttempt(
            identity=identity,
            state=str(row["state"]),
            effect_state=str(row["effect_state"]),
            staged_result=staged_result,
            committed_result=committed_result,
        )

    @staticmethod
    def _verified_output_projection(
        raw_json: str | None,
        claimed_sha256: str | None,
        *,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        if raw_json is None and claimed_sha256 is None:
            return None
        if raw_json is None or claimed_sha256 is None:
            raise DagRunStoreError("dag_attempt_output_hash_mismatch", attempt_id)
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise DagRunStoreError("dag_attempt_output_hash_mismatch", attempt_id) from exc
        if not isinstance(payload, dict) or canonical_sha256(payload) != claimed_sha256:
            raise DagRunStoreError("dag_attempt_output_hash_mismatch", attempt_id)
        return payload

    class _Transaction:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __enter__(self) -> None:
            self.connection.execute("BEGIN IMMEDIATE")

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            self.connection.execute("ROLLBACK" if exc_type else "COMMIT")

    def _transaction(self) -> _Transaction:
        return self._Transaction(self._connection)
