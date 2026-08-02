from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.artifact_reference import (
    ARTIFACT_REFERENCE_SCHEMA,
    dereference_artifact_reference,
)
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, canonical_sha256
from tau_coding.dag_runtime.node_input_manifest import (
    NODE_INPUT_MANIFEST_SCHEMA,
    resolve_node_input_manifest,
)
from tau_coding.dag_runtime.run_store import DagAttemptIdentity, SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan


def test_compiled_generic_context_binding_has_explicit_compatibility_defaults(
    tmp_path: Path,
) -> None:
    plan = _producer_consumer_plan(tmp_path)

    binding = plan.context_bindings[0]

    assert binding.accepted_source_schemas == ("*",)
    assert binding.selector_kind == "accepted_output"
    assert binding.materialization_mode == "by_value"
    assert binding.on_missing == "omit"
    assert binding.on_invalid == "omit"
    assert plan.to_payload()["context_bindings"][0] == {
        "binding_id": "generic-context:producer:consumer",
        "source_node_id": "producer",
        "target_node_id": "consumer",
        "control_edge_id": "generic-dependency:producer:consumer",
        "projection": "accepted_output_if_present",
        "activation": "after_source_pass",
        "origin": "explicit",
        "accepted_source_schemas": ["*"],
        "selector_kind": "accepted_output",
        "materialization_mode": "by_value",
        "on_missing": "omit",
        "on_invalid": "omit",
        "max_reference_bytes": None,
    }


def test_scheduler_persists_manifest_and_exposes_attempt_boundary(
    tmp_path: Path,
) -> None:
    plan = _producer_consumer_plan(tmp_path)
    observed_consumer_inputs: list[dict[str, Any]] = []
    observed_manifest_paths: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        if node.node_id == "consumer":
            observed_consumer_inputs.extend(accepted_inputs)
            assert attempt.input_manifest_path is not None
            assert attempt.input_manifest_sha256 is not None
            assert attempt.input_manifest_admission_id is not None
            observed_manifest_paths.append(attempt.input_manifest_path)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {
                "schema": "source.output.v1",
                "source_node_id": node.node_id,
            },
        }

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-node-input-manifest",
        )
        admissions = store.list_admissions(
            plan.plan_id,
            receipt_kind=NODE_INPUT_MANIFEST_SCHEMA,
        )

    assert result.status == "PASS"
    assert observed_consumer_inputs == [
        {"schema": "source.output.v1", "source_node_id": "producer"}
    ]
    assert len(admissions) == 2
    consumer_manifest = json.loads(Path(observed_manifest_paths[0]).read_text(encoding="utf-8"))
    assert consumer_manifest["schema"] == NODE_INPUT_MANIFEST_SCHEMA
    assert consumer_manifest["node_id"] == "consumer"
    assert consumer_manifest["accepted_input_count"] == 1
    entry = consumer_manifest["bindings"][0]
    assert entry["disposition"] == "included"
    assert entry["source_node_id"] == "producer"
    assert entry["source_attempt_id"] is not None
    assert entry["selected_sha256"] == canonical_sha256(
        {"schema": "source.output.v1", "source_node_id": "producer"}
    )
    expected_hash = canonical_sha256(
        {key: value for key, value in consumer_manifest.items() if key != "canonical_manifest_hash"}
    )
    assert consumer_manifest["canonical_manifest_hash"] == expected_hash


def test_by_value_declared_hash_mismatch_blocks_before_consumer_dispatch(
    tmp_path: Path,
) -> None:
    plan = _replace_single_binding(_producer_consumer_plan(tmp_path), on_invalid="omit")
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {
                "schema": "source.output.v1",
                "value": "tampered",
                "sha256": "sha256:" + "0" * 64,
            },
        }

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "BLOCKED"
    assert result.verdict == "NODE_INPUT_DECLARED_HASH_MISMATCH"
    assert called == ["producer"]


def test_by_value_declared_hash_malformed_blocks_before_consumer_dispatch(
    tmp_path: Path,
) -> None:
    plan = _producer_consumer_plan(tmp_path)
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {
                "schema": "source.output.v1",
                "value": "bad-declaration",
                "sha256": "sha256:not-a-real-digest",
            },
        }

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "BLOCKED"
    assert result.verdict == "NODE_INPUT_DECLARED_HASH_MALFORMED"
    assert called == ["producer"]


def test_by_value_declared_hash_match_passes_and_records_computed_hash(
    tmp_path: Path,
) -> None:
    plan = _producer_consumer_plan(tmp_path)
    observed_inputs: list[dict[str, Any]] = []
    observed_manifest_paths: list[str] = []
    payload_without_hash = {"schema": "source.output.v1", "value": "trusted"}
    declared_hash = canonical_sha256(payload_without_hash)

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        if node.node_id == "consumer":
            observed_inputs.extend(accepted_inputs)
            assert attempt.input_manifest_path is not None
            observed_manifest_paths.append(attempt.input_manifest_path)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {**payload_without_hash, "sha256": declared_hash},
        }

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-declared-hash",
        )

    assert result.status == "PASS"
    assert observed_inputs == [{**payload_without_hash, "sha256": declared_hash}]
    manifest = json.loads(Path(observed_manifest_paths[0]).read_text(encoding="utf-8"))
    assert manifest["bindings"][0]["selected_sha256"] == declared_hash
    assert manifest["canonical_manifest_hash"] == canonical_sha256(
        {key: value for key, value in manifest.items() if key != "canonical_manifest_hash"}
    )


def test_node_input_manifest_hashes_are_stable_across_replay_resolution(
    tmp_path: Path,
) -> None:
    plan = _producer_consumer_plan(tmp_path)
    binding = plan.context_bindings[0]
    consumer = next(node for node in plan.nodes if node.node_id == "consumer")
    identity = DagAttemptIdentity(
        run_id="stable-manifest-test",
        node_id="consumer",
        attempt=1,
        attempt_id="attempt-consumer-1",
        idempotency_key="attempt-consumer-1:effect",
    )
    kwargs = {
        "plan": plan,
        "node": consumer,
        "identity": identity,
        "bindings": (binding,),
        "edge_states": {binding.control_edge_id: "success"},
        "results": {
            "producer": {
                "scheduler_attempt_id": "attempt-producer-1",
                "accepted_output": {"schema": "source.output.v1", "value": "stable"},
            }
        },
    }

    first = resolve_node_input_manifest(**kwargs).manifest
    second = resolve_node_input_manifest(**kwargs).manifest

    assert second == first
    assert first["bindings"][0]["selected_sha256"] == canonical_sha256(
        {"schema": "source.output.v1", "value": "stable"}
    )


def test_schema_projection_exposes_only_selected_artifact(tmp_path: Path) -> None:
    plan = _replace_single_binding(
        _producer_consumer_plan(tmp_path),
        selector_kind="artifact_by_schema",
        accepted_source_schemas=("wanted.artifact.v1",),
        on_invalid="block",
    )
    observed_inputs: list[dict[str, Any]] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del attempt
        if node.node_id == "consumer":
            observed_inputs.extend(accepted_inputs)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {
                "artifacts": [
                    {"schema": "wanted.artifact.v1", "value": "visible"},
                    {"schema": "other.artifact.v1", "secret": "not exposed"},
                ]
            },
        }

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "PASS"
    assert observed_inputs == [{"schema": "wanted.artifact.v1", "value": "visible"}]


def test_required_missing_input_blocks_before_adapter_dispatch(tmp_path: Path) -> None:
    plan = _replace_single_binding(
        _producer_consumer_plan(tmp_path),
        on_missing="block",
    )
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        return {"node_id": node.node_id, "status": "PASS", "verdict": "PASS"}

    result = run_dag_plan(plan, execute_node=execute)

    assert result.status == "BLOCKED"
    assert result.verdict == "NODE_INPUT_MISSING"
    assert called == ["producer"]


def test_inactive_context_binding_contributes_no_input(tmp_path: Path) -> None:
    plan = _producer_consumer_plan(tmp_path)
    binding = plan.context_bindings[0]
    consumer = next(node for node in plan.nodes if node.node_id == "consumer")

    resolution = resolve_node_input_manifest(
        plan=plan,
        node=consumer,
        identity=DagAttemptIdentity(
            run_id="node-input-manifest-test",
            node_id="consumer",
            attempt=1,
            attempt_id="attempt-consumer-1",
            idempotency_key="attempt-consumer-1:effect",
        ),
        bindings=(binding,),
        edge_states={binding.control_edge_id: "failed"},
        results={
            "producer": {
                "accepted_output": {"schema": "source.output.v1", "source_node_id": "producer"}
            }
        },
    )

    assert resolution.accepted_inputs == ()
    assert resolution.blocked_result is None
    assert resolution.manifest["bindings"][0]["disposition"] == "omitted"
    assert resolution.manifest["bindings"][0]["reason"] == "control_edge_inactive"


def test_by_reference_binding_passes_hash_addressed_artifact_reference(
    tmp_path: Path,
) -> None:
    plan = _replace_single_binding(
        _producer_consumer_plan(tmp_path),
        selector_kind="receipt_by_schema",
        accepted_source_schemas=("large.report.v1",),
        materialization_mode="by_reference",
        on_invalid="block",
        max_reference_bytes=4096,
    )
    observed_inputs: list[dict[str, Any]] = []
    selected_payload: dict[str, Any] | None = None

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        nonlocal selected_payload
        if node.node_id == "consumer":
            observed_inputs.extend(accepted_inputs)
            selected_payload = dereference_artifact_reference(
                accepted_inputs[0],
                run_store_root=tmp_path,
                selector={"kind": "json_key", "key": "section"},
            )
            return {"node_id": node.node_id, "status": "PASS", "verdict": "PASS"}
        receipt_path = tmp_path / "receipts" / "producer.json"
        receipt_payload = {
            "schema": "large.report.v1",
            "section": {"kept": True},
            "hidden": "not embedded in context",
        }
        digest, size = _write_json_receipt(receipt_path, receipt_payload)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(receipt_path),
            "accepted_output": {
                "receipts": [
                    {
                        "schema": "large.report.v1",
                        "path": str(receipt_path),
                        "sha256": digest,
                        "size_bytes": size,
                        "receipt_kind": "node_receipt",
                    }
                ]
            },
        }

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-by-reference",
        )

    assert result.status == "PASS"
    assert observed_inputs[0]["schema"] == ARTIFACT_REFERENCE_SCHEMA
    assert observed_inputs[0]["artifact_schema"] == "large.report.v1"
    assert "hidden" not in observed_inputs[0]
    assert selected_payload == {
        "schema": "tau.artifact_reference.selected_json_key.v1",
        "key": "section",
        "value": {"kept": True},
    }


def test_by_reference_hash_mismatch_blocks_before_consumer_dispatch(
    tmp_path: Path,
) -> None:
    plan = _replace_single_binding(
        _producer_consumer_plan(tmp_path),
        selector_kind="receipt_by_schema",
        accepted_source_schemas=("large.report.v1",),
        materialization_mode="by_reference",
        on_invalid="block",
    )
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        receipt_path = tmp_path / "receipts" / "producer.json"
        digest, size = _write_json_receipt(receipt_path, {"schema": "large.report.v1"})
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(receipt_path),
            "accepted_output": {
                "receipts": [
                    {
                        "schema": "large.report.v1",
                        "path": str(receipt_path),
                        "sha256": digest,
                        "size_bytes": size,
                        "receipt_kind": "node_receipt",
                    }
                ]
            },
        }

    def mutate_after_producer(event: dict[str, Any]) -> None:
        if event.get("event") == "node_completed" and event.get("node_id") == "producer":
            (tmp_path / "receipts" / "producer.json").write_text(
                '{"schema":"large.report.v1","mutated":true}\n',
                encoding="utf-8",
            )

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-by-reference",
            event_sink=mutate_after_producer,
        )

    assert result.status == "BLOCKED"
    assert result.verdict == "NODE_INPUT_REFERENCE_HASH_MISMATCH"
    assert called == ["producer"]


def test_by_reference_over_budget_blocks_before_consumer_dispatch(
    tmp_path: Path,
) -> None:
    plan = _replace_single_binding(
        _producer_consumer_plan(tmp_path),
        selector_kind="receipt_by_schema",
        accepted_source_schemas=("large.report.v1",),
        materialization_mode="by_reference",
        on_invalid="block",
        max_reference_bytes=10,
    )
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        receipt_path = tmp_path / "receipts" / "producer.json"
        digest, size = _write_json_receipt(
            receipt_path,
            {"schema": "large.report.v1", "payload": "larger than ten bytes"},
        )
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(receipt_path),
            "accepted_output": {
                "receipts": [
                    {
                        "schema": "large.report.v1",
                        "path": str(receipt_path),
                        "sha256": digest,
                        "size_bytes": size,
                        "receipt_kind": "node_receipt",
                    }
                ]
            },
        }

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-by-reference",
        )

    assert result.status == "BLOCKED"
    assert result.verdict == "NODE_INPUT_REFERENCE_OVER_BUDGET"
    assert called == ["producer"]


def test_by_reference_missing_admission_blocks_before_consumer_dispatch(
    tmp_path: Path,
) -> None:
    plan = _replace_single_binding(
        _producer_consumer_plan(tmp_path),
        selector_kind="receipt_by_schema",
        accepted_source_schemas=("large.report.v1",),
        materialization_mode="by_reference",
        on_invalid="block",
    )
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        receipt_path = tmp_path / "receipts" / "producer.json"
        digest, size = _write_json_receipt(receipt_path, {"schema": "large.report.v1"})
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": {
                "receipts": [
                    {
                        "schema": "large.report.v1",
                        "path": str(receipt_path),
                        "sha256": digest,
                        "size_bytes": size,
                        "receipt_kind": "node_receipt",
                    }
                ]
            },
        }

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-by-reference",
        )

    assert result.status == "BLOCKED"
    assert result.verdict == "NODE_INPUT_REFERENCE_ADMISSION_MISSING"
    assert called == ["producer"]


def test_by_reference_path_escape_blocks_before_consumer_dispatch(tmp_path: Path) -> None:
    plan = _replace_single_binding(
        _producer_consumer_plan(tmp_path),
        selector_kind="receipt_by_schema",
        accepted_source_schemas=("large.report.v1",),
        materialization_mode="by_reference",
        on_invalid="block",
    )
    outside_path = tmp_path.parent / f"{tmp_path.name}-outside.json"
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        digest, size = _write_json_receipt(outside_path, {"schema": "large.report.v1"})
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(outside_path),
            "accepted_output": {
                "receipts": [
                    {
                        "schema": "large.report.v1",
                        "path": str(outside_path),
                        "sha256": digest,
                        "size_bytes": size,
                        "receipt_kind": "node_receipt",
                    }
                ]
            },
        }

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-by-reference",
        )

    assert result.status == "BLOCKED"
    assert result.verdict == "NODE_INPUT_REFERENCE_PATH_ESCAPE"
    assert called == ["producer"]


def test_by_reference_symlink_escape_blocks_before_consumer_dispatch(tmp_path: Path) -> None:
    plan = _replace_single_binding(
        _producer_consumer_plan(tmp_path),
        selector_kind="receipt_by_schema",
        accepted_source_schemas=("large.report.v1",),
        materialization_mode="by_reference",
        on_invalid="block",
    )
    outside_path = tmp_path.parent / f"{tmp_path.name}-outside-symlink-target.json"
    symlink_path = tmp_path / "receipts" / "producer-link.json"
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        digest, size = _write_json_receipt(outside_path, {"schema": "large.report.v1"})
        symlink_path.parent.mkdir(parents=True, exist_ok=True)
        symlink_path.symlink_to(outside_path)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(symlink_path),
            "accepted_output": {
                "receipts": [
                    {
                        "schema": "large.report.v1",
                        "path": str(symlink_path),
                        "sha256": digest,
                        "size_bytes": size,
                        "receipt_kind": "node_receipt",
                    }
                ]
            },
        }

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-by-reference",
        )

    assert result.status == "BLOCKED"
    assert result.verdict == "NODE_INPUT_REFERENCE_PATH_ESCAPE"
    assert called == ["producer"]


def test_by_reference_wrong_schema_blocks_before_consumer_dispatch(tmp_path: Path) -> None:
    plan = _replace_single_binding(
        _producer_consumer_plan(tmp_path),
        selector_kind="receipt_by_schema",
        accepted_source_schemas=("wanted.report.v1",),
        materialization_mode="by_reference",
        on_invalid="block",
    )
    called: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        called.append(node.node_id)
        receipt_path = tmp_path / "receipts" / "producer.json"
        digest, size = _write_json_receipt(receipt_path, {"schema": "other.report.v1"})
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(receipt_path),
            "accepted_output": {
                "receipts": [
                    {
                        "schema": "other.report.v1",
                        "path": str(receipt_path),
                        "sha256": digest,
                        "size_bytes": size,
                        "receipt_kind": "node_receipt",
                    }
                ]
            },
        }

    with SqliteDagRunStore(tmp_path / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="test-by-reference",
        )

    assert result.status == "BLOCKED"
    assert result.verdict == "NODE_INPUT_SCHEMA_MISMATCH"
    assert called == ["producer"]


def _producer_consumer_plan(tmp_path: Path) -> DagPlan:
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "node-input-manifest-test",
            "run_dir": str(tmp_path / "run"),
            "nodes": [
                _node(tmp_path, "producer"),
                _node(tmp_path, "consumer", depends_on=["producer"]),
            ],
        },
        source_path=tmp_path / "dag.json",
    )


def _replace_single_binding(plan: DagPlan, **updates: Any) -> DagPlan:
    return replace(
        plan,
        context_bindings=(replace(plan.context_bindings[0], **updates),),
    ).with_computed_hash()


def _node(
    tmp_path: Path,
    node_id: str,
    *,
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": depends_on or [],
        "accepted_context_from": depends_on or [],
        "receipt_path": str(tmp_path / "receipts" / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": 1,
    }


def _write_json_receipt(path: Path, payload: dict[str, Any]) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(encoded)
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}", len(encoded)
