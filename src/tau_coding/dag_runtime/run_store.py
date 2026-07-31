"""Transactional SQLite journal for the canonical DAG scheduler."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from tau_coding.dag_runtime.model import (
    DAG_PLAN_SCHEMA,
    DagPlan,
    DagPlanContextBinding,
    DagPlanEdge,
    DagPlanNode,
    DagPlanTerminal,
    FrozenJson,
    canonical_json,
    canonical_sha256,
)
from tau_coding.dag_viewer.redaction import redact_for_storage
from tau_coding.runtime_backends.contracts import RuntimeEvent, RuntimeStateProjection

EVENT_SCHEMA = "tau.dag_run_event.v1"
RUNTIME_EVENT_JOURNAL_ENTRY_SCHEMA = "tau.runtime_event_journal_entry.v1"
DIAGNOSTIC_EVENT_SCHEMA = "tau.dag_diagnostic_event.v1"
CORRECTION_JOURNAL_ENTRY_SCHEMA = "tau.correction_journal_entry.v1"
MAX_DIAGNOSTIC_EVENT_BYTES = 64 * 1024
STORE_SCHEMA_VERSION = 3
STORE_COMPATIBLE_READ_VERSIONS = frozenset({1, STORE_SCHEMA_VERSION})
DAG_RUN_RECONCILIATION_DECISION_SCHEMA = "tau.dag_run_reconciliation_decision.v1"
DAG_RUN_STALE_LEASE_CLEAR_SCHEMA = "tau.dag_run_stale_lease_clear.v1"


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


@dataclass(frozen=True, slots=True)
class DagRunRecord:
    run_id: str
    plan_id: str
    plan_sha256: str
    status: str
    verdict: str | None
    lease_owner: str | None
    lease_epoch: int
    lease_expires_at_ms: int | None


@dataclass(frozen=True, slots=True)
class DagJournalEvent:
    sequence: int
    event_key: str
    event_type: str
    entity_type: str
    entity_id: str
    attempt_id: str | None
    lease_epoch: int
    created_at: str
    payload: dict[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "seq": self.sequence,
            "event_key": self.event_key,
            "event_type": self.event_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "attempt_id": self.attempt_id,
            "lease_epoch": self.lease_epoch,
            "created_at": self.created_at,
            "payload": self.payload,
        }


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
        status IN ('RUNNING', 'PASS', 'BLOCKED', 'CANCELLED', 'RECONCILIATION_REQUIRED')
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

CREATE INDEX IF NOT EXISTS idx_dag_run_events_runtime_endpoint
ON dag_run_events(run_id, event_type, entity_type, entity_id, seq);

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

CREATE TABLE IF NOT EXISTS dag_store_migrations (
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY(from_version, to_version)
);

CREATE TABLE IF NOT EXISTS receipt_admissions (
    admission_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES dag_runs(run_id),
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    receipt_kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    legacy INTEGER NOT NULL DEFAULT 0,
    admitted_event_seq INTEGER,
    admitted_at TEXT NOT NULL,
    UNIQUE(run_id, node_id, attempt_id, receipt_kind)
);

CREATE TRIGGER IF NOT EXISTS receipt_admissions_no_update
BEFORE UPDATE ON receipt_admissions
BEGIN
    SELECT RAISE(ABORT, 'receipt_admissions is append-only');
END;

CREATE TRIGGER IF NOT EXISTS receipt_admissions_no_delete
BEFORE DELETE ON receipt_admissions
BEGIN
    SELECT RAISE(ABORT, 'receipt_admissions is append-only');
END;

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


def _store_version_detail(actual: int | str | None, expected: int = STORE_SCHEMA_VERSION) -> str:
    return f"actual={actual if actual is not None else 'missing'} expected={expected}"


def _store_schema_version(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        "SELECT value FROM dag_store_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        return user_version if user_version else None
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise DagRunStoreError(
            "dag_run_store_schema_mismatch", _store_version_detail(str(row[0]))
        ) from exc


def _record_store_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        """
        INSERT INTO dag_store_meta(key, value) VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(version),),
    )
    connection.execute(f"PRAGMA user_version = {version}")


def _migrate_store_schema(connection: sqlite3.Connection, actual_version: int | None) -> None:
    if actual_version is None:
        _record_store_version(connection, STORE_SCHEMA_VERSION)
        return
    if actual_version > STORE_SCHEMA_VERSION:
        raise DagRunStoreError(
            "dag_run_store_schema_mismatch",
            _store_version_detail(actual_version),
        )
    version = actual_version
    while version < STORE_SCHEMA_VERSION:
        migration = _STORE_MIGRATIONS.get(version)
        if migration is None:
            raise DagRunStoreError(
                "dag_run_store_schema_mismatch",
                _store_version_detail(actual_version),
            )
        next_version = version + 1
        migration(connection)
        connection.execute(
            """
            INSERT OR IGNORE INTO dag_store_migrations(from_version, to_version, applied_at)
            VALUES (?, ?, ?)
            """,
            (version, next_version, _now_iso()),
        )
        _record_store_version(connection, next_version)
        version = next_version


def _migrate_store_v1_to_v2(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dag_store_migrations (
            from_version INTEGER NOT NULL,
            to_version INTEGER NOT NULL,
            applied_at TEXT NOT NULL,
            PRIMARY KEY(from_version, to_version)
        )
        """
    )


def _migrate_store_v2_to_v3(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS receipt_admissions (
            admission_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES dag_runs(run_id),
            node_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            receipt_kind TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            legacy INTEGER NOT NULL DEFAULT 0,
            admitted_event_seq INTEGER,
            admitted_at TEXT NOT NULL,
            UNIQUE(run_id, node_id, attempt_id, receipt_kind)
        );
        CREATE TRIGGER IF NOT EXISTS receipt_admissions_no_update
        BEFORE UPDATE ON receipt_admissions
        BEGIN
            SELECT RAISE(ABORT, 'receipt_admissions is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS receipt_admissions_no_delete
        BEFORE DELETE ON receipt_admissions
        BEGIN
            SELECT RAISE(ABORT, 'receipt_admissions is append-only');
        END;
        """
    )


_STORE_MIGRATIONS = {1: _migrate_store_v1_to_v2, 2: _migrate_store_v2_to_v3}


def _refresh_runtime_journal_hashes(payload: dict[str, Any]) -> None:
    if payload.get("schema") != RUNTIME_EVENT_JOURNAL_ENTRY_SCHEMA:
        return
    runtime_payload = payload.get("runtime_event")
    if not isinstance(runtime_payload, dict):
        return
    payload["runtime_event_sha256"] = canonical_sha256(runtime_payload)
    identity_payload = dict(runtime_payload)
    identity_payload.pop("observed_at", None)
    payload["runtime_event_identity_sha256"] = canonical_sha256(identity_payload)


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


def _runtime_event_is_lossy(event: RuntimeEvent) -> bool:
    transport = event.observation.to_value().get("transport")
    return not isinstance(transport, dict) or (
        transport.get("raw_payload_sha256") is None
        or transport.get("raw_payload_truncated") is True
    )


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
    if canonical_sha256(identity_payload) != payload.get("runtime_event_identity_sha256"):
        raise DagRunStoreError("runtime_event_identity_hash_mismatch", str(row["seq"]))
    return payload


def _runtime_event_from_journal_row(
    row: sqlite3.Row,
    *,
    expected_run_id: str,
) -> RuntimeEvent:
    if (
        row["event_schema"] != EVENT_SCHEMA
        or int(row["event_version"]) != 1
        or row["event_type"] != "runtime_event_appended"
        or row["entity_type"] != "runtime_endpoint"
    ):
        raise DagRunStoreError("runtime_event_journal_metadata_invalid", str(row["seq"]))
    payload = _decoded_runtime_journal_payload(row)
    runtime_payload = cast(dict[str, Any], payload["runtime_event"])
    try:
        runtime_event = RuntimeEvent.from_payload(runtime_payload)
    except (TypeError, ValueError) as exc:
        raise DagRunStoreError("runtime_event_schema_invalid", str(row["seq"])) from exc
    if payload.get("endpoint_lease_sha256") != runtime_event.endpoint_lease_sha256:
        raise DagRunStoreError("runtime_event_endpoint_mismatch", runtime_event.event_id)
    if row["entity_id"] != runtime_event.endpoint_lease_sha256:
        raise DagRunStoreError("runtime_event_endpoint_mismatch", runtime_event.event_id)
    if runtime_event.run_id != expected_run_id:
        raise DagRunStoreError("runtime_event_run_mismatch", runtime_event.event_id)
    expected_key = f"runtime:{runtime_event.endpoint_lease_sha256}:{runtime_event.event_id}"
    if row["event_key"] != expected_key:
        raise DagRunStoreError("runtime_event_key_mismatch", runtime_event.event_id)
    if payload.get("transport_mode") != _runtime_transport_mode(runtime_event):
        raise DagRunStoreError("runtime_event_transport_mode_mismatch", runtime_event.event_id)
    return runtime_event


def _verified_event(row: sqlite3.Row) -> DagJournalEvent:
    if row["event_schema"] != EVENT_SCHEMA or int(row["event_version"]) != 1:
        raise DagRunStoreError("dag_run_event_schema_invalid", str(row["seq"]))
    try:
        payload = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise DagRunStoreError("dag_run_event_invalid", str(row["seq"])) from exc
    if not isinstance(payload, dict) or canonical_sha256(payload) != row["payload_sha256"]:
        raise DagRunStoreError("dag_run_event_hash_mismatch", str(row["seq"]))
    return DagJournalEvent(
        sequence=int(row["seq"]),
        event_key=str(row["event_key"]),
        event_type=str(row["event_type"]),
        entity_type=str(row["entity_type"]),
        entity_id=str(row["entity_id"]),
        attempt_id=str(row["attempt_id"]) if row["attempt_id"] is not None else None,
        lease_epoch=int(row["lease_epoch"]),
        created_at=str(row["created_at"]),
        payload=payload,
    )


def _plan_from_payload(payload: dict[str, Any]) -> DagPlan:
    if payload.get("schema") != DAG_PLAN_SCHEMA:
        raise DagRunStoreError("dag_run_plan_schema_invalid")
    source = payload.get("source")
    completion = payload.get("completion_policy")
    if not isinstance(source, dict) or not isinstance(completion, dict):
        raise DagRunStoreError("dag_run_plan_schema_invalid")
    try:
        plan = DagPlan(
            schema=str(payload["schema"]),
            plan_id=str(payload["plan_id"]),
            source_family=str(source["family"]),
            source_schema=str(source["schema"]),
            source_logical_id=str(source["logical_id"]),
            source_payload_sha256=str(source["canonical_source_sha256"]),
            goal_binding=FrozenJson.from_value(payload["goal_binding"]),
            target_binding=FrozenJson.from_value(payload["target_binding"]),
            entry_node_ids=tuple(str(item) for item in payload["entry_node_ids"]),
            terminal_endpoints=tuple(
                DagPlanTerminal(str(item["terminal_id"]), str(item["kind"]), str(item["origin"]))
                for item in payload["terminal_endpoints"]
            ),
            completion_policy=str(completion["kind"]),
            nodes=tuple(_plan_node_from_payload(item) for item in payload["nodes"]),
            control_edges=tuple(_plan_edge_from_payload(item) for item in payload["control_edges"]),
            context_bindings=tuple(
                _plan_context_binding_from_payload(item) for item in payload["context_bindings"]
            ),
            runtime_bindings=tuple(
                FrozenJson.from_value(item) for item in payload["runtime_bindings"]
            ),
            route_contracts=tuple(
                FrozenJson.from_value(item) for item in payload["route_contracts"]
            ),
            join_contracts=tuple(FrozenJson.from_value(item) for item in payload["join_contracts"]),
            required_evidence=tuple(str(item) for item in payload["required_evidence"]),
            fail_closed_on=tuple(str(item) for item in payload["fail_closed_on"]),
            security_declarations=FrozenJson.from_value(payload["security_declarations"]),
            execution_limits=FrozenJson.from_value(payload["execution_limits"]),
            source_extensions=FrozenJson.from_value(payload["source_extensions"]),
            plan_sha256=str(payload["plan_sha256"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DagRunStoreError("dag_run_plan_schema_invalid") from exc
    if plan.with_computed_hash().plan_sha256 != plan.plan_sha256:
        raise DagRunStoreError("dag_run_plan_hash_mismatch", plan.plan_id)
    return plan


def _plan_node_from_payload(item: Mapping[str, Any]) -> DagPlanNode:
    adapter = cast(Mapping[str, Any], item["adapter"])
    retry = cast(Mapping[str, Any], item["retry_policy"])
    timeout = cast(Mapping[str, Any], item["timeout_policy"])
    return DagPlanNode(
        node_id=str(item["node_id"]),
        role=str(item["role"]),
        executor=str(item["executor"]),
        adapter_kind=str(adapter["kind"]),
        adapter_config=FrozenJson.from_value(adapter["config"]),
        max_attempts=int(retry["max_attempts"]),
        timeout_kind=str(timeout["kind"]),
        timeout_seconds=float(timeout["seconds"]) if timeout["seconds"] is not None else None,
        required_evidence=tuple(str(value) for value in item["required_evidence"]),
        static_context=FrozenJson.from_value(item["static_context"]),
        requested_capabilities=tuple(
            FrozenJson.from_value(value) for value in item["requested_capabilities"]
        ),
        source_bindings=tuple(FrozenJson.from_value(value) for value in item["source_bindings"]),
        source_extensions=FrozenJson.from_value(item["source_extensions"]),
        runtime_requirement=FrozenJson.from_value(item["runtime_requirement"]),
    )


def _plan_edge_from_payload(item: Mapping[str, Any]) -> DagPlanEdge:
    target = cast(Mapping[str, Any], item["target"])
    return DagPlanEdge(
        edge_id=str(item["edge_id"]),
        source_node_id=str(item["source_node_id"]),
        target_id=str(target["id"]),
        target_kind=str(target["kind"]),
        condition=FrozenJson.from_value(item["condition"])
        if item["condition"] is not None
        else None,
        source_ordinal=int(item["source_ordinal"]) if item["source_ordinal"] is not None else None,
    )


def _plan_context_binding_from_payload(item: Mapping[str, Any]) -> DagPlanContextBinding:
    accepted_schemas = item.get("accepted_source_schemas", ("*",))
    if isinstance(accepted_schemas, str):
        accepted_source_schemas = (accepted_schemas,)
    elif isinstance(accepted_schemas, list | tuple):
        accepted_source_schemas = tuple(str(value) for value in accepted_schemas)
    else:
        accepted_source_schemas = ("*",)
    return DagPlanContextBinding(
        binding_id=str(item["binding_id"]),
        source_node_id=str(item["source_node_id"]),
        target_node_id=str(item["target_node_id"]),
        control_edge_id=str(item["control_edge_id"]),
        projection=str(item["projection"]),
        activation=str(item["activation"]),
        origin=str(item["origin"]),
        accepted_source_schemas=accepted_source_schemas or ("*",),
        selector_kind=str(item.get("selector_kind", "accepted_output")),
        materialization_mode=str(item.get("materialization_mode", "by_value")),
        on_missing=str(item.get("on_missing", "omit")),
        on_invalid=str(item.get("on_invalid", "omit")),
        max_reference_bytes=(
            int(item["max_reference_bytes"])
            if item.get("max_reference_bytes") is not None
            else None
        ),
    )


class SqliteDagRunReader:
    """Query-only reader for live or completed durable DAG runs."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        if not self.path.is_file():
            raise DagRunStoreError("dag_run_store_missing", str(self.path))
        uri = f"file:{quote(str(self.path), safe='/')}?mode=ro"
        self._connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA query_only = ON")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 1000")
        version = self._connection.execute(
            "SELECT value FROM dag_store_meta WHERE key = 'schema_version'"
        ).fetchone()
        if version is None:
            raise DagRunStoreError(
                "dag_run_store_schema_mismatch",
                _store_version_detail(None),
            )
        try:
            actual_version = int(version[0])
        except (TypeError, ValueError) as exc:
            raise DagRunStoreError(
                "dag_run_store_schema_mismatch",
                _store_version_detail(str(version[0])),
            ) from exc
        if actual_version not in STORE_COMPATIBLE_READ_VERSIONS:
            raise DagRunStoreError(
                "dag_run_store_schema_mismatch",
                _store_version_detail(actual_version),
            )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SqliteDagRunReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    class _Snapshot:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __enter__(self) -> None:
            self.connection.execute("BEGIN")

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            self.connection.execute("ROLLBACK" if exc_type else "COMMIT")

    def snapshot(self) -> _Snapshot:
        """Hold one consistent read snapshot across all projection queries."""

        return self._Snapshot(self._connection)

    def load_run_record(self, run_id: str) -> DagRunRecord:
        row = self._connection.execute(
            "SELECT * FROM dag_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise DagRunStoreError("dag_run_missing", run_id)
        return DagRunRecord(
            run_id=str(row["run_id"]),
            plan_id=str(row["plan_id"]),
            plan_sha256=str(row["plan_sha256"]),
            status=str(row["status"]),
            verdict=str(row["verdict"]) if row["verdict"] is not None else None,
            lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
            lease_epoch=int(row["lease_epoch"]),
            lease_expires_at_ms=int(row["lease_expires_at_ms"])
            if row["lease_expires_at_ms"] is not None
            else None,
        )

    def run_ids(self) -> tuple[str, ...]:
        rows = self._connection.execute("SELECT run_id FROM dag_runs ORDER BY run_id").fetchall()
        return tuple(str(row[0]) for row in rows)

    def load_plan(self, run_id: str) -> DagPlan:
        row = self._connection.execute(
            "SELECT plan_json, plan_sha256 FROM dag_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise DagRunStoreError("dag_run_missing", run_id)
        try:
            payload = json.loads(row["plan_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise DagRunStoreError("dag_run_plan_schema_invalid", run_id) from exc
        if not isinstance(payload, dict):
            raise DagRunStoreError("dag_run_plan_schema_invalid", run_id)
        plan = _plan_from_payload(payload)
        if plan.plan_sha256 != row["plan_sha256"]:
            raise DagRunStoreError("dag_run_plan_hash_mismatch", run_id)
        return plan

    def load_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        limit: int = 500,
    ) -> tuple[DagJournalEvent, ...]:
        if (
            after_sequence < 0
            or through_sequence is not None
            and through_sequence < 1
            or through_sequence is not None
            and through_sequence <= after_sequence
            or limit < 1
            or limit > 5000
        ):
            raise DagRunStoreError("dag_viewer_event_range_invalid")
        if through_sequence is None:
            rows = self._connection.execute(
                "SELECT * FROM dag_run_events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, after_sequence, limit),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """SELECT * FROM dag_run_events
                   WHERE run_id = ? AND seq > ? AND seq <= ? ORDER BY seq LIMIT ?""",
                (run_id, after_sequence, through_sequence, limit),
            ).fetchall()
        return tuple(_verified_event(cast(sqlite3.Row, row)) for row in rows)

    def event_sequence_belongs_to_run(self, run_id: str, sequence: int) -> bool:
        if sequence < 1:
            return False
        row = self._connection.execute(
            "SELECT 1 FROM dag_run_events WHERE run_id = ? AND seq = ?",
            (run_id, sequence),
        ).fetchone()
        return row is not None

    def load_attempts(self, run_id: str) -> tuple[StoredAttempt, ...]:
        rows = self._connection.execute(
            """SELECT a.*, o.staged_json, o.staged_sha256, o.validation_json,
                      o.validation_sha256, o.committed_json, o.committed_sha256
               FROM dag_node_attempts a LEFT JOIN dag_attempt_outputs o
               ON o.attempt_id = a.attempt_id WHERE a.run_id = ?
               ORDER BY a.attempt_no, a.node_id""",
            (run_id,),
        ).fetchall()
        return tuple(SqliteDagRunStore._stored_attempt(cast(sqlite3.Row, row)) for row in rows)

    def runtime_projections(self, run_id: str) -> tuple[RuntimeStateProjection, ...]:
        rows = self._connection.execute(
            """SELECT * FROM dag_run_events
               WHERE run_id = ? AND event_type = 'runtime_event_appended'
               ORDER BY seq""",
            (run_id,),
        ).fetchall()
        grouped: dict[str, list[RuntimeEvent]] = {}
        for row in rows:
            event = _runtime_event_from_journal_row(cast(sqlite3.Row, row), expected_run_id=run_id)
            grouped.setdefault(event.endpoint_lease_sha256, []).append(event)
        return tuple(
            RuntimeStateProjection(
                run_id,
                endpoint,
                values[-1].state,
                values[-1].liveness,
                values[-1].confidence,
                values[-1].event_id,
                len(values),
            )
            for endpoint, values in sorted(grouped.items())
        )

    def latest_sequence(self, run_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM dag_run_events WHERE run_id = ?", (run_id,)
        ).fetchone()
        return int(row[0])

    def list_admissions(
        self,
        run_id: str,
        *,
        receipt_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        if receipt_kind is None:
            rows = self._connection.execute(
                """SELECT * FROM receipt_admissions WHERE run_id = ?
                   ORDER BY node_id, attempt_id, receipt_kind""",
                (run_id,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """SELECT * FROM receipt_admissions
                   WHERE run_id = ? AND receipt_kind = ?
                   ORDER BY node_id, attempt_id, receipt_kind""",
                (run_id, receipt_kind),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_admission(self, run_id: str, admission_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM receipt_admissions WHERE run_id = ? AND admission_id = ?",
            (run_id, admission_id),
        ).fetchone()
        if row is None:
            raise DagRunStoreError("dag_admission_missing", admission_id)
        return dict(row)

    def review_scope_snapshot(
        self,
        run_id: str,
        *,
        goal_hash: str,
        reviewed_node_ids: tuple[str, ...] | list[str] | None = None,
        journal_sequence_start: int = 0,
        through_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Return a reviewer-scope snapshot from the durable run store."""

        with self.snapshot():
            record = self.load_run_record(run_id)
            sequence_end = (
                self.latest_sequence(run_id) if through_sequence is None else through_sequence
            )
            nodes = set(reviewed_node_ids or ())
            attempts = [
                attempt
                for attempt in self.load_attempts(run_id)
                if not nodes or attempt.identity.node_id in nodes
            ]
            if not nodes:
                nodes = {attempt.identity.node_id for attempt in attempts}
            attempt_ids = {attempt.identity.attempt_id for attempt in attempts}
            artifacts = [
                _review_scope_admission_descriptor(row)
                for row in self.list_admissions(run_id)
                if row.get("attempt_id") in attempt_ids
            ]
        return {
            "schema": "tau.review_scope.v1",
            "goal_hash": goal_hash,
            "plan_sha256": record.plan_sha256,
            "reviewed_node_ids": sorted(nodes),
            "reviewed_attempt_ids": sorted(attempt_ids),
            "admitted_artifacts": sorted(
                artifacts,
                key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
            ),
            "journal_sequence_start": journal_sequence_start,
            "journal_sequence_end": sequence_end,
        }


class SqliteDagRunStore:
    """File-backed append-only event journal with transactional projections."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection = sqlite3.connect(self.path, isolation_level=None)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            journal_mode = str(self._connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
            if journal_mode.lower() != "wal":
                raise DagRunStoreError("dag_run_store_wal_unavailable", journal_mode)
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.executescript(_SCHEMA)
            _migrate_store_schema(self._connection, _store_schema_version(self._connection))
        except DagRunStoreError:
            if hasattr(self, "_connection"):
                with suppress(Exception):
                    self._connection.close()
            raise
        except sqlite3.Error as exc:
            if hasattr(self, "_connection"):
                with suppress(Exception):
                    self._connection.close()
            raise DagRunStoreError("dag_run_store_open_failed", f"{self.path}:{exc}") from exc

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

    def assert_active_lease(self, lease: DagRunLease) -> None:
        """Fail closed unless ``lease`` still owns the authoritative run."""

        self._assert_lease(lease)

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
        return str(row["status"]), (str(row["verdict"]) if row["verdict"] is not None else None)

    def load_run_record(self, run_id: str) -> DagRunRecord:
        row = self._connection.execute(
            "SELECT * FROM dag_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise DagRunStoreError("dag_run_missing", run_id)
        return DagRunRecord(
            run_id=str(row["run_id"]),
            plan_id=str(row["plan_id"]),
            plan_sha256=str(row["plan_sha256"]),
            status=str(row["status"]),
            verdict=str(row["verdict"]) if row["verdict"] is not None else None,
            lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
            lease_epoch=int(row["lease_epoch"]),
            lease_expires_at_ms=int(row["lease_expires_at_ms"])
            if row["lease_expires_at_ms"] is not None
            else None,
        )

    def reconciliation_required_runs(self) -> tuple[DagRunRecord, ...]:
        """Return runs waiting for an explicit operator reconciliation decision."""

        rows = self._connection.execute(
            "SELECT run_id FROM dag_runs WHERE status = 'RECONCILIATION_REQUIRED' "
            "ORDER BY updated_at, run_id"
        ).fetchall()
        return tuple(self.load_run_record(str(row["run_id"])) for row in rows)

    def resolve_reconciliation_required_run(
        self,
        *,
        run_id: str,
        decision: str,
        operator_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Record an operator decision for a run with uncertain dispatched effects."""

        normalized_decision = decision.strip().lower().replace("-", "_")
        if normalized_decision in {"reconcile", "authorize", "authorize_new_generation"}:
            normalized_decision = "authorize_new_generation"
            verdict = "DAG_RECONCILED_OPERATOR_AUTHORIZED_NEW_GENERATION"
        elif normalized_decision == "abandon":
            verdict = "DAG_RUN_ABANDONED_AFTER_UNCERTAIN_EFFECT"
        else:
            raise DagRunStoreError("dag_reconciliation_decision_invalid", decision)
        if not operator_id.strip():
            raise DagRunStoreError("dag_reconciliation_operator_missing")
        if not reason.strip():
            raise DagRunStoreError("dag_reconciliation_reason_missing")

        with self._transaction():
            row = self._connection.execute(
                "SELECT * FROM dag_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise DagRunStoreError("dag_run_missing", run_id)
            if row["status"] != "RECONCILIATION_REQUIRED":
                raise DagRunStoreError("dag_run_not_reconciliation_required", run_id)
            uncertain_attempts = [
                _stored_attempt_to_receipt(attempt)
                for attempt in self.list_attempts(run_id)
                if attempt.state == "UNCERTAIN"
            ]
            if not uncertain_attempts:
                raise DagRunStoreError("dag_run_reconciliation_attempts_missing", run_id)
            before_event_count = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM dag_run_events WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
            )
            lease = DagRunLease(
                run_id=run_id,
                owner_id=operator_id,
                epoch=int(row["lease_epoch"]),
                expires_at_ms=0,
            )
            payload = {
                "schema": DAG_RUN_RECONCILIATION_DECISION_SCHEMA,
                "run_id": run_id,
                "decision": normalized_decision,
                "operator_id": operator_id,
                "reason": reason,
                "previous_status": row["status"],
                "previous_verdict": row["verdict"],
                "uncertain_attempts": uncertain_attempts,
                "terminal_status": "BLOCKED",
                "terminal_verdict": verdict,
            }
            event_seq = self._append_event(
                lease,
                event_key=f"run:reconciliation:{normalized_decision}",
                event_type="run_reconciliation_decision_recorded",
                entity_type="run",
                entity_id=run_id,
                payload=payload,
                check_lease=False,
            )
            self._connection.execute(
                """UPDATE dag_runs SET status = 'BLOCKED', verdict = ?,
                   lease_owner = NULL, lease_expires_at_ms = NULL, updated_at = ?
                   WHERE run_id = ?""",
                (verdict, _now_iso(), run_id),
            )
            after_event_count = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM dag_run_events WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
            )
            receipt = {
                **payload,
                "ok": True,
                "status": "PASS",
                "event_seq": event_seq,
                "event_count_before": before_event_count,
                "event_count_after": after_event_count,
                "journal_preserved": after_event_count > before_event_count,
                "next_execution_run_id": (
                    self.execution_run_id(_base_run_id(run_id))
                    if normalized_decision == "authorize_new_generation"
                    else None
                ),
                "created_at": _now_iso(),
            }
        return receipt

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

    def append_diagnostic_event(
        self,
        lease: DagRunLease,
        *,
        event_key: str,
        node_id: str,
        payload: Mapping[str, Any],
        attempt_id: str | None = None,
    ) -> int:
        """Append bounded diagnostic evidence without changing scheduler state."""

        value = dict(payload)
        if value.get("schema") != DIAGNOSTIC_EVENT_SCHEMA:
            raise DagRunStoreError("dag_diagnostic_event_schema_invalid")
        if len(canonical_json(value).encode("utf-8")) > MAX_DIAGNOSTIC_EVENT_BYTES:
            raise DagRunStoreError("dag_diagnostic_event_too_large")
        with self._transaction():
            self._assert_lease(lease)
            return self._append_event(
                lease,
                event_key=event_key,
                event_type="dag_diagnostic_event_appended",
                entity_type="node",
                entity_id=node_id,
                attempt_id=attempt_id,
                payload=value,
            )

    def append_correction_event(
        self,
        lease: DagRunLease,
        *,
        event_key: str,
        incident_id: str,
        payload: Mapping[str, Any],
    ) -> int:
        """Append one idempotent correction transition under the run lease."""

        value = dict(payload)
        if value.get("schema") != CORRECTION_JOURNAL_ENTRY_SCHEMA:
            raise DagRunStoreError("correction_journal_entry_schema_invalid")
        if value.get("incident_id") != incident_id:
            raise DagRunStoreError("correction_incident_binding_mismatch", incident_id)
        with self._transaction():
            self._assert_lease(lease)
            return self._append_event(
                lease,
                event_key=event_key,
                event_type="correction_state_committed",
                entity_type="correction",
                entity_id=incident_id,
                payload=value,
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

    def clear_stale_lease(
        self,
        *,
        run_id: str,
        operator_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Clear a visible stale lease without deleting the run journal."""

        if not operator_id.strip():
            raise DagRunStoreError("dag_stale_lease_operator_missing")
        if not reason.strip():
            raise DagRunStoreError("dag_stale_lease_reason_missing")
        with self._transaction():
            row = self._connection.execute(
                "SELECT * FROM dag_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise DagRunStoreError("dag_run_missing", run_id)
            prior_owner = row["lease_owner"]
            prior_expiry = row["lease_expires_at_ms"]
            if prior_owner is None:
                raise DagRunStoreError("dag_run_lease_not_held", run_id)
            lease = DagRunLease(
                run_id=run_id,
                owner_id=operator_id,
                epoch=int(row["lease_epoch"]),
                expires_at_ms=int(prior_expiry or 0),
            )
            payload = {
                "schema": DAG_RUN_STALE_LEASE_CLEAR_SCHEMA,
                "run_id": run_id,
                "operator_id": operator_id,
                "reason": reason,
                "prior_owner_id": prior_owner,
                "prior_expires_at_ms": prior_expiry,
                "status": row["status"],
                "verdict": row["verdict"],
            }
            event_seq = self._append_event(
                lease,
                event_key=f"lease:{lease.epoch}:operator-cleared",
                event_type="run_stale_lease_cleared",
                entity_type="run",
                entity_id=run_id,
                payload=payload,
                check_lease=False,
            )
            self._connection.execute(
                """UPDATE dag_runs SET lease_owner = NULL, lease_expires_at_ms = NULL,
                   updated_at = ? WHERE run_id = ?""",
                (_now_iso(), run_id),
            )
            return {
                **payload,
                "ok": True,
                "event_seq": event_seq,
                "lease_released": True,
                "journal_preserved": True,
                "created_at": _now_iso(),
            }

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
        redacted_result = cast(dict[str, Any], redact_for_storage(dict(result)).value)
        canonical = canonical_json(redacted_result)
        digest = canonical_sha256(redacted_result)
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
            self._observe_admission_gap(lease, attempt_id, str(attempt["node_id"]))

    def _observe_admission_gap(self, lease: DagRunLease, attempt_id: str, node_id: str) -> None:
        """Shadow-mode invariant (#202): record, never block.

        Every terminal settlement without an admission row is appended to a
        JSONL bypass ledger beside the store. This measures which legacy
        writer paths still bypass admission before enforcement (contract A7
        step 5) flips on; ledger write failures are swallowed because
        observation must not change production semantics.
        """

        row = self._connection.execute(
            "SELECT COUNT(*) FROM receipt_admissions WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if row is not None and int(row[0]) > 0:
            return
        entry = json.dumps(
            {
                "schema": "tau.admission_bypass.v1",
                "run_id": lease.run_id,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "observed_at": _now_iso(),
            },
            sort_keys=True,
        )
        ledger = self.path.parent / "admission-bypass-ledger.jsonl"
        try:
            fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.write(fd, entry.encode("utf-8") + b"\n")
            finally:
                os.close(fd)
        except OSError:
            pass

    def admit_receipt(
        self,
        lease: DagRunLease,
        attempt_id: str,
        *,
        receipt_kind: str,
        sha256: str,
        path: str,
        size_bytes: int,
        legacy: bool = False,
    ) -> dict[str, Any]:
        """Commit one authoritative receipt admission (contract S7, #199).

        Inside one BEGIN IMMEDIATE transaction: verify the attempt exists,
        insert the admission row, and append the ``receipt_admitted`` event.
        A duplicate admitting writer loses at the UNIQUE constraint: an
        identical digest returns the existing row with ``duplicate=True``
        (idempotent recovery per contract A1) and must not error the node; a
        different digest raises ``dag_admission_conflict`` because two
        non-identical receipts can never both be authoritative for one
        (attempt, kind).
        """

        if not receipt_kind or not sha256.startswith("sha256:"):
            raise DagRunStoreError("dag_admission_invalid", f"{attempt_id}:{receipt_kind}")
        with self._transaction():
            self._assert_lease(lease)
            attempt = self._attempt_row(attempt_id)
            node_id = str(attempt["node_id"])
            existing = self._connection.execute(
                """SELECT * FROM receipt_admissions
                   WHERE run_id = ? AND node_id = ? AND attempt_id = ? AND receipt_kind = ?""",
                (lease.run_id, node_id, attempt_id, receipt_kind),
            ).fetchone()
            if existing is not None:
                if existing["sha256"] != sha256:
                    raise DagRunStoreError("dag_admission_conflict", f"{attempt_id}:{receipt_kind}")
                self._append_event(
                    lease,
                    event_key=f"admission:{attempt_id}:{receipt_kind}:duplicate",
                    event_type="receipt_admission_duplicate_suppressed",
                    entity_type="attempt",
                    entity_id=attempt_id,
                    attempt_id=attempt_id,
                    payload={"receipt_kind": receipt_kind, "sha256": sha256},
                )
                return {**dict(existing), "duplicate": True}
            admitted_at = _now_iso()
            admission_id = canonical_sha256(
                {
                    "schema": "tau.receipt_admission_identity.v1",
                    "run_id": lease.run_id,
                    "node_id": node_id,
                    "attempt_id": attempt_id,
                    "receipt_kind": receipt_kind,
                }
            ).removeprefix("sha256:")[:32]
            event_seq = self._append_event(
                lease,
                event_key=f"admission:{attempt_id}:{receipt_kind}",
                event_type="receipt_admitted",
                entity_type="attempt",
                entity_id=attempt_id,
                attempt_id=attempt_id,
                payload={
                    "receipt_kind": receipt_kind,
                    "sha256": sha256,
                    "path": path,
                    "size_bytes": size_bytes,
                },
            )
            self._connection.execute(
                """INSERT INTO receipt_admissions(
                    admission_id, run_id, node_id, attempt_id, receipt_kind,
                    sha256, path, size_bytes, legacy, admitted_event_seq, admitted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    admission_id,
                    lease.run_id,
                    node_id,
                    attempt_id,
                    receipt_kind,
                    sha256,
                    path,
                    size_bytes,
                    1 if legacy else 0,
                    event_seq,
                    admitted_at,
                ),
            )
            return {
                "admission_id": admission_id,
                "run_id": lease.run_id,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "receipt_kind": receipt_kind,
                "sha256": sha256,
                "path": path,
                "size_bytes": size_bytes,
                "legacy": 1 if legacy else 0,
                "admitted_event_seq": event_seq,
                "admitted_at": admitted_at,
                "duplicate": False,
            }

    def list_admissions(
        self,
        run_id: str,
        *,
        receipt_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        if receipt_kind is None:
            rows = self._connection.execute(
                """SELECT * FROM receipt_admissions WHERE run_id = ?
                   ORDER BY node_id, attempt_id, receipt_kind""",
                (run_id,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """SELECT * FROM receipt_admissions
                   WHERE run_id = ? AND receipt_kind = ?
                   ORDER BY node_id, attempt_id, receipt_kind""",
                (run_id, receipt_kind),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_admission(self, run_id: str, admission_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM receipt_admissions WHERE run_id = ? AND admission_id = ?",
            (run_id, admission_id),
        ).fetchone()
        if row is None:
            raise DagRunStoreError("dag_admission_missing", admission_id)
        return dict(row)

    def review_scope_snapshot(
        self,
        run_id: str,
        *,
        goal_hash: str,
        reviewed_node_ids: tuple[str, ...] | list[str] | None = None,
        journal_sequence_start: int = 0,
        through_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Return a reviewer-scope snapshot from the durable run store."""

        record = self.load_run_record(run_id)
        sequence_end = (
            self.latest_sequence(run_id) if through_sequence is None else through_sequence
        )
        nodes = set(reviewed_node_ids or ())
        attempts = [
            attempt
            for attempt in self.list_attempts(run_id)
            if not nodes or attempt.identity.node_id in nodes
        ]
        if not nodes:
            nodes = {attempt.identity.node_id for attempt in attempts}
        attempt_ids = {attempt.identity.attempt_id for attempt in attempts}
        artifacts = [
            _review_scope_admission_descriptor(row)
            for row in self.list_admissions(run_id)
            if row.get("attempt_id") in attempt_ids
        ]
        return {
            "schema": "tau.review_scope.v1",
            "goal_hash": goal_hash,
            "plan_sha256": record.plan_sha256,
            "reviewed_node_ids": sorted(nodes),
            "reviewed_attempt_ids": sorted(attempt_ids),
            "admitted_artifacts": sorted(
                artifacts,
                key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
            ),
            "journal_sequence_start": journal_sequence_start,
            "journal_sequence_end": sequence_end,
        }

    def latest_sequence(self, run_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM dag_run_events WHERE run_id = ?", (run_id,)
        ).fetchone()
        return int(row[0])

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
        if status not in {"PASS", "BLOCKED", "CANCELLED"}:
            raise DagRunStoreError("dag_run_replay_invalid", status)
        event_type = {
            "PASS": "run_completed",
            "BLOCKED": "run_blocked",
            "CANCELLED": "run_cancelled",
        }[status]
        with self._transaction():
            self._assert_lease(lease, allow_expired=True)
            self._connection.execute(
                "UPDATE dag_runs SET status = ?, verdict = ?, updated_at = ? WHERE run_id = ?",
                (status, verdict, _now_iso(), lease.run_id),
            )
            self._append_event(
                lease,
                event_key=f"run:finished:{status}:{verdict}",
                event_type=event_type,
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
            attempt for attempt in self.list_attempts(lease.run_id) if attempt.state == "UNCERTAIN"
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

    def _append_runtime_event(
        self,
        lease: DagRunLease,
        event: RuntimeEvent,
        *,
        deadline: datetime | None = None,
    ) -> tuple[bool, int, RuntimeStateProjection]:
        """Append one normalized runtime observation without changing DAG authority."""

        if event.run_id != lease.run_id:
            raise DagRunStoreError("runtime_event_run_mismatch", event.event_id)
        if len(event.event_id.encode("utf-8")) > 2048:
            raise DagRunStoreError("runtime_event_id_too_long", event.event_id[:128])
        if any(ord(character) < 32 or ord(character) == 127 for character in event.event_id):
            raise DagRunStoreError("runtime_event_id_invalid", event.event_id[:128])
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
        redacted_journal_payload = cast(
            dict[str, Any], redact_for_storage(dict(journal_payload)).value
        )
        _refresh_runtime_journal_hashes(redacted_journal_payload)
        stored_identity_sha256 = str(redacted_journal_payload["runtime_event_identity_sha256"])
        with self._transaction():
            if deadline is not None and datetime.now(UTC) >= deadline:
                raise DagRunStoreError("runtime_event_deadline_exceeded", event.event_id)
            self._assert_lease(lease)
            existing = self._event_by_key(lease.run_id, event_key)
            if existing is not None:
                existing_payload = _decoded_runtime_journal_payload(existing)
                if existing_payload.get("runtime_event_identity_sha256") != stored_identity_sha256:
                    raise DagRunStoreError("runtime_event_conflict", event.event_id)
                existing_event = _runtime_event_from_journal_row(
                    existing, expected_run_id=lease.run_id
                )
                if _runtime_event_is_lossy(existing_event) or _runtime_event_is_lossy(event):
                    raise DagRunStoreError("runtime_event_lossy_duplicate", event.event_id)
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
            projection = self.runtime_state_projection(lease.run_id, event.endpoint_lease_sha256)
            if projection is None:
                raise DagRunStoreError("runtime_event_projection_missing", event.event_id)
        return appended, sequence, projection

    def load_runtime_events(
        self,
        run_id: str,
        endpoint_lease_sha256: str | None = None,
    ) -> tuple[tuple[int, RuntimeEvent], ...]:
        runtime_events: list[tuple[int, RuntimeEvent]] = []
        query = "SELECT * FROM dag_run_events WHERE run_id = ? AND event_key LIKE ? ORDER BY seq"
        event_key_pattern = (
            f"runtime:{endpoint_lease_sha256}:%"
            if endpoint_lease_sha256 is not None
            else "runtime:%"
        )
        rows = self._connection.execute(query, (run_id, event_key_pattern)).fetchall()
        for row in rows:
            runtime_event = _runtime_event_from_journal_row(
                cast(sqlite3.Row, row), expected_run_id=run_id
            )
            if (
                endpoint_lease_sha256 is None
                or runtime_event.endpoint_lease_sha256 == endpoint_lease_sha256
            ):
                runtime_events.append((int(row["seq"]), runtime_event))
        return tuple(runtime_events)

    def runtime_state_projection(
        self,
        run_id: str,
        endpoint_lease_sha256: str,
    ) -> RuntimeStateProjection | None:
        rows = self._connection.execute(
            "SELECT * FROM dag_run_events WHERE run_id = ? AND event_key LIKE ? ORDER BY seq",
            (run_id, f"runtime:{endpoint_lease_sha256}:%"),
        ).fetchall()
        validated = tuple(
            _runtime_event_from_journal_row(cast(sqlite3.Row, row), expected_run_id=run_id)
            for row in rows
        )
        endpoint_events = tuple(
            event for event in validated if event.endpoint_lease_sha256 == endpoint_lease_sha256
        )
        if not endpoint_events:
            return None
        latest = endpoint_events[-1]
        return RuntimeStateProjection(
            run_id=run_id,
            endpoint_lease_sha256=endpoint_lease_sha256,
            state=latest.state,
            liveness=latest.liveness,
            confidence=latest.confidence,
            last_event_id=latest.event_id,
            event_count=len(endpoint_events),
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
        payload_dict = cast(dict[str, Any], redact_for_storage(dict(payload)).value)
        _refresh_runtime_journal_hashes(payload_dict)
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


def _stored_attempt_to_receipt(attempt: StoredAttempt) -> dict[str, Any]:
    return {
        "run_id": attempt.identity.run_id,
        "node_id": attempt.identity.node_id,
        "attempt": attempt.identity.attempt,
        "attempt_id": attempt.identity.attempt_id,
        "idempotency_key": attempt.identity.idempotency_key,
        "state": attempt.state,
        "effect_state": attempt.effect_state,
        "staged_result_present": attempt.staged_result is not None,
        "committed_result_present": attempt.committed_result is not None,
    }


def _review_scope_admission_descriptor(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        "schema": str(row["receipt_kind"]),
        "id": str(row["admission_id"]),
        "path": str(row["path"]),
        "sha256": str(row["sha256"]),
    }


def _base_run_id(run_id: str) -> str:
    marker = ":generation:"
    prefix, found, suffix = run_id.rpartition(marker)
    if found and prefix and suffix.isdigit():
        return prefix
    return run_id
