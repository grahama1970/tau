"""Tiny in-kernel client for emitting host-call intents without authority."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TauHostClient:
    """Writes requested host calls for the Tau host to authorize and execute."""

    def __init__(
        self,
        request_path: str | Path,
        *,
        endpoint_lease_sha256: str,
        execution_token: str,
        generation_id: str,
        execution_id: str,
        goal_hash: str,
        policy_sha256: str,
        data_boundary_sha256: str,
        worktree_sha256: str,
    ) -> None:
        self._request_path = Path(request_path)
        self._binding = {
            "endpoint_lease_sha256": endpoint_lease_sha256,
            "execution_token": execution_token,
            "generation_id": generation_id,
            "execution_id": execution_id,
            "goal_hash": goal_hash,
            "policy_sha256": policy_sha256,
            "data_boundary_sha256": data_boundary_sha256,
            "worktree_sha256": worktree_sha256,
        }
        self.source = _Namespace(self, "source")
        self.code = _Namespace(self, "code")
        self.graph = _Namespace(self, "graph")
        self.artifact = _Namespace(self, "artifact")
        self.evidence = _Namespace(self, "evidence")
        self.progress = _Namespace(self, "progress")

    def emit(self, kind: str, **params: Any) -> dict[str, Any]:
        payload = {
            "schema": "tau.python_host_call_intent.v1",
            "kind": kind,
            "params": params,
            "binding": self._binding,
        }
        self._request_path.parent.mkdir(parents=True, exist_ok=True)
        self._request_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return payload


class _Namespace:
    def __init__(self, client: TauHostClient, prefix: str) -> None:
        self._client = client
        self._prefix = prefix

    def read(self, **params: Any) -> dict[str, Any]:
        return self._client.emit(f"{self._prefix}.read", **params)

    def search(self, **params: Any) -> dict[str, Any]:
        return self._client.emit(f"{self._prefix}.search", **params)

    def query(self, **params: Any) -> dict[str, Any]:
        return self._client.emit(f"{self._prefix}.query", **params)

    def put(self, **params: Any) -> dict[str, Any]:
        return self._client.emit(f"{self._prefix}.put", **params)

    def emit(self, **params: Any) -> dict[str, Any]:
        return self._client.emit(f"{self._prefix}.emit", **params)
