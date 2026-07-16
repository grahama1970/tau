"""Typed contracts for the DAG viewer read model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DagSourceReference:
    source_schema: str
    source_sha256: str
    canonical_source_path: str
    original_source_path: str
    written_at: str

    def to_payload(self) -> dict[str, Any]:
        return {"schema": "tau.dag_source_reference.v1", **asdict(self)}


def viewer_capabilities() -> dict[str, Any]:
    return {
        "schema": "tau.dag_viewer_capabilities.v1",
        "viewer_version": "1",
        "manifest_schema": "tau.dag_view_manifest.v1",
        "snapshot_schema": "tau.dag_view_snapshot.v2",
        "event_schema": "tau.dag_live_event.v1",
        "supports_live": True,
        "supports_replay": True,
        "supports_historical_sequences": True,
        "supports_source_json": True,
        "supports_receipt_inspection": True,
        "supports_causal_explanations": True,
        "supports_route_join_projection": True,
        "supports_attention_items": True,
        "read_only": True,
    }
