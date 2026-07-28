"""Admission-grade durable file writes for authoritative receipts.

Implements S2-S5 of docs/design/receipt-admission-contract.md section 3:

    S2 write to a temp file in the destination directory
    S3 fsync the temp file
    S4 atomic rename onto the final path
    S5 fsync the parent directory

After S5 the object is DURABLE: a crash or kill at any earlier boundary leaves
either no final file or the previous final file — never a torn one. Admission
(the S7 database transaction) is a separate concern owned by the run store;
this module only guarantees evidence durability and reports the content digest
an admission row must bind.

This is the replacement for the three duplicated non-atomic ``write_json``
copies (`generic_artifact_transaction.py`, `canonical_scheduler_conformance.py`,
`traycer/receipts.py`); callers migrate in later tickets, not here.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AdmissionWriteError(RuntimeError):
    """Raised when a durable receipt write cannot be completed."""


@dataclass(frozen=True)
class DurableWriteResult:
    """What an admission row must bind for the object written."""

    path: Path
    sha256: str
    size_bytes: int


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_durable_json(path: Path, payload: dict[str, Any]) -> DurableWriteResult:
    """Write ``payload`` to ``path`` with full S2-S5 durability.

    The temp file lives in the destination directory so the rename in S4 is
    atomic on POSIX. On any failure the temp file is removed; the final path
    is either untouched or fully replaced.
    """

    destination = path.expanduser()
    directory = destination.parent
    directory.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    digest = f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=directory
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:  # S2
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())  # S3
        os.replace(temp_path, destination)  # S4
        _fsync_directory(directory)  # S5
    except OSError as exc:
        with tempfile_suppress():
            temp_path.unlink(missing_ok=True)
        raise AdmissionWriteError(f"durable write failed for {destination}: {exc}") from exc
    return DurableWriteResult(path=destination, sha256=digest, size_bytes=len(encoded))


def read_back_durable_json(result: DurableWriteResult) -> dict[str, Any]:
    """Re-read a written object and verify content identity.

    Callers use this immediately before admission (S6): the admission row must
    bind the digest of what is on disk, not of what was in memory.
    """

    raw = result.path.read_bytes()
    digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    if digest != result.sha256:
        raise AdmissionWriteError(
            f"read-back digest mismatch for {result.path}: {digest} != {result.sha256}"
        )
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise AdmissionWriteError(f"read-back payload is not an object: {result.path}")
    return payload


class tempfile_suppress:
    """Context manager suppressing cleanup errors without masking the original."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> bool:
        return True
