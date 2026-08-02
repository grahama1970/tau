"""Resolve declared DAG context bindings into durable node input manifests.

The scheduler uses this module at the attempt boundary. It records exactly which
predecessor data was supplied to an adapter, which declared bindings were
inactive or invalid, and the canonical hash needed to replay or inspect that
attempt later. Missing or invalid required context fails closed before the
adapter is called.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tau_coding.dag_runtime.admission import write_durable_json
from tau_coding.dag_runtime.artifact_reference import (
    ArtifactReferenceError,
    build_artifact_reference,
)
from tau_coding.dag_runtime.model import (
    CONTEXT_BINDING_MATERIALIZATION_MODES,
    CONTEXT_BINDING_ON_INVALID,
    CONTEXT_BINDING_ON_MISSING,
    CONTEXT_BINDING_SELECTOR_KINDS,
    DagPlan,
    DagPlanContextBinding,
    DagPlanNode,
    canonical_sha256,
)
from tau_coding.dag_runtime.run_store import DagAttemptIdentity, DagRunLease, SqliteDagRunStore
from tau_coding.public_dag_contracts import immutable_json

NODE_INPUT_MANIFEST_SCHEMA = "tau.node_input_manifest.v1"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class NodeInputManifestResolution:
    accepted_inputs: tuple[Mapping[str, Any], ...]
    manifest: Mapping[str, Any]
    blocked_result: Mapping[str, Any] | None = None
    admission: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "accepted_inputs",
            tuple(immutable_json(item) for item in self.accepted_inputs),
        )
        object.__setattr__(self, "manifest", immutable_json(self.manifest))
        if self.blocked_result is not None:
            object.__setattr__(self, "blocked_result", immutable_json(self.blocked_result))
        if self.admission is not None:
            object.__setattr__(self, "admission", immutable_json(self.admission))


def resolve_node_input_manifest(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    identity: DagAttemptIdentity,
    bindings: tuple[DagPlanContextBinding, ...],
    edge_states: Mapping[str, str],
    results: Mapping[str, Mapping[str, Any]],
    run_store: SqliteDagRunStore | None = None,
) -> NodeInputManifestResolution:
    """Resolve declared context bindings and build a replayable manifest."""

    accepted_inputs: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    blocked_result: dict[str, Any] | None = None

    for binding in bindings:
        entry = _base_entry(binding)
        invalid_code = _binding_config_error(binding)
        if invalid_code is not None:
            entry.update({"disposition": "invalid", "reason": invalid_code})
            entries.append(entry)
            blocked_result = blocked_result or _blocked_result(node.node_id, invalid_code)
            continue
        if edge_states.get(binding.control_edge_id) != "success":
            entry.update({"disposition": "omitted", "reason": "control_edge_inactive"})
            entries.append(entry)
            continue
        source_result = results.get(binding.source_node_id)
        if not isinstance(source_result, Mapping):
            entry.update({"disposition": "omitted", "reason": "source_result_missing"})
            entries.append(entry)
            blocked_result = blocked_result or _policy_block(
                node.node_id, binding.on_missing, "NODE_INPUT_MISSING"
            )
            continue
        accepted_output = source_result.get("accepted_output")
        entry["source_attempt_id"] = _optional_str(source_result.get("scheduler_attempt_id"))
        if not isinstance(accepted_output, Mapping):
            entry.update({"disposition": "omitted", "reason": "accepted_output_missing"})
            entries.append(entry)
            blocked_result = blocked_result or _policy_block(
                node.node_id, binding.on_missing, "NODE_INPUT_MISSING"
            )
            continue
        selected, reason = _select_declared_value(binding, accepted_output)
        if selected is None:
            entry.update({"disposition": "invalid", "reason": reason})
            entries.append(entry)
            blocked_result = blocked_result or _policy_block(
                node.node_id, binding.on_invalid, reason
            )
            continue
        if binding.materialization_mode == "by_reference":
            if run_store is None:
                entry.update(
                    {
                        "disposition": "invalid",
                        "reason": "NODE_INPUT_REFERENCE_REQUIRES_RUN_STORE",
                    }
                )
                entries.append(entry)
                blocked_result = blocked_result or _blocked_result(
                    node.node_id, "NODE_INPUT_REFERENCE_REQUIRES_RUN_STORE"
                )
                continue
            source_attempt_id = entry.get("source_attempt_id")
            if not isinstance(source_attempt_id, str):
                entry.update(
                    {
                        "disposition": "invalid",
                        "reason": "NODE_INPUT_REFERENCE_SOURCE_ATTEMPT_MISSING",
                    }
                )
                entries.append(entry)
                blocked_result = blocked_result or _policy_block(
                    node.node_id,
                    binding.on_invalid,
                    "NODE_INPUT_REFERENCE_SOURCE_ATTEMPT_MISSING",
                )
                continue
            try:
                materialized = build_artifact_reference(
                    run_store=run_store,
                    run_id=identity.run_id,
                    binding=binding,
                    selected=selected,
                    source_node_id=binding.source_node_id,
                    source_attempt_id=source_attempt_id,
                    target_node_id=node.node_id,
                )
            except ArtifactReferenceError as exc:
                entry.update({"disposition": "invalid", "reason": exc.code})
                entries.append(entry)
                blocked_result = blocked_result or _policy_block(
                    node.node_id, binding.on_invalid, exc.code
                )
                continue
            reference = materialized.reference
            entry.update(
                {
                    "disposition": "referenced",
                    "reason": "selected_by_reference",
                    "selected_schema": _optional_str(reference.get("artifact_schema")),
                    "selected_path": _optional_str(reference.get("path")),
                    "selected_sha256": _optional_str(reference.get("sha256")),
                    "admitted_artifact_id": reference["admitted_artifact_id"],
                    "artifact_reference_sha256": reference["reference_sha256"],
                    "dereference_receipt": materialized.dereference_receipt,
                }
            )
            entries.append(entry)
            accepted_inputs.append(reference)
            continue
        selected_sha256, hash_error = _selected_hash(selected)
        if hash_error is not None:
            entry.update(
                {
                    "disposition": "invalid",
                    "reason": hash_error,
                    "selected_schema": _optional_str(selected.get("schema")),
                    "selected_path": _optional_str(selected.get("path")),
                    "selected_sha256": selected_sha256,
                    "declared_sha256": _optional_str(selected.get("sha256")),
                }
            )
            entries.append(entry)
            blocked_result = blocked_result or _blocked_result(node.node_id, hash_error)
            continue
        entry.update(
            {
                "disposition": "included",
                "reason": "selected",
                "selected_schema": _optional_str(selected.get("schema")),
                "selected_path": _optional_str(selected.get("path")),
                "selected_sha256": selected_sha256,
            }
        )
        entries.append(entry)
        accepted_inputs.append(dict(selected))

    manifest_without_hash = {
        "schema": NODE_INPUT_MANIFEST_SCHEMA,
        "run_id": identity.run_id,
        "plan_sha256": plan.plan_sha256,
        "node_id": node.node_id,
        "attempt_id": identity.attempt_id,
        "attempt": identity.attempt,
        "bindings": entries,
        "accepted_input_count": len(accepted_inputs),
    }
    manifest = {
        **manifest_without_hash,
        "canonical_manifest_hash": canonical_sha256(manifest_without_hash),
    }
    return NodeInputManifestResolution(
        accepted_inputs=tuple(accepted_inputs),
        manifest=manifest,
        blocked_result=blocked_result,
    )


def admit_node_input_manifest(
    *,
    run_store: SqliteDagRunStore,
    lease: DagRunLease,
    identity: DagAttemptIdentity,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Durably write and admit one ``tau.node_input_manifest.v1`` receipt."""

    result = write_durable_json(
        run_store.path.parent / "node-input-manifests" / f"{identity.attempt_id}.json",
        manifest,
    )
    return run_store.admit_receipt(
        lease,
        identity.attempt_id,
        receipt_kind=NODE_INPUT_MANIFEST_SCHEMA,
        sha256=result.sha256,
        path=str(result.path),
        size_bytes=result.size_bytes,
    )


def _base_entry(binding: DagPlanContextBinding) -> dict[str, Any]:
    return {
        "binding_id": binding.binding_id,
        "source_node_id": binding.source_node_id,
        "target_node_id": binding.target_node_id,
        "control_edge_id": binding.control_edge_id,
        "projection": binding.projection,
        "activation": binding.activation,
        "origin": binding.origin,
        "accepted_source_schemas": list(binding.accepted_source_schemas),
        "selector_kind": binding.selector_kind,
        "materialization_mode": binding.materialization_mode,
        "on_missing": binding.on_missing,
        "on_invalid": binding.on_invalid,
    }


def _binding_config_error(binding: DagPlanContextBinding) -> str | None:
    if binding.selector_kind not in CONTEXT_BINDING_SELECTOR_KINDS:
        return "NODE_INPUT_BINDING_SELECTOR_INVALID"
    if binding.materialization_mode not in CONTEXT_BINDING_MATERIALIZATION_MODES:
        return "NODE_INPUT_BINDING_MATERIALIZATION_INVALID"
    if binding.on_missing not in CONTEXT_BINDING_ON_MISSING:
        return "NODE_INPUT_BINDING_ON_MISSING_INVALID"
    if binding.on_invalid not in CONTEXT_BINDING_ON_INVALID:
        return "NODE_INPUT_BINDING_ON_INVALID_INVALID"
    if not binding.accepted_source_schemas:
        return "NODE_INPUT_BINDING_SCHEMA_SET_EMPTY"
    if binding.max_reference_bytes is not None and (
        not isinstance(binding.max_reference_bytes, int) or binding.max_reference_bytes < 1
    ):
        return "NODE_INPUT_BINDING_REFERENCE_BUDGET_INVALID"
    return None


def _select_declared_value(
    binding: DagPlanContextBinding,
    accepted_output: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    schemas = set(binding.accepted_source_schemas)
    if binding.selector_kind == "accepted_output":
        schema = accepted_output.get("schema")
        if "*" not in schemas and schema not in schemas:
            return None, "NODE_INPUT_SCHEMA_MISMATCH"
        return dict(accepted_output), "selected"
    collection_name = "artifacts" if binding.selector_kind == "artifact_by_schema" else "receipts"
    collection = accepted_output.get(collection_name)
    if not isinstance(collection, list):
        return None, f"NODE_INPUT_{collection_name.upper()}_MISSING"
    selected = [
        item
        for item in collection
        if isinstance(item, Mapping) and ("*" in schemas or item.get("schema") in schemas)
    ]
    if not selected:
        return None, "NODE_INPUT_SCHEMA_MISMATCH"
    if len(selected) > 1:
        return None, "NODE_INPUT_SCHEMA_AMBIGUOUS"
    return dict(selected[0]), "selected"


def _policy_block(node_id: str, policy: str, code: str) -> dict[str, Any] | None:
    if policy == "omit":
        return None
    suffix = "_FAIL_CLOSED" if policy == "fail" and not code.endswith("_FAIL_CLOSED") else ""
    return _blocked_result(node_id, f"{code}{suffix}")


def _blocked_result(node_id: str, code: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "status": "BLOCKED",
        "verdict": code,
        "errors": [code],
        "retryable": False,
    }


def _selected_hash(value: Mapping[str, Any]) -> tuple[str, str | None]:
    selected = dict(value)
    declared = selected.get("sha256")
    hash_payload = {key: item for key, item in selected.items() if key != "sha256"}
    computed = canonical_sha256(hash_payload)
    if declared is None:
        return computed, None
    if not isinstance(declared, str) or not SHA256_RE.fullmatch(declared):
        return computed, "NODE_INPUT_DECLARED_HASH_MALFORMED"
    if declared != computed:
        return computed, "NODE_INPUT_DECLARED_HASH_MISMATCH"
    return computed, None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
