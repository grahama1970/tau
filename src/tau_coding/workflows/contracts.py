"""Public contracts for Tau's packaged workflow catalog."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKFLOW_DEFINITION_SCHEMA = "tau.workflow_definition.v1"
WORKFLOW_CATALOG_SCHEMA = "tau.workflow_catalog.v1"
WORKFLOW_RUN_RECEIPT_SCHEMA = "tau.workflow_run_receipt.v1"
WORKFLOW_METADATA_SCHEMA = "tau.workflow_metadata.v1"

_WORKFLOW_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    workflow_id: str
    workflow_version: int
    title: str
    summary: str
    topology: str
    availability: str
    input_schema: str
    result_schema: str
    result_node_id: str
    template: str
    runtime: dict[str, bool]

    def validate(self, *, package_root: Path) -> None:
        if _WORKFLOW_ID.fullmatch(self.workflow_id) is None:
            raise RuntimeError("workflow definition workflow_id is invalid")
        if type(self.workflow_version) is not int or self.workflow_version < 1:
            raise RuntimeError("workflow definition workflow_version must be positive")
        if self.topology not in {"LINEAR", "MULTI_STEP_SEQUENTIAL"}:
            raise RuntimeError("workflow definition topology is unsupported")
        if self.availability != "AVAILABLE":
            raise RuntimeError("workflow definition availability must be AVAILABLE")
        template = Path(self.template)
        if template.is_absolute() or ".." in template.parts:
            raise RuntimeError("workflow definition template must be package-relative")
        resolved_template = (package_root / template).resolve()
        if package_root.resolve() not in resolved_template.parents:
            raise RuntimeError("workflow definition template escapes workflow package")
        if not resolved_template.is_file():
            raise RuntimeError(f"workflow definition template not found: {self.template}")
        required_runtime = {
            "local": True,
            "network_required": False,
            "provider_required": False,
            "mutation_allowed": False,
        }
        if self.runtime != required_runtime:
            raise RuntimeError("workflow definition runtime policy is invalid")

    def public_payload(self) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_DEFINITION_SCHEMA,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "title": self.title,
            "summary": self.summary,
            "topology": self.topology,
            "availability": self.availability,
            "input_schema": self.input_schema,
            "result_schema": self.result_schema,
            "result_node_id": self.result_node_id,
            "template": self.template,
            "runtime": dict(self.runtime),
            "proof_boundary": {
                "mocked": False,
                "live": True,
                "provider_live": False,
            },
        }


@dataclass(frozen=True, slots=True)
class RepositoryReadinessRequest:
    repo_path: Path
    human_goal: str
    require_clean: bool


@dataclass(frozen=True, slots=True)
class OperatorReferenceRequest:
    repo_path: Path
    required_workflow: str
