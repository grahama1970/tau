"""Resolve declared DAG context bindings into durable node input manifests.

The scheduler uses this module at the attempt boundary. It records exactly which
predecessor data was supplied to an adapter, which declared bindings were
inactive or invalid, and the canonical hash needed to replay or inspect that
attempt later. Missing or invalid required context fails closed before the
adapter is called.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tau_coding.dag_runtime.admission import read_back_durable_json, write_durable_json
from tau_coding.dag_runtime.artifact_reference import (
    ArtifactReferenceError,
    build_artifact_reference,
)
from tau_coding.dag_runtime.model import (
    CONTEXT_BINDING_MATERIALIZATION_MODES,
    CONTEXT_BINDING_ON_INVALID,
    CONTEXT_BINDING_ON_MISSING,
    CONTEXT_BINDING_SELECTOR_KINDS,
    DAG_NODE_INPUT_CONTRACT_ACCEPTED_INPUTS,
    DAG_NODE_INPUT_CONTRACT_COMPAT,
    DAG_NODE_INPUT_CONTRACT_IDS,
    DAG_NODE_INPUT_CONTRACT_NONE,
    DagPlan,
    DagPlanContextBinding,
    DagPlanNode,
    canonical_sha256,
)
from tau_coding.dag_runtime.run_store import DagAttemptIdentity, DagRunLease, SqliteDagRunStore
from tau_coding.public_dag_contracts import immutable_json

NODE_INPUT_MANIFEST_SCHEMA = "tau.node_input_manifest.v1"
DAG_NODE_DISPATCH_SCHEMA = "tau.dag_node_dispatch.v1"
DAG_NODE_DISPATCH_VALIDATION_SCHEMA = "tau.dag_node_dispatch_validation.v1"
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


@dataclass(frozen=True, slots=True)
class DagNodeDispatchProjection:
    """Typed scheduler-to-adapter view derived from the strict dispatch envelope."""

    envelope: Mapping[str, Any]
    accepted_inputs: tuple[dict[str, Any], ...]
    dispatch_envelope_sha256: str
    dispatch_envelope_path: str | None = None
    dispatch_envelope_admission_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "envelope", immutable_json(self.envelope))
        object.__setattr__(
            self,
            "accepted_inputs",
            tuple(dict(immutable_json(item)) for item in self.accepted_inputs),
        )


class DagNodeDispatchAdmissionError(ValueError):
    """Raised when a scheduler-to-node dispatch envelope is not admissible."""

    def __init__(self, code: str, path: str) -> None:
        super().__init__(f"{code}:{path}")
        self.code = code
        self.path = path


class _DispatchRefModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ref_type: str = Field(min_length=1)
    ref_id: str = Field(min_length=1)
    sha256: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if SHA256_RE.fullmatch(value) is None:
            raise ValueError("dag_node_dispatch_hash_invalid")
        return value

    @model_validator(mode="after")
    def _ref_hash_matches_metadata(self) -> _DispatchRefModel:
        if canonical_sha256(self.metadata) != self.sha256:
            raise ValueError("dag_node_dispatch_ref_hash_mismatch")
        return self


class DagNodeDispatchEnvelopeModel(BaseModel):
    """Strict external handoff model for the scheduler-to-node trust boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_: Literal["tau.dag_node_dispatch.v1"] = Field(alias="schema")
    run_id: str = Field(min_length=1)
    dag_id: str = Field(min_length=1)
    plan_sha256: str
    plan_revision: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    adapter_kind: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    goal_version: str = Field(min_length=1)
    goal_hash: str
    checkpoint_id: str = Field(min_length=1)
    checkpoint_hash: str
    input_schema_id: str = Field(min_length=1)
    input_schema_version: str = Field(min_length=1)
    input_schema_declaration: Literal["explicit"]
    resolved_input_manifest_sha256: str
    resolved_input_manifest: dict[str, Any]
    accepted_parent_results: list[_DispatchRefModel] = Field(default_factory=list)
    accepted_evidence_refs: list[_DispatchRefModel] = Field(default_factory=list)
    route_decision_refs: list[_DispatchRefModel] = Field(default_factory=list)
    join_decision_refs: list[_DispatchRefModel] = Field(default_factory=list)
    workspace_revision: _DispatchRefModel
    source_revision: _DispatchRefModel
    policy_refs: list[_DispatchRefModel] = Field(default_factory=list)
    capability_refs: list[_DispatchRefModel] = Field(default_factory=list)
    resource_lease_refs: list[_DispatchRefModel] = Field(default_factory=list)
    allowed_effect_refs: list[_DispatchRefModel] = Field(default_factory=list)
    runtime_binding: _DispatchRefModel
    accepted_inputs: list[dict[str, Any]] = Field(default_factory=list)
    accepted_inputs_sha256: str
    envelope_sha256: str

    @field_validator(
        "plan_sha256",
        "goal_hash",
        "checkpoint_hash",
        "resolved_input_manifest_sha256",
        "accepted_inputs_sha256",
        "envelope_sha256",
    )
    @classmethod
    def _sha256(cls, value: str) -> str:
        if SHA256_RE.fullmatch(value) is None:
            raise ValueError("dag_node_dispatch_hash_invalid")
        return value

    @field_validator("input_schema_id")
    @classmethod
    def _input_schema_id(cls, value: str) -> str:
        if value not in DAG_NODE_INPUT_CONTRACT_IDS:
            raise ValueError("dag_node_dispatch_input_schema_unknown")
        return value

    @model_validator(mode="after")
    def _cross_checks(self) -> DagNodeDispatchEnvelopeModel:
        payload = self.model_dump(by_alias=True)
        envelope_sha256 = payload.pop("envelope_sha256")
        if canonical_sha256(payload) != envelope_sha256:
            raise ValueError("dag_node_dispatch_envelope_hash_mismatch")
        if canonical_sha256(list(self.accepted_inputs)) != self.accepted_inputs_sha256:
            raise ValueError("dag_node_dispatch_accepted_inputs_hash_mismatch")
        if (
            canonical_sha256(self.resolved_input_manifest)
            != self.resolved_input_manifest_sha256
        ):
            raise ValueError("dag_node_dispatch_manifest_hash_mismatch")
        if self.input_schema_id == DAG_NODE_INPUT_CONTRACT_NONE and self.accepted_inputs:
            raise ValueError("dag_node_dispatch_input_forbidden")
        if (
            self.input_schema_id
            in {DAG_NODE_INPUT_CONTRACT_ACCEPTED_INPUTS, DAG_NODE_INPUT_CONTRACT_COMPAT}
            and not isinstance(self.accepted_inputs, list)
        ):
            raise ValueError("dag_node_dispatch_inputs_invalid")
        source_payload_hash = self.source_revision.metadata.get("source_payload_sha256")
        if source_payload_hash != self.plan_revision:
            raise ValueError("dag_node_dispatch_plan_revision_mismatch")
        if self.runtime_binding.ref_id != self.node_id:
            raise ValueError("dag_node_dispatch_runtime_binding_mismatch")
        for ref in self.resource_lease_refs:
            if ref.ref_type == "resource_lease_token":
                if ref.metadata.get("run_id") != self.run_id:
                    raise ValueError("dag_node_dispatch_resource_lease_run_mismatch")
                if ref.metadata.get("node_id") != self.node_id:
                    raise ValueError("dag_node_dispatch_resource_lease_node_mismatch")
                if ref.metadata.get("attempt_id") != self.attempt_id:
                    raise ValueError("dag_node_dispatch_resource_lease_attempt_mismatch")
            if ref.ref_type == "worker_assignment_admission":
                if ref.metadata.get("run_id") != self.run_id:
                    raise ValueError("dag_node_dispatch_worker_assignment_run_mismatch")
                if ref.metadata.get("node_id") != self.node_id:
                    raise ValueError("dag_node_dispatch_worker_assignment_node_mismatch")
                if ref.metadata.get("attempt_id") != self.attempt_id:
                    raise ValueError("dag_node_dispatch_worker_assignment_attempt_mismatch")
        return self


def build_node_dispatch_projection(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    identity: DagAttemptIdentity,
    input_resolution: NodeInputManifestResolution,
    input_admission: Mapping[str, Any] | None,
    edge_states: Mapping[str, str],
    results: Mapping[str, Mapping[str, Any]],
    initial_workspace_read_set: Mapping[str, Any] | None = None,
    resource_lease_tokens: tuple[Any, ...] = (),
    worker_assignment_admission: Mapping[str, Any] | None = None,
) -> DagNodeDispatchProjection:
    """Build and validate one strict dispatch envelope before adapter control."""

    if identity.node_id != node.node_id:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_attempt_node_mismatch",
            "$.attempt_id",
        )
    input_schema_id, input_schema_declaration = _node_input_schema(node)
    accepted_inputs = [dict(item) for item in input_resolution.accepted_inputs]
    manifest = dict(input_resolution.manifest)
    manifest_payload_sha256 = canonical_sha256(manifest)
    manifest_admission_sha256 = _input_manifest_admission_sha256(
        identity=identity,
        input_admission=input_admission,
        manifest_durable_sha256=_durable_json_sha256(manifest),
        fallback_sha256=manifest_payload_sha256,
    )
    _validate_materialized_input_contract(
        input_schema_id=input_schema_id,
        accepted_inputs=accepted_inputs,
    )
    goal = _goal_identity(plan)
    payload_without_hash = {
        "schema": DAG_NODE_DISPATCH_SCHEMA,
        "run_id": identity.run_id,
        "dag_id": plan.plan_id,
        "plan_sha256": plan.plan_sha256,
        "plan_revision": plan.source_payload_sha256,
        "node_id": node.node_id,
        "adapter_kind": node.adapter_kind,
        "attempt_id": identity.attempt_id,
        "attempt": identity.attempt,
        "idempotency_key": identity.idempotency_key,
        "goal_id": goal["goal_id"],
        "goal_version": goal["goal_version"],
        "goal_hash": goal["goal_hash"],
        "checkpoint_id": _checkpoint_id(identity, input_admission),
        "checkpoint_hash": manifest_admission_sha256,
        "input_schema_id": input_schema_id,
        "input_schema_version": input_schema_id.rsplit(".", 1)[-1],
        "input_schema_declaration": input_schema_declaration,
        "resolved_input_manifest_sha256": manifest_payload_sha256,
        "resolved_input_manifest": manifest,
        "accepted_parent_results": list(
            _accepted_parent_refs(
                manifest=manifest,
                results=results,
            )
        ),
        "accepted_evidence_refs": list(
            _accepted_evidence_refs(manifest=manifest, results=results)
        ),
        "route_decision_refs": list(
            _route_decision_refs(
                plan=plan,
                manifest=manifest,
                edge_states=edge_states,
            )
        ),
        "join_decision_refs": list(
            _join_decision_refs(plan=plan, node=node, edge_states=edge_states)
        ),
        "workspace_revision": _workspace_revision_ref(initial_workspace_read_set),
        "source_revision": _source_revision_ref(plan),
        "policy_refs": list(_policy_refs(plan=plan, node=node)),
        "capability_refs": list(_capability_refs(node)),
        "resource_lease_refs": list(
            _resource_lease_refs(
                node=node,
                identity=identity,
                tokens=resource_lease_tokens,
                worker_assignment_admission=worker_assignment_admission,
            )
        ),
        "allowed_effect_refs": list(_allowed_effect_refs(node)),
        "runtime_binding": _runtime_binding_ref(node),
        "accepted_inputs": accepted_inputs,
        "accepted_inputs_sha256": canonical_sha256(accepted_inputs),
    }
    envelope = {
        **payload_without_hash,
        "envelope_sha256": canonical_sha256(payload_without_hash),
    }
    normalized = validate_node_dispatch_envelope(envelope).model_dump(by_alias=True)
    return DagNodeDispatchProjection(
        envelope=normalized,
        accepted_inputs=tuple(dict(item) for item in normalized["accepted_inputs"]),
        dispatch_envelope_sha256=normalized["envelope_sha256"],
    )


def admit_node_dispatch_envelope(
    *,
    run_store: SqliteDagRunStore,
    lease: DagRunLease,
    identity: DagAttemptIdentity,
    plan: DagPlan,
    node: DagPlanNode,
    input_resolution: NodeInputManifestResolution,
    input_admission: Mapping[str, Any] | None,
    projection: DagNodeDispatchProjection,
    edge_states: Mapping[str, str] | None = None,
    results: Mapping[str, Mapping[str, Any]] | None = None,
    initial_workspace_read_set: Mapping[str, Any] | None = None,
    resource_lease_tokens: tuple[Any, ...] = (),
    worker_assignment_admission: Mapping[str, Any] | None = None,
) -> DagNodeDispatchProjection:
    """Durably write and admit one ``tau.dag_node_dispatch.v1`` envelope."""

    normalized_projection = validate_node_dispatch_projection_against_scheduler_state(
        plan=plan,
        node=node,
        identity=identity,
        input_resolution=input_resolution,
        input_admission=input_admission,
        projection=projection,
        edge_states=edge_states,
        results=results,
        initial_workspace_read_set=initial_workspace_read_set,
        resource_lease_tokens=resource_lease_tokens,
        worker_assignment_admission=worker_assignment_admission,
    )
    result = write_durable_json(
        run_store.path.parent / "node-dispatch-envelopes" / f"{identity.attempt_id}.json",
        normalized_projection,
    )
    readback = read_back_durable_json(result)
    normalized = validate_node_dispatch_envelope(readback).model_dump(by_alias=True)
    validate_node_dispatch_projection_against_scheduler_state(
        plan=plan,
        node=node,
        identity=identity,
        input_resolution=input_resolution,
        input_admission=input_admission,
        projection=DagNodeDispatchProjection(
            envelope=normalized,
            accepted_inputs=tuple(dict(item) for item in normalized["accepted_inputs"]),
            dispatch_envelope_sha256=normalized["envelope_sha256"],
        ),
        edge_states=edge_states,
        results=results,
        initial_workspace_read_set=initial_workspace_read_set,
        resource_lease_tokens=resource_lease_tokens,
        worker_assignment_admission=worker_assignment_admission,
    )
    if normalized["envelope_sha256"] != projection.dispatch_envelope_sha256:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_persisted_hash_mismatch",
            "$.envelope_sha256",
        )
    admission = run_store.admit_receipt(
        lease,
        identity.attempt_id,
        receipt_kind=DAG_NODE_DISPATCH_SCHEMA,
        sha256=result.sha256,
        path=str(result.path),
        size_bytes=result.size_bytes,
    )
    return DagNodeDispatchProjection(
        envelope=normalized,
        accepted_inputs=projection.accepted_inputs,
        dispatch_envelope_sha256=projection.dispatch_envelope_sha256,
        dispatch_envelope_path=str(result.path),
        dispatch_envelope_admission_id=str(admission["admission_id"]),
    )


def validate_node_dispatch_projection_against_scheduler_state(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    identity: DagAttemptIdentity,
    input_resolution: NodeInputManifestResolution | None = None,
    input_admission: Mapping[str, Any] | None,
    projection: DagNodeDispatchProjection,
    edge_states: Mapping[str, str] | None = None,
    results: Mapping[str, Mapping[str, Any]] | None = None,
    initial_workspace_read_set: Mapping[str, Any] | None = None,
    resource_lease_tokens: tuple[Any, ...] = (),
    worker_assignment_admission: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a dispatch projection against scheduler-owned identity."""

    envelope = validate_node_dispatch_envelope(projection.envelope).model_dump(by_alias=True)
    if envelope["envelope_sha256"] != projection.dispatch_envelope_sha256:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_projection_hash_mismatch",
            "$.envelope_sha256",
        )
    if tuple(dict(item) for item in envelope["accepted_inputs"]) != projection.accepted_inputs:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_projection_inputs_mismatch",
            "$.accepted_inputs",
        )
    if input_resolution is not None:
        resolved_inputs = tuple(dict(item) for item in input_resolution.accepted_inputs)
        if projection.accepted_inputs != resolved_inputs:
            raise DagNodeDispatchAdmissionError(
                "dag_node_dispatch_resolved_inputs_mismatch",
                "$.accepted_inputs",
            )
    goal = _goal_identity(plan)
    input_schema_id, input_schema_declaration = _node_input_schema(node)
    expected = {
        "schema": DAG_NODE_DISPATCH_SCHEMA,
        "run_id": identity.run_id,
        "dag_id": plan.plan_id,
        "plan_sha256": plan.plan_sha256,
        "plan_revision": plan.source_payload_sha256,
        "node_id": node.node_id,
        "adapter_kind": node.adapter_kind,
        "attempt_id": identity.attempt_id,
        "attempt": identity.attempt,
        "idempotency_key": identity.idempotency_key,
        "goal_id": goal["goal_id"],
        "goal_version": goal["goal_version"],
        "goal_hash": goal["goal_hash"],
        "checkpoint_id": _checkpoint_id(identity, input_admission),
        "input_schema_id": input_schema_id,
        "input_schema_version": input_schema_id.rsplit(".", 1)[-1],
        "input_schema_declaration": input_schema_declaration,
    }
    for field, value in expected.items():
        if envelope.get(field) != value:
            raise DagNodeDispatchAdmissionError(
                f"dag_node_dispatch_{field}_mismatch",
                f"$.{field}",
            )
    manifest = envelope.get("resolved_input_manifest")
    if not isinstance(manifest, Mapping):
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_manifest_invalid",
            "$.resolved_input_manifest",
        )
    expected_manifest = {
        "schema": NODE_INPUT_MANIFEST_SCHEMA,
        "run_id": identity.run_id,
        "plan_sha256": plan.plan_sha256,
        "node_id": node.node_id,
        "attempt_id": identity.attempt_id,
        "attempt": identity.attempt,
    }
    for field, value in expected_manifest.items():
        if manifest.get(field) != value:
            raise DagNodeDispatchAdmissionError(
                f"dag_node_dispatch_manifest_{field}_mismatch",
                f"$.resolved_input_manifest.{field}",
            )
    checkpoint_hash = _input_manifest_admission_sha256(
        identity=identity,
        input_admission=input_admission,
        manifest_durable_sha256=_durable_json_sha256(manifest),
        fallback_sha256=canonical_sha256(manifest),
    )
    if envelope.get("checkpoint_hash") != checkpoint_hash:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_checkpoint_hash_mismatch",
            "$.checkpoint_hash",
        )
    _require_dispatch_value(
        envelope,
        "source_revision",
        _source_revision_ref(plan),
    )
    _require_dispatch_value(
        envelope,
        "policy_refs",
        list(_policy_refs(plan=plan, node=node)),
    )
    _require_dispatch_value(envelope, "capability_refs", list(_capability_refs(node)))
    _require_dispatch_value(envelope, "allowed_effect_refs", list(_allowed_effect_refs(node)))
    _require_dispatch_value(envelope, "runtime_binding", _runtime_binding_ref(node))
    if initial_workspace_read_set is not None:
        _require_dispatch_value(
            envelope,
            "workspace_revision",
            _workspace_revision_ref(initial_workspace_read_set),
        )
    if edge_states is not None and results is not None:
        _require_dispatch_value(
            envelope,
            "accepted_parent_results",
            list(_accepted_parent_refs(manifest=manifest, results=results)),
        )
        _require_dispatch_value(
            envelope,
            "accepted_evidence_refs",
            list(_accepted_evidence_refs(manifest=manifest, results=results)),
        )
        _require_dispatch_value(
            envelope,
            "route_decision_refs",
            list(_route_decision_refs(plan=plan, manifest=manifest, edge_states=edge_states)),
        )
        _require_dispatch_value(
            envelope,
            "join_decision_refs",
            list(_join_decision_refs(plan=plan, node=node, edge_states=edge_states)),
        )
    if resource_lease_tokens or worker_assignment_admission is not None:
        _require_dispatch_value(
            envelope,
            "resource_lease_refs",
            list(
                _resource_lease_refs(
                    node=node,
                    identity=identity,
                    tokens=resource_lease_tokens,
                    worker_assignment_admission=worker_assignment_admission,
                )
            ),
        )
    return envelope


def validate_admitted_node_dispatch_envelope(
    *,
    admission: Mapping[str, Any],
    plan: DagPlan,
    node: DagPlanNode,
    identity: DagAttemptIdentity,
    input_admission: Mapping[str, Any] | None,
    edge_states: Mapping[str, str] | None = None,
    results: Mapping[str, Mapping[str, Any]] | None = None,
    initial_workspace_read_set: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read back and validate one admitted dispatch envelope against the scheduler."""

    expected = {
        "run_id": identity.run_id,
        "node_id": identity.node_id,
        "attempt_id": identity.attempt_id,
        "receipt_kind": DAG_NODE_DISPATCH_SCHEMA,
    }
    for field, value in expected.items():
        if admission.get(field) != value:
            raise DagNodeDispatchAdmissionError(
                f"dag_node_dispatch_admission_{field}_mismatch",
                f"$.admission.{field}",
            )
    path = admission.get("path")
    admitted_sha256 = admission.get("sha256")
    if not isinstance(path, str) or not path.strip():
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_admission_path_invalid",
            "$.admission.path",
        )
    if not isinstance(admitted_sha256, str) or SHA256_RE.fullmatch(admitted_sha256) is None:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_admission_sha256_invalid",
            "$.admission.sha256",
        )
    try:
        encoded = Path(path).read_bytes()
        payload = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_admission_readback_invalid",
            "$.admission.path",
        ) from exc
    if not isinstance(payload, Mapping):
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_admission_payload_invalid",
            "$.admission.path",
        )
    actual_sha256 = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    if actual_sha256 != admitted_sha256:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_admission_sha256_mismatch",
            "$.admission.sha256",
        )
    envelope = validate_node_dispatch_envelope(payload).model_dump(by_alias=True)
    return validate_node_dispatch_projection_against_scheduler_state(
        plan=plan,
        node=node,
        identity=identity,
        input_resolution=None,
        input_admission=input_admission,
        projection=DagNodeDispatchProjection(
            envelope=envelope,
            accepted_inputs=tuple(dict(item) for item in envelope["accepted_inputs"]),
            dispatch_envelope_sha256=envelope["envelope_sha256"],
            dispatch_envelope_path=path,
            dispatch_envelope_admission_id=str(admission.get("admission_id") or ""),
        ),
        edge_states=edge_states,
        results=results,
        initial_workspace_read_set=initial_workspace_read_set,
    )


def validate_node_dispatch_envelope(payload: Mapping[str, Any]) -> DagNodeDispatchEnvelopeModel:
    """Strictly validate a scheduler-owned DAG dispatch envelope."""

    try:
        return DagNodeDispatchEnvelopeModel.model_validate(dict(payload))
    except ValidationError as exc:
        first = exc.errors()[0]
        location = tuple(first.get("loc", ()))
        message = str(first.get("msg") or "")
        code = _dispatch_validation_error_code(message)
        raise DagNodeDispatchAdmissionError(
            code,
            "$"
            + "".join(
                f".{part}" if isinstance(part, str) else f"[{part}]" for part in location
            ),
        ) from exc


def _require_dispatch_value(envelope: Mapping[str, Any], field: str, expected: Any) -> None:
    if envelope.get(field) != expected:
        raise DagNodeDispatchAdmissionError(
            f"dag_node_dispatch_{field}_mismatch",
            f"$.{field}",
        )


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


def _node_input_schema(node: DagPlanNode) -> tuple[str, str]:
    extensions = node.source_extensions.to_value()
    if not isinstance(extensions, Mapping):
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_input_contract_missing",
            "$.source_extensions",
        )
    declared = extensions.get("input_contract_id", extensions.get("input_schema_id"))
    if declared is None:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_input_contract_missing",
            "$.input_schema_id",
        )
    if not isinstance(declared, str) or declared not in DAG_NODE_INPUT_CONTRACT_IDS:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_input_schema_unknown",
            "$.input_schema_id",
        )
    expected_version = declared.rsplit(".", 1)[-1]
    if extensions.get("input_schema_version") != expected_version:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_input_schema_version_mismatch",
            "$.input_schema_version",
        )
    if extensions.get("input_contract_origin") == "canonical_scheduler_default":
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_input_contract_not_explicit",
            "$.input_contract_origin",
        )
    return declared, "explicit"


def _validate_materialized_input_contract(
    *,
    input_schema_id: str,
    accepted_inputs: list[dict[str, Any]],
) -> None:
    if input_schema_id == DAG_NODE_INPUT_CONTRACT_NONE and accepted_inputs:
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_input_forbidden",
            "$.accepted_inputs",
        )
    if input_schema_id == DAG_NODE_INPUT_CONTRACT_NONE:
        return
    if input_schema_id in {DAG_NODE_INPUT_CONTRACT_ACCEPTED_INPUTS, DAG_NODE_INPUT_CONTRACT_COMPAT}:
        for index, item in enumerate(accepted_inputs):
            try:
                canonical_sha256(item)
            except RuntimeError as exc:
                raise DagNodeDispatchAdmissionError(
                    "dag_node_dispatch_input_non_canonical",
                    f"$.accepted_inputs[{index}]",
                ) from exc
        return
    raise DagNodeDispatchAdmissionError(
        "dag_node_dispatch_input_schema_unknown",
        "$.input_schema_id",
    )


def _goal_identity(plan: DagPlan) -> dict[str, str]:
    goal = plan.goal_binding.to_value()
    if isinstance(goal, Mapping):
        goal_hash = goal.get("goal_hash")
        if not isinstance(goal_hash, str) or SHA256_RE.fullmatch(goal_hash) is None:
            goal_hash = plan.runtime_goal_hash
        return {
            "goal_id": str(goal.get("goal_id") or goal.get("kind") or plan.plan_id),
            "goal_version": str(goal.get("goal_version") or "0"),
            "goal_hash": goal_hash,
        }
    return {"goal_id": plan.plan_id, "goal_version": "0", "goal_hash": plan.runtime_goal_hash}


def _checkpoint_id(identity: DagAttemptIdentity, admission: Mapping[str, Any] | None) -> str:
    if isinstance(admission, Mapping) and isinstance(admission.get("admission_id"), str):
        return str(admission["admission_id"])
    return f"{identity.attempt_id}:node-input-manifest"


def _input_manifest_admission_sha256(
    *,
    identity: DagAttemptIdentity,
    input_admission: Mapping[str, Any] | None,
    manifest_durable_sha256: str,
    fallback_sha256: str,
) -> str:
    if input_admission is None:
        return fallback_sha256
    if not isinstance(input_admission, Mapping):
        raise DagNodeDispatchAdmissionError(
            "dag_node_dispatch_checkpoint_admission_invalid",
            "$.checkpoint_id",
        )
    expected = {
        "run_id": identity.run_id,
        "node_id": identity.node_id,
        "attempt_id": identity.attempt_id,
        "receipt_kind": NODE_INPUT_MANIFEST_SCHEMA,
        "sha256": manifest_durable_sha256,
    }
    for field, value in expected.items():
        if input_admission.get(field) != value:
            raise DagNodeDispatchAdmissionError(
                f"dag_node_dispatch_checkpoint_{field}_mismatch",
                f"$.checkpoint_id.{field}",
            )
    return str(input_admission["sha256"])


def _durable_json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _accepted_manifest_entries(manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    entries = manifest.get("bindings")
    if not isinstance(entries, list):
        return ()
    return tuple(
        entry
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("disposition") in {"included", "referenced"}
    )


def _accepted_parent_refs(
    *,
    manifest: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    refs = []
    for entry in _accepted_manifest_entries(manifest):
        source_id = str(entry.get("source_node_id") or "")
        result = results.get(source_id)
        refs.append(
            _ref(
                "accepted_parent_result",
                f"{source_id}:{entry.get('source_attempt_id') or 'unknown'}",
                {
                    "binding_id": entry.get("binding_id"),
                    "control_edge_id": entry.get("control_edge_id"),
                    "source_node_id": source_id,
                    "source_attempt_id": entry.get("source_attempt_id"),
                    "selected_sha256": entry.get("selected_sha256")
                    or entry.get("artifact_reference_sha256"),
                    "result_sha256": (
                        canonical_sha256(result) if isinstance(result, Mapping) else None
                    ),
                },
            )
        )
    return tuple(refs)


def _accepted_evidence_refs(
    *,
    manifest: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    for entry in _accepted_manifest_entries(manifest):
        source_id = str(entry.get("source_node_id") or "")
        accepted_output = results.get(source_id, {}).get("accepted_output")
        if not isinstance(accepted_output, Mapping):
            continue
        for key in ("evidence", "artifacts", "receipts"):
            values = accepted_output.get(key)
            if isinstance(values, list):
                for index, value in enumerate(values):
                    if isinstance(value, Mapping):
                        refs.append(_ref(f"accepted_{key}", f"{source_id}:{key}:{index}", value))
    return tuple(refs)


def _route_decision_refs(
    *,
    plan: DagPlan,
    manifest: Mapping[str, Any],
    edge_states: Mapping[str, str],
) -> tuple[dict[str, Any], ...]:
    active_edge_ids = {
        str(entry.get("control_edge_id"))
        for entry in _accepted_manifest_entries(manifest)
        if entry.get("control_edge_id")
    }
    refs = [
        _ref("route_edge_state", edge_id, {"edge_id": edge_id, "state": edge_states.get(edge_id)})
        for edge_id in sorted(active_edge_ids)
    ]
    for frozen in plan.route_contracts:
        route = frozen.to_value()
        if isinstance(route, Mapping):
            ordered = route.get("ordered_edge_ids")
            if isinstance(ordered, list) and any(edge_id in ordered for edge_id in active_edge_ids):
                refs.append(_ref("route_contract", str(route.get("source_node_id") or ""), route))
    return tuple(refs)


def _join_decision_refs(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    edge_states: Mapping[str, str],
) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    for frozen in plan.join_contracts:
        join = frozen.to_value()
        if not isinstance(join, Mapping) or join.get("join_node_id") != node.node_id:
            continue
        incoming = join.get("incoming_edge_ids")
        states = {}
        if isinstance(incoming, list):
            states = {
                str(edge_id): edge_states.get(str(edge_id))
                for edge_id in incoming
                if isinstance(edge_id, str)
            }
        refs.append(_ref("join_contract", node.node_id, {"contract": join, "edge_states": states}))
    return tuple(refs)


def _workspace_revision_ref(read_set: Mapping[str, Any] | None) -> dict[str, Any]:
    value = dict(read_set or {"schema": "tau.workspace_read_set.none.v1", "entry_count": 0})
    ref_id = str(value.get("read_set_id") or value.get("attempt_id") or "workspace:none")
    return _ref("workspace_revision", ref_id, value)


def _source_revision_ref(plan: DagPlan) -> dict[str, Any]:
    return _ref(
        "source_revision",
        plan.source_logical_id,
        {
            "source_family": plan.source_family,
            "source_schema": plan.source_schema,
            "source_logical_id": plan.source_logical_id,
            "source_payload_sha256": plan.source_payload_sha256,
        },
    )


def _policy_refs(*, plan: DagPlan, node: DagPlanNode) -> tuple[dict[str, Any], ...]:
    return (
        _ref("security_declarations", "plan-security", plan.security_declarations.to_value()),
        _ref("execution_limits", "plan-execution-limits", plan.execution_limits.to_value()),
        _ref("node_static_context", node.node_id, node.static_context.to_value()),
    )


def _capability_refs(node: DagPlanNode) -> tuple[dict[str, Any], ...]:
    return tuple(
        _ref("requested_capability", f"{node.node_id}:{index}", item.to_value())
        for index, item in enumerate(node.requested_capabilities)
    )


def _declared_resource_lease_refs(node: DagPlanNode) -> tuple[dict[str, Any], ...]:
    runtime = node.runtime_requirement.to_value()
    resources = runtime.get("resource_leases") if isinstance(runtime, Mapping) else None
    if not isinstance(resources, list):
        return ()
    return tuple(
        _ref("resource_lease_requirement", f"{node.node_id}:declared:{index}", item)
        for index, item in enumerate(resources)
        if isinstance(item, Mapping)
    )


def _resource_lease_refs(
    *,
    node: DagPlanNode,
    identity: DagAttemptIdentity,
    tokens: tuple[Any, ...],
    worker_assignment_admission: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = list(_declared_resource_lease_refs(node))
    for index, token in enumerate(tokens):
        payload = token.to_payload() if hasattr(token, "to_payload") else None
        if not isinstance(payload, Mapping):
            raise DagNodeDispatchAdmissionError(
                "dag_node_dispatch_resource_lease_invalid",
                f"$.resource_lease_refs[{index}]",
            )
        if payload.get("run_id") != identity.run_id:
            raise DagNodeDispatchAdmissionError(
                "dag_node_dispatch_resource_lease_run_mismatch",
                f"$.resource_lease_refs[{index}].metadata.run_id",
            )
        if payload.get("node_id") != identity.node_id:
            raise DagNodeDispatchAdmissionError(
                "dag_node_dispatch_resource_lease_node_mismatch",
                f"$.resource_lease_refs[{index}].metadata.node_id",
            )
        if payload.get("attempt_id") != identity.attempt_id:
            raise DagNodeDispatchAdmissionError(
                "dag_node_dispatch_resource_lease_attempt_mismatch",
                f"$.resource_lease_refs[{index}].metadata.attempt_id",
            )
        refs.append(
            _ref(
                "resource_lease_token",
                str(payload.get("token") or f"{identity.attempt_id}:{index}"),
                dict(payload),
            )
        )
    if worker_assignment_admission is not None:
        refs.append(
            _ref(
                "worker_assignment_admission",
                str(
                    worker_assignment_admission.get("admission_id")
                    or f"{identity.attempt_id}:worker-assignment"
                ),
                dict(worker_assignment_admission),
            )
        )
    return tuple(refs)


def _allowed_effect_refs(node: DagPlanNode) -> tuple[dict[str, Any], ...]:
    config = node.adapter_config.to_value()
    refs: list[dict[str, Any]] = []
    if isinstance(config, Mapping):
        for key in ("transaction", "effect_intent", "allowed_effects"):
            value = config.get(key)
            if value is not None:
                refs.append(_ref("allowed_effect_intent", f"{node.node_id}:{key}", value))
    return tuple(refs)


def _runtime_binding_ref(node: DagPlanNode) -> dict[str, Any]:
    return _ref("runtime_backend_binding", node.node_id, node.runtime_requirement.to_value())


def _ref(ref_type: str, ref_id: str, value: object) -> dict[str, Any]:
    metadata = dict(value) if isinstance(value, Mapping) else {"value": value}
    return {
        "ref_type": ref_type,
        "ref_id": ref_id or "unknown",
        "sha256": canonical_sha256(metadata),
        "metadata": metadata,
    }


def _dispatch_validation_error_code(message: str) -> str:
    for candidate in (
        "dag_node_dispatch_envelope_hash_mismatch",
        "dag_node_dispatch_accepted_inputs_hash_mismatch",
        "dag_node_dispatch_manifest_hash_mismatch",
        "dag_node_dispatch_input_schema_unknown",
        "dag_node_dispatch_input_schema_version_mismatch",
        "dag_node_dispatch_input_contract_missing",
        "dag_node_dispatch_input_forbidden",
        "dag_node_dispatch_inputs_invalid",
        "dag_node_dispatch_hash_invalid",
        "dag_node_dispatch_ref_hash_mismatch",
        "dag_node_dispatch_plan_revision_mismatch",
        "dag_node_dispatch_runtime_binding_mismatch",
        "dag_node_dispatch_attempt_node_mismatch",
        "dag_node_dispatch_checkpoint_admission_invalid",
        "dag_node_dispatch_checkpoint_run_id_mismatch",
        "dag_node_dispatch_checkpoint_node_id_mismatch",
        "dag_node_dispatch_checkpoint_attempt_id_mismatch",
        "dag_node_dispatch_checkpoint_receipt_kind_mismatch",
        "dag_node_dispatch_checkpoint_sha256_mismatch",
        "dag_node_dispatch_persisted_hash_mismatch",
        "dag_node_dispatch_resource_lease_invalid",
        "dag_node_dispatch_resource_lease_run_mismatch",
        "dag_node_dispatch_resource_lease_node_mismatch",
        "dag_node_dispatch_resource_lease_attempt_mismatch",
        "dag_node_dispatch_worker_assignment_run_mismatch",
        "dag_node_dispatch_worker_assignment_node_mismatch",
        "dag_node_dispatch_worker_assignment_attempt_mismatch",
    ):
        if candidate in message:
            return candidate
    if "Extra inputs are not permitted" in message:
        return "dag_node_dispatch_unknown_field"
    return "dag_node_dispatch_invalid"


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
