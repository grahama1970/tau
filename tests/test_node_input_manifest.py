from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

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
    expected_hash = canonical_sha256(
        {key: value for key, value in consumer_manifest.items() if key != "canonical_manifest_hash"}
    )
    assert consumer_manifest["canonical_manifest_hash"] == expected_hash


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
