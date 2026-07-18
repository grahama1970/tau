"""Materialize Tau's packaged workflows into run directories."""

from __future__ import annotations

import json
import shutil
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
OPERATOR_REFERENCE_GOAL_WITHOUT_HASH: dict[str, object] = {
    "goal_id": "tau-canonical-workflow-slice-02",
    "goal_version": 1,
    "summary": (
        "Generate a validated Tau operator reference from fixed local repository "
        "sources and versioned public CLI evidence."
    ),
    "completion_criteria": [
        "Expose tau-operator-reference through the packaged workflow catalog.",
        "Execute the four locked operator-reference nodes sequentially at concurrency one.",
        "Read only the fixed Tau source set from the requested local repository.",
        "Capture actual local executable output for the fixed versioned public CLI probes.",
        "Keep composed JSON and Markdown drafts under intermediate.",
        "Publish JSON and Markdown results only after independent validator recomputation.",
        "Block validation with required_workflow_missing when the required workflow is absent.",
        "Carry the full immutable goal hash and accepted_output in every node receipt.",
    ],
}
OPERATOR_SOURCE_PATHS = (
    "pyproject.toml",
    "README.md",
    "docs/getting-started.md",
    "docs/live-dag-viewer.md",
    "docs/generic-dag-runner.md",
)
OPERATOR_CLI_PROBE_MANIFEST: dict[str, object] = {
    "schema": "tau.operator_cli_probe_manifest.v1",
    "manifest_version": 1,
    "probes": [
        {
            "probe_id": "workflow-catalog",
            "argv": ["tau", "workflows", "list", "--json"],
            "output_format": "json",
        },
        {
            "probe_id": "workflow-run-help",
            "argv": ["tau", "workflows", "run", "--help"],
            "output_format": "text",
        },
        {
            "probe_id": "dag-viewer-capabilities",
            "argv": ["tau", "dag-view-capabilities", "--json"],
            "output_format": "json",
        },
    ],
}
EVIDENCE_MAP_COMPLETION_CRITERIA = [
    "Inventory the exact requested Git repository without mutation.",
    "Analyze documentation, tests, and package metadata concurrently.",
    "Publish a repository evidence map only after every required branch is accepted.",
]
APPROVED_RELEASE_COMPLETION_CRITERIA = [
    "Prepare the exact requested Git repository without mutation.",
    "Accept revised release notes, a release manifest, and release policy concurrently.",
    "Stop before publication until the exact accepted bundle is approved by a human.",
    "Publish one hash-bound release bundle with rollback on failed verification.",
]
DURABLE_QUALIFICATION_COMPLETION_CRITERIA = [
    "Capture the exact requested Git repository without mutation.",
    "Qualify documentation, tests, and package metadata concurrently.",
    "Repair only a blocked qualification branch while preserving accepted work.",
    "Publish one idempotent qualification result after exact human approval.",
]
GS001_CLOSURE_REQUEST_SCHEMA = "tau.gs001_closure_audit_request.v1"
GS001_CLOSURE_COMPLETION_CRITERIA = [
    "Validate every supplied PDF Lab artifact against its declared schema without mutation.",
    "Recompute the GS001 closure verdict deterministically from artifacts, never from model prose.",
    "Project one dry-run defect ticket per fingerprinted backlog entry with stable defect_key dedup.",
    "Publish a hash-bound closure report; human review decides acceptance.",
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


def materialize_gs001_closure_audit(
    *,
    definition: WorkflowDefinition,
    comparison_json: Path,
    backlog_json: Path,
    triage_queue_json: Path,
    expected_contract_json: Path,
    goal_md: Path,
    run_dir: Path,
    github_owner: str = "grahama1970",
    github_repo: str = "pdf_oxide",
    step_delay_seconds: float = 0.0,
) -> MaterializedWorkflow:
    if definition.workflow_id != "gs001-closure-audit":
        raise RuntimeError("materializer supports only gs001-closure-audit")
    if step_delay_seconds < 0:
        raise RuntimeError("step_delay_seconds must be non-negative")
    resolved_run_dir = run_dir.expanduser().resolve()
    if (resolved_run_dir / "dag-run.sqlite3").exists():
        raise RuntimeError(f"workflow run already exists: {resolved_run_dir}")

    request_seed = {
        "schema": GS001_CLOSURE_REQUEST_SCHEMA,
        "comparison_json": str(comparison_json.expanduser().resolve()),
        "backlog_json": str(backlog_json.expanduser().resolve()),
        "triage_queue_json": str(triage_queue_json.expanduser().resolve()),
        "expected_contract_json": str(expected_contract_json.expanduser().resolve()),
        "goal_md": str(goal_md.expanduser().resolve()),
        "github": {"owner": github_owner, "repo": github_repo},
    }
    request_sha = canonical_sha256(request_seed)
    goal_without_hash: dict[str, object] = {
        "goal_id": f"gs001-closure-audit:{request_sha.removeprefix('sha256:')[:12]}",
        "goal_version": 1,
        "summary": "Judge GS001 PDF Lab artifacts and publish a closure verdict with dry-run defect tickets.",
        "completion_criteria": list(GS001_CLOSURE_COMPLETION_CRITERIA),
    }
    goal = {**goal_without_hash, "goal_hash": canonical_sha256(goal_without_hash)}
    request = {**request_seed, "request_sha256": request_sha, "goal": goal}
    run_id = f"gs001-closure-audit-{request_sha.removeprefix('sha256:')[:12]}"

    for relative in ("workflow", "input", "receipts", "intermediate", "results"):
        (resolved_run_dir / relative).mkdir(parents=True, exist_ok=True)
    request_path = resolved_run_dir / "input" / "gs001-closure-audit-request.json"
    _write_json(request_path, request)

    template_resource = resources.files("tau_coding.workflows").joinpath(definition.template)
    template = json.loads(template_resource.read_text(encoding="utf-8"))
    if not isinstance(template, dict):
        raise RuntimeError("gs001-closure-audit template must be an object")
    workflow_metadata = {
        "schema": WORKFLOW_METADATA_SCHEMA,
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.workflow_version,
        "title": definition.title,
        "summary": "Validate, judge, project, and publish GS001 closure.",
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
        "${VALIDATION_PATH}": str(
            resolved_run_dir / "intermediate" / "gs001-artifact-validation.json"
        ),
        "${VERDICT_PATH}": str(
            resolved_run_dir / "intermediate" / "gs001-closure-verdict.json"
        ),
        "${PROJECTION_PATH}": str(
            resolved_run_dir / "intermediate" / "gs001-ticket-projection.json"
        ),
        "${RESULT_JSON}": str(resolved_run_dir / "results" / "gs001-closure-report.json"),
        "${RESULT_MARKDOWN}": str(resolved_run_dir / "results" / "gs001-closure-report.md"),
        "${VALIDATION_RECEIPT}": str(
            resolved_run_dir / "receipts" / "validate-artifacts.json"
        ),
        "${VERDICT_RECEIPT}": str(resolved_run_dir / "receipts" / "verdict-closure.json"),
        "${PROJECTION_RECEIPT}": str(
            resolved_run_dir / "receipts" / "project-tickets.json"
        ),
        "${PUBLISH_RECEIPT}": str(resolved_run_dir / "receipts" / "publish-closure.json"),
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


def materialize_tau_operator_reference(
    *,
    definition: WorkflowDefinition,
    repo_path: Path,
    required_workflow: str,
    run_dir: Path,
    step_delay_seconds: float = 0.0,
) -> MaterializedWorkflow:
    if definition.workflow_id != "tau-operator-reference":
        raise RuntimeError("materializer supports only tau-operator-reference")
    resolved_repo = repo_path.expanduser().resolve()
    if not resolved_repo.is_dir():
        raise RuntimeError(f"repository path is not a directory: {resolved_repo}")
    missing_sources = [
        path for path in OPERATOR_SOURCE_PATHS if not (resolved_repo / path).is_file()
    ]
    if missing_sources:
        raise RuntimeError(f"operator reference source is missing: {missing_sources[0]}")
    if not required_workflow.strip():
        raise RuntimeError("required_workflow must be a non-empty string")
    if step_delay_seconds < 0:
        raise RuntimeError("step_delay_seconds must be non-negative")
    tau_executable = shutil.which("tau")
    if tau_executable is None:
        raise RuntimeError("local tau executable is unavailable")
    resolved_run_dir = run_dir.expanduser().resolve()
    if (resolved_run_dir / "dag-run.sqlite3").exists():
        raise RuntimeError(f"workflow run already exists: {resolved_run_dir}")

    goal = {
        **OPERATOR_REFERENCE_GOAL_WITHOUT_HASH,
        "goal_hash": canonical_sha256(OPERATOR_REFERENCE_GOAL_WITHOUT_HASH),
    }
    request_seed = {
        "schema": "tau.operator_reference_request.v1",
        "repo_path": str(resolved_repo),
        "required_workflow": required_workflow.strip(),
        "source_paths": list(OPERATOR_SOURCE_PATHS),
        "cli_probe_manifest": OPERATOR_CLI_PROBE_MANIFEST,
        "tau_executable": str(Path(tau_executable).resolve()),
        "goal": goal,
    }
    request_sha = canonical_sha256(request_seed)
    request = {**request_seed, "request_sha256": request_sha}
    run_id = f"tau-operator-reference-{request_sha.removeprefix('sha256:')[:12]}"

    for relative in ("workflow", "input", "receipts", "intermediate"):
        (resolved_run_dir / relative).mkdir(parents=True, exist_ok=True)
    request_path = resolved_run_dir / "input" / "tau-operator-reference-request.json"
    _write_json(request_path, request)

    template_resource = resources.files("tau_coding.workflows").joinpath(definition.template)
    template = json.loads(template_resource.read_text(encoding="utf-8"))
    if not isinstance(template, dict):
        raise RuntimeError("tau-operator-reference template must be an object")
    workflow_metadata = {
        "schema": WORKFLOW_METADATA_SCHEMA,
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.workflow_version,
        "title": definition.title,
        "summary": definition.summary,
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
        "${SOURCES_PATH}": str(resolved_run_dir / "intermediate" / "operator-sources.json"),
        "${PROBES_PATH}": str(resolved_run_dir / "intermediate" / "operator-cli.json"),
        "${DRAFT_JSON}": str(
            resolved_run_dir / "intermediate" / "tau-operator-reference.draft.json"
        ),
        "${DRAFT_MARKDOWN}": str(
            resolved_run_dir / "intermediate" / "tau-operator-reference.draft.md"
        ),
        "${RESULT_JSON}": str(resolved_run_dir / "results" / "tau-operator-reference.json"),
        "${RESULT_MARKDOWN}": str(
            resolved_run_dir / "results" / "tau-operator-reference.md"
        ),
        "${COLLECT_RECEIPT}": str(
            resolved_run_dir / "receipts" / "collect-operator-sources.json"
        ),
        "${CAPTURE_RECEIPT}": str(
            resolved_run_dir / "receipts" / "capture-operator-cli.json"
        ),
        "${COMPOSE_RECEIPT}": str(
            resolved_run_dir / "receipts" / "compose-operator-reference.json"
        ),
        "${VALIDATE_RECEIPT}": str(
            resolved_run_dir / "receipts" / "validate-operator-reference.json"
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


def materialize_repository_evidence_map(
    *,
    definition: WorkflowDefinition,
    repo_path: Path,
    human_goal: str,
    require_tests: bool,
    run_dir: Path,
    step_delay_seconds: float = 0.0,
) -> MaterializedWorkflow:
    if definition.workflow_id != "repository-evidence-map":
        raise RuntimeError("materializer supports only repository-evidence-map")
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
        "schema": "tau.repository_evidence_map_request.v1",
        "repo_path": str(resolved_repo),
        "human_goal": human_goal.strip(),
        "require_tests": require_tests,
    }
    request_sha = canonical_sha256(request_seed)
    goal_without_hash: dict[str, object] = {
        "goal_id": f"repository-evidence-map:{request_sha.removeprefix('sha256:')[:12]}",
        "goal_version": 1,
        "summary": human_goal.strip(),
        "completion_criteria": list(EVIDENCE_MAP_COMPLETION_CRITERIA),
    }
    goal = {**goal_without_hash, "goal_hash": canonical_sha256(goal_without_hash)}
    request = {**request_seed, "request_sha256": request_sha, "goal": goal}
    run_id = f"repository-evidence-map-{request_sha.removeprefix('sha256:')[:12]}"
    for relative in ("workflow", "input", "receipts", "intermediate"):
        (resolved_run_dir / relative).mkdir(parents=True, exist_ok=True)
    request_path = resolved_run_dir / "input" / "repository-evidence-map-request.json"
    _write_json(request_path, request)

    template_resource = resources.files("tau_coding.workflows").joinpath(definition.template)
    template = json.loads(template_resource.read_text(encoding="utf-8"))
    if not isinstance(template, dict):
        raise RuntimeError("repository-evidence-map template must be an object")
    workflow_metadata = {
        "schema": WORKFLOW_METADATA_SCHEMA,
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.workflow_version,
        "title": definition.title,
        "summary": definition.summary,
        "topology": definition.topology,
        "result_node_id": definition.result_node_id,
        "result_schema": definition.result_schema,
    }
    intermediate = resolved_run_dir / "intermediate"
    receipts = resolved_run_dir / "receipts"
    replacements: dict[str, object] = {
        "${RUN_ID}": run_id,
        "${RUN_DIR}": str(resolved_run_dir),
        "${GOAL_OBJECT}": goal,
        "${GOAL_HASH}": goal["goal_hash"],
        "${WORKFLOW_METADATA}": workflow_metadata,
        "${PYTHON}": sys.executable,
        "${REQUEST_PATH}": str(request_path),
        "${INVENTORY_PATH}": str(intermediate / "repository-inventory.json"),
        "${DOCUMENTATION_PATH}": str(intermediate / "documentation-analysis.json"),
        "${TESTS_PATH}": str(intermediate / "test-analysis.json"),
        "${PACKAGE_PATH}": str(intermediate / "package-analysis.json"),
        "${RESULT_JSON}": str(resolved_run_dir / "results" / "repository-evidence-map.json"),
        "${RESULT_MARKDOWN}": str(resolved_run_dir / "results" / "repository-evidence-map.md"),
        "${INVENTORY_RECEIPT}": str(receipts / "inventory-repository.json"),
        "${DOCUMENTATION_RECEIPT}": str(receipts / "analyze-documentation.json"),
        "${TESTS_RECEIPT}": str(receipts / "analyze-tests.json"),
        "${PACKAGE_RECEIPT}": str(receipts / "analyze-package.json"),
        "${PUBLISH_RECEIPT}": str(receipts / "publish-evidence-map.json"),
        "${STEP_DELAY_SECONDS}": str(step_delay_seconds),
    }
    source_dag_path = resolved_run_dir / "workflow" / "dag.json"
    _write_json(source_dag_path, _replace_tokens(template, replacements))
    return MaterializedWorkflow(
        definition=definition,
        request_path=request_path,
        source_dag_path=source_dag_path,
        run_dir=resolved_run_dir,
        run_id=run_id,
        goal=goal,
    )


def materialize_approved_release_bundle(
    *,
    definition: WorkflowDefinition,
    repo_path: Path,
    human_goal: str,
    publish_path: Path,
    run_dir: Path,
    force_terminal_failure: bool = False,
    simulate_publish_verification_failure: bool = False,
    step_delay_seconds: float = 0.0,
) -> MaterializedWorkflow:
    if definition.workflow_id != "approved-release-bundle":
        raise RuntimeError("materializer supports only approved-release-bundle")
    resolved_repo = repo_path.expanduser().resolve()
    if not resolved_repo.is_dir():
        raise RuntimeError(f"repository path is not a directory: {resolved_repo}")
    if not human_goal.strip():
        raise RuntimeError("workflow goal must be a non-empty string")
    if step_delay_seconds < 0:
        raise RuntimeError("step_delay_seconds must be non-negative")
    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_publish_path = publish_path.expanduser().resolve()
    if resolved_publish_path == resolved_repo or resolved_repo in resolved_publish_path.parents:
        raise RuntimeError("publish path must be outside the inspected repository")
    if resolved_publish_path.exists():
        raise RuntimeError(f"publish path already exists: {resolved_publish_path}")
    if (resolved_run_dir / "dag-run.sqlite3").exists():
        raise RuntimeError(f"workflow run already exists: {resolved_run_dir}")

    request_seed = {
        "schema": "tau.approved_release_request.v1",
        "repo_path": str(resolved_repo),
        "human_goal": human_goal.strip(),
        "publish_path": str(resolved_publish_path),
        "force_terminal_failure": force_terminal_failure,
        "simulate_publish_verification_failure": simulate_publish_verification_failure,
        "step_delay_seconds": step_delay_seconds,
    }
    request_sha = canonical_sha256(request_seed)
    goal_without_hash: dict[str, object] = {
        "goal_id": f"approved-release-bundle:{request_sha.removeprefix('sha256:')[:12]}",
        "goal_version": 1,
        "summary": human_goal.strip(),
        "completion_criteria": list(APPROVED_RELEASE_COMPLETION_CRITERIA),
    }
    goal = {**goal_without_hash, "goal_hash": canonical_sha256(goal_without_hash)}
    request = {**request_seed, "request_sha256": request_sha, "goal": goal}
    run_id = f"approved-release-bundle-{request_sha.removeprefix('sha256:')[:12]}"
    for relative in ("workflow", "input", "receipts", "intermediate"):
        (resolved_run_dir / relative).mkdir(parents=True, exist_ok=True)
    request_path = resolved_run_dir / "input" / "approved-release-request.json"
    _write_json(request_path, request)
    notes_work_order = resolved_run_dir / "input" / "release-notes-work-order.json"
    publish_work_order = resolved_run_dir / "input" / "release-publication-work-order.json"
    transaction_artifacts = resolved_run_dir / "transaction-artifacts"
    _write_json(
        notes_work_order,
        {
            "schema": "tau.release_notes_work_order.v1",
            "goal": goal,
            "artifact_root": str(transaction_artifacts / "release-notes"),
        },
    )
    _write_json(
        publish_work_order,
        {
            "schema": "tau.release_publication_work_order.v1",
            "publish_path": str(resolved_publish_path),
            "artifact_root": str(transaction_artifacts / "release-publication"),
            "goal": goal,
        },
    )

    template_resource = resources.files("tau_coding.workflows").joinpath(definition.template)
    template = json.loads(template_resource.read_text(encoding="utf-8"))
    if not isinstance(template, dict):
        raise RuntimeError("approved-release-bundle template must be an object")
    workflow_metadata = {
        "schema": WORKFLOW_METADATA_SCHEMA,
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.workflow_version,
        "title": definition.title,
        "summary": definition.summary,
        "topology": definition.topology,
        "result_node_id": definition.result_node_id,
        "result_schema": definition.result_schema,
    }
    intermediate = resolved_run_dir / "intermediate"
    receipts = resolved_run_dir / "receipts"
    transactions = transaction_artifacts
    replacements: dict[str, object] = {
        "${RUN_ID}": run_id,
        "${RUN_DIR}": str(resolved_run_dir),
        "${GOAL_OBJECT}": goal,
        "${GOAL_HASH}": goal["goal_hash"],
        "${WORKFLOW_METADATA}": workflow_metadata,
        "${PYTHON}": sys.executable,
        "${REQUEST_PATH}": str(request_path),
        "${PREPARE_PATH}": str(intermediate / "release-preparation.json"),
        "${MANIFEST_PATH}": str(intermediate / "release-manifest.json"),
        "${POLICY_PATH}": str(intermediate / "release-policy.json"),
        "${ASSEMBLED_PATH}": str(intermediate / "assembled-release-bundle.json"),
        "${PREPARE_RECEIPT}": str(receipts / "prepare-release.json"),
        "${NOTES_RECEIPT}": str(receipts / "draft-release-notes.json"),
        "${MANIFEST_RECEIPT}": str(receipts / "build-release-manifest.json"),
        "${POLICY_RECEIPT}": str(receipts / "verify-release-policy.json"),
        "${ASSEMBLE_RECEIPT}": str(receipts / "assemble-release-bundle.json"),
        "${PUBLISH_RECEIPT}": str(receipts / "publish-approved-release.json"),
        "${FINALIZE_RECEIPT}": str(receipts / "finalize-approved-release.json"),
        "${NOTES_WORK_ORDER}": str(notes_work_order),
        "${PUBLISH_WORK_ORDER}": str(publish_work_order),
        "${NOTES_ARTIFACT_ROOT}": str(transactions / "release-notes"),
        "${PUBLISH_ARTIFACT_ROOT}": str(transactions / "release-publication"),
        "${APPROVAL_PACKET}": str(resolved_run_dir / "input" / "approval.json"),
        "${RESULT_JSON}": str(resolved_run_dir / "results" / "approved-release-bundle.json"),
        "${RESULT_MARKDOWN}": str(resolved_run_dir / "results" / "approved-release-bundle.md"),
        "${ROLLBACK_RECEIPT}": str(receipts / "publication-rollback.json"),
        "${STEP_DELAY_SECONDS}": str(step_delay_seconds),
    }
    source_dag_path = resolved_run_dir / "workflow" / "dag.json"
    _write_json(source_dag_path, _replace_tokens(template, replacements))
    return MaterializedWorkflow(
        definition=definition,
        request_path=request_path,
        source_dag_path=source_dag_path,
        run_dir=resolved_run_dir,
        run_id=run_id,
        goal=goal,
    )


def materialize_durable_repository_qualification(
    *,
    definition: WorkflowDefinition,
    repo_path: Path,
    human_goal: str,
    publish_path: Path,
    run_dir: Path,
    inject_test_branch_failure: bool = False,
    step_delay_seconds: float = 0.0,
) -> MaterializedWorkflow:
    if definition.workflow_id != "durable-repository-qualification":
        raise RuntimeError("materializer supports only durable-repository-qualification")
    resolved_repo = repo_path.expanduser().resolve()
    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_publish_path = publish_path.expanduser().resolve()
    if not resolved_repo.is_dir():
        raise RuntimeError(f"repository path is not a directory: {resolved_repo}")
    if not human_goal.strip():
        raise RuntimeError("workflow goal must be a non-empty string")
    if step_delay_seconds < 0:
        raise RuntimeError("step_delay_seconds must be non-negative")
    if resolved_publish_path == resolved_repo or resolved_repo in resolved_publish_path.parents:
        raise RuntimeError("publish path must be outside the inspected repository")
    if resolved_publish_path.exists():
        raise RuntimeError(f"publish path already exists: {resolved_publish_path}")
    if (resolved_run_dir / "dag-run.sqlite3").exists():
        raise RuntimeError(f"workflow run already exists: {resolved_run_dir}")

    request_seed = {
        "schema": "tau.durable_repository_qualification_request.v1",
        "repo_path": str(resolved_repo),
        "human_goal": human_goal.strip(),
        "publish_path": str(resolved_publish_path),
        "inject_test_branch_failure": inject_test_branch_failure,
        "step_delay_seconds": step_delay_seconds,
    }
    request_sha = canonical_sha256(request_seed)
    goal_without_hash: dict[str, object] = {
        "goal_id": (
            "durable-repository-qualification:"
            f"{request_sha.removeprefix('sha256:')[:12]}"
        ),
        "goal_version": 1,
        "summary": human_goal.strip(),
        "completion_criteria": list(DURABLE_QUALIFICATION_COMPLETION_CRITERIA),
    }
    goal = {**goal_without_hash, "goal_hash": canonical_sha256(goal_without_hash)}
    request = {**request_seed, "request_sha256": request_sha, "goal": goal}
    run_id = f"durable-repository-qualification-{request_sha.removeprefix('sha256:')[:12]}"
    for relative in ("workflow", "input", "receipts", "intermediate"):
        (resolved_run_dir / relative).mkdir(parents=True, exist_ok=True)
    request_path = resolved_run_dir / "input" / "durable-qualification-request.json"
    _write_json(request_path, request)
    artifact_root = resolved_run_dir / "transaction-artifacts" / "qualification"
    work_order = resolved_run_dir / "input" / "qualification-publication-work-order.json"
    _write_json(
        work_order,
        {
            "schema": "tau.qualification_publication_work_order.v1",
            "artifact_root": str(artifact_root),
            "publish_path": str(resolved_publish_path),
            "goal": goal,
        },
    )

    template_resource = resources.files("tau_coding.workflows").joinpath(definition.template)
    template = json.loads(template_resource.read_text(encoding="utf-8"))
    if not isinstance(template, dict):
        raise RuntimeError("durable repository qualification template must be an object")
    workflow_metadata = {
        "schema": WORKFLOW_METADATA_SCHEMA,
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.workflow_version,
        "title": definition.title,
        "summary": definition.summary,
        "topology": definition.topology,
        "result_node_id": definition.result_node_id,
        "result_schema": definition.result_schema,
    }
    intermediate = resolved_run_dir / "intermediate"
    receipts = resolved_run_dir / "receipts"
    replacements: dict[str, object] = {
        "${RUN_ID}": run_id,
        "${RUN_DIR}": str(resolved_run_dir),
        "${GOAL_OBJECT}": goal,
        "${GOAL_HASH}": goal["goal_hash"],
        "${WORKFLOW_METADATA}": workflow_metadata,
        "${PYTHON}": sys.executable,
        "${REQUEST_PATH}": str(request_path),
        "${CAPTURE_PATH}": str(intermediate / "repository-capture.json"),
        "${DOCUMENTATION_PATH}": str(intermediate / "documentation-qualification.json"),
        "${TESTS_PATH}": str(intermediate / "test-qualification.json"),
        "${PACKAGE_PATH}": str(intermediate / "package-qualification.json"),
        "${RECONCILE_PATH}": str(intermediate / "repository-qualification.json"),
        "${CAPTURE_RECEIPT}": str(receipts / "capture-repository.json"),
        "${DOCUMENTATION_RECEIPT}": str(receipts / "qualify-documentation.json"),
        "${TESTS_RECEIPT}": str(receipts / "qualify-tests.json"),
        "${PACKAGE_RECEIPT}": str(receipts / "qualify-package.json"),
        "${RECONCILE_RECEIPT}": str(receipts / "reconcile-qualification.json"),
        "${PUBLISH_RECEIPT}": str(receipts / "publish-qualification.json"),
        "${FINALIZE_RECEIPT}": str(receipts / "finalize-qualification.json"),
        "${REPAIR_PACKET}": str(resolved_run_dir / "input" / "repair-qualify-tests.json"),
        "${APPROVAL_PACKET}": str(resolved_run_dir / "input" / "approval.json"),
        "${WORK_ORDER}": str(work_order),
        "${ARTIFACT_ROOT}": str(artifact_root),
        "${RESULT_JSON}": str(
            resolved_run_dir / "results" / "durable-repository-qualification.json"
        ),
        "${RESULT_MARKDOWN}": str(
            resolved_run_dir / "results" / "durable-repository-qualification.md"
        ),
        "${PUBLICATION_LEDGER}": str(receipts / "qualification-publication-ledger.json"),
        "${STEP_DELAY_SECONDS}": str(step_delay_seconds),
    }
    source_dag_path = resolved_run_dir / "workflow" / "dag.json"
    _write_json(source_dag_path, _replace_tokens(template, replacements))
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
