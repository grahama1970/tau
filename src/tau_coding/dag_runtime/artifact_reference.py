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
from typing import Any, Protocol

from tau_coding.dag_runtime.model import DagPlanContextBinding, canonical_sha256
from tau_coding.dag_runtime.run_store import DagRunStoreError, SqliteDagRunStore

ARTIFACT_REFERENCE_SCHEMA = "tau.artifact_reference.v1"
DEFAULT_MAX_REFERENCE_BYTES = 1024 * 1024
_REFERENCE_KEYS = frozenset(
    {
        "schema",
        "admitted_artifact_id",
        "artifact_schema",
        "uri",
        "path",
        "sha256",
        "size_bytes",
        "producer",
        "consumer",
        "receipt_kind",
        "policy_sha256",
        "data_boundary_sha256",
        "selector",
        "reference_sha256",
    }
)


class ArtifactAdmissionReader(Protocol):
    path: Path

    def load_admission(self, run_id: str, admission_id: str) -> dict[str, Any]: ...


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
    dereference_receipt: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArtifactDereferenceResult:
    value: bytes | dict[str, Any]
    receipt: dict[str, Any]


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
        "policy_sha256": _binding_policy_sha256(binding),
        "data_boundary_sha256": _data_boundary_sha256(
            run_store_root=run_store.path.parent,
            path=path,
            sha256=str(admission["sha256"]),
            size_bytes=int(admission["size_bytes"]),
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
    return ArtifactReferenceMaterialization(
        reference=reference,
        admission=admission,
        dereference_receipt=_dereference_receipt(
            reference=reference,
            admission=admission,
            binding=binding,
            disposition="built",
        ),
    )


def dereference_artifact_reference(
    reference: Mapping[str, Any],
    *,
    run_store: ArtifactAdmissionReader,
    expected_run_id: str,
    expected_producer_node_id: str,
    expected_producer_attempt_id: str,
    expected_consumer_node_id: str,
    binding: DagPlanContextBinding,
    run_store_root: Path,
    max_bytes: int = DEFAULT_MAX_REFERENCE_BYTES,
    selector: Mapping[str, Any] | None = None,
    return_receipt: bool = False,
) -> bytes | dict[str, Any] | ArtifactDereferenceResult:
    """Read an artifact reference through Tau-owned verification gates."""

    _verify_reference_envelope(reference)
    admission = _verify_reference_admission(
        reference=reference,
        run_store=run_store,
        expected_run_id=expected_run_id,
        expected_producer_node_id=expected_producer_node_id,
        expected_producer_attempt_id=expected_producer_attempt_id,
        expected_consumer_node_id=expected_consumer_node_id,
        binding=binding,
        run_store_root=run_store_root,
    )
    path_value = str(reference["path"])
    sha_value = str(reference["sha256"])
    path = _contained_path(Path(path_value), root=run_store_root)
    payload = path.read_bytes()
    if len(payload) > max_bytes:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_OVER_BUDGET")
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if digest != sha_value:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_HASH_MISMATCH")
    _verify_embedded_schema(str(reference["artifact_schema"]), payload)
    if selector is None:
        value: bytes | dict[str, Any] = payload
    else:
        _verify_selector_allowed(reference, selector)
        value = _apply_selector(payload, selector)
    receipt = _dereference_receipt(
        reference=reference,
        admission=admission,
        binding=binding,
        disposition="dereferenced",
        selector=selector,
    )
    if return_receipt:
        return ArtifactDereferenceResult(value=value, receipt=receipt)
    return value


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
    embedded = _embedded_json_schema(payload)
    if isinstance(schema, str) and schema:
        if embedded is not None and embedded != schema:
            raise ArtifactReferenceError("NODE_INPUT_REFERENCE_EMBEDDED_SCHEMA_MISMATCH")
        return schema
    if embedded is not None:
        return embedded
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        return "application/octet-stream"
    if isinstance(parsed, Mapping) and isinstance(parsed.get("schema"), str):
        return str(parsed["schema"])
    return "application/json"


def _verify_reference_envelope(reference: Mapping[str, Any]) -> None:
    if set(reference) != _REFERENCE_KEYS:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_ENVELOPE_INVALID")
    if reference.get("schema") != ARTIFACT_REFERENCE_SCHEMA:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SCHEMA_INVALID")
    reference_hash = reference.get("reference_sha256")
    if not isinstance(reference_hash, str) or not reference_hash.startswith("sha256:"):
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_HASH_INVALID")
    without_hash = {key: value for key, value in reference.items() if key != "reference_sha256"}
    if canonical_sha256(without_hash) != reference_hash:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_ENVELOPE_HASH_MISMATCH")
    for key in (
        "admitted_artifact_id",
        "artifact_schema",
        "uri",
        "path",
        "sha256",
        "receipt_kind",
        "policy_sha256",
        "data_boundary_sha256",
    ):
        if not isinstance(reference.get(key), str) or not reference[key]:
            raise ArtifactReferenceError("ARTIFACT_REFERENCE_INVALID")
    if not isinstance(reference["size_bytes"], int) or isinstance(reference["size_bytes"], bool):
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_INVALID")
    if not isinstance(reference["producer"], Mapping) or not isinstance(
        reference["consumer"], Mapping
    ):
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_INVALID")
    if not isinstance(reference["selector"], Mapping):
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SELECTOR_INVALID")


def _verify_reference_admission(
    *,
    reference: Mapping[str, Any],
    run_store: ArtifactAdmissionReader,
    expected_run_id: str,
    expected_producer_node_id: str,
    expected_producer_attempt_id: str,
    expected_consumer_node_id: str,
    binding: DagPlanContextBinding,
    run_store_root: Path,
) -> dict[str, Any]:
    try:
        admission = run_store.load_admission(
            expected_run_id,
            str(reference["admitted_artifact_id"]),
        )
    except DagRunStoreError as exc:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_ADMISSION_MISSING") from exc
    if str(admission["admission_id"]) != reference["admitted_artifact_id"]:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_ADMISSION_MISMATCH")
    if str(admission["run_id"]) != expected_run_id:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_PRODUCER_MISMATCH")
    if str(admission["node_id"]) != expected_producer_node_id:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_PRODUCER_MISMATCH")
    if str(admission["attempt_id"]) != expected_producer_attempt_id:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_PRODUCER_MISMATCH")
    producer = reference["producer"]
    consumer = reference["consumer"]
    if (
        producer.get("run_id") != expected_run_id
        or producer.get("node_id") != expected_producer_node_id
        or producer.get("attempt_id") != expected_producer_attempt_id
    ):
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_PRODUCER_MISMATCH")
    if consumer.get("node_id") != expected_consumer_node_id:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_CONSUMER_MISMATCH")
    if str(admission["receipt_kind"]) != reference["receipt_kind"]:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_RECEIPT_KIND_MISMATCH")
    if str(admission["path"]) != reference["path"]:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_PATH_MISMATCH")
    if str(admission["sha256"]) != reference["sha256"]:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_HASH_MISMATCH")
    if int(admission["size_bytes"]) != reference["size_bytes"]:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SIZE_MISMATCH")
    path = _contained_path(Path(str(reference["path"])), root=run_store_root)
    if path.as_uri() != reference["uri"]:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_URI_MISMATCH")
    if reference["policy_sha256"] != _binding_policy_sha256(binding):
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_POLICY_MISMATCH")
    if reference["data_boundary_sha256"] != _data_boundary_sha256(
        run_store_root=run_store_root,
        path=path,
        sha256=str(reference["sha256"]),
        size_bytes=int(reference["size_bytes"]),
    ):
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_DATA_BOUNDARY_MISMATCH")
    _verify_binding_selector(reference, binding)
    return admission


def _binding_policy_sha256(binding: DagPlanContextBinding) -> str:
    return canonical_sha256(
        {
            "materialization_mode": binding.materialization_mode,
            "accepted_source_schemas": list(binding.accepted_source_schemas),
            "selector_kind": binding.selector_kind,
            "max_reference_bytes": binding.max_reference_bytes,
        }
    )


def _data_boundary_sha256(
    *,
    run_store_root: Path,
    path: Path,
    sha256: str,
    size_bytes: int,
) -> str:
    return canonical_sha256(
        {
            "run_store_root": str(run_store_root.resolve()),
            "path": str(path.resolve()),
            "sha256": sha256,
            "size_bytes": size_bytes,
        }
    )


def _verify_binding_selector(reference: Mapping[str, Any], binding: DagPlanContextBinding) -> None:
    selector = reference["selector"]
    expected = {
        "kind": binding.selector_kind,
        "projection": binding.projection,
        "accepted_source_schemas": list(binding.accepted_source_schemas),
    }
    if dict(selector) != expected:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SELECTOR_POLICY_MISMATCH")


def _verify_selector_allowed(reference: Mapping[str, Any], selector: Mapping[str, Any]) -> None:
    if selector.get("kind") != "json_key":
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SELECTOR_INVALID")
    key = selector.get("key")
    if not isinstance(key, str) or not key:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SELECTOR_INVALID")
    reference_selector = reference["selector"]
    projection = reference_selector.get("projection")
    if projection not in {
        "accepted_output_if_present",
        "activated_predecessor_evidence_and_artifacts",
    }:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_SELECTOR_POLICY_MISMATCH")


def _verify_embedded_schema(claimed_schema: str, payload: bytes) -> None:
    embedded = _embedded_json_schema(payload)
    if embedded is not None and embedded != claimed_schema:
        raise ArtifactReferenceError("ARTIFACT_REFERENCE_EMBEDDED_SCHEMA_MISMATCH")


def _embedded_json_schema(payload: bytes) -> str | None:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if isinstance(parsed, Mapping) and isinstance(parsed.get("schema"), str):
        return str(parsed["schema"])
    return None


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


def _dereference_receipt(
    *,
    reference: Mapping[str, Any],
    admission: Mapping[str, Any],
    binding: DagPlanContextBinding,
    disposition: str,
    selector: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    receipt_without_hash = {
        "schema": "tau.artifact_dereference_receipt.v1",
        "disposition": disposition,
        "reference_sha256": reference["reference_sha256"],
        "admitted_artifact_id": admission["admission_id"],
        "producer": dict(reference["producer"]),
        "consumer": dict(reference["consumer"]),
        "receipt_kind": admission["receipt_kind"],
        "path": admission["path"],
        "sha256": admission["sha256"],
        "size_bytes": int(admission["size_bytes"]),
        "policy_sha256": _binding_policy_sha256(binding),
        "data_boundary_sha256": reference["data_boundary_sha256"],
        "selector": dict(selector) if selector is not None else None,
    }
    return {
        **receipt_without_hash,
        "receipt_sha256": canonical_sha256(receipt_without_hash),
    }


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
