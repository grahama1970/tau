"""Read the immutable packaged workflow catalog."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from tau_coding.workflows.contracts import (
    DAG_LADDER_MANIFEST_SCHEMA,
    WORKFLOW_CATALOG_SCHEMA,
    WORKFLOW_DEFINITION_SCHEMA,
    WorkflowDefinition,
)


def list_workflows() -> tuple[WorkflowDefinition, ...]:
    package_root = resources.files("tau_coding.workflows")
    definitions_root = package_root.joinpath("definitions")
    definitions: list[WorkflowDefinition] = []
    for resource in sorted(definitions_root.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".json"):
            continue
        payload = json.loads(resource.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("schema") == DAG_LADDER_MANIFEST_SCHEMA:
            continue
        if not isinstance(payload, dict) or payload.get("schema") != WORKFLOW_DEFINITION_SCHEMA:
            raise RuntimeError(f"invalid packaged workflow definition: {resource.name}")
        definition = _definition_from_payload(payload)
        definition.validate(package_root=Path(str(package_root)))
        definitions.append(definition)
    return tuple(sorted(definitions, key=lambda item: (item.rung, item.workflow_id)))


def get_workflow(workflow_id: str) -> WorkflowDefinition:
    for definition in list_workflows():
        if definition.workflow_id == workflow_id:
            return definition
    raise RuntimeError(f"unknown workflow_id: {workflow_id}")


def workflow_catalog_payload() -> dict[str, object]:
    return {
        "schema": WORKFLOW_CATALOG_SCHEMA,
        "workflows": [definition.public_payload() for definition in list_workflows()],
    }


def dag_ladder_manifest_payload() -> dict[str, object]:
    package_root = resources.files("tau_coding.workflows")
    resource = package_root.joinpath("definitions", "ladder-manifest.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != DAG_LADDER_MANIFEST_SCHEMA:
        raise RuntimeError("invalid packaged DAG ladder manifest")
    _validate_ladder_manifest(payload)
    return payload


def _definition_from_payload(payload: dict[str, Any]) -> WorkflowDefinition:
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict) or not all(
        isinstance(key, str) and type(value) is bool for key, value in runtime.items()
    ):
        raise RuntimeError("workflow definition runtime must be a boolean object")
    fields = (
        "workflow_id",
        "title",
        "summary",
        "topology",
        "availability",
        "input_schema",
        "result_schema",
        "result_node_id",
        "template",
    )
    if not all(isinstance(payload.get(field), str) and payload[field] for field in fields):
        raise RuntimeError("workflow definition contains an invalid string field")
    workflow_version = payload.get("workflow_version")
    if type(workflow_version) is not int:
        raise RuntimeError("workflow definition workflow_version must be an integer")
    rung = payload.get("rung")
    if type(rung) is not int:
        raise RuntimeError("workflow definition rung must be an integer")
    return WorkflowDefinition(
        workflow_id=str(payload["workflow_id"]),
        workflow_version=workflow_version,
        rung=rung,
        title=str(payload["title"]),
        summary=str(payload["summary"]),
        topology=str(payload["topology"]),
        availability=str(payload["availability"]),
        input_schema=str(payload["input_schema"]),
        result_schema=str(payload["result_schema"]),
        result_node_id=str(payload["result_node_id"]),
        template=str(payload["template"]),
        runtime={str(key): value for key, value in runtime.items()},
    )


def _validate_ladder_manifest(payload: dict[str, Any]) -> None:
    rungs = payload.get("rungs")
    if not isinstance(rungs, list) or len(rungs) != 5:
        raise RuntimeError("DAG ladder manifest must name exactly five rungs")
    definitions = {definition.workflow_id: definition for definition in list_workflows()}
    expected_ids = [
        definition.workflow_id
        for definition in sorted(definitions.values(), key=lambda item: item.rung)
    ]
    observed_ids: list[str] = []
    observed_topologies: list[str] = []
    for expected_rung, rung in enumerate(rungs, start=1):
        if not isinstance(rung, dict):
            raise RuntimeError("DAG ladder rung must be an object")
        workflow_id = rung.get("workflow_id")
        topology = rung.get("topology_class")
        if not isinstance(workflow_id, str) or workflow_id not in definitions:
            raise RuntimeError("DAG ladder rung workflow_id is invalid")
        definition = definitions[workflow_id]
        if rung.get("rung") != expected_rung or definition.rung != expected_rung:
            raise RuntimeError("DAG ladder rung order is invalid")
        if topology != definition.topology:
            raise RuntimeError("DAG ladder rung topology does not match definition")
        boundary = rung.get("acceptance_boundary")
        if not isinstance(boundary, dict) or not boundary:
            raise RuntimeError("DAG ladder rung acceptance_boundary is required")
        if not isinstance(rung.get("next_proof_required"), (str, type(None))):
            raise RuntimeError("DAG ladder rung next_proof_required is invalid")
        observed_ids.append(workflow_id)
        observed_topologies.append(str(topology))
    if observed_ids != expected_ids:
        raise RuntimeError("DAG ladder manifest order does not match packaged workflows")
    if payload.get("topology_progression") != observed_topologies:
        raise RuntimeError("DAG ladder topology progression is invalid")
