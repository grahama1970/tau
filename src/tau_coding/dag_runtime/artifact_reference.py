"""Hash-addressed by-reference artifact inputs for DAG context bindings.

References are not arbitrary model-supplied paths. A valid reference is derived
from Tau's receipt admission ledger, re-reads the admitted file, verifies its
hash and size, rejects path/symlink escapes from the run evidence root, and then
returns a small `tau.artifact_reference.v1` object that downstream adapters can
dereference through this module when they actually need the bytes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import DagPlanContextBinding, canonical_sha256
from tau_coding.dag_runtime.run_store import SqliteDagRunStore

ARTIFACT_REFERENCE_SCHEMA = "tau.artifact_reference.v1"
DEFAULT_MAX_REFERENCE_BYTES = 1024 * 1024


class ArtifactReferenceError(RuntimeError):
    """Fail-closed artifact-reference error with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class ArtifactReferenceMaterialization:
    reference: dict[str, Any]
    admission: dict[str, Any]


def build_artifact_reference(
    *,
    run_store: SqliteDagRunStore,
    run_id: str,
    binding: DagPlanContextBinding,
    selected: Mapping[str, Any],
    source_node_id: str,
    source_attempt_id: str,
    target_node_id: str,
) -> ArtifactReferenceMaterialization:
    """Build and verify a `tau.artifact_reference.v1` from an admitted artifact."""

    admission = _matching_admission(
        run_store=run_store,
        run_id=run_id,
        source_attempt_id=source_attempt_id,
        selected=selected,
    )
    path = _contained_path(
        Path(str(admission["path"])),
        root=run_store.path.parent,
    )
    payload = path.read_bytes()
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if digest != admission["sha256"]:
        raise ArtifactReferenceError("NODE_INPUT_REFERENCE_HASH_MISMATCH")
    if len(payload) != int(admission["size_bytes"]):
        raise ArtifactReferenceError("NODE_INPUT_REFERENCE_SIZE_MISMATCH")
    max_bytes = binding.max_reference_bytes or DEFAULT_MAX_REFERENCE_BYTES
    if len(payload) > max_bytes:
        raise ArtifactReferenceError("NODE_INPUT_REFERENCE_OVER_BUDGET")
    artifact_schema = _artifact_schema(selected, payload)
    schemas = set(binding.accepted_source_schemas)
    if "*" not in schemas and artifact_schema not in schemas:
        raise ArtifactReferenceError("NODE_INPUT_REFERENCE_SCHEMA_MISMATCH")
    reference_without_hash = {
        "schema": ARTIFACT_REFERENCE_SCHEMA,
        "admitted_artifact_id": str(admission["admission_id"]),
        "artifact_schema": artifact_schema,
        "uri": path.as_uri(),
        "path": str(path),
        "sha256": str(admission["sha256"]),
        "size_bytes": int(admission["size_bytes"]),
        "producer": {
            "run_id": run_id,
            "node_id": source_node_id,
            "attempt_id": source_attempt_id,
        },
        "consumer": {"node_id": target_node_id},
        "receipt_kind": str(admission["receipt_kind"]),
        "policy_sha256": canonical_sha256(
            {
                "materialization_mode": binding.materialization_mode,
                "accepted_source_schemas": list(binding.accepted_source_schemas),
                "selector_kind": binding.selector_kind,
                "max_reference_bytes": binding.max_reference_bytes,
            }
        ),
        "data_boundary_sha256": canonical_sha256(
            {
                "run_store_root": str(run_store.path.parent.resolve()),
                "path": str(path),
                "sha256": str(admission["sha256"]),
                "size_bytes": int(admission["size_bytes"]),
            }
        ),
        "selector": {
            "kind": binding.selector_kind,
            "projection": binding.projection,
            "accepted_source_schemas": list(binding.accepted_source_schemas),
        },
    }
    reference = {
        **reference_without_hash,
        "reference_sha256": canonical_sha256(reference_without_hash),
    }
    return ArtifactReferenceMaterialization(reference=reference, admission=admission)


def dereference_artifact_reference(
    reference: Mapping[str, Any],
    *,
    run_store_root: Path,
    max_bytes: int = DEFAULT_MAX_REFERENCE_BYTES,
    selector: Mapping[str, Any] | None = None,
) -> bytes | dict[str, Any]:
    """Read an artifact reference through Tau-owned verification gates."""

    if reference.get("schema") != ARTIFACT_REFERENCE_SCHEMA:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SCHEMA_INVALID")
    path_value = reference.get("path")
    sha_value = reference.get("sha256")
    if not isinstance(path_value, str) or not isinstance(sha_value, str):
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_INVALID")
    path = _contained_path(Path(path_value), root=run_store_root)
    payload = path.read_bytes()
    if len(payload) > max_bytes:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_OVER_BUDGET")
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if digest != sha_value:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_HASH_MISMATCH")
    if selector is None:
        return payload
    return _apply_selector(payload, selector)


def _matching_admission(
    *,
    run_store: SqliteDagRunStore,
    run_id: str,
    source_attempt_id: str,
    selected: Mapping[str, Any],
) -> dict[str, Any]:
    expected_admission_id = _optional_str(selected.get("admission_id"))
    expected_sha = _optional_str(selected.get("sha256"))
    expected_path = _optional_str(selected.get("path"))
    expected_kind = _optional_str(selected.get("receipt_kind"))
    matches: list[dict[str, Any]] = []
    for admission in run_store.list_admissions(run_id):
        if admission["attempt_id"] != source_attempt_id:
            continue
        if expected_admission_id and admission["admission_id"] != expected_admission_id:
            continue
        if expected_sha and admission["sha256"] != expected_sha:
            continue
        if expected_path and str(Path(str(admission["path"])).resolve()) != str(
            Path(expected_path).expanduser().resolve()
        ):
            continue
        if expected_kind and admission["receipt_kind"] != expected_kind:
            continue
        matches.append(admission)
    if not matches:
        raise ArtifactReferenceError("NODE_INPUT_REFERENCE_ADMISSION_MISSING")
    if len(matches) > 1:
        raise ArtifactReferenceError("NODE_INPUT_REFERENCE_ADMISSION_AMBIGUOUS")
    return matches[0]


def _contained_path(path: Path, *, root: Path) -> Path:
    try:
        resolved_root = root.expanduser().resolve(strict=True)
        resolved_path = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ArtifactReferenceError("NODE_INPUT_REFERENCE_PATH_MISSING", str(path)) from exc
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ArtifactReferenceError("NODE_INPUT_REFERENCE_PATH_ESCAPE", str(path)) from exc
    return resolved_path


def _artifact_schema(selected: Mapping[str, Any], payload: bytes) -> str:
    schema = selected.get("schema")
    if isinstance(schema, str) and schema:
        return schema
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        return "application/octet-stream"
    if isinstance(parsed, Mapping) and isinstance(parsed.get("schema"), str):
        return str(parsed["schema"])
    return "application/json"


def _apply_selector(payload: bytes, selector: Mapping[str, Any]) -> dict[str, Any]:
    if selector.get("kind") != "json_key":
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SELECTOR_INVALID")
    key = selector.get("key")
    if not isinstance(key, str) or not key:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SELECTOR_INVALID")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_JSON_INVALID") from exc
    if not isinstance(parsed, Mapping) or key not in parsed:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SELECTOR_MISSING")
    return {
        "schema": "tau.artifact_reference.selected_json_key.v1",
        "key": key,
        "value": parsed[key],
    }


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
