"""Read the immutable packaged workflow catalog."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from tau_coding.workflows.contracts import (
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
        if not isinstance(payload, dict) or payload.get("schema") != WORKFLOW_DEFINITION_SCHEMA:
            raise RuntimeError(f"invalid packaged workflow definition: {resource.name}")
        definition = _definition_from_payload(payload)
        definition.validate(package_root=Path(str(package_root)))
        definitions.append(definition)
    return tuple(sorted(definitions, key=lambda item: item.workflow_id))


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
    return WorkflowDefinition(
        workflow_id=str(payload["workflow_id"]),
        workflow_version=workflow_version,
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
