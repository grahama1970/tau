"""Frozen receipt allowlist for browser-safe receipt inspection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.transition import DagCommittedReceipt
from tau_coding.dag_viewer.redaction import redact_for_viewer

MAX_RECEIPT_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class IndexedReceipt:
    receipt_id: str
    schema: str
    path: Path
    path_display: str
    sha256: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "schema": self.schema,
            "path_display": self.path_display,
            "sha256": self.sha256,
            "available": True,
        }


class ReceiptIndex:
    def __init__(self, run_dir: Path, entries: tuple[IndexedReceipt, ...]) -> None:
        self.run_dir = run_dir
        self.entries = entries
        self._by_id = {entry.receipt_id: entry for entry in entries}

    def public_entries(self) -> list[dict[str, Any]]:
        return [entry.to_payload() for entry in self.entries]

    def read_projection(self, receipt_id: str) -> dict[str, Any]:
        entry = self._by_id.get(receipt_id)
        if entry is None:
            raise RuntimeError("dag_viewer_receipt_not_found")
        resolved = entry.path.resolve()
        if not _is_beneath(resolved, self.run_dir):
            raise RuntimeError("dag_viewer_receipt_path_escape")
        if entry.path.is_symlink() and not _is_beneath(resolved, self.run_dir):
            raise RuntimeError("dag_viewer_receipt_symlink_escape")
        try:
            data = resolved.read_bytes()
        except OSError as exc:
            raise RuntimeError("dag_viewer_receipt_not_found") from exc
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if digest != entry.sha256:
            raise RuntimeError("dag_viewer_receipt_hash_mismatch")
        try:
            payload = json.loads(data)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("dag_viewer_receipt_invalid") from exc
        if not isinstance(payload, dict) or payload.get("schema") != entry.schema:
            raise RuntimeError("dag_viewer_receipt_invalid")
        redacted = redact_for_viewer(payload)
        return {
            "schema": "tau.dag_viewer_receipt_projection.v1",
            "receipt_id": entry.receipt_id,
            "source_schema": entry.schema,
            "source_sha256": entry.sha256,
            "receipt": redacted.value,
            "redaction": {
                "redacted": redacted.redacted,
                "redacted_paths": list(redacted.redacted_paths),
                "truncated": redacted.truncated,
            },
        }


def build_receipt_index(
    run_dir: Path, receipt_refs: tuple[DagCommittedReceipt, ...]
) -> ReceiptIndex:
    root = run_dir.expanduser().resolve()
    entries: list[IndexedReceipt] = []
    ids: set[str] = set()
    for receipt_ref in sorted(receipt_refs, key=lambda item: item.path):
        candidate = Path(receipt_ref.path)
        resolved = candidate.resolve()
        if candidate.is_symlink() and not _is_beneath(resolved, root):
            raise RuntimeError("dag_viewer_receipt_symlink_escape")
        if not _is_beneath(resolved, root):
            raise RuntimeError("dag_viewer_receipt_path_escape")
        if not resolved.is_file():
            raise RuntimeError("dag_viewer_receipt_not_found")
        if resolved.stat().st_size > MAX_RECEIPT_BYTES:
            raise RuntimeError("dag_viewer_receipt_too_large")
        data = resolved.read_bytes()
        try:
            payload = json.loads(data)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("dag_viewer_receipt_invalid") from exc
        schema = payload.get("schema") if isinstance(payload, dict) else None
        if not isinstance(schema, str) or not schema.startswith("tau."):
            raise RuntimeError("dag_viewer_receipt_invalid")
        digest_hex = hashlib.sha256(data).hexdigest()
        digest = f"sha256:{digest_hex}"
        if digest != receipt_ref.file_sha256:
            raise RuntimeError("dag_viewer_receipt_hash_mismatch")
        path_display = resolved.relative_to(root).as_posix()
        identity = hashlib.sha256(f"{path_display}\0{digest}".encode()).hexdigest()
        receipt_id = f"sha256-{identity[:24]}"
        if receipt_id in ids:
            raise RuntimeError("dag_viewer_receipt_id_collision")
        ids.add(receipt_id)
        entries.append(
            IndexedReceipt(
                receipt_id=receipt_id,
                schema=schema,
                path=resolved,
                path_display=path_display,
                sha256=digest,
            )
        )
    return ReceiptIndex(root, tuple(entries))


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
