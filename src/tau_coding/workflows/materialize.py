"""Materialize the repository-readiness workflow into a run directory."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.workflows.contracts import WORKFLOW_METADATA_SCHEMA, WorkflowDefinition

REQUEST_SCHEMA = "tau.repository_readiness_request.v1"
COMPLETION_CRITERIA = [
    "Inspect the exact requested Git repository without mutation.",
    "Apply the requested clean-worktree policy.",
    "Publish a repository-readiness report only after validation passes.",
]


@dataclass(frozen=True, slots=True)
class MaterializedWorkflow:
    definition: WorkflowDefinition
    request_path: Path
    source_dag_path: Path
    run_dir: Path
    run_id: str
    goal: dict[str, object]


def materialize_repository_readiness(
    *,
    definition: WorkflowDefinition,
    repo_path: Path,
    human_goal: str,
    require_clean: bool,
    run_dir: Path,
    step_delay_seconds: float = 0.0,
) -> MaterializedWorkflow:
    if definition.workflow_id != "repository-readiness":
        raise RuntimeError("materializer supports only repository-readiness")
    resolved_repo = repo_path.expanduser().resolve()
    if not resolved_repo.is_dir():
        raise RuntimeError(f"repository path is not a directory: {resolved_repo}")
    if not human_goal.strip():
        raise RuntimeError("workflow goal must be a non-empty string")
    if step_delay_seconds < 0:
        raise RuntimeError("step_delay_seconds must be non-negative")
    resolved_run_dir = run_dir.expanduser().resolve()
    if (resolved_run_dir / "dag-run.sqlite3").exists():
        raise RuntimeError(f"workflow run already exists: {resolved_run_dir}")

    request_seed = {
        "schema": REQUEST_SCHEMA,
        "repo_path": str(resolved_repo),
        "human_goal": human_goal.strip(),
        "require_clean": require_clean,
    }
    request_sha = canonical_sha256(request_seed)
    goal_without_hash: dict[str, object] = {
        "goal_id": f"repository-readiness:{request_sha.removeprefix('sha256:')[:12]}",
        "goal_version": 1,
        "summary": human_goal.strip(),
        "completion_criteria": list(COMPLETION_CRITERIA),
    }
    goal = {**goal_without_hash, "goal_hash": canonical_sha256(goal_without_hash)}
    request = {**request_seed, "request_sha256": request_sha, "goal": goal}
    run_id = f"repository-readiness-{request_sha.removeprefix('sha256:')[:12]}"

    for relative in ("workflow", "input", "receipts", "intermediate", "results"):
        (resolved_run_dir / relative).mkdir(parents=True, exist_ok=True)
    request_path = resolved_run_dir / "input" / "repository-readiness-request.json"
    _write_json(request_path, request)

    template_resource = resources.files("tau_coding.workflows").joinpath(definition.template)
    template = json.loads(template_resource.read_text(encoding="utf-8"))
    if not isinstance(template, dict):
        raise RuntimeError("repository-readiness template must be an object")
    workflow_metadata = {
        "schema": WORKFLOW_METADATA_SCHEMA,
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.workflow_version,
        "title": definition.title,
        "summary": "Inspect, validate, and publish repository readiness.",
        "topology": definition.topology,
        "result_node_id": definition.result_node_id,
        "result_schema": definition.result_schema,
    }
    replacements: dict[str, object] = {
        "${RUN_ID}": run_id,
        "${RUN_DIR}": str(resolved_run_dir),
        "${GOAL_OBJECT}": goal,
        "${GOAL_HASH}": goal["goal_hash"],
        "${WORKFLOW_METADATA}": workflow_metadata,
        "${PYTHON}": sys.executable,
        "${REQUEST_PATH}": str(request_path),
        "${INSPECTION_PATH}": str(
            resolved_run_dir / "intermediate" / "repository-inspection.json"
        ),
        "${VALIDATION_PATH}": str(
            resolved_run_dir / "intermediate" / "repository-readiness-validation.json"
        ),
        "${RESULT_JSON}": str(resolved_run_dir / "results" / "repository-readiness.json"),
        "${RESULT_MARKDOWN}": str(
            resolved_run_dir / "results" / "repository-readiness.md"
        ),
        "${INSPECTION_RECEIPT}": str(
            resolved_run_dir / "receipts" / "inspect-repository.json"
        ),
        "${VALIDATION_RECEIPT}": str(
            resolved_run_dir / "receipts" / "validate-readiness.json"
        ),
        "${PUBLISH_RECEIPT}": str(
            resolved_run_dir / "receipts" / "publish-readiness.json"
        ),
        "${STEP_DELAY_SECONDS}": str(step_delay_seconds),
    }
    materialized = _replace_tokens(template, replacements)
    source_dag_path = resolved_run_dir / "workflow" / "dag.json"
    _write_json(source_dag_path, materialized)
    return MaterializedWorkflow(
        definition=definition,
        request_path=request_path,
        source_dag_path=source_dag_path,
        run_dir=resolved_run_dir,
        run_id=run_id,
        goal=goal,
    )


def _replace_tokens(value: Any, replacements: dict[str, object]) -> Any:
    if isinstance(value, str):
        if value in replacements:
            return replacements[value]
        replaced = value
        for token, replacement in replacements.items():
            if token in replaced:
                if not isinstance(replacement, str):
                    raise RuntimeError(f"object token {token} must occupy the full JSON value")
                replaced = replaced.replace(token, replacement)
        return replaced
    if isinstance(value, list):
        return [_replace_tokens(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_tokens(item, replacements) for key, item in value.items()}
    return value


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
