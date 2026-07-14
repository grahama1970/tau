"""Runtime backend protocol; implementations provide mechanics, never DAG authority."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tau_coding.dag_runtime.model import FrozenJson
from tau_coding.runtime_backends.contracts import (
    RuntimeCapabilities,
    RuntimeEndpointLease,
    RuntimeEvent,
    RuntimeSubmitReceipt,
)


class RuntimeBackend(Protocol):
    def capabilities(self) -> RuntimeCapabilities: ...

    def ensure_scope(self, request: FrozenJson) -> FrozenJson: ...

    def spawn(self, request: FrozenJson) -> RuntimeEndpointLease: ...

    def submit(
        self, endpoint: RuntimeEndpointLease, work_order: FrozenJson
    ) -> RuntimeSubmitReceipt: ...

    def capture(self, endpoint: RuntimeEndpointLease, lines: int) -> FrozenJson: ...

    def observe(self, endpoint: RuntimeEndpointLease) -> RuntimeEvent: ...

    def wait_event(
        self,
        endpoint: RuntimeEndpointLease,
        cursor: str | None,
        deadline: datetime,
    ) -> RuntimeEvent | None: ...

    def list_owned(self, run_id: str) -> list[RuntimeEndpointLease]: ...

    def terminate(
        self, endpoint: RuntimeEndpointLease, authorization: FrozenJson
    ) -> FrozenJson: ...
