"""Durable peer-message queue substrate for Tau TUI instances."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PEER_ENVELOPE_SCHEMA = "tau.tui_peer_envelope.v1"
PEER_QUEUE_SCHEMA = "tau.tui_peer_queue.v1"


@dataclass(frozen=True, slots=True)
class PeerEnvelope:
    """Typed peer envelope addressed between Tau harness instances."""

    envelope_id: str
    source_harness: str
    target_harness: str
    goal_hash: str
    kind: str
    payload: dict[str, Any]
    created_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": PEER_ENVELOPE_SCHEMA,
            "envelope_id": self.envelope_id,
            "source_harness": self.source_harness,
            "target_harness": self.target_harness,
            "goal_hash": self.goal_hash,
            "kind": self.kind,
            "payload": self.payload,
            "created_at": self.created_at,
        }


def build_peer_envelope(
    *,
    envelope_id: str,
    source_harness: str,
    target_harness: str,
    goal_hash: str,
    kind: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a fail-closed peer envelope for a target harness."""

    for label, value in {
        "envelope_id": envelope_id,
        "source_harness": source_harness,
        "target_harness": target_harness,
        "goal_hash": goal_hash,
        "kind": kind,
    }.items():
        if not value.strip():
            raise RuntimeError(f"{label} must be non-empty")
    return PeerEnvelope(
        envelope_id=envelope_id,
        source_harness=source_harness,
        target_harness=target_harness,
        goal_hash=goal_hash,
        kind=kind,
        payload=payload,
        created_at=_utc_stamp(),
    ).to_json()


class DurablePeerQueue:
    """Small JSON-backed peer queue that drains only while idle."""

    def __init__(self, path: Path, *, harness_id: str) -> None:
        if not harness_id.strip():
            raise RuntimeError("harness_id must be non-empty")
        self.path = path.expanduser().resolve()
        self.harness_id = harness_id

    def enqueue(self, envelope: dict[str, Any]) -> dict[str, Any]:
        _validate_envelope(envelope, target_harness=self.harness_id)
        state = self._load()
        item = {
            "id": envelope["envelope_id"],
            "state": "queued",
            "envelope": envelope,
            "created_at": envelope["created_at"],
            "updated_at": _utc_stamp(),
            "attempts": 0,
        }
        state["items"].append(item)
        self._save(state)
        return item

    def drain_idle(
        self,
        *,
        idle: bool,
        limit: int = 1,
        scratch_root: Path | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise RuntimeError("limit must be at least 1")
        state = self._load()
        if not idle:
            self._save(state)
            return []
        drained: list[dict[str, Any]] = []
        for item in state["items"]:
            if len(drained) >= limit:
                break
            if item.get("state") != "queued":
                continue
            if scratch_root is not None:
                item["scratch_worktree"] = _write_scratch_artifacts(
                    item,
                    harness_id=self.harness_id,
                    scratch_root=scratch_root,
                )
            item["state"] = "awaiting_approval"
            item["updated_at"] = _utc_stamp()
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["approval_gate"] = {
                "required": True,
                "status": "BLOCKED",
                "reason": "human_approval_required_before_worktree_effects",
            }
            drained.append(item)
        self._save(state)
        return drained

    def snapshot(self) -> dict[str, Any]:
        return self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema": PEER_QUEUE_SCHEMA,
                "harness_id": self.harness_id,
                "items": [],
                "updated_at": _utc_stamp(),
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"peer queue is unreadable: {self.path}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != PEER_QUEUE_SCHEMA:
            raise RuntimeError(f"peer queue schema mismatch: {self.path}")
        if payload.get("harness_id") != self.harness_id:
            raise RuntimeError(f"peer queue harness mismatch: {self.path}")
        if not isinstance(payload.get("items"), list):
            raise RuntimeError(f"peer queue items must be a list: {self.path}")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        payload["updated_at"] = _utc_stamp()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sse_event(*, event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    """Serialize a Server-Sent Event payload."""

    if not event.strip():
        raise RuntimeError("event must be non-empty")
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    encoded = json.dumps(data, sort_keys=True)
    for line in encoded.splitlines() or ["{}"]:
        lines.append(f"data: {line}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _validate_envelope(envelope: dict[str, Any], *, target_harness: str) -> None:
    if envelope.get("schema") != PEER_ENVELOPE_SCHEMA:
        raise RuntimeError("peer envelope schema mismatch")
    if envelope.get("target_harness") != target_harness:
        raise RuntimeError("peer envelope target does not match this harness")
    for key in ("envelope_id", "source_harness", "goal_hash", "kind"):
        value = envelope.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"peer envelope missing {key}")


def _write_scratch_artifacts(
    item: dict[str, Any],
    *,
    harness_id: str,
    scratch_root: Path,
) -> dict[str, Any]:
    root = scratch_root.expanduser().resolve()
    item_id = _safe_path_component(str(item.get("id") or "item"))
    item_dir = (root / harness_id / item_id).resolve()
    item_dir.relative_to(root)
    item_dir.mkdir(parents=True, exist_ok=True)

    envelope = item["envelope"]
    work_order_path = item_dir / "work-order.json"
    patch_path = item_dir / "candidate.patch"
    work_order_path.write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    patch_text = _payload_patch_text(envelope)
    patch_path.write_text(patch_text, encoding="utf-8")
    return {
        "path": str(item_dir),
        "artifacts": {
            "work_order": str(work_order_path),
            "candidate_patch": str(patch_path),
        },
        "confined_to": str(root),
    }


def _payload_patch_text(envelope: dict[str, Any]) -> str:
    payload = envelope.get("payload")
    if isinstance(payload, dict):
        patch = payload.get("patch")
        if isinstance(patch, str) and patch:
            return patch if patch.endswith("\n") else f"{patch}\n"
    return "# no candidate patch supplied by peer work order\n"


def _safe_path_component(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_", ".") else "-" for char in value)
    safe = safe.strip(".-")
    if not safe:
        raise RuntimeError("peer queue item id cannot form a safe scratch path")
    return safe


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
