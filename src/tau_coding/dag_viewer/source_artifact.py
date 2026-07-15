"""Immutable retention of the public DAG source used for a run."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import canonical_json, canonical_sha256
from tau_coding.dag_viewer.contracts import DagSourceReference


def write_dag_source_artifact(
    *, source_payload: Mapping[str, Any], source_schema: str, source_path: Path, run_dir: Path
) -> DagSourceReference:
    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(source_payload)
    source_sha256 = canonical_sha256(payload)
    source_target = resolved_run_dir / "source-dag.json"
    reference_target = resolved_run_dir / "source-dag-reference.json"
    source_bytes = (canonical_json(payload) + "\n").encode()
    if source_target.exists() and source_target.read_bytes() != source_bytes:
        raise RuntimeError("dag_source_artifact_conflict")
    written_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    reference = DagSourceReference(
        source_schema=source_schema,
        source_sha256=source_sha256,
        canonical_source_path="source-dag.json",
        original_source_path=str(source_path.expanduser().resolve()),
        written_at=written_at,
    )
    if not source_target.exists():
        _atomic_write(source_target, source_bytes)
    reference_payload = reference.to_payload()
    if reference_target.exists():
        existing = json.loads(reference_target.read_text())
        if existing.get("source_sha256") != source_sha256:
            raise RuntimeError("dag_source_artifact_conflict")
        reference = DagSourceReference(
            source_schema=str(existing["source_schema"]),
            source_sha256=str(existing["source_sha256"]),
            canonical_source_path=str(existing["canonical_source_path"]),
            original_source_path=str(existing["original_source_path"]),
            written_at=str(existing["written_at"]),
        )
    else:
        _atomic_write(reference_target, (canonical_json(reference_payload) + "\n").encode())
    return reference


def _atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
