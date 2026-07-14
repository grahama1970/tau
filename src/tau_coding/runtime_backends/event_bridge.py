"""Durable bridge from backend observations into Tau's authoritative journal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.dag_runtime.run_store import (
    DagRunLease,
    DagRunStoreError,
    SqliteDagRunStore,
)
from tau_coding.runtime_backends.base import RuntimeBackend
from tau_coding.runtime_backends.contracts import (
    RuntimeEndpointLease,
    RuntimeEvent,
    RuntimeStateProjection,
)

_MAX_DEPTH = 6
_MAX_ITEMS = 64
_MAX_STRING_LENGTH = 2048
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "credential",
    "password",
    "prompt",
    "secret",
    "stderr",
    "stdout",
    "terminal_output",
    "token",
    "visible_text",
)


@dataclass(frozen=True, slots=True)
class RuntimeEventAppendResult:
    appended: bool
    journal_sequence: int
    event_id: str
    projection: RuntimeStateProjection


class RuntimeEventBridge:
    """Validate, sanitize, and durably append backend runtime observations."""

    def __init__(self, store: SqliteDagRunStore) -> None:
        self._store = store

    def wait_and_append(
        self,
        *,
        lease: DagRunLease,
        backend: RuntimeBackend,
        endpoint: RuntimeEndpointLease,
        cursor: str | None,
        deadline: datetime,
    ) -> RuntimeEventAppendResult | None:
        if deadline.tzinfo is None:
            raise DagRunStoreError("runtime_event_deadline_invalid", "timezone_required")
        if datetime.now(UTC) >= deadline:
            return None
        capabilities = backend.capabilities()
        self._validate_bindings(
            lease=lease,
            endpoint=endpoint,
            backend_name=capabilities.backend,
            capabilities_sha256=capabilities.sha256,
        )
        event = backend.wait_event(endpoint, cursor, deadline)
        if datetime.now(UTC) >= deadline:
            return None
        if event is None:
            return None
        if not isinstance(event, RuntimeEvent):
            raise DagRunStoreError("runtime_event_schema_invalid", type(event).__name__)
        try:
            validated = RuntimeEvent.from_payload(event.to_payload())
        except (TypeError, ValueError) as exc:
            raise DagRunStoreError("runtime_event_schema_invalid", event.event_id) from exc
        self._validate_event(
            event=validated,
            lease=lease,
            endpoint=endpoint,
            backend_name=capabilities.backend,
        )
        normalized = _normalized_runtime_event(validated, native=capabilities.native_events)
        appended, sequence, projection = self._store.append_runtime_event(lease, normalized)
        return RuntimeEventAppendResult(
            appended=appended,
            journal_sequence=sequence,
            event_id=normalized.event_id,
            projection=projection,
        )

    @staticmethod
    def _validate_bindings(
        *,
        lease: DagRunLease,
        endpoint: RuntimeEndpointLease,
        backend_name: str,
        capabilities_sha256: str,
    ) -> None:
        if endpoint.run_id != lease.run_id:
            raise DagRunStoreError("runtime_event_run_mismatch", endpoint.endpoint_id)
        if endpoint.backend != backend_name:
            raise DagRunStoreError("runtime_event_backend_mismatch", endpoint.endpoint_id)
        if endpoint.capabilities_sha256 != capabilities_sha256:
            raise DagRunStoreError(
                "runtime_event_capabilities_mismatch", endpoint.endpoint_id
            )

    @staticmethod
    def _validate_event(
        *,
        event: RuntimeEvent,
        lease: DagRunLease,
        endpoint: RuntimeEndpointLease,
        backend_name: str,
    ) -> None:
        if event.run_id != lease.run_id:
            raise DagRunStoreError("runtime_event_run_mismatch", event.event_id)
        if event.endpoint_lease_sha256 != endpoint.sha256:
            raise DagRunStoreError("runtime_event_endpoint_mismatch", event.event_id)
        if event.source != backend_name:
            raise DagRunStoreError("runtime_event_backend_mismatch", event.event_id)


def _normalized_runtime_event(event: RuntimeEvent, *, native: bool) -> RuntimeEvent:
    observation = event.observation.to_value()
    transport_value = observation.pop("transport", None)
    if transport_value is not None and not isinstance(transport_value, dict):
        raise DagRunStoreError("runtime_event_transport_invalid", event.event_id)
    transport = dict(transport_value or {})
    expected_mode = "native" if native else "poll"
    declared_mode = transport.get("mode")
    if declared_mode is not None and declared_mode != expected_mode:
        raise DagRunStoreError("runtime_event_transport_mode_mismatch", event.event_id)
    backend_sequence = transport.get("backend_sequence")
    if backend_sequence is not None and (
        type(backend_sequence) is not int or backend_sequence < 0
    ):
        raise DagRunStoreError("runtime_event_transport_sequence_invalid", event.event_id)
    stream_id = _optional_string(transport.get("stream_id"), "stream_id", event.event_id)
    backend_cursor = _optional_string(
        transport.get("backend_cursor"), "backend_cursor", event.event_id
    )
    raw_projection = transport.get("raw_payload_projection")
    if raw_projection is not None and not isinstance(raw_projection, dict):
        raise DagRunStoreError("runtime_event_raw_projection_invalid", event.event_id)
    full_observation = event.observation.to_value()
    bounded_observation = _bounded_redacted(observation)
    if not isinstance(bounded_observation, dict):
        raise DagRunStoreError("runtime_event_observation_invalid", event.event_id)
    bounded_projection = _bounded_redacted(raw_projection) if raw_projection is not None else None
    bounded_observation["transport"] = {
        "mode": expected_mode,
        "stream_id": stream_id,
        "backend_cursor": backend_cursor,
        "backend_sequence": backend_sequence,
        "raw_payload_sha256": canonical_sha256(full_observation),
        "raw_payload_projection": bounded_projection,
        "raw_payload_omitted_reason": (
            None if bounded_projection is not None else "not_available"
        ),
        "raw_payload_truncated": bounded_projection != raw_projection,
    }
    return RuntimeEvent(
        event_id=event.event_id,
        run_id=event.run_id,
        endpoint_lease_sha256=event.endpoint_lease_sha256,
        event_type=event.event_type,
        observed_at=event.observed_at,
        state=event.state,
        liveness=event.liveness,
        confidence=event.confidence,
        source=event.source,
        observation=FrozenJson.from_value(bounded_observation),
    )


def _optional_string(value: object, label: str, event_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise DagRunStoreError(f"runtime_event_transport_{label}_invalid", event_id)
    return value[:_MAX_STRING_LENGTH]


def _bounded_redacted(value: object, *, depth: int = 0, key: str = "") -> object:
    if key and not key.endswith("_sha256") and any(
        part in key.casefold() for part in _SENSITIVE_KEY_PARTS
    ):
        return "<redacted>"
    if depth >= _MAX_DEPTH:
        return "<max-depth>"
    if isinstance(value, dict):
        bounded: dict[str, object] = {}
        for index, item_key in enumerate(sorted(value)):
            if index >= _MAX_ITEMS:
                bounded["_truncated"] = True
                break
            bounded[str(item_key)[:128]] = _bounded_redacted(
                value[item_key], depth=depth + 1, key=str(item_key)
            )
        return bounded
    if isinstance(value, list):
        items = [
            _bounded_redacted(item, depth=depth + 1)
            for item in value[:_MAX_ITEMS]
        ]
        if len(value) > _MAX_ITEMS:
            items.append("<truncated>")
        return items
    if isinstance(value, str):
        return value[:_MAX_STRING_LENGTH]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<unsupported:{type(value).__name__}>"
