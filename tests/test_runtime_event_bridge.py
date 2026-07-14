from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import FrozenJson, canonical_json, canonical_sha256
from tau_coding.dag_runtime.run_store import DagRunStoreError, SqliteDagRunStore
from tau_coding.runtime_backends.contracts import (
    RuntimeCapabilities,
    RuntimeEndpointLease,
    RuntimeEvent,
)
from tau_coding.runtime_backends.event_bridge import RuntimeEventBridge


class EventBackend:
    def __init__(
        self,
        events: list[RuntimeEvent | None | BaseException | object],
        *,
        native: bool = False,
        backend: str = "fixture",
        delay_seconds: float = 0.0,
    ) -> None:
        self.events = events
        self.wait_count = 0
        self.delay_seconds = delay_seconds
        self._capabilities = RuntimeCapabilities(
            backend=backend,
            version="1",
            interactive=True,
            one_shot=False,
            native_events=native,
            native_agent_state=native,
            foreground_process_state=True,
            structured_composer_state=False,
            stable_endpoint_id=True,
            human_attach=True,
            supports_working_directory=True,
            supports_owned_inventory=True,
            supports_terminate=True,
            observation_confidence_levels=("NATIVE", "PROCESS", "UNKNOWN"),
            supported_session_scopes=("persistent_subagent",),
        )

    def capabilities(self) -> RuntimeCapabilities:
        return self._capabilities

    def wait_event(
        self,
        endpoint: RuntimeEndpointLease,
        cursor: str | None,
        deadline: datetime,
    ) -> RuntimeEvent | None:
        del endpoint, cursor, deadline
        self.wait_count += 1
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        item = self.events.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item  # type: ignore[return-value]

    def ensure_scope(self, request: FrozenJson) -> FrozenJson:
        raise NotImplementedError

    def spawn(self, request: FrozenJson) -> RuntimeEndpointLease:
        raise NotImplementedError

    def submit(self, endpoint: RuntimeEndpointLease, work_order: FrozenJson) -> Any:
        raise NotImplementedError

    def capture(self, endpoint: RuntimeEndpointLease, lines: int) -> FrozenJson:
        raise NotImplementedError

    def observe(self, endpoint: RuntimeEndpointLease) -> RuntimeEvent:
        raise NotImplementedError

    def list_owned(self, run_id: str) -> list[RuntimeEndpointLease]:
        raise NotImplementedError

    def terminate(self, endpoint: RuntimeEndpointLease, authorization: FrozenJson) -> FrozenJson:
        raise NotImplementedError


def test_polling_runtime_events_append_deduplicate_and_project(tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite3"
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = _endpoint(run_id="run-1")
        first_event = _event(endpoint, event_id="event-1", state="RUNNING")
        duplicate = replace(first_event, observed_at="2026-07-14T12:00:01+00:00")
        backend = EventBackend([first_event, duplicate])
        bridge = RuntimeEventBridge(store)

        first = bridge.wait_and_append(
            lease=lease,
            backend=backend,
            endpoint=endpoint,
            cursor=None,
            deadline=_deadline(),
        )
        second = bridge.wait_and_append(
            lease=lease,
            backend=backend,
            endpoint=endpoint,
            cursor="event-1",
            deadline=_deadline(),
        )

        assert first is not None and first.appended is True
        assert second is not None and second.appended is False
        assert second.journal_sequence == first.journal_sequence
        assert second.projection.to_payload() == {
            "schema": "tau.runtime_state_projection.v1",
            "run_id": "run-1",
            "endpoint_lease_sha256": endpoint.sha256,
            "state": "RUNNING",
            "liveness": "ALIVE",
            "confidence": "PROCESS",
            "last_event_id": "event-1",
            "event_count": 1,
        }
        assert store.runtime_event_cursor("run-1", endpoint.sha256) == "event-1"


def test_append_builds_returned_projection_inside_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = _endpoint(run_id="run-1")
        event = _event(
            endpoint,
            observation={"transport": {"mode": "poll"}},
        )
        original = store.runtime_state_projection
        transaction_states: list[bool] = []

        def observed_projection(run_id: str, endpoint_sha256: str):
            transaction_states.append(store._connection.in_transaction)
            return original(run_id, endpoint_sha256)

        monkeypatch.setattr(store, "runtime_state_projection", observed_projection)

        appended, _, projection = store.append_runtime_event(lease, event)

        assert appended is True
        assert projection.state == "RUNNING"
        assert transaction_states == [True]


def test_changed_observation_appends_but_reused_event_id_conflicts(tmp_path: Path) -> None:
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = _endpoint(run_id="run-1")
        bridge = RuntimeEventBridge(store)
        running = _event(endpoint, event_id="event-1", state="RUNNING")
        ready = _event(endpoint, event_id="event-2", state="READY")
        backend = EventBackend([running, ready])

        bridge.wait_and_append(
            lease=lease, backend=backend, endpoint=endpoint, cursor=None, deadline=_deadline()
        )
        changed = bridge.wait_and_append(
            lease=lease,
            backend=backend,
            endpoint=endpoint,
            cursor="event-1",
            deadline=_deadline(),
        )
        assert changed is not None and changed.appended is True
        assert changed.projection.event_count == 2
        assert changed.projection.state == "READY"

        conflicting = replace(ready, event_id="event-1")
        with pytest.raises(DagRunStoreError, match="runtime_event_conflict"):
            bridge.wait_and_append(
                lease=lease,
                backend=EventBackend([conflicting]),
                endpoint=endpoint,
                cursor=None,
                deadline=_deadline(),
            )


def test_runtime_event_replay_survives_reopen_without_duplication(tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite3"
    plan = _plan(tmp_path)
    endpoint = _endpoint(run_id="run-1")
    event = _event(endpoint, event_id="event-1", state="RUNNING")
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        result = RuntimeEventBridge(store).wait_and_append(
            lease=lease,
            backend=EventBackend([event]),
            endpoint=endpoint,
            cursor=None,
            deadline=_deadline(),
        )
        assert result is not None
        original_projection = canonical_json(result.projection.to_payload())

    with SqliteDagRunStore(database) as reopened:
        lease = reopened.acquire_run(plan=plan, run_id="run-1", owner_id="owner-a")
        replayed = reopened.runtime_state_projection("run-1", endpoint.sha256)
        duplicate = RuntimeEventBridge(reopened).wait_and_append(
            lease=lease,
            backend=EventBackend([replace(event, observed_at="2026-07-14T12:00:02+00:00")]),
            endpoint=endpoint,
            cursor="event-1",
            deadline=_deadline(),
        )

        assert replayed is not None
        assert canonical_json(replayed.to_payload()) == original_projection
        assert duplicate is not None and duplicate.appended is False
        assert canonical_json(duplicate.projection.to_payload()) == original_projection


def test_replay_blocks_tampered_runtime_event_identity_hash(tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite3"
    endpoint = _endpoint(run_id="run-1")
    with SqliteDagRunStore(database) as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        RuntimeEventBridge(store).wait_and_append(
            lease=lease,
            backend=EventBackend([_event(endpoint)]),
            endpoint=endpoint,
            cursor=None,
            deadline=_deadline(),
        )

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT seq, payload_json FROM dag_run_events "
            "WHERE event_type = 'runtime_event_appended'"
        ).fetchone()
        assert row is not None
        payload = json.loads(row[1])
        payload["runtime_event_identity_sha256"] = canonical_sha256("forged")
        connection.execute("DROP TRIGGER dag_run_events_no_update")
        connection.execute(
            "UPDATE dag_run_events SET payload_json = ?, payload_sha256 = ? WHERE seq = ?",
            (canonical_json(payload), canonical_sha256(payload), row[0]),
        )

    with SqliteDagRunStore(database) as reopened, pytest.raises(
        DagRunStoreError, match="runtime_event_identity_hash_mismatch"
    ):
        reopened.load_runtime_events("run-1", endpoint.sha256)


def test_bridge_blocks_invalid_bindings_schema_and_transport(tmp_path: Path) -> None:
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = _endpoint(run_id="run-1")
        bridge = RuntimeEventBridge(store)

        with pytest.raises(DagRunStoreError, match="runtime_event_run_mismatch"):
            bridge.wait_and_append(
                lease=lease,
                backend=EventBackend([_event(endpoint)]),
                endpoint=replace(endpoint, run_id="run-other"),
                cursor=None,
                deadline=_deadline(),
            )
        with pytest.raises(DagRunStoreError, match="runtime_event_endpoint_mismatch"):
            bridge.wait_and_append(
                lease=lease,
                backend=EventBackend(
                    [
                        replace(
                            _event(endpoint),
                            endpoint_lease_sha256=canonical_sha256("wrong"),
                        )
                    ]
                ),
                endpoint=endpoint,
                cursor=None,
                deadline=_deadline(),
            )
        with pytest.raises(DagRunStoreError, match="runtime_event_schema_invalid"):
            bridge.wait_and_append(
                lease=lease,
                backend=EventBackend([{"schema": "unknown"}]),
                endpoint=endpoint,
                cursor=None,
                deadline=_deadline(),
            )
        native_transport = _event(
            endpoint,
            observation={"transport": {"mode": "native", "backend_sequence": 1}},
        )
        with pytest.raises(DagRunStoreError, match="runtime_event_transport_mode_mismatch"):
            bridge.wait_and_append(
                lease=lease,
                backend=EventBackend([native_transport]),
                endpoint=endpoint,
                cursor=None,
                deadline=_deadline(),
            )


def test_backend_contract_failure_propagates_and_unknown_event_remains_diagnostic(
    tmp_path: Path,
) -> None:
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = _endpoint(run_id="run-1")
        failed_backend = EventBackend([RuntimeError("endpoint_binding_mismatch")])
        with pytest.raises(RuntimeError, match="endpoint_binding_mismatch"):
            RuntimeEventBridge(store).wait_and_append(
                lease=lease,
                backend=failed_backend,
                endpoint=endpoint,
                cursor=None,
                deadline=_deadline(),
            )
        assert store.load_runtime_events("run-1", endpoint.sha256) == ()

        unknown = _event(
            endpoint,
            event_id="unknown-1",
            state="UNKNOWN",
            liveness="UNKNOWN",
            confidence="UNKNOWN",
            observation={"observation_error_code": "process_info_unavailable"},
        )
        result = RuntimeEventBridge(store).wait_and_append(
            lease=lease,
            backend=EventBackend([unknown]),
            endpoint=endpoint,
            cursor=None,
            deadline=_deadline(),
        )

        assert result is not None
        assert result.projection.state == "UNKNOWN"
        assert result.projection.liveness == "UNKNOWN"
        stored = store.load_runtime_events("run-1", endpoint.sha256)[0][1]
        assert stored.observation.to_value()["observation_error_code"] == (
            "process_info_unavailable"
        )

        expired_backend = EventBackend([_event(endpoint)])
        assert RuntimeEventBridge(store).wait_and_append(
            lease=lease,
            backend=expired_backend,
            endpoint=endpoint,
            cursor=None,
            deadline=datetime.now(UTC) - timedelta(seconds=1),
        ) is None
        assert expired_backend.wait_count == 0


def test_event_returned_after_deadline_is_not_appended(tmp_path: Path) -> None:
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = _endpoint(run_id="run-1")
        result = RuntimeEventBridge(store).wait_and_append(
            lease=lease,
            backend=EventBackend([_event(endpoint)], delay_seconds=0.02),
            endpoint=endpoint,
            cursor=None,
            deadline=datetime.now(UTC) + timedelta(milliseconds=5),
        )

        assert result is None
        assert store.load_runtime_events("run-1", endpoint.sha256) == ()


def test_endpoint_capabilities_hash_must_match_selected_backend(tmp_path: Path) -> None:
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = replace(
            _endpoint(run_id="run-1"),
            capabilities_sha256=canonical_sha256("stale-capabilities"),
        )
        backend = EventBackend([])

        with pytest.raises(DagRunStoreError, match="runtime_event_capabilities_mismatch"):
            RuntimeEventBridge(store).wait_and_append(
                lease=lease,
                backend=backend,
                endpoint=endpoint,
                cursor=None,
                deadline=_deadline(),
            )
        assert backend.wait_count == 0


def test_store_rejects_runtime_event_without_normalized_transport(tmp_path: Path) -> None:
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = _endpoint(run_id="run-1")

        with pytest.raises(DagRunStoreError, match="runtime_event_transport_mode_invalid"):
            store.append_runtime_event(lease, _event(endpoint))
        assert store.load_runtime_events("run-1", endpoint.sha256) == ()


def test_native_transport_metadata_is_nested_and_deduplicated(tmp_path: Path) -> None:
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = _endpoint(run_id="run-1", native=True)
        event = _event(
            endpoint,
            confidence="NATIVE",
            observation={
                "agent_status": "working",
                "transport": {
                    "mode": "native",
                    "stream_id": "stream-1",
                    "backend_cursor": "cursor-184",
                    "backend_sequence": 184,
                    "raw_payload_projection": {"event": "pane.agent_status_changed"},
                },
            },
        )
        bridge = RuntimeEventBridge(store)
        first = bridge.wait_and_append(
            lease=lease,
            backend=EventBackend([event], native=True),
            endpoint=endpoint,
            cursor=None,
            deadline=_deadline(),
        )
        duplicate = bridge.wait_and_append(
            lease=lease,
            backend=EventBackend(
                [replace(event, observed_at="2026-07-14T12:00:02+00:00")],
                native=True,
            ),
            endpoint=endpoint,
            cursor="cursor-184",
            deadline=_deadline(),
        )

        assert first is not None and duplicate is not None
        assert duplicate.appended is False
        stored = store.load_runtime_events("run-1", endpoint.sha256)[0][1]
        transport = stored.observation.to_value()["transport"]
        assert transport["mode"] == "native"
        assert transport["backend_sequence"] == 184
        assert transport["backend_cursor"] == "cursor-184"
        assert store.runtime_event_cursor("run-1", endpoint.sha256) == "cursor-184"
        assert set(stored.to_payload()) == {
            "schema", "event_id", "run_id", "endpoint_lease_sha256", "event_type",
            "observed_at", "state", "liveness", "confidence", "source", "observation",
        }


def test_runtime_terminal_text_is_diagnostic_and_cannot_complete_run(tmp_path: Path) -> None:
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = _endpoint(run_id="run-1")
        event = _event(
            endpoint,
            observation={
                "visible_text": "PASS done tests passed",
                "visible_text_diagnostic_only": True,
            },
        )
        result = RuntimeEventBridge(store).wait_and_append(
            lease=lease,
            backend=EventBackend([event]),
            endpoint=endpoint,
            cursor=None,
            deadline=_deadline(),
        )

        assert result is not None
        assert store.run_outcome("run-1") == ("RUNNING", None)
        assert store.list_attempts("run-1") == ()
        stored = store.load_runtime_events("run-1", endpoint.sha256)[0][1]
        assert stored.observation.to_value()["visible_text"] == "<redacted>"


def test_missed_poll_is_reconciled_by_later_changed_observation(tmp_path: Path) -> None:
    with SqliteDagRunStore(tmp_path / "run.sqlite3") as store:
        lease = store.acquire_run(
            plan=_plan(tmp_path), run_id="run-1", owner_id="owner-a"
        )
        endpoint = _endpoint(run_id="run-1")
        backend = EventBackend([None, _event(endpoint, state="BLOCKED")])
        bridge = RuntimeEventBridge(store)

        assert bridge.wait_and_append(
            lease=lease, backend=backend, endpoint=endpoint, cursor=None, deadline=_deadline()
        ) is None
        reconciled = bridge.wait_and_append(
            lease=lease, backend=backend, endpoint=endpoint, cursor=None, deadline=_deadline()
        )

        assert reconciled is not None
        assert reconciled.projection.state == "BLOCKED"
        assert reconciled.projection.event_count == 1


def _deadline() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=1)


def _endpoint(*, run_id: str, native: bool = False) -> RuntimeEndpointLease:
    capabilities = EventBackend([], native=native).capabilities()
    return RuntimeEndpointLease(
        run_id=run_id,
        plan_revision=canonical_sha256({"plan": 1}),
        dag_id="dag-1",
        node_id="worker",
        attempt_id="attempt-1",
        attempt_number=1,
        execution_token="token-1",
        backend="fixture",
        backend_session_id="session-1",
        scope_id="scope-1",
        endpoint_id="endpoint-1",
        work_order_sha256=canonical_sha256({"work": 1}),
        goal_hash=canonical_sha256({"goal": 1}),
        owner="tau",
        created_at="2026-07-14T12:00:00+00:00",
        expires_at="2026-07-14T13:00:00+00:00",
        heartbeat_policy=FrozenJson.from_value({}),
        cleanup_policy=FrozenJson.from_value({}),
        capabilities_sha256=capabilities.sha256,
        backend_ids=FrozenJson.from_value({"endpoint": "endpoint-1"}),
    )


def _event(
    endpoint: RuntimeEndpointLease,
    *,
    event_id: str = "event-1",
    state: str = "RUNNING",
    liveness: str = "ALIVE",
    confidence: str = "PROCESS",
    observation: dict[str, Any] | None = None,
) -> RuntimeEvent:
    return RuntimeEvent.from_payload(
        {
            "schema": "tau.runtime_event.v1",
            "event_id": event_id,
            "run_id": endpoint.run_id,
            "endpoint_lease_sha256": endpoint.sha256,
            "event_type": "RUNTIME_OBSERVATION_RECORDED",
            "observed_at": "2026-07-14T12:00:00+00:00",
            "state": state,
            "liveness": liveness,
            "confidence": confidence,
            "source": endpoint.backend,
            "observation": observation or {"agent_status": state.casefold()},
        }
    )


def _plan(tmp_path: Path):
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "runtime-event-bridge-test",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                {
                    "node_id": "worker",
                    "role": "worker",
                    "command": ["true"],
                    "depends_on": [],
                    "accepted_context_from": [],
                    "receipt_path": str(tmp_path / "worker.json"),
                    "timeout_seconds": 1,
                    "max_attempts": 1,
                }
            ],
        },
        source_path=tmp_path / "dag.json",
    )
