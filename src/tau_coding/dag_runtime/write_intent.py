"""Append-only write-intent sidecar with CRC framing.

Contract 8.A8.10 (docs/design/receipt-admission-contract.md): every record
carries run/node/attempt/kind identity; records are length-prefixed and
CRC-framed; appends use a single ``O_APPEND`` write per record; a torn final
append fails its CRC and is ignored as trailing garbage — detected and
reported, never fatal, never able to corrupt earlier records.

The sidecar is the attempt witness that survives transaction rollback: it is
written at S1, before any receipt bytes exist, so its presence distinguishes
"attempted and swallowed" from "never attempted" when a receipt is absent.

Frame layout (all integers little-endian):

    magic   4 bytes  b"TWI1"
    length  4 bytes  payload byte count
    crc32   4 bytes  CRC-32 of payload
    payload N bytes  canonical JSON (sorted keys, utf-8)
"""

from __future__ import annotations

import json
import os
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MAGIC = b"TWI1"
_HEADER_BYTES = 12
WRITE_INTENT_SCHEMA = "tau.write_intent_record.v1"


class WriteIntentError(RuntimeError):
    """Raised when an intent record cannot be appended or is malformed."""


@dataclass(frozen=True)
class SidecarReadResult:
    """All valid records plus what, if anything, was ignored at the tail."""

    records: tuple[dict[str, Any], ...]
    torn_tail_bytes: int
    torn_tail_reason: str | None


def _encode_record(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    header = _MAGIC + len(body).to_bytes(4, "little") + zlib.crc32(body).to_bytes(4, "little")
    return header + body


def append_intent(
    sidecar_path: Path,
    *,
    run_id: str,
    node_id: str,
    attempt_id: str,
    receipt_kind: str,
    stage: str,
    target_path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one intent record durably and return the payload written.

    One ``os.write`` on an ``O_APPEND`` descriptor keeps concurrent writers
    from interleaving inside a frame; fsync makes the record durable before
    the caller proceeds to the write the record witnesses.
    """

    for label, value in (
        ("run_id", run_id),
        ("node_id", node_id),
        ("attempt_id", attempt_id),
        ("receipt_kind", receipt_kind),
        ("stage", stage),
    ):
        if not value or not isinstance(value, str):
            raise WriteIntentError(f"intent {label} must be a non-empty string")
    payload: dict[str, Any] = {
        "schema": WRITE_INTENT_SCHEMA,
        "run_id": run_id,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "receipt_kind": receipt_kind,
        "stage": stage,
    }
    if target_path is not None:
        payload["target_path"] = target_path
    if extra:
        payload["extra"] = extra
    frame = _encode_record(payload)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(sidecar_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        written = os.write(fd, frame)
        if written != len(frame):
            raise WriteIntentError(
                f"short append to {sidecar_path}: {written} of {len(frame)} bytes"
            )
        os.fsync(fd)
    finally:
        os.close(fd)
    return payload


def read_sidecar(sidecar_path: Path) -> SidecarReadResult:
    """Read every valid record; classify — never raise on — a torn tail.

    Corruption that is *followed by more data* is not a torn tail and raises,
    because mid-file damage means earlier history cannot be trusted blindly.
    Only trailing garbage is tolerated.
    """

    try:
        blob = sidecar_path.read_bytes()
    except FileNotFoundError:
        return SidecarReadResult(records=(), torn_tail_bytes=0, torn_tail_reason=None)

    records: list[dict[str, Any]] = []
    offset = 0
    total = len(blob)
    while offset < total:
        remaining = total - offset
        if remaining < _HEADER_BYTES:
            return SidecarReadResult(
                records=tuple(records),
                torn_tail_bytes=remaining,
                torn_tail_reason="truncated_header",
            )
        if blob[offset : offset + 4] != _MAGIC:
            raise WriteIntentError(
                f"bad frame magic at offset {offset} in {sidecar_path}"
            )
        length = int.from_bytes(blob[offset + 4 : offset + 8], "little")
        crc_expected = int.from_bytes(blob[offset + 8 : offset + 12], "little")
        body_start = offset + _HEADER_BYTES
        if body_start + length > total:
            return SidecarReadResult(
                records=tuple(records),
                torn_tail_bytes=remaining,
                torn_tail_reason="truncated_body",
            )
        body = blob[body_start : body_start + length]
        if zlib.crc32(body) != crc_expected:
            if body_start + length == total:
                return SidecarReadResult(
                    records=tuple(records),
                    torn_tail_bytes=remaining,
                    torn_tail_reason="crc_mismatch",
                )
            raise WriteIntentError(
                f"mid-file CRC mismatch at offset {offset} in {sidecar_path}"
            )
        records.append(json.loads(body.decode("utf-8")))
        offset = body_start + length
    return SidecarReadResult(records=tuple(records), torn_tail_bytes=0, torn_tail_reason=None)
