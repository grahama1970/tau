from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import MappingProxyType
from typing import get_type_hints

import pytest

from tau_coding.dag_runtime.compiler import compile_project_dag_plan
from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.node_input_manifest import NodeInputManifestResolution
from tau_coding.dag_runtime.transition import DagNodeCompletion, DagRunBlock, DagTransitionBatch
from tau_coding.generic_dag import DagNode, validate_generic_dag_spec
from tau_coding.node_completion_boundary import NodeCompletionBoundaryValidation
from tau_coding.project_dag import ProjectDagContract, ProjectDagNode, validate_dag_contract
from tau_coding.public_dag_contracts import ImmutableJsonDict


def test_project_contract_validation_breaks_source_aliases_and_blocks_mutation(
    tmp_path: Path,
) -> None:
    payload = _project_payload(tmp_path)

    contract = validate_dag_contract(payload)
    payload["goal"]["goal_id"] = "mutated"
    payload["target"]["allowed_paths"].append("unexpected.py")
    payload["nodes"][0]["context"]["labels"].append("mutated")

    assert isinstance(contract.payload, ImmutableJsonDict)
    assert isinstance(contract.nodes, MappingProxyType)
    assert contract.goal["goal_id"] == "goal-immutable"
    assert contract.target["allowed_paths"] == ["src/tau_coding"]
    assert contract.nodes["coder"].context["labels"] == ["initial"]
    with pytest.raises(TypeError):
        contract.goal["goal_id"] = "blocked"  # type: ignore[index]
    with pytest.raises(TypeError):
        contract.nodes["coder"].context["labels"].append("blocked")  # type: ignore[union-attr]
    with pytest.raises(TypeError):
        contract.nodes["new"] = contract.nodes["coder"]  # type: ignore[index]


def test_project_plan_payload_round_trip_is_mutation_isolated(tmp_path: Path) -> None:
    contract_path = tmp_path / "project.dag.json"
    payload = _project_payload(tmp_path)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    plan = compile_project_dag_plan(payload, source_path=contract_path)
    before_hash = plan.plan_sha256
    first = plan.to_payload()
    first["goal_binding"]["goal_id"] = "mutated"
    first["nodes"][0]["static_context"]["node"]["labels"].append("mutated")
    second = plan.to_payload()

    assert before_hash
    assert second["goal_binding"]["goal_id"] == "goal-immutable"
    assert second["nodes"][0]["static_context"]["node"]["labels"] == ["initial"]
    payload_without_hash = {key: value for key, value in second.items() if key != "plan_sha256"}
    assert canonical_sha256(payload_without_hash) == before_hash
    assert plan.with_computed_hash().plan_sha256 == before_hash
    assert json.loads(json.dumps(second)) == second


def test_generic_dag_validation_freezes_command_vector_and_source_aliases(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    command = ["python3", "-c", "print('ok')"]
    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "immutability-generic",
        "run_dir": str(tmp_path / "run"),
        "nodes": [
            {
                "node_id": "one",
                "role": "producer",
                "command": command,
                "receipt_path": str(receipt_path),
            }
        ],
    }

    nodes = validate_generic_dag_spec(spec, source_path=tmp_path / "generic.dag.json")
    command.append("mutated")
    spec["nodes"][0]["role"] = "mutated"

    node = nodes["one"]
    assert node.command == ("python3", "-c", "print('ok')")
    assert node.role == "producer"
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.command = ("blocked",)  # type: ignore[misc]


def test_transition_and_manifest_objects_freeze_nested_json() -> None:
    raw_result = {"accepted_output": {"items": ["kept"]}}
    completion = DagNodeCompletion(
        node_id="node",
        attempt=1,
        status="PASS",
        verdict="PASS",
        retryable=False,
        raw_result=raw_result,
    )
    raw_result["accepted_output"]["items"].append("mutated")

    assert completion.raw_result["accepted_output"]["items"] == ["kept"]
    with pytest.raises(TypeError):
        completion.raw_result["accepted_output"]["items"].append("blocked")  # type: ignore[union-attr]

    block = DagRunBlock("blocked", "message", {"details": {"codes": ["A"]}})
    batch = DagTransitionBatch(events=({"kind": "event", "values": [1]},), block_run=block)
    manifest = NodeInputManifestResolution(
        accepted_inputs=({"schema": "input.v1", "values": [1]},),
        manifest={"schema": "manifest.v1", "bindings": [{"id": "b"}]},
    )
    boundary = NodeCompletionBoundaryValidation(
        ok=True,
        boundary={"schema": "boundary.v1", "sections": [{"name": "checked"}]},
        boundary_sha256="sha256:" + "0" * 64,
        alert_codes=(),
        errors=(),
        required_sections=(),
        non_empty_sections=(),
    )

    with pytest.raises(TypeError):
        batch.events[0]["values"].append(2)  # type: ignore[union-attr]
    with pytest.raises(TypeError):
        batch.block_run.evidence["details"]["codes"].append("B")  # type: ignore[union-attr]
    with pytest.raises(TypeError):
        manifest.manifest["bindings"].append({"id": "c"})  # type: ignore[union-attr]
    with pytest.raises(TypeError):
        boundary.boundary["sections"].append({"name": "mutated"})  # type: ignore[union-attr]


def test_authority_dataclass_annotations_do_not_expose_mutable_collections() -> None:
    mutable_tokens = ("dict[", "list[", "set[")
    authority_classes = (
        ProjectDagNode,
        ProjectDagContract,
        DagNode,
        DagNodeCompletion,
        DagRunBlock,
        DagTransitionBatch,
        NodeInputManifestResolution,
        NodeCompletionBoundaryValidation,
    )

    offenders: list[str] = []
    for cls in authority_classes:
        hints = get_type_hints(cls)
        for field in dataclasses.fields(cls):
            rendered = str(hints.get(field.name, field.type))
            if any(token in rendered for token in mutable_tokens):
                offenders.append(f"{cls.__name__}.{field.name}:{rendered}")

    assert offenders == []


def _project_payload(tmp_path: Path) -> dict[str, object]:
    command_spec_dir = tmp_path / "specs" / "coder"
    command_spec_dir.mkdir(parents=True)
    (command_spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps({"schema": "tau.agent_handoff_command.v1", "command": ["true"]}),
        encoding="utf-8",
    )
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": "immutability-project",
        "goal": {
            "goal_id": "goal-immutable",
            "goal_version": 1,
            "goal_hash": "sha256:goal",
            "summary": "keep validated contracts immutable",
            "completion_criteria": ["source mutation cannot alter contract"],
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "issue-297",
            "allowed_paths": ["src/tau_coding"],
        },
        "entry_node": "coder",
        "terminal_nodes": ["done"],
        "limits": {"max_total_attempts": 1, "default_timeout_seconds": 60},
        "context": {"run": {"labels": ["contract"]}},
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(command_spec_dir),
                "required_evidence": ["creator_artifact"],
                "context": {"labels": ["initial"]},
            }
        ],
        "edges": [{"from": "coder", "to": "done"}],
        "required_evidence": [],
        "fail_closed_on": [],
    }
