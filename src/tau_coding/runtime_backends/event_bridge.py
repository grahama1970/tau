"""Durable bridge from backend observations into Tau's authoritative journal."""

from __future__ import annotations

import heapq
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
_MAX_TOTAL_NODES = 512
_MAX_TOTAL_CHARACTERS = 16_384
_SENSITIVE_KEY_PARTS = (
    "access_key",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "client_secret",
    "cookie",
    "credential",
    "passphrase",
    "password",
    "private_key",
    "prompt",
    "refresh_token",
    "secret",
    "session_token",
    "stderr",
    "stdout",
    "terminal_output",
    "token",
    "visible_text",
)
_SENSITIVE_EXACT_KEYS = ("session",)


@dataclass(frozen=True, slots=True)
class RuntimeEventAppendResult:
    appended: bool
    journal_sequence: int
    event_id: str
    projection: RuntimeStateProjection


@dataclass(slots=True)
class _RedactionBudget:
    nodes: int = 0
    characters: int = 0
    redacted: bool = False
    truncated: bool = False

    def consume_node(self) -> bool:
        self.nodes += 1
        if self.nodes > _MAX_TOTAL_NODES:
            self.truncated = True
            return False
        return True

    def consume_characters(self, requested: int) -> int:
        remaining = max(0, _MAX_TOTAL_CHARACTERS - self.characters)
        accepted = min(requested, remaining)
        self.characters += accepted
        if accepted < requested:
            self.truncated = True
        return accepted


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
        if cursor is not None:
            _optional_string(cursor, "cursor", "wait_event")
        capabilities = backend.capabilities()
        self._validate_bindings(
            lease=lease,
            endpoint=endpoint,
            backend_name=capabilities.backend,
            capabilities_sha256=capabilities.sha256,
        )
        self._store.assert_active_lease(lease)
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
        appended, sequence, projection = self._store._append_runtime_event(lease, normalized)
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
            raise DagRunStoreError("runtime_event_capabilities_mismatch", endpoint.endpoint_id)

    @staticmethod
    def _validate_event(
        *,
        event: RuntimeEvent,
        lease: DagRunLease,
        endpoint: RuntimeEndpointLease,
        backend_name: str,
    ) -> None:
        if len(event.event_id.encode("utf-8")) > 2048:
            raise DagRunStoreError("runtime_event_id_too_long", event.event_id[:128])
        if any(ord(character) < 32 or ord(character) == 127 for character in event.event_id):
            raise DagRunStoreError("runtime_event_id_invalid", event.event_id[:128])
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
    declared_mode = transport.get("mode")
    mode = declared_mode or "poll"
    if mode not in {"poll", "native"}:
        raise DagRunStoreError("runtime_event_transport_mode_mismatch", event.event_id)
    if mode == "native" and not native:
        raise DagRunStoreError("runtime_event_transport_mode_mismatch", event.event_id)
    backend_sequence = transport.get("backend_sequence")
    if backend_sequence is not None and (
        type(backend_sequence) is not int or backend_sequence < 0 or backend_sequence > 2**63 - 1
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
    evidence_budget = _RedactionBudget()
    bounded_observation = _bounded_redacted(observation, budget=evidence_budget)
    if not isinstance(bounded_observation, dict):
        raise DagRunStoreError("runtime_event_observation_invalid", event.event_id)
    bounded_projection = (
        _bounded_redacted(raw_projection, budget=evidence_budget)
        if raw_projection is not None
        else None
    )
    raw_hash_omitted = evidence_budget.redacted or evidence_budget.truncated
    bounded_observation["transport"] = {
        "mode": mode,
        "stream_id": stream_id,
        "backend_cursor": backend_cursor,
        "backend_sequence": backend_sequence,
        "raw_payload_sha256": (None if raw_hash_omitted else canonical_sha256(full_observation)),
        "raw_payload_projection": bounded_projection,
        "raw_payload_omitted_reason": (
            "sensitive_fields_redacted"
            if evidence_budget.redacted
            else "payload_truncated"
            if evidence_budget.truncated
            else None
        ),
        "raw_payload_truncated": (
            evidence_budget.truncated or bounded_projection != raw_projection
        ),
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
    if len(value) > _MAX_STRING_LENGTH:
        raise DagRunStoreError(f"runtime_event_transport_{label}_too_long", event_id)
    return value


def _bounded_redacted(
    value: object,
    *,
    budget: _RedactionBudget,
    depth: int = 0,
    key: str = "",
) -> object:
    if not budget.consume_node():
        return "<budget-exhausted>"
    normalized_key = key.casefold()
    if (
        key
        and not normalized_key.endswith("_sha256")
        and (
            normalized_key in _SENSITIVE_EXACT_KEYS
            or any(part in normalized_key for part in _SENSITIVE_KEY_PARTS)
        )
    ):
        budget.redacted = True
        return "<redacted>"
    if depth >= _MAX_DEPTH:
        budget.truncated = True
        return "<max-depth>"
    if isinstance(value, dict):
        bounded: dict[str, object] = {}
        selected_keys = heapq.nsmallest(_MAX_ITEMS + 1, value, key=str)
        for item_key in sorted(selected_keys[:_MAX_ITEMS]):
            bounded_key = str(item_key)[:128]
            budget.consume_characters(len(bounded_key))
            bounded[bounded_key] = _bounded_redacted(
                value[item_key], budget=budget, depth=depth + 1, key=str(item_key)
            )
        if len(selected_keys) > _MAX_ITEMS:
            budget.truncated = True
            bounded["_truncated"] = True
        return bounded
    if isinstance(value, list):
        items = [
            _bounded_redacted(item, budget=budget, depth=depth + 1) for item in value[:_MAX_ITEMS]
        ]
        if len(value) > _MAX_ITEMS:
            budget.truncated = True
            items.append("<truncated>")
        return items
    if isinstance(value, str):
        accepted = budget.consume_characters(min(len(value), _MAX_STRING_LENGTH))
        if len(value) > _MAX_STRING_LENGTH:
            budget.truncated = True
        return value[:accepted]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<unsupported:{type(value).__name__}>"
