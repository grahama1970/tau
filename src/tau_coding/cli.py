"""Command-line entry point for Tau."""

import asyncio
import fnmatch
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout, suppress
from dataclasses import replace
from datetime import UTC, datetime
from os import environ
from pathlib import Path
from shutil import which
from typing import Annotated, Any

import anyio
import httpx
import typer

from tau_agent import AssistantMessage, ToolResultMessage, UserMessage
from tau_agent.session import (
    JsonlSessionStorage,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionEntry,
    SessionInfoEntry,
    SessionState,
    SessionStorage,
)
from tau_ai import (
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
    FakeProvider,
    ModelProvider,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
)
from tau_ai.env import DEFAULT_OPENAI_COMPATIBLE_BASE_URL
from tau_coding import __version__
from tau_coding.airgap_no_egress import write_airgap_no_egress_receipt
from tau_coding.approval_gate import evaluate_approval_gate
from tau_coding.browser_cdp_proof import (
    DEFAULT_BROWSER_PROOF_RUN_ID,
    DEFAULT_SURF_WRAPPER,
    write_browser_cdp_proof,
)
from tau_coding.code_patch import apply_code_patch_receipt
from tau_coding.code_runner_skill_adapter import write_code_runner_skill_adapter_receipt
from tau_coding.coding_worker_adapters import (
    write_omp_worker_doctor_receipt,
    write_omp_worker_launch_receipt,
    write_omp_worker_receipt,
    write_scillm_worker_launch_receipt,
    write_scillm_worker_receipt,
)
from tau_coding.commit_plan import write_commit_plan_receipt
from tau_coding.compliance_package import build_compliance_evidence_package
from tau_coding.course_correction import write_course_correction_receipt
from tau_coding.credentials import FileCredentialStore
from tau_coding.dag_branch_locks import write_dag_branch_lock_validation_receipt
from tau_coding.dag_expansion import (
    write_dag_expansion_apply_receipt,
    write_dag_expansion_policy_receipt,
    write_dag_expansion_validation_receipt,
)
from tau_coding.dag_motif import write_dag_motif_validation_receipt
from tau_coding.dag_route_memory import (
    write_dag_route_memory_candidate_receipt,
    write_dag_route_memory_sync_receipt,
)
from tau_coding.dag_runtime import write_dag_plan
from tau_coding.dag_runtime.retention import expire_dag_run_directories
from tau_coding.dag_runtime.run_store import DagRunStoreError, SqliteDagRunStore
from tau_coding.dag_signals import write_dag_signal_receipt
from tau_coding.dag_stress_poc import (
    inspect_dag_stress_campaign,
    inspect_dag_stress_run,
    run_dag_stress_campaign,
    run_dag_stress_poc,
)
from tau_coding.dag_template_registry import (
    dag_template_catalog_payload,
    dag_template_registry_payload,
    describe_dag_template,
    preview_dag_template,
    select_dag_template_from_facts,
    validate_dag_template_params,
    write_dag_template_compile_receipt,
)
from tau_coding.dag_viewer.contracts import viewer_capabilities
from tau_coding.dag_viewer.projection import (
    build_dag_live_events,
    build_dag_live_snapshot,
    load_dag_replay,
)
from tau_coding.dag_viewer.receipt_index import build_receipt_index
from tau_coding.dag_viewer.server import create_dag_viewer_server
from tau_coding.debug_session_receipt import write_debug_session_receipt
from tau_coding.debugger_skill_adapter import write_debugger_skill_adapter_receipt
from tau_coding.demo_airgap_itar import run_demo_airgap_itar_basic
from tau_coding.diagnostics import configure_tau_logging
from tau_coding.docker_sandbox import write_docker_sandbox_receipt
from tau_coding.embry_sparta_demo import run_demo_embry_sparta_airgap
from tau_coding.evidence_case_skill_adapter import write_evidence_case_skill_adapter_receipt
from tau_coding.evidence_manifest import write_evidence_validation_receipt
from tau_coding.generated_ticket import (
    load_generated_ticket,
    project_agent_handoff,
    validate_generated_ticket,
    write_agent_handoff_chain_receipt,
    write_agent_handoff_loop_receipt,
    write_agent_handoff_projection_receipt,
)
from tau_coding.generic_dag import (
    inspect_generic_dag_run,
    resume_generic_dag_from_run,
    run_generic_dag,
)
from tau_coding.generic_provider_adapter import run_generic_provider_dag_node
from tau_coding.github_apply_policy import write_github_apply_policy_receipt
from tau_coding.github_handoff import (
    fetch_goal_guardian_ticket_source_from_github,
    redact_github_projection,
    transport_command_loop_terminal_to_github,
    transport_generated_ticket_to_github,
    transport_goal_guardian_reconciliation_to_github,
    transport_handoff_projection_to_github,
)
from tau_coding.github_read_schemes import write_github_read_receipt
from tau_coding.goal_run import run_goal_until_complete
from tau_coding.gs001_closure_publisher import publish_gs001_closure_receipt
from tau_coding.handoff_dispatch import (
    TAU_AGENT_HANDOFF_DISPATCH_RECEIPT_SCHEMA,
    load_agent_dispatch_command_spec,
    validate_command_dispatch_spec,
    write_agent_handoff_command_dispatch_receipt,
    write_agent_handoff_command_loop_receipt,
    write_agent_handoff_dispatch_receipt,
)
from tau_coding.herdr_cleanup import run_herdr_cleanup, run_herdr_gc
from tau_coding.herdr_observation_gate import write_herdr_observation_gate_receipt
from tau_coding.human_goal_change import write_human_goal_change_bridge_receipt
from tau_coding.init_project import initialize_tau_project
from tau_coding.itar_boundary import write_itar_access_preflight_receipt
from tau_coding.itar_contract import write_itar_contract_receipt
from tau_coding.local_provider_readiness import write_local_provider_readiness_receipt
from tau_coding.loop_monitor import (
    check_loop_receipt_monitor_contract,
    create_loop_receipt_monitor_server,
)
from tau_coding.loop_receipt import (
    LoopReceiptConfig,
    backfill_loop_receipt_artifact_index,
    emit_loop_peer_to_switchboard,
    loop_receipt_summary,
)
from tau_coding.loop_sanity import run_loop2_sanity
from tau_coding.loop_validation import (
    validate_loop2_contract_file,
    validate_loop_receipt_with_loop2_contracts,
    validate_native_loop2_run_with_contracts,
)
from tau_coding.lsp_receipts import (
    write_lsp_diagnostics_receipt,
    write_lsp_rename_plan_receipt,
    write_lsp_symbol_receipt,
)
from tau_coding.media_explainer_orchestration import (
    inspect_media_explainer_run,
    run_media_explainer_smoke,
)
from tau_coding.memory_acquisition import (
    DEFAULT_MEMORY_URL,
    write_evidence_case_acquisition_receipt,
    write_memory_intent_acquisition_receipt,
    write_skill_chain_selection_receipt,
    write_tool_chain_selection_receipt,
)
from tau_coding.orchestration_evidence import build_orchestration_evidence
from tau_coding.orchestration_redteam import run_orchestration_redteam
from tau_coding.orchestration_reliability import write_orchestration_reliability_receipt
from tau_coding.package_validate import write_compliance_package_validation_receipt
from tau_coding.paths import TauPaths
from tau_coding.pdf_lab_second_pass_review import write_pdf_lab_second_pass_review_receipt
from tau_coding.permission_receipts import (
    write_permission_reply_receipt,
    write_permission_request_receipt,
)
from tau_coding.persona_dream_panel_proof import (
    DEFAULT_AGENT_REGISTRY_ROOT as DEFAULT_PERSONA_DREAM_PANEL_AGENT_ROOT,
)
from tau_coding.persona_dream_panel_proof import (
    DEFAULT_COMMAND_SPEC_ROOT as DEFAULT_PERSONA_DREAM_PANEL_COMMAND_SPEC_ROOT,
)
from tau_coding.persona_dream_panel_proof import (
    DEFAULT_GOAL_HASH as DEFAULT_PERSONA_DREAM_PANEL_GOAL_HASH,
)
from tau_coding.persona_dream_panel_proof import (
    write_persona_dream_panel_proof,
)
from tau_coding.policy_profile import write_zero_trust_preflight_receipt
from tau_coding.project_dag import (
    DAG_CONTRACT_SCHEMA,
    dag_contract_error_payload,
    load_dag_contract_payload,
    run_project_dag_contract,
    write_fail_closed_registry_receipt,
)
from tau_coding.project_profile import write_project_profile_validation_receipt
from tau_coding.project_spine import write_project_spine_check_receipt
from tau_coding.proof_index import build_proof_index
from tau_coding.provenance import (
    build_actor_manifest,
    build_environment_manifest,
    parse_actor_spec,
)
from tau_coding.provider_config import (
    ANTHROPIC_AUTH_TOKEN_ENV,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER_NAME,
    RUNTIME_API_KEY_ENV,
    CredentialReader,
    OpenAICompatibleProviderConfig,
    ProviderConfig,
    ProviderSettings,
    ScopedModelConfig,
    load_provider_settings,
    provider_config_from_catalog_entry,
    provider_kind,
    resolve_provider_selection,
    save_provider_settings,
    upsert_openai_compatible_provider,
)
from tau_coding.provider_dag_poc import (
    inspect_provider_dag_run,
    plan_provider_dag_poc,
    run_provider_dag_orchestrator,
    run_provider_dag_poc,
)
from tau_coding.provider_pane_poc import (
    inspect_provider_pane_run,
    inspect_provider_readiness_run,
    run_provider_pane_poc,
    run_provider_readiness_poc,
)
from tau_coding.provider_runtime import create_model_provider
from tau_coding.receipt_signing import sign_receipt, verify_signed_receipt
from tau_coding.rendering import PrintOutputMode, create_event_renderer
from tau_coding.research_query_gate import write_research_query_safety_receipt
from tau_coding.research_skill_adapter import write_research_skill_adapter_receipt
from tau_coding.research_source_receipt import write_research_source_receipt
from tau_coding.resources import TauResourcePaths
from tau_coding.review_code_skill_adapter import write_review_code_skill_adapter_receipt
from tau_coding.review_findings import write_review_findings_receipt
from tau_coding.run_report import write_run_report
from tau_coding.run_status import build_dag_viewer_link, build_run_status
from tau_coding.sandbox_run import run_sandboxed_command
from tau_coding.scillm_chat_review import write_scillm_chat_review_receipt
from tau_coding.scillm_subagent_gate import validate_scillm_subagent_loop_summary
from tau_coding.self_fix_repair_loop import write_coder_reviewer_repair_loop
from tau_coding.self_fix_ticket_repair import run_ticket_repair
from tau_coding.server import serve_tau_api
from tau_coding.session import (
    CodingSession,
    CodingSessionConfig,
    TerminalCommandResult,
    jsonl_session_storage,
    parse_terminal_command,
)
from tau_coding.session_export import (
    default_session_export_artifact_path,
    export_session_artifact,
    normalize_export_format,
)
from tau_coding.session_manager import (
    CodingSessionRecord,
    SessionManager,
    assert_valid_session_id,
)
from tau_coding.skill_capability_registry import (
    write_default_skill_capability_registry,
    write_skill_capability_registry_validation_receipt,
)
from tau_coding.skill_composition_redteam import run_skill_composition_redteam
from tau_coding.skill_invocation import write_skill_invocation_receipt
from tau_coding.sparta_posture import write_sparta_posture_contract
from tau_coding.test_run_receipt import write_test_run_receipt
from tau_coding.thinking import DEFAULT_THINKING_LEVEL, ThinkingLevel, normalize_thinking_level
from tau_coding.ticket_closure_evidence import (
    validate_subagent_code_ticket_closure,
    write_ticket_subagent_closure_proof,
)
from tau_coding.tools import create_write_tool
from tau_coding.traycer.cli import parse_traycer_validate_cli_args, traycer_validate_command
from tau_coding.trust import DefaultProjectTrust
from tau_coding.tui import run_tui_app
from tau_coding.tui.config import load_project_tui_settings, load_tui_settings
from tau_coding.tui.proof import (
    DEFAULT_TUI_PROOF_PROMPT,
    DEFAULT_TUI_PROOF_RUN_ID,
    render_textual_tui_memory_stage_proof,
)
from tau_coding.updater import update_tau
from tau_coding.visible_dag_poc import inspect_visible_dag_run, run_visible_dag_poc
from tau_coding.workflows.catalog import (
    get_workflow,
    workflow_catalog_payload,
)
from tau_coding.workflows.runner import (
    approve_packaged_workflow,
    repair_durable_repository_qualification,
    resume_packaged_workflow,
    run_approved_release_bundle_workflow,
    run_durable_repository_qualification_workflow,
    run_repository_evidence_map_workflow,
    run_repository_readiness_workflow,
    run_tau_operator_reference_workflow,
)
from tau_coding.zero_trust_redteam import run_zero_trust_redteam

app = typer.Typer(
    name="tau",
    help="Tau coding-agent harness.",
    add_completion=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
workflows_app = typer.Typer(
    add_completion=False,
    help="Discover and run packaged canonical Tau workflows.",
)
app.add_typer(workflows_app, name="workflows")

RUN_REGISTRY_SCHEMA = "tau.run_registry.v1"


@app.command("github-redact-projection")
def github_redact_projection_cli_command(
    projection: Annotated[Path, typer.Option("--projection")],
    out: Annotated[Path, typer.Option("--out")],
    receipt: Annotated[Path | None, typer.Option("--receipt")] = None,
) -> None:
    payload = redact_github_projection(
        projection_path=projection,
        output_path=out,
        receipt_path=receipt,
    )
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("ok") is not True:
        raise typer.Exit(1)


@app.command("tui-proof")
def tui_proof_cli_command(
    output_dir: Annotated[Path, typer.Option("--out-dir")] = Path(".tmp/tui-proof"),
    prompt: Annotated[str, typer.Option("--prompt")] = DEFAULT_TUI_PROOF_PROMPT,
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_TUI_PROOF_RUN_ID,
    route: Annotated[str, typer.Option("--route")] = "COMPLIANCE",
    next_agent: Annotated[str, typer.Option("--next-agent")] = "reviewer",
) -> None:
    """Render a fixture-backed Textual TUI proof receipt."""

    if not prompt.strip():
        raise typer.BadParameter("--prompt must not be empty")
    if not run_id.strip():
        raise typer.BadParameter("--run-id must not be empty")
    if not route.strip():
        raise typer.BadParameter("--route must not be empty")
    if not next_agent.strip():
        raise typer.BadParameter("--next-agent must not be empty")
    ok = tui_proof_command(
        output_dir=output_dir,
        prompt=prompt,
        run_id=run_id,
        route=route,
        next_agent=next_agent,
    )
    if not ok:
        raise typer.Exit(1)


@app.command("ticket-subagent-closure-proof")
def ticket_subagent_closure_proof_cli_command(
    output: Annotated[Path, typer.Option("--output")],
    allow_live_filesystem: Annotated[bool, typer.Option("--allow-live-filesystem")] = False,
) -> None:
    """Prove code-ticket subagent closure requires live non-mocked E2E evidence."""

    try:
        payload = write_ticket_subagent_closure_proof(
            output,
            allow_live_filesystem=allow_live_filesystem,
        )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("status") != "PASS":
        raise typer.Exit(1)


@workflows_app.command("list")
def workflows_list_command(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    payload = workflow_catalog_payload()
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    workflows = payload["workflows"]
    if not isinstance(workflows, list):
        raise RuntimeError("workflow catalog workflows must be a list")
    for workflow in workflows:
        if isinstance(workflow, dict):
            typer.echo(
                f"rung {workflow['rung']}\t{workflow['workflow_id']}\t"
                f"{workflow['topology']}\t{workflow['title']}"
            )


def _run_registry_path() -> Path:
    override = environ.get("TAU_RUN_REGISTRY")
    if override:
        return Path(override).expanduser().resolve()
    return (TauPaths().home / "runs.json").expanduser().resolve()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _read_json_file(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": RUN_REGISTRY_SCHEMA, "runs": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"run registry is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"run registry must contain a JSON object: {path}")
    return payload


def _load_run_registry() -> list[dict[str, object]]:
    payload = _read_json_file(_run_registry_path())
    raw_runs = payload.get("runs", [])
    if not isinstance(raw_runs, list):
        raise RuntimeError("run registry runs must be a list")
    runs: list[dict[str, object]] = []
    for item in raw_runs:
        if isinstance(item, dict):
            runs.append(dict(item))
    return runs


def _write_run_registry(runs: list[dict[str, object]]) -> None:
    registry_path = _run_registry_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema": RUN_REGISTRY_SCHEMA,
                "updated_at": _now_iso(),
                "runs": runs,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _workflow_run_metadata(run_dir: Path, payload: Mapping[str, object]) -> dict[str, str]:
    resolved = run_dir.expanduser().resolve()
    workflow_id = _optional_str(payload.get("workflow_id")) or "UNKNOWN"
    status = _optional_str(payload.get("status")) or "UNKNOWN"
    run_id = _optional_str(payload.get("run_id"))
    dag_path = resolved / "workflow" / "dag.json"
    if run_id is None or workflow_id == "UNKNOWN":
        with suppress(OSError, json.JSONDecodeError, KeyError, TypeError):
            dag_payload = json.loads(dag_path.read_text(encoding="utf-8"))
            if isinstance(dag_payload, dict):
                run_id = run_id or _optional_str(dag_payload.get("run_id"))
                workflow_id = _optional_str(dag_payload.get("workflow_id")) or workflow_id
    return {
        "run_id": run_id or resolved.name,
        "workflow_id": workflow_id,
        "state": status,
        "run_dir": str(resolved),
    }


def _record_workflow_run(payload: Mapping[str, object]) -> None:
    run_dir_value = payload.get("run_dir")
    if not isinstance(run_dir_value, str) or not run_dir_value:
        return
    run_dir = Path(run_dir_value)
    metadata = _workflow_run_metadata(run_dir, payload)
    now = _now_iso()
    runs = _load_run_registry()
    existing = next(
        (item for item in runs if item.get("run_dir") == metadata["run_dir"]),
        None,
    )
    started_at = now
    if existing is not None:
        started_at = str(existing.get("started_at") or now)
        runs = [item for item in runs if item.get("run_dir") != metadata["run_dir"]]
    entry: dict[str, object] = {
        **metadata,
        "started_at": started_at,
        "updated_at": now,
    }
    runs.insert(0, entry)
    _write_run_registry(runs)


def _run_available(entry: Mapping[str, object]) -> bool:
    run_dir = entry.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir:
        return False
    return (Path(run_dir).expanduser().resolve() / "dag-run.sqlite3").exists()


def _run_entry_public_payload(entry: Mapping[str, object]) -> dict[str, object]:
    available = _run_available(entry)
    return {
        "run_id": str(entry.get("run_id") or "UNKNOWN"),
        "workflow_id": str(entry.get("workflow_id") or "UNKNOWN"),
        "state": str(entry.get("state") or "UNKNOWN") if available else "UNAVAILABLE",
        "started_at": str(entry.get("started_at") or ""),
        "updated_at": str(entry.get("updated_at") or ""),
        "run_dir": str(entry.get("run_dir") or ""),
        "available": available,
    }


def _list_runs_payload(*, limit: int | None = None) -> dict[str, object]:
    runs = [_run_entry_public_payload(entry) for entry in _load_run_registry()]
    if limit is not None:
        runs = runs[:limit]
    return {
        "schema": "tau.runs_list.v1",
        "ok": True,
        "status": "PASS",
        "registry_path": str(_run_registry_path()),
        "runs": runs,
    }


def _resolve_last_run_dir() -> Path:
    runs = _load_run_registry()
    if not runs:
        raise RuntimeError("tau_run_registry_empty: run a workflow or pass --run-dir")
    entry = runs[0]
    public = _run_entry_public_payload(entry)
    if public["available"] is not True:
        run_dir = public["run_dir"] or "UNKNOWN"
        raise RuntimeError(f"tau_last_run_unavailable: {run_dir}")
    return Path(str(public["run_dir"]))


def _parse_runs_cli_args(args: list[str]) -> tuple[dict[str, object], bool]:
    if not args or args[0] != "list":
        raise RuntimeError("Usage: tau runs list [--json] [--limit <n>]")
    json_output = False
    limit: int | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--json":
            json_output = True
            index += 1
            continue
        if arg == "--limit":
            if index + 1 >= len(args):
                raise RuntimeError("--limit requires a value")
            try:
                limit = int(args[index + 1])
            except ValueError as exc:
                raise RuntimeError("--limit must be an integer") from exc
            index += 2
            continue
        if arg.startswith("--limit="):
            try:
                limit = int(arg.partition("=")[2])
            except ValueError as exc:
                raise RuntimeError("--limit must be an integer") from exc
            index += 1
            continue
        raise RuntimeError(f"unknown runs list option: {arg}")
    if limit is not None and limit < 1:
        raise RuntimeError("--limit must be at least 1")
    return _list_runs_payload(limit=limit), json_output


def _echo_runs_list(payload: Mapping[str, object]) -> None:
    runs = payload.get("runs", [])
    if not isinstance(runs, list):
        raise RuntimeError("runs payload runs must be a list")
    for run in runs:
        if not isinstance(run, dict):
            continue
        typer.echo(
            f"{run.get('run_id', 'UNKNOWN')}\t{run.get('workflow_id', 'UNKNOWN')}\t"
            f"{run.get('state', 'UNKNOWN')}\t{run.get('started_at', '')}\t"
            f"{run.get('run_dir', '')}"
        )


@workflows_app.command("describe")
def workflows_describe_command(
    workflow_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        payload = get_workflow(workflow_id).public_payload()
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"{payload['title']} ({payload['workflow_id']})")
    typer.echo(str(payload["summary"]))
    typer.echo(f"Rung: {payload['rung']}")
    typer.echo(f"Topology: {payload['topology']}")


@workflows_app.command("run")
def workflows_run_command(
    workflow_id: str,
    repo: Annotated[Path, typer.Option("--repo")],
    run_dir: Annotated[Path, typer.Option("--run-dir")],
    goal: Annotated[str | None, typer.Option("--goal")] = None,
    required_workflow: Annotated[
        str | None,
        typer.Option("--required-workflow"),
    ] = None,
    require_clean: Annotated[bool, typer.Option("--require-clean")] = False,
    require_tests: Annotated[bool, typer.Option("--require-tests")] = False,
    publish_path: Annotated[Path | None, typer.Option("--publish-path")] = None,
    inject_test_branch_failure: Annotated[
        bool, typer.Option("--inject-test-branch-failure", hidden=True)
    ] = False,
    open_viewer: Annotated[bool, typer.Option("--open-viewer")] = False,
    no_browser_open: Annotated[bool, typer.Option("--no-browser-open")] = False,
    viewer_hold_seconds: Annotated[
        float | None,
        typer.Option("--viewer-hold-seconds", hidden=True),
    ] = None,
) -> None:
    if workflow_id not in {
        "approved-release-bundle",
        "durable-repository-qualification",
        "repository-readiness",
        "repository-evidence-map",
        "tau-operator-reference",
    }:
        raise typer.BadParameter(f"unknown workflow_id: {workflow_id}")
    try:
        if workflow_id in {
            "approved-release-bundle",
            "durable-repository-qualification",
        }:
            if goal is None or publish_path is None:
                raise RuntimeError(f"{workflow_id} requires --goal and --publish-path")
            if workflow_id == "approved-release-bundle":
                payload = run_approved_release_bundle_workflow(
                    repo_path=repo,
                    human_goal=goal,
                    publish_path=publish_path,
                    run_dir=run_dir,
                    open_viewer=open_viewer,
                    browser_open=not no_browser_open,
                    viewer_hold_seconds=viewer_hold_seconds,
                )
            else:
                payload = run_durable_repository_qualification_workflow(
                    repo_path=repo,
                    human_goal=goal,
                    publish_path=publish_path,
                    run_dir=run_dir,
                    open_viewer=open_viewer,
                    browser_open=not no_browser_open,
                    viewer_hold_seconds=viewer_hold_seconds,
                    inject_test_branch_failure=inject_test_branch_failure,
                )
        elif workflow_id == "repository-readiness":
            if goal is None:
                raise RuntimeError("repository-readiness requires --goal")
            payload = run_repository_readiness_workflow(
                repo_path=repo,
                human_goal=goal,
                require_clean=require_clean,
                run_dir=run_dir,
                open_viewer=open_viewer,
                browser_open=not no_browser_open,
                viewer_hold_seconds=viewer_hold_seconds,
            )
        elif workflow_id == "tau-operator-reference":
            payload = run_tau_operator_reference_workflow(
                repo_path=repo,
                required_workflow=required_workflow or "tau-operator-reference",
                run_dir=run_dir,
                open_viewer=open_viewer,
                browser_open=not no_browser_open,
                viewer_hold_seconds=viewer_hold_seconds,
            )
        else:
            if goal is None:
                raise RuntimeError("repository-evidence-map requires --goal")
            payload = run_repository_evidence_map_workflow(
                repo_path=repo,
                human_goal=goal,
                require_tests=require_tests,
                run_dir=run_dir,
                open_viewer=open_viewer,
                browser_open=not no_browser_open,
                viewer_hold_seconds=viewer_hold_seconds,
            )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _record_workflow_run(payload)
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("ok") is not True:
        raise typer.Exit(1)


@workflows_app.command("approve")
def workflows_approve_command(
    run_dir: Annotated[Path, typer.Argument()],
    approval_packet: Annotated[
        Path | None,
        typer.Option(
            "--approval-packet",
            help="Out-of-band human approval packet to validate and bind to this run.",
        ),
    ] = None,
) -> None:
    try:
        payload = approve_packaged_workflow(
            run_dir=run_dir,
            approval_packet=approval_packet,
        )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _record_workflow_run(payload)
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("ok") is not True:
        raise typer.Exit(1)


@workflows_app.command("resume")
def workflows_resume_command(
    run_dir: Annotated[Path, typer.Argument()],
) -> None:
    try:
        payload = resume_packaged_workflow(run_dir=run_dir)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _record_workflow_run(payload)
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("ok") is not True:
        raise typer.Exit(1)


@workflows_app.command("repair")
def workflows_repair_command(
    run_dir: Annotated[Path, typer.Argument()],
    node_id: Annotated[str, typer.Option("--node")],
    approval_packet: Annotated[
        Path | None,
        typer.Option(
            "--approval-packet",
            help="Out-of-band human approval packet authorizing this targeted repair.",
        ),
    ] = None,
) -> None:
    try:
        payload = repair_durable_repository_qualification(
            run_dir=run_dir,
            node_id=node_id,
            approval_packet=approval_packet,
        )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _record_workflow_run(payload)
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("ok") is not True:
        raise typer.Exit(1)


def doctor_command(
    *,
    repo_root: Path | None = None,
    memory_url: str | None = None,
    service_probe: Callable[[str, float], tuple[bool, str | None]] | None = None,
) -> dict[str, object]:
    """Return a read-only Tau runtime preflight receipt."""

    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    pyproject = root / "pyproject.toml"
    cli_path = root / "src" / "tau_coding" / "cli.py"
    proofs_root = root / "experiments" / "goal-locked-subagents" / "proofs"
    chat_contract = root / "ui" / "tau-chat-contract.json"
    errors: list[str] = []
    warnings: list[str] = []

    required_paths = {
        "repo_root": root,
        "pyproject": pyproject,
        "cli": cli_path,
    }
    for name, path in required_paths.items():
        if not path.exists():
            errors.append(f"missing required path: {name}={path}")

    command_paths = {
        "python": sys.executable,
        "uv": which("uv"),
        "git": which("git"),
        "gh": which("gh"),
        "herdr": which("herdr"),
        "surf": str(DEFAULT_SURF_WRAPPER) if DEFAULT_SURF_WRAPPER.exists() else which("surf"),
    }

    provider_payload: dict[str, object]
    try:
        settings = load_provider_settings()
        credential_reader = FileCredentialStore()
        provider_payload = {
            "default_provider": settings.default_provider,
            "provider_count": len(settings.providers),
            "providers": [
                {
                    "name": item.name,
                    "kind": provider_kind(item),
                    "credential": _provider_credential_status(
                        item,
                        credential_reader=credential_reader,
                    ),
                }
                for item in settings.providers
            ],
        }
    except Exception as exc:  # pragma: no cover - defensive preflight fallback
        provider_payload = {
            "default_provider": None,
            "provider_count": 0,
            "providers": [],
            "error": str(exc),
        }
        warnings.append(f"provider settings could not be loaded: {exc}")

    herdr_ready = command_paths["herdr"] is not None
    gh_ready = command_paths["gh"] is not None
    surf_ready = command_paths["surf"] is not None
    external_services = _doctor_external_services(
        memory_url=memory_url or environ.get("TAU_MEMORY_URL") or DEFAULT_MEMORY_URL,
        probe=service_probe,
    )
    degraded = any(
        service.get("required") is True and service.get("state") == "unreachable"
        for service in external_services
    )
    if degraded:
        warnings.append("one or more required external services are unreachable")

    lanes = {
        "local_cli": {
            "ready": len(errors) == 0,
            "reason": "required Tau runtime files are present"
            if len(errors) == 0
            else "required Tau runtime files are missing",
        },
        "local_sanity": {
            "ready": command_paths["uv"] is not None and pyproject.exists(),
            "reason": "uv and pyproject.toml are available"
            if command_paths["uv"] is not None and pyproject.exists()
            else "uv or pyproject.toml is unavailable",
        },
        "herdr": {
            "ready": herdr_ready,
            "reason": "herdr executable found"
            if herdr_ready
            else "herdr executable not found on PATH",
        },
        "provider_live": {
            "ready": False,
            "reason": "doctor does not allocate provider panes or call model providers",
        },
        "github_dry_run": {
            "ready": gh_ready,
            "reason": "gh executable found" if gh_ready else "gh executable not found on PATH",
        },
        "github_apply": {
            "ready": False,
            "reason": (
                "live GitHub mutation requires approval, preflight, redaction, "
                "and apply policy receipts"
            ),
        },
        "browser_cdp": {
            "ready": surf_ready,
            "reason": (
                "Surf wrapper or surf executable found; run tau browser-cdp-proof "
                "for screenshot proof"
            )
            if surf_ready
            else "Surf wrapper or surf executable not found",
        },
    }

    ok = len(errors) == 0
    return {
        "schema": "tau.doctor.v1",
        "ok": ok,
        "status": "PASS" if ok and not degraded else "DEGRADED" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "version": __version__,
        "repo_root": str(root),
        "commands": command_paths,
        "paths": {
            "pyproject": {"path": str(pyproject), "exists": pyproject.exists()},
            "cli": {"path": str(cli_path), "exists": cli_path.exists()},
            "proofs_root": {"path": str(proofs_root), "exists": proofs_root.exists()},
            "chat_contract": {"path": str(chat_contract), "exists": chat_contract.exists()},
        },
        "lanes": lanes,
        "modes": _doctor_mode_manifest(),
        "external_services": external_services,
        "provider_settings": provider_payload,
        "errors": errors,
        "warnings": warnings,
        "proof_boundary": {
            "proves": [
                "Tau runtime import and CLI dispatch can emit a read-only preflight receipt.",
                "Required local Tau runtime paths were checked.",
                "Optional local executables for uv, git, gh, and Herdr were detected "
                "without side effects.",
                "Configured provider entries were inspected without making provider/model calls.",
                "Configured external service endpoints were probed with read-only health checks.",
            ],
            "does_not_prove": [
                "Herdr pane readiness.",
                "Live provider/model semantic quality.",
                "Provider DAG execution.",
                "GitHub live mutation.",
                "Browser/CDP UI proof; run tau browser-cdp-proof for screenshot artifacts.",
                "Full hardening roadmap completion.",
                "Semantic correctness of any external service response.",
            ],
        },
    }


def _doctor_external_services(
    *,
    memory_url: str,
    probe: Callable[[str, float], tuple[bool, str | None]] | None = None,
) -> list[dict[str, object]]:
    probe = probe or _doctor_http_probe
    services: list[dict[str, object]] = [
        _doctor_service_probe(
            name="memory",
            endpoint=memory_url.rstrip("/"),
            health_path="/health",
            required=True,
            remedy="Start Graph Memory on 127.0.0.1:8601 or set TAU_MEMORY_URL.",
            probe=probe,
        )
    ]
    optional_specs = (
        (
            "surf",
            environ.get("TAU_SURF_URL"),
            "/health",
            "Set TAU_SURF_URL when Surf service probing is desired.",
        ),
        (
            "chatterbox_tts",
            environ.get("TAU_CHATTERBOX_TTS_URL"),
            "/health",
            "Set TAU_CHATTERBOX_TTS_URL when voice output is configured.",
        ),
        (
            "realtime_stt",
            environ.get("TAU_REALTIMESTT_URL"),
            "/health",
            "Set TAU_REALTIMESTT_URL when voice input is configured.",
        ),
    )
    for name, endpoint, health_path, remedy in optional_specs:
        if endpoint:
            services.append(
                _doctor_service_probe(
                    name=name,
                    endpoint=endpoint.rstrip("/"),
                    health_path=health_path,
                    required=False,
                    remedy=remedy,
                    probe=probe,
                )
            )
        else:
            services.append(
                {
                    "name": name,
                    "required": False,
                    "state": "not_configured",
                    "endpoint": None,
                    "remedy": remedy,
                }
            )
    return services


def _doctor_service_probe(
    *,
    name: str,
    endpoint: str,
    health_path: str,
    required: bool,
    remedy: str,
    probe: Callable[[str, float], tuple[bool, str | None]],
) -> dict[str, object]:
    url = f"{endpoint.rstrip('/')}{health_path}"
    reachable, error = probe(url, 2.0)
    payload: dict[str, object] = {
        "name": name,
        "required": required,
        "state": "reachable" if reachable else "unreachable",
        "endpoint": endpoint,
        "health_url": url,
        "remedy": remedy,
    }
    if error:
        payload["error"] = error
    return payload


def _doctor_http_probe(url: str, timeout_seconds: float) -> tuple[bool, str | None]:
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=0.5)) as client:
            response = client.get(url)
        if response.status_code < 400:
            return True, None
        return False, f"HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        return False, str(exc)


def _doctor_mode_manifest() -> dict[str, dict[str, object]]:
    """Return Tau's operator-facing execution mode defaults."""
    return {
        "build": {
            "ready": True,
            "mutating_default": True,
            "permission_default": "ask_for_sensitive_actions",
            "description": "Editing and approved local command execution.",
        },
        "plan": {
            "ready": True,
            "mutating_default": False,
            "permission_default": "deny_mutations_without_approval",
            "description": "Read-only planning; edits and writes require approval.",
        },
        "review": {
            "ready": True,
            "mutating_default": False,
            "permission_default": "deny_mutations_without_approval",
            "description": "Read-only review with evidence and verdict constraints.",
        },
        "general": {
            "ready": True,
            "mutating_default": False,
            "permission_default": "ask_before_mutation",
            "description": "Bounded helper mode; mutation requires promotion or approval.",
        },
    }


async def status_command(
    cwd: Path,
    session_manager: SessionManager,
    session_id: str | None = None,
) -> dict[str, object]:
    """Return a non-interactive Tau operator status receipt."""
    resolved_cwd = cwd.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if session_id is None:
        record = session_manager.latest_session_for_cwd(resolved_cwd)
        selection = "latest_for_cwd"
    else:
        record = session_manager.get_session(session_id)
        selection = "explicit_session_id"
        if record is None:
            errors.append(f"session not found: {session_id}")

    session_payload: dict[str, object] = {
        "available": record is not None,
        "selection": selection,
        "requested_session_id": session_id,
    }
    if record is None:
        if session_id is None:
            warnings.append(f"no indexed session found for cwd: {resolved_cwd}")
    else:
        session_payload.update(
            await _status_session_payload(record, warnings=warnings, errors=errors)
        )

    provider_payload = _status_provider_payload(record.provider_name if record else None)
    ok = not errors
    return {
        "schema": "tau.status.v1",
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "cwd": str(resolved_cwd),
        "session": session_payload,
        "provider": provider_payload,
        "runtime": {
            "active_tool": {
                "available": False,
                "reason": "active tool state is process-local and unavailable to this CLI receipt",
            },
            "queues": {
                "available": False,
                "steering": None,
                "follow_up": None,
                "reason": "queued prompts are process-local until injected into the session",
            },
            "last_error": errors[-1] if errors else None,
        },
        "errors": errors,
        "warnings": warnings,
        "proof_boundary": {
            "proves": [
                "Tau CLI can inspect indexed session metadata without opening the TUI.",
                "Tau CLI can read and summarize the current session JSONL when it is available.",
                "Provider credential configuration was inspected without making "
                "provider/model calls.",
            ],
            "does_not_prove": [
                "A TUI process is currently running.",
                "Live active tool state or queued prompts from another Tau process.",
                "Provider/model semantic quality.",
                "Remote service availability.",
            ],
        },
    }


async def _status_session_payload(
    record: CodingSessionRecord,
    *,
    warnings: list[str],
    errors: list[str],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": record.id,
        "path": str(record.path),
        "cwd": str(record.cwd),
        "title": record.title,
        "model": record.model,
        "provider_name": record.provider_name,
        "parent_session_id": record.parent_session_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "file": {
            "exists": record.path.exists(),
            "bytes": record.path.stat().st_size if record.path.exists() else None,
        },
    }
    if not record.path.exists():
        errors.append(f"session file does not exist: {record.path}")
        payload["transcript"] = {"available": False}
        return payload

    try:
        entries = await JsonlSessionStorage(record.path).read_all()
    except Exception as exc:  # noqa: BLE001 - receipt should preserve exact read failure
        errors.append(f"session file is unreadable: {record.path}: {exc}")
        payload["transcript"] = {"available": False}
        return payload

    linear_state = SessionState.from_entries(entries)
    active_state = linear_state
    if linear_state.active_leaf_id is not None:
        try:
            active_state = SessionState.from_entries(entries, leaf_id=linear_state.active_leaf_id)
        except Exception as exc:  # noqa: BLE001 - stale leaf should be visible, not fatal to counts
            warnings.append(f"active leaf replay failed; using linear replay: {exc}")
            active_state = linear_state

    payload["transcript"] = {
        "available": True,
        "entry_count": len(entries),
        "active_entry_count": len(active_state.entries),
        "active_leaf_id": linear_state.active_leaf_id,
        "message_count": len(active_state.messages),
        "user_message_count": sum(
            isinstance(message, UserMessage) for message in active_state.messages
        ),
        "assistant_message_count": sum(
            isinstance(message, AssistantMessage) for message in active_state.messages
        ),
        "tool_result_count": sum(
            isinstance(message, ToolResultMessage) for message in active_state.messages
        ),
        "tool_call_count": sum(
            len(message.tool_calls)
            for message in active_state.messages
            if isinstance(message, AssistantMessage)
        ),
        "compaction_count": len(active_state.compaction_entries),
        "thinking_level": active_state.thinking_level,
        "model": active_state.model or record.model,
    }
    return payload


def _status_provider_payload(provider_name: str | None) -> dict[str, object]:
    try:
        settings = load_provider_settings()
        credential_reader = FileCredentialStore()
    except Exception as exc:  # pragma: no cover - defensive status fallback
        return {
            "available": False,
            "current": provider_name,
            "credential": "unknown",
            "error": str(exc),
        }
    current = settings.get_provider(provider_name) if provider_name else None
    return {
        "available": current is not None,
        "current": provider_name,
        "default_provider": settings.default_provider,
        "provider_count": len(settings.providers),
        "credential": _provider_credential_status(current, credential_reader=credential_reader)
        if current is not None
        else "provider_not_configured",
    }


async def replacement_harness_sanity_command(
    startup_cwd: Path,
    run_dir: Path,
    session_manager: SessionManager | None = None,
) -> dict[str, object]:
    """Exercise Tau's minimum local replacement-harness loop and write receipts."""

    resolved_run_dir = run_dir.expanduser().resolve()
    receipts_dir = resolved_run_dir / "receipts"
    artifacts_dir = resolved_run_dir / "artifacts"
    temp_repo = resolved_run_dir / "temp-repo"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    temp_repo.mkdir(parents=True, exist_ok=True)
    (temp_repo / "README.md").write_text("# Tau replacement sanity\n", encoding="utf-8")
    run_token = hashlib.sha256(f"{resolved_run_dir}:{_receipt_utc_stamp()}".encode()).hexdigest()[
        :12
    ]
    session_id = f"replacement-sanity-{run_token}"

    doctor_payload = doctor_command(repo_root=Path(__file__).resolve().parents[2])
    doctor_path = receipts_dir / "doctor.json"
    _write_receipt_json(doctor_path, doctor_payload)

    plan_target = temp_repo / "plan-mode-mutation.txt"
    plan_permission = write_permission_request_receipt(
        action="working_tree_mutation",
        resources=[str(plan_target)],
        source_node="plan-mode-write-attempt",
        run_dir=resolved_run_dir,
        output=receipts_dir / "plan-mode-denied-permission.json",
        session_id=session_id,
        request_id=f"{session_id}-plan-denied",
        mode="plan",
        denied=True,
        reason="plan mode is read-only; write attempt stopped before mutation",
    )

    build_tool = create_write_tool(cwd=temp_repo)
    build_result = await build_tool.execute(
        {
            "path": "build-mode-output.txt",
            "content": "Tau build-mode local write smoke.\n",
        }
    )
    build_result_path = receipts_dir / "build-mode-write-result.json"
    _write_receipt_json(
        build_result_path,
        {
            "schema": "tau.replacement_harness_build_write_result.v1",
            "ok": build_result.ok,
            "status": "PASS" if build_result.ok else "BLOCKED",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "tool_result": build_result.model_dump(mode="json"),
            "timestamp": _receipt_utc_stamp(),
        },
    )

    approval_packet_path = artifacts_dir / "approval-packet.json"
    _write_receipt_json(
        approval_packet_path,
        {
            "schema": "tau.machine_approval_packet.v1",
            "approved": True,
            "action": "working_tree_mutation",
            "origin": "machine",
            "actor": {"id": "tau:local-sanity", "auth_method": "machine-sanity-receipt"},
            "target": {"id": "replacement-harness-sanity"},
            "reason": "Approve bounded local sanity side effect.",
            "evidence": [str(build_result_path)],
            "nonce": "replacement-harness-sanity-nonce",
            "signature": "local-sanity-receipt",
        },
    )
    approval_receipt_path = receipts_dir / "approval-gate-receipt.json"
    approval_receipt = evaluate_approval_gate(
        approval_packet=approval_packet_path,
        requested_action="working_tree_mutation",
        run_dir=resolved_run_dir,
        output=approval_receipt_path,
        expected_target={"id": "replacement-harness-sanity"},
    )

    manager = session_manager or SessionManager(
        TauPaths(session_root=resolved_run_dir / "sessions")
    )
    record = manager.create_session(
        cwd=temp_repo,
        model="local-sanity-model",
        provider_name=None,
        title="Replacement harness sanity",
        session_id=session_id,
    )
    storage = JsonlSessionStorage(record.path)
    info = SessionInfoEntry(id="sanity-info", cwd=str(temp_repo), title=record.title)
    model = ModelChangeEntry(id="sanity-model", parent_id=info.id, model=record.model)
    user = MessageEntry(
        id="sanity-user",
        parent_id=model.id,
        message=UserMessage(content="Run the local replacement-harness sanity check."),
    )
    assistant = MessageEntry(
        id="sanity-assistant",
        parent_id=user.id,
        message=AssistantMessage(content="Local sanity artifacts were produced."),
    )
    leaf = LeafEntry(id="sanity-leaf", parent_id=assistant.id, entry_id=assistant.id)
    for entry in (info, model, user, assistant, leaf):
        await storage.append(entry)

    entries = await storage.read_all()
    export_path = export_session_artifact(
        entries,
        artifacts_dir / "replacement-sanity-session.html",
        title="Tau Replacement Harness Sanity",
        source=str(record.path),
    )
    status_payload = await status_command(temp_repo, manager, record.id)
    status_path = receipts_dir / "status.json"
    _write_receipt_json(status_path, status_payload)

    artifacts = [
        _artifact_record(doctor_path, kind="doctor_receipt"),
        _artifact_record(plan_permission["receipt_path"], kind="permission_request_receipt"),
        _artifact_record(build_result_path, kind="build_write_result"),
        _artifact_record(approval_packet_path, kind="approval_packet"),
        _artifact_record(approval_receipt_path, kind="approval_gate_receipt"),
        _artifact_record(record.path, kind="session_jsonl"),
        _artifact_record(export_path, kind="session_export"),
        _artifact_record(status_path, kind="status_receipt"),
    ]
    build_output = temp_repo / "build-mode-output.txt"
    gates = {
        "doctor": doctor_payload.get("ok") is True,
        "plan_mode_denied_without_mutation": (
            plan_permission.get("status") == "BLOCKED" and not plan_target.exists()
        ),
        "build_mode_local_write": build_result.ok
        and build_output.read_text(encoding="utf-8") == "Tau build-mode local write smoke.\n",
        "approval_gate_rejects_machine_packet": approval_receipt.get("status") == "BLOCKED"
        and isinstance(approval_receipt.get("packet_summary"), dict)
        and approval_receipt["packet_summary"].get("authorship") == "machine_originated",
        "session_export": export_path.exists(),
        "status_receipt": status_payload.get("ok") is True,
    }
    ok = all(gates.values())
    payload: dict[str, object] = {
        "schema": "tau.replacement_harness_sanity.v1",
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "startup_cwd": str(startup_cwd.resolve()),
        "run_dir": str(resolved_run_dir),
        "temp_repo": str(temp_repo),
        "session_id": record.id,
        "gates": gates,
        "artifacts": artifacts,
        "proof_scope": {
            "proves": [
                "Tau can emit one local replacement-harness sanity receipt bundle",
                "Tau can record read-only plan-mode mutation denial without creating the file",
                "Tau can perform a local build-mode write through the real Tau write tool",
                "Tau can classify a local machine sanity packet without letting it satisfy "
                "a production human approval gate",
                "Tau can export and report a local indexed session without opening the TUI",
            ],
            "does_not_prove": [
                "interactive TUI usability",
                "live provider/model semantic behavior",
                "approval replies applied to a running process",
                "complete Pi feature parity",
            ],
        },
        "timestamp": _receipt_utc_stamp(),
    }
    summary_path = resolved_run_dir / "replacement-harness-sanity-receipt.json"
    payload["receipt_path"] = str(summary_path)
    _write_receipt_json(summary_path, payload)
    return payload


def _artifact_record(path_like: object, *, kind: str) -> dict[str, object]:
    path = Path(str(path_like)).expanduser().resolve()
    return {
        "kind": kind,
        "path": str(path),
        "exists": path.exists(),
        "sha256": _receipt_file_sha256(path),
        "bytes": path.stat().st_size if path.exists() else None,
    }


def _write_receipt_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _receipt_file_sha256(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _receipt_utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def render_status_payload(payload: dict[str, object]) -> None:
    """Render a compact human status view."""
    typer.echo(f"Status: {payload['status']}")
    typer.echo(f"CWD: {payload['cwd']}")
    session = payload.get("session")
    if isinstance(session, dict) and session.get("available"):
        typer.echo(f"Session: {session.get('id')} ({session.get('title') or 'untitled'})")
        typer.echo(f"File: {session.get('path')}")
        typer.echo(f"Model: {session.get('provider_name') or 'unknown'}/{session.get('model')}")
        transcript = session.get("transcript")
        if isinstance(transcript, dict) and transcript.get("available"):
            typer.echo(
                "Transcript: "
                f"{transcript.get('message_count')} messages, "
                f"{transcript.get('tool_call_count')} tool calls"
            )
    else:
        typer.echo("Session: unavailable")
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            typer.echo(f"Warning: {warning}")
    errors = payload.get("errors")
    if isinstance(errors, list):
        for error in errors:
            typer.echo(f"Error: {error}")


def providers_command() -> None:
    """List configured model providers."""
    render_provider_settings(load_provider_settings(), credential_reader=FileCredentialStore())


def setup_command(
    *,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    base_url: str = DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    api_key_env: str = "OPENAI_API_KEY",
    model: str = DEFAULT_MODEL,
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    set_default: bool = True,
) -> None:
    """Create or update an OpenAI-compatible provider entry."""
    settings = load_provider_settings()
    provider = OpenAICompatibleProviderConfig(
        name=provider_name,
        base_url=base_url.rstrip("/"),
        api_key_env=api_key_env,
        models=(model,),
        default_model=model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_retry_delay_seconds=max_retry_delay_seconds,
    )
    updated = upsert_openai_compatible_provider(settings, provider, set_default=set_default)
    path = save_provider_settings(updated)
    typer.echo(f"Saved provider '{provider.name}' to {path}")
    if provider.api_key_env not in environ:
        typer.echo(f"Set {provider.api_key_env} before running Tau with this provider.", err=True)


def setup_chutes_command(
    *,
    model: str | None = None,
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    set_default: bool = True,
) -> None:
    """Create or update Tau's built-in Chutes.ai provider entry."""

    provider = provider_config_from_catalog_entry("chutes")
    if not isinstance(provider, OpenAICompatibleProviderConfig):
        raise RuntimeError("Chutes provider must be OpenAI-compatible")
    if model is not None:
        models = provider.models if model in provider.models else (*provider.models, model)
        provider = OpenAICompatibleProviderConfig(
            name=provider.name,
            base_url=provider.base_url,
            api_key_env=provider.api_key_env,
            credential_name=provider.credential_name,
            models=models,
            default_model=model,
            context_windows=provider.context_windows,
            headers=provider.headers,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_retry_delay_seconds=max_retry_delay_seconds,
            thinking_levels=provider.thinking_levels,
            thinking_models=provider.thinking_models,
            thinking_default=provider.thinking_default,
            thinking_parameter=provider.thinking_parameter,
        )
    else:
        provider = OpenAICompatibleProviderConfig(
            name=provider.name,
            base_url=provider.base_url,
            api_key_env=provider.api_key_env,
            credential_name=provider.credential_name,
            models=provider.models,
            default_model=provider.default_model,
            context_windows=provider.context_windows,
            headers=provider.headers,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_retry_delay_seconds=max_retry_delay_seconds,
            thinking_levels=provider.thinking_levels,
            thinking_models=provider.thinking_models,
            thinking_default=provider.thinking_default,
            thinking_parameter=provider.thinking_parameter,
        )
    updated = upsert_openai_compatible_provider(
        load_provider_settings(),
        provider,
        set_default=set_default,
    )
    if model is not None:
        updated = _replace_openai_compatible_provider(updated, provider)
    path = save_provider_settings(updated)
    typer.echo(f"Saved provider '{provider.name}' to {path}")
    if provider.api_key_env not in environ:
        typer.echo(f"Set {provider.api_key_env} before running Tau with this provider.", err=True)


def _replace_openai_compatible_provider(
    settings: ProviderSettings,
    provider: OpenAICompatibleProviderConfig,
) -> ProviderSettings:
    providers = tuple(
        provider if item.name == provider.name else item for item in settings.providers
    )
    return ProviderSettings(
        default_provider=settings.default_provider,
        providers=providers,
        scoped_models=settings.scoped_models,
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    prompt_args: Annotated[
        list[str] | None,
        typer.Argument(help="Initial prompt to run in interactive TUI mode."),
    ] = None,
    print_mode: Annotated[
        bool,
        typer.Option(
            "--print",
            "-p",
            help="Run the positional prompt in non-interactive print mode.",
        ),
    ] = False,
    prompt_option: Annotated[
        str | None,
        typer.Option(
            "--prompt",
            help="Removed; pass the prompt positionally and use --print instead.",
            hidden=True,
        ),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Configured provider name to use."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Model name to request from the provider."),
    ] = None,
    runtime_api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Runtime API key for the selected explicit model."),
    ] = None,
    thinking: Annotated[
        str | None,
        typer.Option(
            "--thinking",
            help="Set startup thinking level: off, minimal, low, medium, high, xhigh, or max.",
        ),
    ] = None,
    system_prompt: Annotated[
        str | None,
        typer.Option("--system-prompt", help="Use this text or file as the base system prompt."),
    ] = None,
    append_system_prompt: Annotated[
        list[str] | None,
        typer.Option(
            "--append-system-prompt",
            help="Append this text or file to the system prompt; repeatable.",
        ),
    ] = None,
    model_patterns: Annotated[
        str | None,
        typer.Option("--models", help="Comma-separated model patterns for scoped cycling."),
    ] = None,
    list_models: Annotated[
        bool,
        typer.Option("--list-models", help="List configured provider models and exit."),
    ] = False,
    setup_base_url: Annotated[
        str,
        typer.Option("--base-url", help="OpenAI-compatible base URL for `tau setup`."),
    ] = DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    setup_api_key_env: Annotated[
        str,
        typer.Option("--api-key-env", help="API key environment variable for `tau setup`."),
    ] = "OPENAI_API_KEY",
    setup_timeout_seconds: Annotated[
        float,
        typer.Option(
            "--timeout-seconds",
            help="HTTP timeout in seconds for `tau setup` provider requests.",
        ),
    ] = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
    setup_max_retries: Annotated[
        int,
        typer.Option("--max-retries", help="Provider retry count for `tau setup`."),
    ] = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    setup_max_retry_delay_seconds: Annotated[
        float,
        typer.Option(
            "--max-retry-delay-seconds",
            help="Provider retry delay in seconds for `tau setup`.",
        ),
    ] = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    setup_default: Annotated[
        bool,
        typer.Option("--set-default/--no-set-default", help="Make setup provider the default."),
    ] = True,
    cwd: Annotated[
        Path | None,
        typer.Option("--cwd", help="Working directory for built-in coding tools."),
    ] = None,
    mode: Annotated[
        PrintOutputMode | None,
        typer.Option(
            "--mode",
            help="Run in non-interactive print mode with this output format "
            "(text, json, or transcript).",
        ),
    ] = None,
    output: Annotated[
        PrintOutputMode | None,
        typer.Option(
            "--output",
            "-o",
            help="Removed; use --mode instead.",
            hidden=True,
        ),
    ] = None,
    session: Annotated[
        str | None,
        typer.Option("--session", help="Resume a session id in TUI mode."),
    ] = None,
    exact_session_id: Annotated[
        str | None,
        typer.Option(
            "--session-id",
            help="Use an exact project session id, creating it if missing.",
        ),
    ] = None,
    session_name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Set session display name at startup."),
    ] = None,
    fork_session_ref: Annotated[
        str | None,
        typer.Option("--fork", help="Fork a session id or JSONL path into a new session."),
    ] = None,
    resume_picker: Annotated[
        bool,
        typer.Option("--resume", "-r", help="Select a session to resume in TUI mode."),
    ] = False,
    new_session: Annotated[
        bool,
        typer.Option("--new-session", help="Create a new session in TUI mode (default)."),
    ] = False,
    continue_session: Annotated[
        bool,
        typer.Option("--continue", "-c", help="Continue the latest session for this cwd."),
    ] = False,
    session_dir: Annotated[
        Path | None,
        typer.Option("--session-dir", help="Directory for indexed session storage."),
    ] = None,
    no_session: Annotated[
        bool,
        typer.Option("--no-session", help="Run without saving or indexing a session."),
    ] = False,
    no_context_files: Annotated[
        bool,
        typer.Option(
            "--no-context-files",
            "-nc",
            help="Disable AGENTS.md and CLAUDE.md context discovery.",
        ),
    ] = False,
    no_tools: Annotated[
        bool,
        typer.Option("--no-tools", "-nt", help="Disable all tools for the startup session."),
    ] = False,
    no_builtin_tools: Annotated[
        bool,
        typer.Option(
            "--no-builtin-tools",
            "-nbt",
            help="Disable built-in tools while keeping explicit custom tools available.",
        ),
    ] = False,
    tools: Annotated[
        str | None,
        typer.Option(
            "--tools",
            "-t",
            help="Comma-separated allowlist of tool names to enable.",
        ),
    ] = None,
    exclude_tools: Annotated[
        str | None,
        typer.Option(
            "--exclude-tools",
            "-xt",
            help="Comma-separated denylist of tool names to disable.",
        ),
    ] = None,
    no_skills: Annotated[
        bool,
        typer.Option("--no-skills", "-ns", help="Disable skills discovery and loading."),
    ] = False,
    no_prompt_templates: Annotated[
        bool,
        typer.Option(
            "--no-prompt-templates",
            "-np",
            help="Disable prompt template discovery and loading.",
        ),
    ] = False,
    no_themes: Annotated[
        bool,
        typer.Option("--no-themes", help="Disable custom TUI theme discovery and loading."),
    ] = False,
    no_extensions: Annotated[
        bool,
        typer.Option(
            "--no-extensions",
            "-ne",
            help="Disable user extension discovery while keeping explicit --extension paths.",
        ),
    ] = False,
    skill_paths: Annotated[
        list[Path] | None,
        typer.Option("--skill", help="Load a skill markdown file or directory; repeatable."),
    ] = None,
    prompt_template_paths: Annotated[
        list[Path] | None,
        typer.Option(
            "--prompt-template",
            help="Load a prompt template markdown file or directory; repeatable.",
        ),
    ] = None,
    theme_paths: Annotated[
        list[Path] | None,
        typer.Option("--theme", help="Load a custom theme JSON file or directory; repeatable."),
    ] = None,
    extension_paths: Annotated[
        list[Path] | None,
        typer.Option(
            "--extension",
            "-e",
            help="Load a Python extension file or directory; repeatable.",
        ),
    ] = None,
    approve_project: Annotated[
        bool,
        typer.Option("--approve", "-a", help="Trust project-local resources for this run."),
    ] = False,
    no_approve_project: Annotated[
        bool,
        typer.Option("--no-approve", "-na", help="Ignore project-local resources for this run."),
    ] = False,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="Disable startup network operations where supported.",
        ),
    ] = False,
    verbose_startup: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Force verbose startup output, overriding quiet startup settings.",
        ),
    ] = False,
    log_level: Annotated[
        str | None,
        typer.Option(
            "--log-level",
            help="Set Tau diagnostic log level: trace, debug, info, warning, error, critical.",
        ),
    ] = None,
    log_file: Annotated[
        Path | None,
        typer.Option(
            "--log-file",
            help="Write Tau structured diagnostics to this JSONL file.",
        ),
    ] = None,
    auto_compact_threshold: Annotated[
        int | None,
        typer.Option(
            "--auto-compact-threshold",
            help="Automatically compact TUI context above this rough token estimate.",
        ),
    ] = None,
    loop2_receipt_root: Annotated[
        Path | None,
        typer.Option(
            "--loop2-receipt-root",
            help="Write Loop2-compatible receipt artifacts under this run root in print mode.",
        ),
    ] = None,
    loop2_node_id: Annotated[
        str,
        typer.Option("--loop2-node-id", help="Node id for Loop2 receipt artifacts."),
    ] = "tau-print",
    loop2_allowed_globs: Annotated[
        list[str] | None,
        typer.Option(
            "--loop2-allowed-glob",
            help="Allowed file glob for the Loop2 contract; repeatable.",
        ),
    ] = None,
    loop2_required_changed_globs: Annotated[
        list[str] | None,
        typer.Option(
            "--loop2-required-changed-glob",
            help="Required changed-file glob for the Loop2 contract; repeatable.",
        ),
    ] = None,
    loop2_checks: Annotated[
        list[str] | None,
        typer.Option("--loop2-check", help="Local check command for Loop2 receipts; repeatable."),
    ] = None,
    loop2_serve_host: Annotated[
        str,
        typer.Option("--loop2-serve-host", help="Host for `tau loop2-serve`."),
    ] = "127.0.0.1",
    loop2_serve_port: Annotated[
        int,
        typer.Option("--loop2-serve-port", help="Port for `tau loop2-serve`."),
    ] = 8765,
    loop2_switchboard_url: Annotated[
        str,
        typer.Option(
            "--loop2-switchboard-url",
            help="pi-mono switchboard base URL for `tau loop2-emit-peer`.",
        ),
    ] = "http://127.0.0.1:7890",
    loop2_peer_target: Annotated[
        str,
        typer.Option("--loop2-peer-target", help="Target harness for `tau loop2-emit-peer`."),
    ] = "pi-mono",
    loop2_src: Annotated[
        Path | None,
        typer.Option(
            "--loop2-src",
            help="Path to the Loop2 source directory containing the loop2 package.",
        ),
    ] = None,
    loop2_inspect_validate: Annotated[
        bool,
        typer.Option(
            "--loop2-inspect-validate",
            help="Include Loop2 contract validation in `tau loop2-inspect` output.",
        ),
    ] = False,
    loop2_sanity_root: Annotated[
        Path,
        typer.Option(
            "--loop2-sanity-root",
            help="Root directory for `tau loop2-sanity` fixture receipt runs.",
        ),
    ] = Path(".loop2/sanity"),
    loop2_scillm_doctor_receipt: Annotated[
        Path | None,
        typer.Option(
            "--loop2-scillm-doctor-receipt",
            help="Passing Scillm doctor receipt required before delegated Scillm loop2 runs.",
        ),
    ] = None,
    export: Annotated[
        bool,
        typer.Option(
            "--export",
            help="Export the given session id or JSONL path (mirrors `tau export`).",
        ),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", "-v", help="Show Tau's version and exit."),
    ] = False,
) -> None:
    """Run the Tau CLI."""
    if version:
        typer.echo(f"tau {__version__}")
        raise typer.Exit()

    try:
        configure_tau_logging(log_path=log_file, level=log_level, verbose=verbose_startup)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if ctx.invoked_subcommand is not None:
        return

    if offline:
        environ["TAU_OFFLINE"] = "1"
        environ["PI_OFFLINE"] = "1"
        environ["PI_SKIP_VERSION_CHECK"] = "1"

    if verbose_startup:
        environ["TAU_VERBOSE_STARTUP"] = "1"

    if runtime_api_key is not None:
        if not runtime_api_key.strip():
            raise typer.BadParameter("--api-key requires a non-empty value")
        if model is None and model_patterns is None:
            raise typer.BadParameter("--api-key requires --model or --models")
        previous_runtime_api_key = environ.get(RUNTIME_API_KEY_ENV)

        def restore_runtime_api_key() -> None:
            if previous_runtime_api_key is None:
                environ.pop(RUNTIME_API_KEY_ENV, None)
            else:
                environ[RUNTIME_API_KEY_ENV] = previous_runtime_api_key

        ctx.call_on_close(restore_runtime_api_key)
        environ[RUNTIME_API_KEY_ENV] = runtime_api_key

    if session is not None and new_session:
        raise typer.BadParameter("--session and --new-session cannot be used together")

    if exact_session_id is not None:
        try:
            assert_valid_session_id(exact_session_id)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    if exact_session_id is not None and session is not None:
        raise typer.BadParameter("--session-id and --session cannot be used together")

    if exact_session_id is not None and continue_session:
        raise typer.BadParameter("--session-id and --continue cannot be used together")

    if exact_session_id is not None and resume_picker:
        raise typer.BadParameter("--session-id and --resume cannot be used together")

    if resume_picker and session is not None:
        raise typer.BadParameter("--resume and --session cannot be used together")

    if fork_session_ref is not None and session is not None:
        raise typer.BadParameter("--fork and --session cannot be used together")

    if fork_session_ref is not None and resume_picker:
        raise typer.BadParameter("--fork and --resume cannot be used together")

    if fork_session_ref is not None and continue_session:
        raise typer.BadParameter("--fork and --continue cannot be used together")

    if fork_session_ref is not None and new_session:
        raise typer.BadParameter("--fork and --new-session cannot be used together")

    if fork_session_ref is not None and no_session:
        raise typer.BadParameter("--fork and --no-session cannot be used together")

    if continue_session and session is not None:
        raise typer.BadParameter("--continue and --session cannot be used together")

    if continue_session and resume_picker:
        raise typer.BadParameter("--continue and --resume cannot be used together")

    if continue_session and new_session:
        raise typer.BadParameter("--continue and --new-session cannot be used together")

    if continue_session and session_name is not None:
        raise typer.BadParameter("--continue and --name cannot be used together")

    if resume_picker and new_session:
        raise typer.BadParameter("--resume and --new-session cannot be used together")

    if resume_picker and session_name is not None:
        raise typer.BadParameter("--resume and --name cannot be used together")

    if no_session and session is not None:
        raise typer.BadParameter("--no-session and --session cannot be used together")

    if no_session and resume_picker:
        raise typer.BadParameter("--no-session and --resume cannot be used together")

    if no_session and continue_session:
        raise typer.BadParameter("--no-session and --continue cannot be used together")

    if no_session and new_session:
        raise typer.BadParameter("--no-session and --new-session cannot be used together")

    if no_session and session_name is not None:
        raise typer.BadParameter("--no-session and --name cannot be used together")

    if prompt_option is not None:
        raise typer.BadParameter(
            "--prompt was removed. Pass the prompt positionally and use --print, e.g. "
            f'`tau --print "{prompt_option}"`.'
        )

    if output is not None:
        raise typer.BadParameter(
            f"--output was renamed to --mode. Use `tau --mode {output.value}` instead."
        )

    if session_name is not None and not session_name.strip():
        raise typer.BadParameter("--name requires a non-empty value")

    startup_default_project_trust = _resolve_startup_project_trust_override(
        approve_project=approve_project,
        no_approve_project=no_approve_project,
    )
    print_requested = print_mode or mode is not None
    effective_output = mode or PrintOutputMode.text

    if continue_session and print_requested:
        raise typer.BadParameter("--continue is supported for TUI startup only")

    if resume_picker and print_requested:
        raise typer.BadParameter("--resume is supported for TUI startup only")

    if fork_session_ref is not None and print_requested:
        raise typer.BadParameter("--fork is supported for TUI startup only")

    tool_allowlist = _parse_csv_option(tools, flag_name="--tools")
    tool_denylist = _parse_csv_option(exclude_tools, flag_name="--exclude-tools") or ()
    startup_thinking_level = _parse_startup_thinking_level(thinking)
    startup_cwd = cwd or Path.cwd()
    resolved_system_prompt = _resolve_prompt_input_option(
        system_prompt,
        cwd=startup_cwd,
        flag_name="--system-prompt",
    )
    resolved_append_system_prompt = _resolve_append_system_prompt_option(
        append_system_prompt,
        cwd=startup_cwd,
    )
    resolved_skill_paths = _resolve_cli_resource_paths(skill_paths, cwd=startup_cwd)
    resolved_prompt_template_paths = _resolve_cli_resource_paths(
        prompt_template_paths,
        cwd=startup_cwd,
    )
    resolved_theme_paths = _resolve_cli_resource_paths(theme_paths, cwd=startup_cwd)
    resolved_extension_paths = _resolve_cli_resource_paths(extension_paths, cwd=startup_cwd)

    provider_settings_override = None
    if model_patterns is not None:
        if print_requested:
            raise typer.BadParameter("--models is supported for TUI startup only")
        try:
            provider_settings_override = scoped_settings_from_model_patterns(
                load_provider_settings(),
                model_patterns,
                provider_name=provider,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc

    raw_positional_args = [*(prompt_args or []), *ctx.args]
    positional_args, extension_flag_values = _split_startup_extension_flags(
        raw_positional_args,
    )
    command = positional_args[0] if positional_args else None

    if command is not None and _manual_command_help_requested(positional_args[1:]):
        manual_help = _manual_command_help(command)
        if manual_help is not None:
            typer.echo(manual_help)
            raise typer.Exit()

    if list_models:
        if print_requested:
            raise typer.BadParameter("--list-models cannot be combined with --print/--mode")
        render_model_list(
            load_provider_settings(),
            provider_name=provider,
            search=_list_models_search_from_args(positional_args),
        )
        raise typer.Exit()

    if export:
        if print_requested:
            raise typer.BadParameter("--export cannot be combined with --print/--mode.")
        _run_export_cli(positional_args, session_manager=_session_manager_from_dir(session_dir))

    if not print_requested and not export and command == "update":
        if len(positional_args) != 1:
            raise typer.BadParameter("Usage: tau update")
        update_command()
        raise typer.Exit()

    if not print_requested and command == "workflows":
        if "--help" in positional_args[1:]:
            workflows_command = typer.main.get_command(workflows_app)
            workflows_command.main(
                args=positional_args[1:],
                prog_name="tau workflows",
                standalone_mode=False,
            )
            raise typer.Exit()
        try:
            payload, json_output = _dispatch_workflows_cli(positional_args[1:])
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if json_output or positional_args[1:2] == ["run"]:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        elif positional_args[1:2] == ["list"]:
            for workflow in payload["workflows"]:
                typer.echo(
                    f"rung {workflow['rung']}\t{workflow['workflow_id']}\t"
                    f"{workflow['topology']}\t{workflow['title']}"
                )
        else:
            typer.echo(f"{payload['title']} ({payload['workflow_id']})")
            typer.echo(str(payload["summary"]))
            typer.echo(f"Rung: {payload['rung']}")
            typer.echo(f"Topology: {payload['topology']}")
        if payload.get("ok") is False:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "runs":
        try:
            payload, json_output = _parse_runs_cli_args(positional_args[1:])
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if json_output:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            _echo_runs_list(payload)
        raise typer.Exit()

    if not print_requested and not export and command == "sessions" and len(positional_args) == 1:
        render_session_list(_session_manager_from_dir(session_dir).list_sessions())
        raise typer.Exit()

    if not print_requested and not export and command == "status":
        try:
            status_options = _parse_status_cli_args(positional_args[1:])
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        status_payload = anyio.run(
            status_command,
            startup_cwd,
            _session_manager_from_dir(session_dir),
            status_options["session_id"],
        )
        if status_options["json_output"]:
            typer.echo(json.dumps(status_payload, indent=2, sort_keys=True))
        else:
            render_status_payload(status_payload)
        if not status_payload.get("ok"):
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and not export and command == "replacement-harness-sanity":
        try:
            sanity_options = _parse_replacement_harness_sanity_cli_args(positional_args[1:])
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        payload = anyio.run(
            replacement_harness_sanity_command,
            startup_cwd,
            sanity_options["run_dir"],
        )
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and not export and command == "export":
        try:
            session_ref, output_path, export_format = _parse_export_cli_args(positional_args[1:])
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        try:
            exported_path = anyio.run(
                export_session_command,
                session_ref,
                output_path,
                export_format,
                _session_manager_from_dir(session_dir),
            )
        except (RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"Exported session to {exported_path}")
        raise typer.Exit()

    if not print_requested and command == "providers" and len(positional_args) == 1:
        providers_command()
        raise typer.Exit()

    if not print_requested and command == "doctor":
        try:
            _parse_doctor_cli_args(positional_args[1:])
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        payload = doctor_command(repo_root=Path(__file__).resolve().parents[2])
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if not payload.get("ok"):
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-view-capabilities":
        if positional_args[1:] not in ([], ["--json"]):
            raise typer.BadParameter("Usage: tau dag-view-capabilities [--json]")
        typer.echo(json.dumps(viewer_capabilities(), indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command in {"dag-view-snapshot", "dag-view-events"}:
        try:
            options = _parse_dag_view_cli_args(positional_args[1:], command=str(command))
            replay, events = load_dag_replay(
                run_dir=Path(str(options["run_dir"])), run_id=options.get("run_id")
            )
            if command == "dag-view-snapshot":
                run_dir = Path(str(options["run_dir"]))
                payload = build_dag_live_snapshot(
                    replay=replay,
                    recent_events=events,
                    receipt_index=build_receipt_index(run_dir, replay.transition_receipts),
                )
            else:
                after = int(options["after_sequence"])
                limit = int(options["limit"])
                payload = build_dag_live_events(
                    replay=replay,
                    events=events,
                    after_sequence=after,
                    limit=limit,
                )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command in {"dag-view", "dag-view-serve"}:
        try:
            options = _parse_dag_view_serve_cli_args(positional_args[1:], command=str(command))
            viewer_server = create_dag_viewer_server(
                run_dir=Path(str(options["run_dir"])),
                run_id=_optional_str(options.get("run_id")),
                host=str(options["host"]),
                port=int(options["port"]),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(viewer_server.receipt(), indent=2, sort_keys=True))
        should_open = bool(options["open"])
        if should_open:
            webbrowser.open(viewer_server.url)
        with suppress(KeyboardInterrupt):
            viewer_server.serve_forever()
        raise typer.Exit()

    if not print_requested and command == "init":
        try:
            options = _parse_init_cli_args(positional_args[1:])
            payload = initialize_tau_project(
                out_dir=Path(str(options["out"])),
                profile=str(options["profile"]),
                force=bool(options["force"]),
            )
        except (RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "project":
        try:
            options = _parse_project_cli_args(positional_args[1:])
            if options["subcommand"] == "check-spine":
                payload = write_project_spine_check_receipt(
                    spine_path=Path(str(options["spine"])),
                    out=Path(str(options["out"])),
                )
            else:
                raise RuntimeError(f"unsupported project subcommand: {options['subcommand']}")
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "demo":
        try:
            options = _parse_demo_cli_args(positional_args[1:])
            name = str(options.pop("name"))
            if name == "airgap-itar-basic":
                payload = run_demo_airgap_itar_basic(**options)
            elif name == "embry-sparta-airgap":
                payload = run_demo_embry_sparta_airgap(**options)
            else:
                raise RuntimeError(f"unsupported demo: {name}")
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "zero-trust-doctor":
        try:
            options = _parse_zero_trust_doctor_cli_args(positional_args[1:])
            payload = write_zero_trust_preflight_receipt(
                policy_profile_path=Path(str(options["policy_profile"])),
                data_boundary_path=(
                    Path(str(options["data_boundary"]))
                    if options.get("data_boundary") is not None
                    else None
                ),
                dag_contract_path=(
                    Path(str(options["dag_contract"]))
                    if options.get("dag_contract") is not None
                    else None
                ),
                receipt_path=(
                    Path(str(options["receipt"])) if options.get("receipt") is not None else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "setup" and len(positional_args) == 1:
        setup_command(
            provider_name=provider or DEFAULT_PROVIDER_NAME,
            base_url=setup_base_url,
            api_key_env=setup_api_key_env,
            model=model or DEFAULT_MODEL,
            timeout_seconds=setup_timeout_seconds,
            max_retries=setup_max_retries,
            max_retry_delay_seconds=setup_max_retry_delay_seconds,
            set_default=setup_default,
        )
        raise typer.Exit()

    if not print_requested and command == "setup-chutes" and len(positional_args) == 1:
        setup_chutes_command(
            model=model,
            timeout_seconds=setup_timeout_seconds,
            max_retries=setup_max_retries,
            max_retry_delay_seconds=setup_max_retry_delay_seconds,
            set_default=setup_default,
        )
        raise typer.Exit()

    if not print_requested and command == "traycer":
        try:
            if len(positional_args) >= 2 and positional_args[1] == "validate":
                options = parse_traycer_validate_cli_args(positional_args[2:])
                payload = traycer_validate_command(options)
            else:
                raise RuntimeError(
                    "Usage: tau traycer validate --trace <trace.jsonl> "
                    "--handoff <final-handoff.json> --active-goal-hash <sha256:...> "
                    "[--required-evidence <required-evidence.json> | "
                    "--start-handoff <start-handoff.json>] "
                    "[--advisory-final-handoff-evidence] --receipt <monitor-receipt.json>"
                )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if not payload.get("ok"):
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "loop2-validate":
        try:
            run_dir = _parse_loop2_run_dir_cli_args(positional_args[1:], command="loop2-validate")
            ok = validate_loop_receipt_command(run_dir, loop2_src=loop2_src)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "loop2-validate-contract":
        try:
            contract_path = _parse_loop2_contract_cli_args(positional_args[1:])
            ok = validate_loop2_contract_command(contract_path, loop2_src=loop2_src)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "loop2-validate-native":
        try:
            run_dir = _parse_loop2_run_dir_cli_args(
                positional_args[1:],
                command="loop2-validate-native",
            )
            ok = validate_native_loop2_run_command(run_dir, loop2_src=loop2_src)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "loop2-run":
        try:
            contract_path = _parse_loop2_run_contract_cli_args(positional_args[1:])
            ok = anyio.run(
                run_loop2_contract_command,
                contract_path,
                model,
                output,
                provider,
                loop2_src,
                loop2_scillm_doctor_receipt,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "loop2-inspect":
        try:
            run_dir = _parse_loop2_run_dir_cli_args(positional_args[1:], command="loop2-inspect")
            ok = inspect_loop_receipt_command(
                run_dir,
                loop2_src=loop2_src,
                include_validation=loop2_inspect_validate,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "loop2-check-monitor":
        try:
            run_dir = _parse_loop2_run_dir_cli_args(
                positional_args[1:],
                command="loop2-check-monitor",
            )
            ok = check_loop_receipt_monitor_command(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "loop2-emit-peer":
        try:
            run_dir = _parse_loop2_run_dir_cli_args(
                positional_args[1:],
                command="loop2-emit-peer",
            )
            ok = emit_loop_peer_command(
                run_dir,
                switchboard_url=loop2_switchboard_url,
                target_harness=loop2_peer_target,
                monitor_base_url=f"http://{loop2_serve_host}:{loop2_serve_port}",
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "loop2-check-scillm-doctor":
        try:
            receipt_path = _parse_loop2_scillm_doctor_receipt_cli_args(positional_args[1:])
            ok = check_loop2_scillm_doctor_command(receipt_path)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "loop2-backfill-artifacts":
        try:
            run_dir = _parse_loop2_run_dir_cli_args(
                positional_args[1:],
                command="loop2-backfill-artifacts",
            )
            ok = backfill_loop_receipt_artifacts_command(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "loop2-sanity":
        try:
            if len(positional_args) != 1:
                raise RuntimeError("Usage: tau loop2-sanity")
            ok = loop2_sanity_command(
                root_dir=loop2_sanity_root,
                repo=cwd or Path.cwd(),
                loop2_src=loop2_src,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "tui-proof":
        if any(arg in {"--help", "-h"} for arg in positional_args[1:]):
            typer.echo(
                "Usage: tau tui-proof [--out-dir DIR] [--prompt TEXT] "
                "[--run-id RUN_ID] [--route ROUTE] [--next-agent AGENT]"
            )
            raise typer.Exit()
        try:
            options = _parse_tui_proof_cli_args(positional_args[1:])
            ok = tui_proof_command(
                output_dir=options["output_dir"],
                prompt=str(options["prompt"]),
                run_id=str(options["run_id"]),
                route=str(options["route"]),
                next_agent=str(options["next_agent"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "browser-cdp-proof":
        try:
            options = _parse_browser_cdp_proof_cli_args(positional_args[1:])
            ok = browser_cdp_proof_command(
                output_dir=options["output_dir"],
                run_id=str(options["run_id"]),
                surf_bin=options["surf_bin"],
                keep_tab=bool(options["keep_tab"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "visible-dag-poc":
        try:
            options = _parse_visible_dag_poc_cli_args(positional_args[1:])
            payload = run_visible_dag_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "visible-dag-inspect":
        try:
            run_dir = _parse_visible_dag_inspect_cli_args(positional_args[1:])
            payload = inspect_visible_dag_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "provider-pane-poc":
        try:
            options = _parse_provider_pane_poc_cli_args(positional_args[1:])
            payload = run_provider_pane_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "provider-pane-inspect":
        try:
            run_dir = _parse_provider_pane_inspect_cli_args(positional_args[1:])
            payload = inspect_provider_pane_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "provider-readiness-poc":
        try:
            options = _parse_provider_readiness_poc_cli_args(positional_args[1:])
            payload = run_provider_readiness_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "provider-readiness-inspect":
        try:
            run_dir = _parse_provider_readiness_inspect_cli_args(positional_args[1:])
            payload = inspect_provider_readiness_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "local-provider-readiness":
        try:
            options = _parse_local_provider_readiness_cli_args(positional_args[1:])
            payload = write_local_provider_readiness_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "airgap-no-egress":
        try:
            options = _parse_airgap_no_egress_cli_args(positional_args[1:])
            payload = write_airgap_no_egress_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "provider-dag-poc":
        try:
            options = _parse_provider_dag_poc_cli_args(positional_args[1:])
            payload = run_provider_dag_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "provider-dag-plan":
        try:
            options = _parse_provider_dag_plan_cli_args(positional_args[1:])
            payload = plan_provider_dag_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "provider-dag-orchestrate":
        try:
            options = _parse_provider_dag_orchestrate_cli_args(positional_args[1:])
            payload = run_provider_dag_orchestrator(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "provider-dag-inspect":
        try:
            run_dir = _parse_provider_dag_inspect_cli_args(positional_args[1:])
            payload = inspect_provider_dag_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "orchestration-evidence":
        try:
            run_dir = _parse_orchestration_evidence_cli_args(positional_args[1:])
            payload = build_orchestration_evidence(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command in {"dag-run", "run"}:
        try:
            payload = _run_dag_cli_command(positional_args[1:], command_name=str(command))
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "gs001-closure-publish":
        try:
            options = _parse_gs001_closure_publish_cli_args(positional_args[1:])
            payload = publish_gs001_closure_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-plan":
        try:
            source_path, output_path = _parse_dag_plan_cli_args(positional_args[1:])
            payload = write_dag_plan(source_path, output_path=output_path)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command == "dag-template-list":
        typer.echo(json.dumps(dag_template_registry_payload(), indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command == "dag-template-catalog":
        typer.echo(json.dumps(dag_template_catalog_payload(), indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command == "dag-template-describe":
        try:
            options = _parse_dag_template_describe_cli_args(positional_args[1:])
            payload = describe_dag_template(str(options["template"]))
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command == "dag-template-validate":
        try:
            options = _parse_dag_template_params_cli_args(
                positional_args[1:],
                command_name="dag-template-validate",
            )
            payload = validate_dag_template_params(
                str(options["template"]),
                Path(str(options["params"])),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "dag-template-preview":
        try:
            options = _parse_dag_template_params_cli_args(
                positional_args[1:],
                command_name="dag-template-preview",
            )
            payload = preview_dag_template(
                str(options["template"]),
                Path(str(options["params"])),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "dag-template-select":
        try:
            options = _parse_dag_template_select_cli_args(positional_args[1:])
            payload = select_dag_template_from_facts(Path(str(options["facts"])))
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "dag-template-compile":
        try:
            options = _parse_dag_template_compile_cli_args(positional_args[1:])
            payload = write_dag_template_compile_receipt(
                template_name=str(options["template"]),
                params_path=Path(str(options["params"])),
                out_path=Path(str(options["out"])),
                receipt_path=Path(str(options["receipt"])),
                missing_out_path=(
                    Path(str(options["missing_out"]))
                    if options.get("missing_out") is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "dag-signals":
        try:
            options = _parse_dag_signals_cli_args(positional_args[1:])
            payload = write_dag_signal_receipt(
                Path(str(options["source"])),
                receipt_path=options.get("receipt_path"),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "evidence-validate":
        try:
            options = _parse_evidence_validate_cli_args(positional_args[1:])
            payload = write_evidence_validation_receipt(
                manifest_path=Path(str(options["manifest"])),
                receipt_path=options.get("receipt"),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-expansion-validate":
        try:
            options = _parse_dag_expansion_validate_cli_args(positional_args[1:])
            payload = write_dag_expansion_validation_receipt(
                dag_contract_path=Path(str(options["dag_contract"])),
                proposal_path=Path(str(options["proposal"])),
                receipt_path=Path(str(options["receipt"])),
                preview_path=options.get("preview"),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-expansion-policy":
        try:
            options = _parse_dag_expansion_policy_cli_args(positional_args[1:])
            payload = write_dag_expansion_policy_receipt(
                validation_receipt_path=Path(str(options["validation_receipt"])),
                receipt_path=Path(str(options["receipt"])),
                signal_receipt_path=options.get("signal_receipt"),
                require_clean_signal=bool(options["require_clean_signal"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-expansion-apply":
        try:
            options = _parse_dag_expansion_apply_cli_args(positional_args[1:])
            payload = write_dag_expansion_apply_receipt(
                validation_receipt_path=Path(str(options["validation_receipt"])),
                out_path=Path(str(options["out"])),
                receipt_path=Path(str(options["receipt"])),
                policy_receipt_path=options.get("policy_receipt"),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-branch-locks-validate":
        try:
            options = _parse_dag_branch_locks_validate_cli_args(positional_args[1:])
            payload = write_dag_branch_lock_validation_receipt(
                dag_contract_path=Path(str(options["dag_contract"])),
                locks_path=Path(str(options["locks"])),
                receipt_path=Path(str(options["receipt"])),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-motif-validate":
        try:
            options = _parse_dag_motif_validate_cli_args(positional_args[1:])
            payload = write_dag_motif_validation_receipt(
                dag_contract_path=Path(str(options["dag_contract"])),
                motif_path=Path(str(options["motif"])),
                receipt_path=Path(str(options["receipt"])),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-route-memory-candidates":
        try:
            options = _parse_dag_route_memory_candidates_cli_args(positional_args[1:])
            payload = write_dag_route_memory_candidate_receipt(
                signal_receipt_path=Path(str(options["signal_receipt"])),
                receipt_path=Path(str(options["receipt"])),
                min_confidence=float(options["min_confidence"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-route-memory-sync":
        try:
            options = _parse_dag_route_memory_sync_cli_args(positional_args[1:])
            payload = write_dag_route_memory_sync_receipt(
                candidate_receipt_path=Path(str(options["candidate_receipt"])),
                receipt_path=Path(str(options["receipt"])),
                collection=str(options["collection"]),
                memory_url=str(options["memory_url"]),
                apply=bool(options["apply"]),
                memory_auth_token=_optional_str(options.get("memory_auth_token")),
                approval_receipt_path=(
                    Path(str(options["approval_receipt"]))
                    if options["approval_receipt"] is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "memory-intent":
        try:
            options = _parse_memory_intent_cli_args(positional_args[1:])
            payload = write_memory_intent_acquisition_receipt(
                query=str(options["query"]),
                receipt_path=Path(str(options["out"])),
                memory_url=_optional_str(options.get("memory_url")),
                scope=str(options["scope"]),
                app=str(options["app"]),
                fast=bool(options["fast"]),
                goal_hash=_optional_str(options.get("goal_hash")),
                target=_json_object_option(options.get("target"), label="--target-json"),
                timeout_seconds=float(options["timeout_seconds"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "evidence-case-create":
        try:
            options = _parse_evidence_case_create_cli_args(positional_args[1:])
            payload = write_evidence_case_acquisition_receipt(
                intent_path=Path(str(options["intent"])),
                receipt_path=Path(str(options["out"])),
                memory_url=_optional_str(options.get("memory_url")),
                question=_optional_str(options.get("question")),
                scope=str(options["scope"]),
                app=str(options["app"]),
                goal_hash=_optional_str(options.get("goal_hash")),
                target=_json_object_option(options.get("target"), label="--target-json"),
                timeout_seconds=float(options["timeout_seconds"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "skill-chain-recall":
        try:
            options = _parse_skill_chain_recall_cli_args(positional_args[1:])
            payload = write_skill_chain_selection_receipt(
                query=str(options["query"]),
                receipt_path=Path(str(options["out"])),
                memory_url=_optional_str(options.get("memory_url")),
                scope=str(options["scope"]),
                app=str(options["app"]),
                k=int(options["k"]),
                goal_hash=_optional_str(options.get("goal_hash")),
                target=_json_object_option(options.get("target"), label="--target-json"),
                fallback_skills=_json_array_option(
                    options.get("fallback_skills"), label="--fallback-skills-json"
                ),
                timeout_seconds=float(options["timeout_seconds"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("status") == "BLOCKED":
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "tool-chain-recall":
        try:
            options = _parse_tool_chain_recall_cli_args(positional_args[1:])
            payload = write_tool_chain_selection_receipt(
                query=str(options["query"]),
                receipt_path=Path(str(options["out"])),
                memory_url=_optional_str(options.get("memory_url")),
                scope=str(options["scope"]),
                app=str(options["app"]),
                k=int(options["k"]),
                goal_hash=_optional_str(options.get("goal_hash")),
                target=_json_object_option(options.get("target"), label="--target-json"),
                timeout_seconds=float(options["timeout_seconds"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command == "dag-inspect":
        try:
            run_dir = _parse_generic_dag_inspect_cli_args(positional_args[1:])
            payload = inspect_generic_dag_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-resume":
        try:
            run_dir = _parse_generic_dag_resume_cli_args(positional_args[1:])
            payload = resume_generic_dag_from_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-retention-expire":
        try:
            options = _parse_dag_retention_expire_cli_args(positional_args[1:])
            payload = expire_dag_run_directories(
                root=Path(str(options["root"])),
                archive_dir=Path(str(options["archive_dir"])),
                keep_count=(
                    int(options["keep_count"]) if options["keep_count"] is not None else None
                ),
                older_than_days=(
                    float(options["older_than_days"])
                    if options["older_than_days"] is not None
                    else None
                ),
                dry_run=bool(options["dry_run"]),
                receipt_path=(
                    Path(str(options["receipt"])) if options["receipt"] is not None else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-reconcile":
        try:
            options = _parse_dag_reconcile_cli_args(positional_args[1:])
            payload = _write_dag_reconciliation_decision_receipt(
                run_dir=Path(str(options["run_dir"])),
                decision=str(options["decision"]),
                operator_id=str(options["operator_id"]),
                reason=str(options["reason"]),
                receipt_path=(
                    Path(str(options["receipt"])) if options["receipt"] is not None else None
                ),
                run_id=_optional_str(options.get("run_id")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-clear-lease":
        try:
            options = _parse_dag_clear_lease_cli_args(positional_args[1:])
            payload = _write_dag_clear_lease_receipt(
                run_dir=Path(str(options["run_dir"])),
                run_id=str(options["run_id"]),
                operator_id=str(options["operator_id"]),
                reason=str(options["reason"]),
                receipt_path=(
                    Path(str(options["receipt"])) if options["receipt"] is not None else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "generic-provider-dag-node":
        try:
            options = _parse_generic_provider_dag_node_cli_args(positional_args[1:])
            payload = run_generic_provider_dag_node(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("status") != "PASS":
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-stress-poc":
        try:
            options = _parse_dag_stress_poc_cli_args(positional_args[1:])
            payload = run_dag_stress_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-stress-inspect":
        try:
            run_dir = _parse_dag_stress_inspect_cli_args(positional_args[1:])
            payload = inspect_dag_stress_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-stress-campaign":
        try:
            options = _parse_dag_stress_campaign_cli_args(positional_args[1:])
            payload = run_dag_stress_campaign(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-stress-campaign-inspect":
        try:
            run_dir = _parse_dag_stress_campaign_inspect_cli_args(positional_args[1:])
            payload = inspect_dag_stress_campaign(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "media-explainer-smoke":
        try:
            options = _parse_media_explainer_smoke_cli_args(positional_args[1:])
            payload = run_media_explainer_smoke(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "media-explainer-inspect":
        try:
            run_dir = _parse_media_explainer_inspect_cli_args(positional_args[1:])
            payload = inspect_media_explainer_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "herdr-cleanup":
        try:
            options = _parse_herdr_cleanup_cli_args(positional_args[1:])
            if options.pop("gc"):
                options.pop("mode", None)
                options.pop("workspace_lease_path", None)
                options.pop("session_ownership_path", None)
                payload = run_herdr_gc(**options)
            else:
                options.pop("apply", None)
                options.pop("approval_receipt_path", None)
                payload = run_herdr_cleanup(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "approval-gate-check":
        try:
            options = _parse_approval_gate_check_cli_args(positional_args[1:])
            payload = evaluate_approval_gate(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "permission-request":
        try:
            options = _parse_permission_request_cli_args(positional_args[1:])
            payload = write_permission_request_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "permission-reply":
        try:
            options = _parse_permission_reply_cli_args(positional_args[1:])
            payload = write_permission_reply_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "run-status":
        try:
            run_dir = _parse_run_status_cli_args(positional_args[1:])
            payload = build_run_status(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "dag-viewer-link":
        try:
            run_dir = _parse_dag_viewer_link_cli_args(positional_args[1:])
            payload = build_dag_viewer_link(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "compliance-package":
        try:
            options = _parse_compliance_package_cli_args(positional_args[1:])
            payload = build_compliance_evidence_package(
                run_dir=Path(str(options["run_dir"])),
                out_dir=Path(str(options["out"])),
                force=bool(options["force"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "actor-manifest":
        try:
            options = _parse_actor_manifest_cli_args(positional_args[1:])
            payload = build_actor_manifest(
                run_id=str(options["run_id"]),
                actors=[parse_actor_spec(str(spec)) for spec in options["actors"]],
                output_path=Path(str(options["out"])) if options.get("out") is not None else None,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "environment-manifest":
        try:
            options = _parse_environment_manifest_cli_args(positional_args[1:])
            payload = build_environment_manifest(
                run_id=str(options["run_id"]),
                network_policy=str(options["network_policy"]),
                provider_access=str(options["provider_access"]),
                mounted_paths=[str(item) for item in options["mounted_paths"]],
                secrets_visible=[str(item) for item in options["secrets_visible"]],
                tool_versions=dict(options["tool_versions"]),
                policy_profile=(
                    str(options["policy_profile"])
                    if options.get("policy_profile") is not None
                    else None
                ),
                data_boundary=(
                    str(options["data_boundary"])
                    if options.get("data_boundary") is not None
                    else None
                ),
                output_path=Path(str(options["out"])) if options.get("out") is not None else None,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "sign-receipt":
        try:
            options = _parse_sign_receipt_cli_args(positional_args[1:])
            payload = sign_receipt(
                receipt_path=Path(str(options["receipt"])),
                key_path=Path(str(options["key"])),
                output_path=Path(str(options["out"])) if options.get("out") is not None else None,
                actor_manifest_path=(
                    Path(str(options["actor_manifest"]))
                    if options.get("actor_manifest") is not None
                    else None
                ),
                environment_manifest_path=(
                    Path(str(options["environment_manifest"]))
                    if options.get("environment_manifest") is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "proof-index":
        try:
            options = _parse_proof_index_cli_args(positional_args[1:])
            payload = build_proof_index(
                Path(str(options["proofs_dir"])),
                output_path=Path(str(options["output_path"])),
                receipt_path=(
                    Path(str(options["receipt_path"]))
                    if options.get("receipt_path") is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "verify-signed-receipt":
        try:
            options = _parse_verify_signed_receipt_cli_args(positional_args[1:])
            payload = verify_signed_receipt(
                signed_receipt_path=Path(str(options["signed_receipt"])),
                key_path=Path(str(options["key"])),
                output_path=Path(str(options["out"])) if options.get("out") is not None else None,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "sandbox-run":
        try:
            options = _parse_sandbox_run_cli_args(positional_args[1:])
            payload = run_sandboxed_command(
                command=[str(item) for item in options["command"]],
                policy_profile_path=Path(str(options["policy_profile"])),
                data_boundary_path=Path(str(options["data_boundary"])),
                receipt_path=Path(str(options["out"])) if options.get("out") is not None else None,
                goal_hash=_optional_str(options.get("goal_hash")),
                work_order_sha256=_optional_str(options.get("work_order_sha256")),
                timeout_seconds=float(options["timeout_seconds"]),
                backend=str(options["backend"]),
                image=_optional_str(options.get("image")),
                stdin_text=(
                    Path(str(options["stdin_file"])).read_text(encoding="utf-8")
                    if options.get("stdin_file") is not None
                    else None
                ),
                work_dir=(
                    Path(str(options["work_dir"])) if options.get("work_dir") is not None else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "report":
        try:
            options = _parse_report_cli_args(positional_args[1:])
            payload = write_run_report(
                run_dir=Path(str(options["run_dir"])),
                out_path=Path(str(options["out"])),
                force=bool(options["force"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "serve":
        try:
            options = _parse_serve_cli_args(positional_args[1:])
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(
            json.dumps(
                {
                    "schema": "tau.serve_start_receipt.v1",
                    "ok": True,
                    "status": "PASS",
                    "mocked": False,
                    "live": True,
                    "provider_live": False,
                    "host": options["host"],
                    "port": options["port"],
                    "proof_scope": {
                        "proves": ["Tau started a local self-hosted API process."],
                        "does_not_prove": [
                            "Production deployment readiness.",
                            "Provider/model semantic quality.",
                            "Sandbox enforcement.",
                        ],
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        serve_tau_api(
            host=str(options["host"]),
            port=int(options["port"]),
            doctor_handler=lambda: doctor_command(repo_root=Path(__file__).resolve().parents[2]),
        )
        raise typer.Exit()

    if not print_requested and command == "dag-fail-closed-registry":
        try:
            output_path = _parse_dag_fail_closed_registry_args(positional_args[1:])
            payload = write_fail_closed_registry_receipt(output_path=output_path)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "course-correction":
        try:
            options = _parse_course_correction_cli_args(positional_args[1:])
            payload = write_course_correction_receipt(
                Path(str(options["out"])),
                trigger=str(options["trigger"]),
                run_id=_optional_str(options.get("run_id")),
                dag_id=_optional_str(options.get("dag_id")),
                goal_hash=_optional_str(options.get("goal_hash")),
                target=_json_object_option(options.get("target"), label="--target-json"),
                node_id=_optional_str(options.get("node_id")),
                agent=_optional_str(options.get("agent")),
                attempt=_optional_int(options.get("attempt")),
                observed_state=_json_object_option(
                    options.get("observed_state"),
                    label="--observed-state-json",
                ),
                observed_artifact_path=(
                    Path(str(options["observed_artifact"]))
                    if _optional_str(options.get("observed_artifact"))
                    else None
                ),
                errors=[str(item) for item in options["error"]],
                reason=_optional_str(options.get("reason")),
                stop_reason=_optional_str(options.get("stop_reason")),
                mocked=bool(options["mocked"]),
                live=bool(options["live"]),
                provider_live=bool(options["provider_live"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("next_allowed") is False else 0)

    if not print_requested and command == "code-patch":
        try:
            options = _parse_code_patch_cli_args(positional_args[1:])
            payload = apply_code_patch_receipt(
                patch_path=Path(str(options["patch"])),
                repo_root=Path(str(options["repo"])),
                receipt_path=Path(str(options["out"])) if options.get("out") is not None else None,
                expected_goal_hash=_optional_str(options.get("goal_hash")),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
                zero_trust=bool(options["zero_trust"]),
                apply=not bool(options["dry_run"]),
                run_id=_optional_str(options.get("run_id")),
                dag_id=_optional_str(options.get("dag_id")),
                node_id=_optional_str(options.get("node_id")),
                agent=_optional_str(options.get("agent")),
                attempt=_optional_int(options.get("attempt")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "review-findings":
        try:
            options = _parse_review_findings_cli_args(positional_args[1:])
            payload = write_review_findings_receipt(
                findings_path=Path(str(options["findings"])),
                receipt_path=Path(str(options["out"])) if options.get("out") is not None else None,
                expected_goal_hash=_optional_str(options.get("goal_hash")),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "lsp-diagnostics":
        try:
            options = _parse_lsp_diagnostics_cli_args(positional_args[1:])
            payload = write_lsp_diagnostics_receipt(
                workspace=Path(str(options["workspace"])),
                output_path=Path(str(options["out"])),
                goal_hash=_optional_str(options.get("goal_hash")),
                required=bool(options["required"]),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
                baseline_receipt_path=(
                    Path(str(options["baseline_receipt"]))
                    if options.get("baseline_receipt") is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "lsp-symbols":
        try:
            options = _parse_lsp_symbols_cli_args(positional_args[1:])
            payload = write_lsp_symbol_receipt(
                workspace=Path(str(options["workspace"])),
                query=str(options["query"]),
                output_path=Path(str(options["out"])),
                goal_hash=_optional_str(options.get("goal_hash")),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "lsp-rename-plan":
        try:
            options = _parse_lsp_rename_plan_cli_args(positional_args[1:])
            payload = write_lsp_rename_plan_receipt(
                workspace=Path(str(options["workspace"])),
                symbol=str(options["symbol"]),
                new_name=str(options["new_name"]),
                output_path=Path(str(options["out"])),
                goal_hash=_optional_str(options.get("goal_hash")),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "test-run":
        try:
            options = _parse_test_run_cli_args(positional_args[1:])
            payload = write_test_run_receipt(
                repo=Path(str(options["repo"])),
                output_path=Path(str(options["out"])),
                command=[str(item) for item in options["command"]],
                tested_paths=[str(item) for item in options["tested_paths"]],
                goal_hash=_optional_str(options.get("goal_hash")),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
                timeout_s=int(options["timeout_s"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "commit-plan":
        try:
            options = _parse_commit_plan_cli_args(positional_args[1:])
            payload = write_commit_plan_receipt(
                repo=Path(str(options["repo"])),
                output_path=Path(str(options["out"])),
                goal_hash=_optional_str(options.get("goal_hash")),
                apply=bool(options["apply"]),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
                evidence_receipt_paths=[Path(str(path)) for path in options["evidence_receipts"]],
                approval_receipt_path=(
                    Path(str(options["approval_receipt"]))
                    if options.get("approval_receipt")
                    else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "orchestration-reliability":
        try:
            options = _parse_orchestration_reliability_cli_args(positional_args[1:])
            payload = write_orchestration_reliability_receipt(
                output_path=Path(str(options["out"])),
                run_dir=(
                    Path(str(options["run_dir"])) if options.get("run_dir") is not None else None
                ),
                dag_receipt_path=(
                    Path(str(options["dag_receipt"]))
                    if options.get("dag_receipt") is not None
                    else None
                ),
                required_receipts=[Path(str(path)) for path in options["required_receipts"]],
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "omp-worker-validate":
        try:
            options = _parse_worker_validate_cli_args(
                positional_args[1:],
                command="omp-worker-validate",
            )
            payload = write_omp_worker_receipt(
                work_order_path=Path(str(options["work_order"])),
                result_path=Path(str(options["result"])),
                output_path=Path(str(options["out"])),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "scillm-worker-validate":
        try:
            options = _parse_worker_validate_cli_args(
                positional_args[1:],
                command="scillm-worker-validate",
            )
            payload = write_scillm_worker_receipt(
                work_order_path=Path(str(options["work_order"])),
                result_path=Path(str(options["result"])),
                output_path=Path(str(options["out"])),
                launch_receipt_path=(
                    Path(str(options["launch_receipt"]))
                    if options.get("launch_receipt") is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "omp-worker-launch":
        try:
            options = _parse_omp_worker_launch_cli_args(positional_args[1:])
            payload = write_omp_worker_launch_receipt(
                work_order_path=Path(str(options["work_order"])),
                output_path=Path(str(options["out"])),
                caller_skill=str(options["caller_skill"]),
                apply=bool(options["apply"]),
                omp_bin=str(options["omp_bin"]),
                timeout_s=int(options["timeout_s"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "omp-worker-doctor":
        try:
            options = _parse_omp_worker_doctor_cli_args(positional_args[1:])
            payload = write_omp_worker_doctor_receipt(
                output_path=Path(str(options["out"])),
                omp_bin=str(options["omp_bin"]),
                timeout_s=int(options["timeout_s"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "scillm-worker-launch":
        try:
            options = _parse_scillm_worker_launch_cli_args(positional_args[1:])
            payload = write_scillm_worker_launch_receipt(
                work_order_path=Path(str(options["work_order"])),
                output_path=Path(str(options["out"])),
                scillm_base_url=str(options["scillm_base_url"]),
                caller_skill=str(options["caller_skill"]),
                apply=bool(options["apply"]),
                auth_token=_optional_str(options.get("auth_token")),
                request_timeout_s=int(options["request_timeout_s"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "scillm-chat-review":
        try:
            options = _parse_scillm_chat_review_cli_args(positional_args[1:])
            payload = write_scillm_chat_review_receipt(
                request_path=Path(str(options["request"])),
                output_path=Path(str(options["out"])),
                response_output_path=(
                    Path(str(options["response_out"]))
                    if options.get("response_out") is not None
                    else None
                ),
                scillm_base_url=str(options["scillm_base_url"]),
                caller_skill=str(options["caller_skill"]),
                apply=bool(options["apply"]),
                auth_token=_optional_str(options.get("auth_token")),
                request_timeout_s=int(options["request_timeout_s"]),
                timeout_diagnosis_mode=str(options["timeout_diagnosis_mode"]),
                timeout_diagnosis_timeout_s=int(options["timeout_diagnosis_timeout_s"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "pdf-lab-second-pass-review":
        try:
            options = _parse_pdf_lab_second_pass_review_cli_args(positional_args[1:])
            payload = write_pdf_lab_second_pass_review_receipt(
                contract_path=Path(str(options["contract"])),
                output_path=Path(str(options["out"])),
                artifact_root=(
                    Path(str(options["artifact_root"]))
                    if options.get("artifact_root") is not None
                    else None
                ),
                scillm_base_url=str(options["scillm_base_url"]),
                caller_skill=str(options["caller_skill"]),
                apply=bool(options["apply"]),
                auth_token=_optional_str(options.get("auth_token")),
                request_timeout_s=int(options["request_timeout_s"]),
                timeout_diagnosis_mode=str(options["timeout_diagnosis_mode"]),
                timeout_diagnosis_timeout_s=int(options["timeout_diagnosis_timeout_s"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "debug-session-receipt":
        try:
            options = _parse_debug_session_receipt_cli_args(positional_args[1:])
            payload = write_debug_session_receipt(
                session_path=Path(str(options["session"])),
                output_path=Path(str(options["out"])),
                required=bool(options["required"]),
                expected_goal_hash=_optional_str(options.get("goal_hash")),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "github-read":
        try:
            options = _parse_github_read_cli_args(positional_args[1:])
            payload = write_github_read_receipt(
                uri=str(options["uri"]),
                output_path=Path(str(options["out"])),
                goal_hash=_optional_str(options.get("goal_hash")),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
                execute=bool(options["execute"]),
                gh_bin=str(options["gh_bin"]),
                timeout_s=int(options["timeout_s"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if not print_requested and command == "herdr-observation-gate":
        try:
            options = _parse_herdr_observation_gate_cli_args(positional_args[1:])
            payload = write_herdr_observation_gate_receipt(
                Path(str(options["out"])),
                snapshot_path=Path(str(options["snapshot"])),
                expected_receipt_path=(
                    Path(str(options["expected_receipt"]))
                    if options.get("expected_receipt") is not None
                    else None
                ),
                expected_workspace_id=_optional_str(options.get("expected_workspace_id")),
                expected_pane_id=_optional_str(options.get("expected_pane_id")),
                expected_terminal_id=_optional_str(options.get("expected_terminal_id")),
                run_id=_optional_str(options.get("run_id")),
                dag_id=_optional_str(options.get("dag_id")),
                goal_hash=_optional_str(options.get("goal_hash")),
                node_id=_optional_str(options.get("node_id")),
                agent=_optional_str(options.get("agent")),
                attempt=_optional_int(options.get("attempt")),
                receipt_overdue=bool(options["receipt_overdue"]),
                receipt_timeout_seconds=(
                    float(options["receipt_timeout_seconds"])
                    if options.get("receipt_timeout_seconds") is not None
                    else None
                ),
                mocked=bool(options["mocked"]),
                live=bool(options["live"]),
                provider_live=bool(options["provider_live"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("ok") is not True else 0)

    if not print_requested and command == "project-profile-validate":
        try:
            options = _parse_project_profile_validate_cli_args(positional_args[1:])
            payload = write_project_profile_validation_receipt(
                profile_path=Path(str(options["profile"])),
                output_path=Path(str(options["out"])),
                capability_registry_path=(
                    Path(str(options["registry"])) if options.get("registry") is not None else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("ok") is not True else 0)

    if not print_requested and command == "skill-capability-registry-validate":
        try:
            options = _parse_skill_capability_registry_validate_cli_args(positional_args[1:])
            payload = write_skill_capability_registry_validation_receipt(
                registry_path=Path(str(options["registry"])),
                output_path=Path(str(options["out"])),
                skills_root=(
                    Path(str(options["skills_root"]))
                    if options.get("skills_root") is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("ok") is not True else 0)

    if not print_requested and command == "skill-capability-registry-default":
        try:
            options = _parse_skill_capability_registry_default_cli_args(positional_args[1:])
            payload = write_default_skill_capability_registry(
                output_path=Path(str(options["out"])),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command == "skill-invocation":
        try:
            options = _parse_skill_invocation_cli_args(positional_args[1:])
            payload = write_skill_invocation_receipt(
                request_path=Path(str(options["request"])),
                output_path=Path(str(options["out"])),
                repo_root=(
                    Path(str(options["repo_root"]))
                    if options.get("repo_root") is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("ok") is not True else 0)

    if not print_requested and command == "debugger-skill-adapter":
        try:
            options = _parse_debugger_skill_adapter_cli_args(positional_args[1:])
            payload = write_debugger_skill_adapter_receipt(
                proof_path=Path(str(options["proof"])),
                output_path=Path(str(options["out"])),
                debug_session_output_path=Path(str(options["debug_session_out"])),
                repo_root=(
                    Path(str(options["repo_root"]))
                    if options.get("repo_root") is not None
                    else None
                ),
                expected_goal_hash=_optional_str(options.get("goal_hash")),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("ok") is not True else 0)

    if not print_requested and command == "code-runner-skill-adapter":
        try:
            options = _parse_code_runner_skill_adapter_cli_args(positional_args[1:])
            payload = write_code_runner_skill_adapter_receipt(
                result_path=Path(str(options["result"])),
                output_path=Path(str(options["out"])),
                repo_root=Path(str(options["repo_root"])),
                expected_goal_hash=_optional_str(options.get("goal_hash")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("ok") is not True else 0)

    if not print_requested and command == "review-code-skill-adapter":
        try:
            options = _parse_review_code_skill_adapter_cli_args(positional_args[1:])
            payload = write_review_code_skill_adapter_receipt(
                review_path=Path(str(options["review"])),
                output_path=Path(str(options["out"])),
                repo_root=Path(str(options["repo_root"])),
                expected_goal_hash=_optional_str(options.get("goal_hash")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("ok") is not True else 0)

    if not print_requested and command == "evidence-case-skill-adapter":
        try:
            options = _parse_evidence_case_skill_adapter_cli_args(positional_args[1:])
            payload = write_evidence_case_skill_adapter_receipt(
                case_path=Path(str(options["case"])),
                output_path=Path(str(options["out"])),
                repo_root=Path(str(options["repo_root"])),
                expected_goal_hash=_optional_str(options.get("goal_hash")),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("ok") is not True else 0)

    if not print_requested and command == "research-skill-adapter":
        try:
            options = _parse_research_skill_adapter_cli_args(positional_args[1:])
            payload = write_research_skill_adapter_receipt(
                report_path=Path(str(options["report"])),
                query_safety_receipt_path=Path(str(options["query_safety"])),
                output_path=Path(str(options["out"])),
                repo_root=Path(str(options["repo_root"])),
                method=str(options["method"]),
                source_type=str(options["source_type"]),
                classification=str(options["classification"]),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("ok") is not True else 0)

    if not print_requested and command == "loop2-serve":
        try:
            run_dir = _parse_loop2_run_dir_cli_args(positional_args[1:], command="loop2-serve")
            serve_loop_receipt_command(
                run_dir,
                host=loop2_serve_host,
                port=loop2_serve_port,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        raise typer.Exit()

    if not print_requested and command == "human-goal-change-bridge":
        try:
            bridge_args = _parse_human_goal_change_bridge_cli_args(positional_args[1:])
            (
                goal_change_path,
                active_goal_hash,
                trusted_human,
                handoff_out,
                receipt_path,
                agents_root,
            ) = bridge_args
            ok = human_goal_change_bridge_command(
                goal_change_path,
                active_goal_hash=active_goal_hash,
                trusted_human=trusted_human,
                handoff_out=handoff_out,
                receipt_path=receipt_path,
                agents_root=agents_root,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-project":
        try:
            handoff_path, active_goal_hash, receipt_path, agents_root = (
                _parse_handoff_project_cli_args(positional_args[1:])
            )
            ok = project_agent_handoff_command(
                handoff_path,
                active_goal_hash=active_goal_hash,
                receipt_path=receipt_path,
                agents_root=agents_root,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-github-transport":
        try:
            (
                handoff_path,
                active_goal_hash,
                receipt_path,
                agents_root,
                apply_github,
                github_apply_policy_receipt,
            ) = _parse_handoff_github_transport_cli_args(positional_args[1:])
            ok = transport_agent_handoff_to_github_command(
                handoff_path,
                active_goal_hash=active_goal_hash,
                receipt_path=receipt_path,
                agents_root=agents_root,
                apply_github=apply_github,
                github_apply_policy_receipt=github_apply_policy_receipt,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "github-redact-projection":
        try:
            projection_path, output_path, receipt_path = _parse_github_redact_projection_args(
                positional_args[1:]
            )
            payload = redact_github_projection(
                projection_path=projection_path,
                output_path=output_path,
                receipt_path=receipt_path,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "github-apply-policy-check":
        try:
            options = _parse_github_apply_policy_check_args(positional_args[1:])
            payload = write_github_apply_policy_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "research-source-receipt":
        try:
            source_path, receipt_path = _parse_research_source_receipt_args(positional_args[1:])
            payload = write_research_source_receipt(
                source_path=source_path,
                receipt_path=receipt_path,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "research-query-gate":
        try:
            options = _parse_research_query_gate_args(positional_args[1:])
            payload = write_research_query_safety_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "itar-access-preflight":
        try:
            options = _parse_itar_access_preflight_args(positional_args[1:])
            payload = write_itar_access_preflight_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "itar-contract-review":
        try:
            options = _parse_itar_contract_review_args(positional_args[1:])
            payload = write_itar_contract_receipt(**options)
        except (RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "sparta-posture-export":
        try:
            options = _parse_sparta_posture_export_args(positional_args[1:])
            payload = write_sparta_posture_contract(**options)
        except (RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("readiness", {}).get("status") != "SIGNOFF_REVIEW_READY":
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "compliance-package-validate":
        try:
            options = _parse_compliance_package_validate_args(positional_args[1:])
            payload = write_compliance_package_validation_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "zero-trust-redteam":
        try:
            run_dir = _parse_zero_trust_redteam_args(positional_args[1:])
            payload = run_zero_trust_redteam(run_dir=run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "orchestration-redteam":
        try:
            run_dir = _parse_orchestration_redteam_args(positional_args[1:])
            payload = run_orchestration_redteam(run_dir=run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "skill-composition-redteam":
        try:
            run_dir = _parse_skill_composition_redteam_args(positional_args[1:])
            payload = run_skill_composition_redteam(run_dir=run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command in {"docker-sandbox-check", "docker-sandbox-run"}:
        try:
            options = _parse_docker_sandbox_check_args(positional_args[1:])
            if command == "docker-sandbox-run":
                options["execute"] = True
            payload = write_docker_sandbox_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "generated-ticket-github-create":
        try:
            (
                ticket_path,
                active_goal_hash,
                receipt_path,
                agents_root,
                apply_github,
                dedupe_preflight_path,
            ) = _parse_generated_ticket_github_create_cli_args(positional_args[1:])
            ok = transport_generated_ticket_to_github_command(
                ticket_path,
                active_goal_hash=active_goal_hash,
                receipt_path=receipt_path,
                agents_root=agents_root,
                apply_github=apply_github,
                dedupe_preflight_path=dedupe_preflight_path,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-command-loop-github-transport":
        try:
            loop_receipt_path, receipt_path, apply_github = (
                _parse_handoff_command_loop_github_transport_args(positional_args[1:])
            )
            ok = transport_handoff_command_loop_terminal_to_github_command(
                loop_receipt_path,
                receipt_path=receipt_path,
                apply_github=apply_github,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "goal-guardian-reconciliation-github-transport":
        try:
            reconciliation_receipt_path, receipt_path, apply_github = (
                _parse_goal_guardian_reconciliation_github_transport_args(positional_args[1:])
            )
            ok = transport_goal_guardian_reconciliation_to_github_command(
                reconciliation_receipt_path,
                receipt_path=receipt_path,
                apply_github=apply_github,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-command-loop-reconciliation-github-transport":
        try:
            loop_receipt_path, receipt_path, apply_github = (
                _parse_handoff_command_loop_reconciliation_github_transport_args(
                    positional_args[1:]
                )
            )
            ok = transport_handoff_command_loop_reconciliation_to_github_command(
                loop_receipt_path,
                receipt_path=receipt_path,
                apply_github=apply_github,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "goal-guardian-ticket-source-github-fetch":
        try:
            repo_name, output_path, receipt_path, execute, state, limit = (
                _parse_goal_guardian_ticket_source_github_fetch_args(positional_args[1:])
            )
            ok = goal_guardian_ticket_source_github_fetch_command(
                repo_name,
                output_path=output_path,
                receipt_path=receipt_path,
                execute=execute,
                state=state,
                limit=limit,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-chain-dry-run":
        try:
            handoff_paths, active_goal_hash, receipt_dir, agents_root = (
                _parse_handoff_chain_cli_args(positional_args[1:])
            )
            ok = project_agent_handoff_chain_command(
                handoff_paths,
                active_goal_hash=active_goal_hash,
                receipt_dir=receipt_dir,
                agents_root=agents_root,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-loop-dry-run":
        try:
            start_path, responses_dir, active_goal_hash, receipt_dir, max_steps, agents_root = (
                _parse_handoff_loop_cli_args(positional_args[1:])
            )
            ok = project_agent_handoff_loop_command(
                start_path,
                responses_dir=responses_dir,
                active_goal_hash=active_goal_hash,
                receipt_dir=receipt_dir,
                max_steps=max_steps,
                agents_root=agents_root,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-dispatch-once":
        try:
            start_path, responses_dir, active_goal_hash, receipt_dir, agents_root = (
                _parse_handoff_dispatch_cli_args(positional_args[1:])
            )
            ok = project_agent_handoff_dispatch_command(
                start_path,
                responses_dir=responses_dir,
                active_goal_hash=active_goal_hash,
                receipt_dir=receipt_dir,
                agents_root=agents_root,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-dispatch-command":
        try:
            start_path, command_spec, active_goal_hash, receipt_dir, agents_root = (
                _parse_handoff_dispatch_command_cli_args(positional_args[1:])
            )
            ok = project_agent_handoff_command_dispatch_command(
                start_path,
                command_spec=command_spec,
                active_goal_hash=active_goal_hash,
                receipt_dir=receipt_dir,
                agents_root=agents_root,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-dispatch-agent-command":
        try:
            start_path, active_goal_hash, receipt_dir, agents_root, command_spec_root = (
                _parse_handoff_dispatch_agent_command_cli_args(positional_args[1:])
            )
            ok = project_agent_handoff_agent_command_dispatch_command(
                start_path,
                active_goal_hash=active_goal_hash,
                receipt_dir=receipt_dir,
                agents_root=agents_root,
                command_spec_root=command_spec_root,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-command-loop":
        try:
            (
                start_path,
                active_goal_hash,
                receipt_dir,
                agents_root,
                command_spec_root,
                command_policy_path,
                goal_guardian_ticket_source,
                max_steps,
            ) = _parse_handoff_command_loop_cli_args(positional_args[1:])
            ok = project_agent_handoff_command_loop_command(
                start_path,
                active_goal_hash=active_goal_hash,
                receipt_dir=receipt_dir,
                agents_root=agents_root,
                command_spec_root=command_spec_root,
                command_policy_path=command_policy_path,
                goal_guardian_ticket_source=goal_guardian_ticket_source,
                max_steps=max_steps,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "goal":
        try:
            options = _parse_goal_cli_args(positional_args[1:])
            payload = run_goal_until_complete(**options)
        except (RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "self-fix":
        try:
            if len(positional_args) > 1 and positional_args[1] == "coder-reviewer-loop":
                options = _parse_self_fix_coder_reviewer_loop_cli_args(positional_args[2:])
                payload = write_coder_reviewer_repair_loop(**options)
                typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                ok = bool(payload.get("ok"))
            else:
                options = _parse_self_fix_cli_args(positional_args[1:])
                if options.pop("_self_fix_mode", "tick") == "poll":
                    ok = project_agent_self_fix_poll_command(**options)
                else:
                    ok = project_agent_self_fix_tick_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "scillm-subagent-gate":
        try:
            summary_path = _parse_scillm_subagent_gate_cli_args(positional_args[1:])
            ok = project_agent_scillm_subagent_gate_command(summary_path)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "ticket-subagent-closure-proof":
        try:
            options = _parse_ticket_subagent_closure_proof_cli_args(positional_args[1:])
            payload = project_agent_ticket_subagent_closure_proof_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("status") != "PASS":
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "persona-dream-panel-proof":
        try:
            options = _parse_persona_dream_panel_proof_cli_args(positional_args[1:])
            ok = project_agent_persona_dream_panel_proof_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if not print_requested and command == "handoff-agent-adapter":
        try:
            options = _parse_handoff_agent_adapter_cli_args(positional_args[1:])
            payload = project_agent_handoff_adapter_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command == "handoff-goal-guardian-adapter":
        try:
            options = _parse_handoff_goal_guardian_adapter_cli_args(positional_args[1:])
            payload = project_agent_handoff_goal_guardian_adapter_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command == "handoff-research-auditor-adapter":
        try:
            payload = project_agent_handoff_research_auditor_adapter_command()
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command == "external-research-receipt":
        try:
            options = _parse_external_research_receipt_cli_args(positional_args[1:])
            payload = project_agent_external_research_receipt_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if not print_requested and command == "subagent-receipt-from-handoff":
        try:
            options = _parse_subagent_receipt_from_handoff_cli_args(positional_args[1:])
            payload = project_agent_subagent_receipt_from_handoff_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    initial_prompt = _build_initial_prompt_from_args(positional_args, cwd=startup_cwd)

    if not print_requested:
        startup_session_id = session
        if fork_session_ref is not None:
            try:
                forked = anyio.run(
                    fork_session_command,
                    fork_session_ref,
                    cwd or Path.cwd(),
                    _session_manager_from_dir(session_dir),
                    session_name,
                    exact_session_id,
                )
            except RuntimeError as exc:
                raise typer.BadParameter(str(exc)) from exc
            startup_session_id = forked.id
            session_name = None
        try:

            async def run_startup_tui() -> str | None:
                kwargs: dict[str, object] = {
                    "model": model,
                    "cwd": startup_cwd,
                    "session_id": startup_session_id,
                    "new_session": new_session,
                    "provider_name": provider,
                    "auto_compact_token_threshold": auto_compact_threshold,
                    "thinking_level": startup_thinking_level,
                    "custom_system_prompt": resolved_system_prompt,
                    "append_system_prompt": resolved_append_system_prompt,
                    "initial_prompt": initial_prompt,
                    "session_name": session_name,
                    "continue_session": continue_session,
                    "resume_picker": resume_picker,
                    "no_session": no_session,
                    "exact_session_id": exact_session_id,
                    "session_dir": session_dir,
                    "provider_settings": provider_settings_override,
                    "default_project_trust": startup_default_project_trust,
                    "no_context_files": no_context_files,
                    "tool_allowlist": tool_allowlist,
                    "tool_denylist": tool_denylist,
                    "no_tools": no_tools,
                    "no_builtin_tools": no_builtin_tools,
                    "no_skills": no_skills,
                    "no_prompt_templates": no_prompt_templates,
                    "no_themes": no_themes,
                    "skill_paths": resolved_skill_paths,
                    "prompt_template_paths": resolved_prompt_template_paths,
                    "theme_paths": resolved_theme_paths,
                }
                if extension_flag_values:
                    kwargs["extension_flag_values"] = extension_flag_values
                if no_extensions or resolved_extension_paths:
                    kwargs["no_extensions"] = no_extensions
                    kwargs["extension_paths"] = resolved_extension_paths
                return await run_openai_tui(**kwargs)  # type: ignore[arg-type]

            resumable_session_id = anyio.run(run_startup_tui)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if resumable_session_id is not None:
            typer.echo(f"To resume this session: tau --session {resumable_session_id}")
        raise typer.Exit()

    prompt = _merge_stdin_prompt(initial_prompt or "")
    if not prompt:
        raise typer.BadParameter(
            'Usage: tau --print "<prompt>" (or --mode text|json|transcript "<prompt>"); '
            "a prompt can also be piped in via stdin"
        )

    try:
        loop_receipt = _loop_receipt_config_from_cli(
            root=loop2_receipt_root,
            node_id=loop2_node_id,
            allowed_globs=loop2_allowed_globs,
            required_changed_globs=loop2_required_changed_globs,
            checks=loop2_checks,
            provider_name=provider,
        )

        async def run_startup_print_mode() -> bool:
            kwargs: dict[str, object] = {
                "prompt": prompt,
                "model": model,
                "cwd": startup_cwd,
                "output": effective_output,
                "provider_name": provider,
                "loop_receipt": loop_receipt,
                "thinking_level": startup_thinking_level,
                "custom_system_prompt": resolved_system_prompt,
                "append_system_prompt": resolved_append_system_prompt,
                "session_name": session_name,
                "no_session": no_session,
                "exact_session_id": exact_session_id,
                "session_dir": session_dir,
                "default_project_trust": startup_default_project_trust,
                "no_context_files": no_context_files,
                "tool_allowlist": tool_allowlist,
                "tool_denylist": tool_denylist,
                "no_tools": no_tools,
                "no_builtin_tools": no_builtin_tools,
                "no_skills": no_skills,
                "no_prompt_templates": no_prompt_templates,
                "no_themes": no_themes,
                "skill_paths": resolved_skill_paths,
                "prompt_template_paths": resolved_prompt_template_paths,
                "theme_paths": resolved_theme_paths,
            }
            if extension_flag_values:
                kwargs["extension_flag_values"] = extension_flag_values
            if no_extensions or resolved_extension_paths:
                kwargs["no_extensions"] = no_extensions
                kwargs["extension_paths"] = resolved_extension_paths
            return await run_openai_print_mode(**kwargs)  # type: ignore[arg-type]

        ok = anyio.run(run_startup_print_mode)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not ok:
        raise typer.Exit(1)


def _parse_csv_option(value: str | None, *, flag_name: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    names = tuple(name.strip() for name in value.split(",") if name.strip())
    if not names:
        raise typer.BadParameter(f"{flag_name} requires at least one non-empty value")
    return names


def _resolve_cli_resource_paths(paths: list[Path] | None, *, cwd: Path) -> tuple[Path, ...]:
    if not paths:
        return ()
    return tuple(path.expanduser() if path.is_absolute() else cwd / path for path in paths)


def _split_startup_extension_flags(
    args: list[str],
) -> tuple[list[str], dict[str, bool | str]]:
    positional: list[str] = []
    extension_flag_values: dict[str, bool | str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg.startswith("--") or arg == "--":
            positional.extend(args[index:])
            break

        raw_flag = arg[2:]
        if not raw_flag:
            positional.extend(args[index:])
            break

        if "=" in raw_flag:
            name, value = raw_flag.split("=", 1)
            extension_flag_values[_normalize_extension_flag_name(name)] = value
            index += 1
            continue

        name = _normalize_extension_flag_name(raw_flag)
        next_arg = args[index + 1] if index + 1 < len(args) else None
        if next_arg is not None and not next_arg.startswith("-") and not next_arg.startswith("@"):
            extension_flag_values[name] = next_arg
            index += 2
            continue

        extension_flag_values[name] = True
        index += 1

    return positional, extension_flag_values


def _normalize_extension_flag_name(name: str) -> str:
    return str(name).strip().removeprefix("--").lower()


def _manual_command_help_requested(args: list[str]) -> bool:
    return "--help" in args or "-h" in args


def _manual_command_help(command: str) -> str | None:
    usage_by_command = {
        "commit-plan": (
            "Usage: tau commit-plan --repo <repo> --out <receipt> "
            "[--evidence-receipt <receipt>] [--approval-receipt <receipt>] [--apply]"
        ),
        "lsp-diagnostics": (
            "Usage: tau lsp-diagnostics --workspace <path> --out <receipt> "
            "[--required] [--baseline-receipt <receipt>] [--zero-trust]"
        ),
        "lsp-rename-plan": (
            "Usage: tau lsp-rename-plan --workspace <path> --symbol <symbol> "
            "--new-name <name> --out <receipt> [--zero-trust]"
        ),
        "lsp-symbols": (
            "Usage: tau lsp-symbols --workspace <path> --query <symbol> "
            "--out <receipt> [--zero-trust]"
        ),
        "review-findings": (
            "Usage: tau review-findings --findings <findings.json> "
            "[--out <receipt>] [--zero-trust]"
        ),
        "dag-run": "Usage: tau dag-run <dag-spec> [--no-resume]",
        "run": "Usage: tau run <dag-spec> [--no-resume]",
        "sandbox-run": (
            "Usage: tau sandbox-run --policy-profile <policy.json> "
            "--data-boundary <boundary.json> [--out <receipt.json>] -- <command...>"
        ),
        "test-run": (
            "Usage: tau test-run --repo <repo> --out <receipt> "
            "[--command <arg>]... [--tested-path <path>]... [--timeout-s <seconds>]"
        ),
    }
    return usage_by_command.get(command)


def _parse_startup_thinking_level(value: str | None) -> ThinkingLevel | None:
    if value is None:
        return None
    if value.strip().lower() == "max":
        return "xhigh"
    try:
        return normalize_thinking_level(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _resolve_prompt_input_option(
    value: str | None,
    *,
    cwd: Path,
    flag_name: str,
) -> str | None:
    if value is None:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    if candidate.exists():
        if not candidate.is_file():
            raise typer.BadParameter(f"{flag_name} path is not a file: {candidate}")
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError as exc:
            raise typer.BadParameter(
                f"Could not read {flag_name} file: {candidate}: {exc}"
            ) from exc
    return value


def _resolve_append_system_prompt_option(
    values: list[str] | None,
    *,
    cwd: Path,
) -> str | None:
    if not values:
        return None
    resolved = [
        _resolve_prompt_input_option(value, cwd=cwd, flag_name="--append-system-prompt")
        for value in values
    ]
    return "\n\n".join(part for part in resolved if part is not None)


def _build_initial_prompt_from_args(args: list[str], *, cwd: Path) -> str | None:
    if not args:
        return None
    messages: list[str] = []
    file_blocks: list[str] = []
    for arg in args:
        if arg.startswith("@") and len(arg) > 1:
            file_blocks.append(_read_startup_file_arg(arg[1:], cwd=cwd))
        else:
            messages.append(arg)
    parts = [block for block in file_blocks if block]
    if messages:
        parts.append(" ".join(messages))
    return "".join(parts) if parts else None


def _list_models_search_from_args(args: list[str]) -> str | None:
    terms = [arg for arg in args if not arg.startswith("@")]
    return " ".join(terms) if terms else None


def _read_startup_file_arg(value: str, *, cwd: Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    path = path.resolve()
    if not path.exists():
        raise typer.BadParameter(f"File not found: {path}")
    if not path.is_file():
        raise typer.BadParameter(f"File argument is not a file: {path}")
    if path.stat().st_size == 0:
        return ""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise typer.BadParameter(f"Could not read file {path}: {exc}") from exc
    return f'<file name="{path}">\n{content}\n</file>\n'


def _resolve_startup_project_trust_override(
    *,
    approve_project: bool,
    no_approve_project: bool,
) -> DefaultProjectTrust | None:
    if approve_project and no_approve_project:
        raise typer.BadParameter("--approve and --no-approve cannot be used together")
    if approve_project:
        return "always"
    if no_approve_project:
        return "never"
    return None


async def run_openai_tui(
    model: str | None,
    cwd: Path,
    session_id: str | None = None,
    new_session: bool = False,
    provider_name: str | None = None,
    auto_compact_token_threshold: int | None = None,
    thinking_level: ThinkingLevel | None = None,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    initial_prompt: str | None = None,
    session_name: str | None = None,
    continue_session: bool = False,
    resume_picker: bool = False,
    no_session: bool = False,
    exact_session_id: str | None = None,
    session_dir: Path | None = None,
    provider_settings: ProviderSettings | None = None,
    default_project_trust: DefaultProjectTrust | None = None,
    no_context_files: bool = False,
    tool_allowlist: tuple[str, ...] | None = None,
    tool_denylist: tuple[str, ...] = (),
    no_tools: bool = False,
    no_builtin_tools: bool = False,
    no_skills: bool = False,
    no_prompt_templates: bool = False,
    no_themes: bool = False,
    no_extensions: bool = False,
    skill_paths: tuple[Path, ...] = (),
    prompt_template_paths: tuple[Path, ...] = (),
    theme_paths: tuple[Path, ...] = (),
    extension_paths: tuple[Path, ...] = (),
    extension_flag_values: Mapping[str, bool | str] | None = None,
) -> str | None:
    """Run the Textual TUI and return its resumable session id, if any."""
    return await run_tui_app(
        model=model,
        cwd=cwd,
        session_id=session_id,
        new_session=new_session,
        provider_name=provider_name,
        auto_compact_token_threshold=auto_compact_token_threshold,
        thinking_level=thinking_level,
        custom_system_prompt=custom_system_prompt,
        append_system_prompt=append_system_prompt,
        initial_prompt=initial_prompt,
        session_name=session_name,
        continue_session=continue_session,
        resume_picker=resume_picker,
        no_session=no_session,
        exact_session_id=exact_session_id,
        session_manager=_session_manager_from_dir(session_dir),
        provider_settings=provider_settings,
        default_project_trust=default_project_trust,
        no_context_files=no_context_files,
        tool_allowlist=tool_allowlist,
        tool_denylist=tool_denylist,
        no_tools=no_tools,
        no_builtin_tools=no_builtin_tools,
        no_skills=no_skills,
        no_prompt_templates=no_prompt_templates,
        no_themes=no_themes,
        no_extensions=no_extensions,
        skill_paths=skill_paths,
        prompt_template_paths=prompt_template_paths,
        theme_paths=theme_paths,
        extension_paths=extension_paths,
        extension_flag_values=extension_flag_values,
    )


def _session_manager_from_dir(session_dir: Path | None) -> SessionManager:
    if session_dir is None:
        return SessionManager()
    return SessionManager(TauPaths(session_root=session_dir.expanduser().resolve()))


def scoped_settings_from_model_patterns(
    settings: ProviderSettings,
    patterns_text: str,
    *,
    provider_name: str | None = None,
) -> ProviderSettings:
    """Return settings with transient scoped models selected by pattern."""
    patterns = tuple(pattern.strip() for pattern in patterns_text.split(",") if pattern.strip())
    if not patterns:
        raise RuntimeError("--models requires at least one non-empty pattern")
    choices: list[ScopedModelConfig] = []
    seen: set[tuple[str, str]] = set()
    for provider in settings.providers:
        if provider_name is not None and provider.name != provider_name:
            continue
        for model in provider.models:
            row = f"{provider.name}:{model}"
            if not any(
                _model_pattern_matches(pattern, provider.name, model, row) for pattern in patterns
            ):
                continue
            key = (provider.name, model)
            if key in seen:
                continue
            choices.append(ScopedModelConfig(provider=provider.name, model=model))
            seen.add(key)
    if not choices:
        raise RuntimeError(f"No configured models match --models: {patterns_text}")
    return replace(settings, scoped_models=tuple(choices))


def _model_pattern_matches(pattern: str, provider_name: str, model: str, row: str) -> bool:
    normalized = pattern.casefold()
    candidates = (model.casefold(), row.casefold(), f"{provider_name}/{model}".casefold())
    return any(
        fnmatch.fnmatchcase(candidate, normalized) or normalized in candidate
        for candidate in candidates
    )


async def fork_session_command(
    source_ref: str,
    cwd: Path,
    session_manager: SessionManager | None = None,
    title: str | None = None,
    session_id: str | None = None,
) -> CodingSessionRecord:
    """Copy a source session into a new indexed session for the target cwd."""
    manager = session_manager or SessionManager()
    if session_id is not None and manager.get_session(session_id) is not None:
        raise RuntimeError(f"Session already exists with id '{session_id}'")
    source_path, source_record = _resolve_fork_source(source_ref, manager)
    entries = await JsonlSessionStorage(source_path).read_all()
    if not entries:
        raise RuntimeError(f"Fork source has no session entries: {source_ref}")
    state = SessionState.from_entries(entries)
    source_title = source_record.title if source_record is not None else source_path.stem
    record = manager.create_session(
        cwd=cwd,
        model=state.model or (source_record.model if source_record is not None else "unknown"),
        provider_name=source_record.provider_name if source_record is not None else None,
        title=title or (f"Fork of {source_title}" if source_title else None),
        session_id=session_id,
        parent_session_id=source_record.id if source_record is not None else None,
    )
    storage = JsonlSessionStorage(record.path)
    for entry in entries:
        await storage.append(entry)
    return record


def _resolve_fork_source(
    source_ref: str,
    manager: SessionManager,
) -> tuple[Path, CodingSessionRecord | None]:
    candidate_path = Path(source_ref).expanduser()
    if candidate_path.exists():
        if candidate_path.is_dir():
            raise RuntimeError(f"Fork source is a directory: {candidate_path}")
        return candidate_path, None

    record = manager.get_session(source_ref)
    if record is not None:
        return record.path, record

    matches = [record for record in manager.list_sessions() if record.id.startswith(source_ref)]
    if len(matches) == 1:
        match = matches[0]
        return match.path, match
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous session id prefix: {source_ref}")
    raise RuntimeError(f"Unknown session or file: {source_ref}")


def render_session_list(records: list[CodingSessionRecord]) -> None:
    """Render indexed sessions for the CLI."""
    if not records:
        typer.echo("No sessions found.")
        return

    for record in records:
        title = record.title or "Untitled"
        typer.echo(f"{record.id}\t{title}\t{record.model}\t{record.cwd}")


def render_model_list(
    settings: ProviderSettings,
    *,
    provider_name: str | None = None,
    search: str | None = None,
) -> None:
    """Render configured provider/model pairs for CLI inspection."""
    needle = search.casefold() if search else None
    matched = False
    for provider in settings.providers:
        if provider_name is not None and provider.name != provider_name:
            continue
        for model in provider.models:
            row = f"{provider.name}\t{model}"
            if needle is not None and needle not in row.casefold():
                continue
            typer.echo(row)
            matched = True
    if not matched:
        typer.echo("No models found.")


def update_command() -> None:
    """Upgrade Tau using the installer that manages the current environment."""
    result = update_tau()
    if not result.succeeded:
        typer.echo("Could not safely update Tau:", err=True)
        for failure in result.failures:
            typer.echo(f"- {failure}", err=True)
        raise typer.Exit(1)
    if result.stdout:
        typer.echo(result.stdout)
    if result.stderr:
        typer.echo(result.stderr, err=True)
    typer.echo(f"Tau update completed with: {' '.join(result.command or ())}")


async def export_session_command(
    session_ref: str,
    output_path: Path | None = None,
    export_format: str | None = None,
    session_manager: SessionManager | None = None,
) -> Path:
    """Export an indexed session id or JSONL file path."""
    session_path, title = _resolve_export_source(session_ref, session_manager)
    entries = await JsonlSessionStorage(session_path).read_all()
    normalized_format = normalize_export_format(
        export_format or (output_path.suffix.removeprefix(".") if output_path else "html")
    )
    destination = _resolve_export_destination(
        output_path,
        session_path=session_path,
        format=normalized_format,
    )
    return export_session_artifact(
        entries,
        destination,
        title=title,
        source=str(session_path),
        format=normalized_format,
    )


def _run_export_cli(args: list[str], *, session_manager: SessionManager | None = None) -> None:
    """Run `tau export`/`tau --export` and exit."""
    try:
        session_ref, output_path, export_format = _parse_export_cli_args(args)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        exported_path = anyio.run(
            export_session_command,
            session_ref,
            output_path,
            export_format,
            session_manager,
        )
    except (RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Exported session to {exported_path}")
    raise typer.Exit()


def _merge_stdin_prompt(prompt: str) -> str:
    """Merge piped stdin content into a print-mode prompt, mirroring Pi."""
    stdin = sys.stdin
    if stdin is None:
        return prompt
    try:
        if stdin.isatty():
            return prompt
    except AttributeError, ValueError:
        return prompt
    try:
        piped = stdin.read()
    except OSError, ValueError:
        return prompt
    if not piped:
        return prompt
    if not prompt:
        return piped
    return f"{piped}\n\n{prompt}"


def _parse_export_cli_args(args: list[str]) -> tuple[str, Path | None, str | None]:
    if not args:
        raise RuntimeError("Usage: tau export <session-id-or-jsonl> [--format html|jsonl] [output]")
    session_ref = args[0]
    output_path: Path | None = None
    export_format: str | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--format":
            index += 1
            if index >= len(args):
                raise RuntimeError(
                    "Usage: tau export <session-id-or-jsonl> [--format html|jsonl] [output]"
                )
            export_format = args[index]
        elif arg.startswith("--format="):
            export_format = arg.partition("=")[2]
        elif arg.startswith("-"):
            raise RuntimeError(f"Unknown export option: {arg}")
        elif output_path is None:
            output_path = Path(arg).expanduser()
        else:
            raise RuntimeError(
                "Usage: tau export <session-id-or-jsonl> [--format html|jsonl] [output]"
            )
        index += 1
    return session_ref, output_path, export_format


def _parse_loop2_run_dir_cli_args(args: list[str], *, command: str) -> Path:
    if len(args) != 1:
        raise RuntimeError(f"Usage: tau {command} <run-dir>")
    return Path(args[0])


def _parse_loop2_contract_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau loop2-validate-contract <contract.json>")
    return Path(args[0])


def _parse_loop2_run_contract_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau loop2-run <contract.json>")
    return Path(args[0])


def _parse_loop2_scillm_doctor_receipt_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau loop2-check-scillm-doctor <receipt.json>")
    return Path(args[0])


def _parse_doctor_cli_args(args: list[str]) -> None:
    allowed = {"--json"}
    unknown = [arg for arg in args if arg not in allowed]
    if unknown:
        raise RuntimeError(f"unknown doctor option: {unknown[0]}")


def _parse_status_cli_args(args: list[str]) -> dict[str, object]:
    json_output = False
    session_id: str | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--json":
            json_output = True
            index += 1
            continue
        if arg == "--session":
            if index + 1 >= len(args):
                raise RuntimeError("Usage: tau status [--json] [--session <session-id>]")
            session_id = args[index + 1]
            index += 2
            continue
        raise RuntimeError(f"unknown status option: {arg}")
    return {"json_output": json_output, "session_id": session_id}


def _parse_replacement_harness_sanity_cli_args(args: list[str]) -> dict[str, Path]:
    run_dir = Path("experiments/replacement-harness-sanity")
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--run-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-dir requires a value")
            run_dir = Path(args[index])
        elif arg.startswith("--run-dir="):
            run_dir = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown replacement-harness-sanity option: {arg}")
        index += 1
    return {"run_dir": run_dir}


def _parse_visible_dag_poc_cli_args(args: list[str]) -> dict[str, object]:
    repo = Path.cwd()
    run_root = Path("experiments/goal-locked-subagents/proofs/visible-dag-poc")
    label = "tau-visible-dag-poc"
    herdr_workstation: Path | None = None
    herdr_bin = "herdr"
    session: str | None = None
    receipt_timeout_seconds = 30.0
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--repo":
            index += 1
            if index >= len(args):
                raise RuntimeError("--repo requires a value")
            repo = Path(args[index])
        elif arg.startswith("--repo="):
            repo = Path(arg.partition("=")[2])
        elif arg == "--run-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-root requires a value")
            run_root = Path(args[index])
        elif arg.startswith("--run-root="):
            run_root = Path(arg.partition("=")[2])
        elif arg == "--label":
            index += 1
            if index >= len(args):
                raise RuntimeError("--label requires a value")
            label = args[index]
        elif arg.startswith("--label="):
            label = arg.partition("=")[2]
        elif arg == "--herdr-workstation":
            index += 1
            if index >= len(args):
                raise RuntimeError("--herdr-workstation requires a value")
            herdr_workstation = Path(args[index])
        elif arg.startswith("--herdr-workstation="):
            herdr_workstation = Path(arg.partition("=")[2])
        elif arg == "--herdr-bin":
            index += 1
            if index >= len(args):
                raise RuntimeError("--herdr-bin requires a value")
            herdr_bin = args[index]
        elif arg.startswith("--herdr-bin="):
            herdr_bin = arg.partition("=")[2]
        elif arg == "--session":
            index += 1
            if index >= len(args):
                raise RuntimeError("--session requires a value")
            session = args[index]
        elif arg.startswith("--session="):
            session = arg.partition("=")[2]
        elif arg == "--receipt-timeout-seconds":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-timeout-seconds requires a value")
            receipt_timeout_seconds = float(args[index])
        elif arg.startswith("--receipt-timeout-seconds="):
            receipt_timeout_seconds = float(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown visible-dag-poc option: {arg}")
        index += 1
    return {
        "repo": repo,
        "run_root": run_root,
        "label": label,
        "herdr_workstation": herdr_workstation,
        "herdr_bin": herdr_bin,
        "session": session,
        "receipt_timeout_seconds": receipt_timeout_seconds,
    }


def _parse_visible_dag_inspect_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau visible-dag-inspect <run-dir>")
    return Path(args[0])


def _parse_provider_pane_poc_cli_args(args: list[str]) -> dict[str, object]:
    repo = Path.cwd()
    run_root = Path("experiments/goal-locked-subagents/proofs/provider-pane-poc")
    label = "tau-provider-pane-poc"
    herdr_workstation: Path | None = None
    herdr_bin = "herdr"
    session: str | None = None
    install_integrations = True
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--repo":
            index += 1
            if index >= len(args):
                raise RuntimeError("--repo requires a value")
            repo = Path(args[index])
        elif arg.startswith("--repo="):
            repo = Path(arg.partition("=")[2])
        elif arg == "--run-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-root requires a value")
            run_root = Path(args[index])
        elif arg.startswith("--run-root="):
            run_root = Path(arg.partition("=")[2])
        elif arg == "--label":
            index += 1
            if index >= len(args):
                raise RuntimeError("--label requires a value")
            label = args[index]
        elif arg.startswith("--label="):
            label = arg.partition("=")[2]
        elif arg == "--herdr-workstation":
            index += 1
            if index >= len(args):
                raise RuntimeError("--herdr-workstation requires a value")
            herdr_workstation = Path(args[index])
        elif arg.startswith("--herdr-workstation="):
            herdr_workstation = Path(arg.partition("=")[2])
        elif arg == "--herdr-bin":
            index += 1
            if index >= len(args):
                raise RuntimeError("--herdr-bin requires a value")
            herdr_bin = args[index]
        elif arg.startswith("--herdr-bin="):
            herdr_bin = arg.partition("=")[2]
        elif arg == "--session":
            index += 1
            if index >= len(args):
                raise RuntimeError("--session requires a value")
            session = args[index]
        elif arg.startswith("--session="):
            session = arg.partition("=")[2]
        elif arg == "--no-install-integrations":
            install_integrations = False
        else:
            raise RuntimeError(f"Unknown provider-pane-poc option: {arg}")
        index += 1
    return {
        "repo": repo,
        "run_root": run_root,
        "label": label,
        "herdr_workstation": herdr_workstation,
        "herdr_bin": herdr_bin,
        "session": session,
        "install_integrations": install_integrations,
    }


def _parse_provider_pane_inspect_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau provider-pane-inspect <run-dir>")
    return Path(args[0])


def _parse_provider_readiness_poc_cli_args(args: list[str]) -> dict[str, object]:
    options = _parse_provider_pane_poc_cli_args(args)
    if options["run_root"] == Path("experiments/goal-locked-subagents/proofs/provider-pane-poc"):
        options["run_root"] = Path(
            "experiments/goal-locked-subagents/proofs/provider-readiness-poc"
        )
    if options["label"] == "tau-provider-pane-poc":
        options["label"] = "tau-provider-readiness-poc"
    return options


def _parse_provider_readiness_inspect_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau provider-readiness-inspect <run-dir>")
    return Path(args[0])


def _parse_local_provider_readiness_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "provider_url": None,
        "model": None,
        "out": None,
        "model_weight_sha256": None,
        "tokenizer_sha256": None,
        "inference_engine": None,
        "timeout_s": 5.0,
        "airgap_mode": False,
        "allow_unavailable_demo": False,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--provider-url",
            "--model",
            "--out",
            "--model-weight-sha256",
            "--tokenizer-sha256",
            "--inference-engine",
            "--timeout-s",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg == "--provider-url":
                options["provider_url"] = value
            elif arg == "--model":
                options["model"] = value
            elif arg == "--out":
                options["out"] = Path(value)
            elif arg == "--model-weight-sha256":
                options["model_weight_sha256"] = value
            elif arg == "--tokenizer-sha256":
                options["tokenizer_sha256"] = value
            elif arg == "--inference-engine":
                options["inference_engine"] = value
            elif arg == "--timeout-s":
                options["timeout_s"] = float(value)
        elif arg.startswith("--provider-url="):
            options["provider_url"] = arg.partition("=")[2]
        elif arg.startswith("--model="):
            options["model"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        elif arg.startswith("--model-weight-sha256="):
            options["model_weight_sha256"] = arg.partition("=")[2]
        elif arg.startswith("--tokenizer-sha256="):
            options["tokenizer_sha256"] = arg.partition("=")[2]
        elif arg.startswith("--inference-engine="):
            options["inference_engine"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-s="):
            options["timeout_s"] = float(arg.partition("=")[2])
        elif arg == "--airgap-mode":
            options["airgap_mode"] = True
        elif arg == "--allow-unavailable-demo":
            options["allow_unavailable_demo"] = True
        else:
            raise RuntimeError(f"unknown local-provider-readiness option: {arg}")
        index += 1
    if not options["provider_url"] or not options["model"]:
        raise RuntimeError(
            "Usage: tau local-provider-readiness --provider-url <url> --model <id> "
            "[--out <receipt>] [--airgap-mode] [--timeout-s <seconds>]"
        )
    if float(options["timeout_s"]) <= 0:
        raise RuntimeError("--timeout-s must be positive")
    return options


def _parse_airgap_no_egress_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "out": None,
        "allowed_local_endpoints": [],
        "dns_probe_host": "example.com",
        "http_probe_url": "http://example.com/",
        "timeout_s": 3.0,
        "assume_no_egress_demo": False,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--out",
            "--allow-local-endpoint",
            "--dns-probe-host",
            "--http-probe-url",
            "--timeout-s",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg == "--out":
                options["out"] = Path(value)
            elif arg == "--allow-local-endpoint":
                endpoints = list(options["allowed_local_endpoints"])
                endpoints.append(value)
                options["allowed_local_endpoints"] = endpoints
            elif arg == "--dns-probe-host":
                options["dns_probe_host"] = value
            elif arg == "--http-probe-url":
                options["http_probe_url"] = value
            elif arg == "--timeout-s":
                options["timeout_s"] = float(value)
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        elif arg.startswith("--allow-local-endpoint="):
            endpoints = list(options["allowed_local_endpoints"])
            endpoints.append(arg.partition("=")[2])
            options["allowed_local_endpoints"] = endpoints
        elif arg.startswith("--dns-probe-host="):
            options["dns_probe_host"] = arg.partition("=")[2]
        elif arg.startswith("--http-probe-url="):
            options["http_probe_url"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-s="):
            options["timeout_s"] = float(arg.partition("=")[2])
        elif arg == "--assume-no-egress-demo":
            options["assume_no_egress_demo"] = True
        else:
            raise RuntimeError(f"unknown airgap-no-egress option: {arg}")
        index += 1
    if float(options["timeout_s"]) <= 0:
        raise RuntimeError("--timeout-s must be positive")
    return options


def _parse_provider_dag_poc_cli_args(args: list[str]) -> dict[str, object]:
    max_attempts = 2
    receipt_timeout_seconds = 300.0
    force_reviewer_revise_attempts: tuple[int, ...] = ()
    allow_final_forced_revise = False
    reviewer_model: str | None = None
    coder_mode = "codex"
    cleanup_mode = "dry-run"
    filtered_args: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--max-attempts":
            index += 1
            if index >= len(args):
                raise RuntimeError("--max-attempts requires a value")
            max_attempts = int(args[index])
        elif arg.startswith("--max-attempts="):
            max_attempts = int(arg.partition("=")[2])
        elif arg == "--receipt-timeout-seconds":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-timeout-seconds requires a value")
            receipt_timeout_seconds = float(args[index])
        elif arg.startswith("--receipt-timeout-seconds="):
            receipt_timeout_seconds = float(arg.partition("=")[2])
        elif arg == "--force-reviewer-revise-attempts":
            index += 1
            if index >= len(args):
                raise RuntimeError("--force-reviewer-revise-attempts requires a value")
            force_reviewer_revise_attempts = _parse_int_csv(
                args[index], "--force-reviewer-revise-attempts"
            )
        elif arg.startswith("--force-reviewer-revise-attempts="):
            force_reviewer_revise_attempts = _parse_int_csv(
                arg.partition("=")[2], "--force-reviewer-revise-attempts"
            )
        elif arg == "--force-reviewer-revise-first":
            force_reviewer_revise_attempts = (1,)
        elif arg == "--allow-final-forced-revise":
            allow_final_forced_revise = True
        elif arg == "--reviewer-model":
            index += 1
            if index >= len(args):
                raise RuntimeError("--reviewer-model requires a value")
            reviewer_model = args[index]
        elif arg.startswith("--reviewer-model="):
            reviewer_model = arg.partition("=")[2]
        elif arg == "--coder-mode":
            index += 1
            if index >= len(args):
                raise RuntimeError("--coder-mode requires a value")
            coder_mode = args[index]
        elif arg.startswith("--coder-mode="):
            coder_mode = arg.partition("=")[2]
        elif arg == "--cleanup-mode":
            index += 1
            if index >= len(args):
                raise RuntimeError("--cleanup-mode requires a value")
            cleanup_mode = args[index]
        elif arg.startswith("--cleanup-mode="):
            cleanup_mode = arg.partition("=")[2]
        else:
            filtered_args.append(arg)
            if arg in {
                "--repo",
                "--run-root",
                "--label",
                "--herdr-workstation",
                "--herdr-bin",
                "--session",
            }:
                index += 1
                if index >= len(args):
                    raise RuntimeError(f"{arg} requires a value")
                filtered_args.append(args[index])
        index += 1
    base = _parse_provider_pane_poc_cli_args(filtered_args)
    if base["run_root"] == Path("experiments/goal-locked-subagents/proofs/provider-pane-poc"):
        base["run_root"] = Path("experiments/goal-locked-subagents/proofs/provider-dag-poc")
    if base["label"] == "tau-provider-pane-poc":
        base["label"] = "tau-provider-dag-poc"
    base["max_attempts"] = max_attempts
    base["receipt_timeout_seconds"] = receipt_timeout_seconds
    base["force_reviewer_revise_attempts"] = force_reviewer_revise_attempts
    base["allow_final_forced_revise"] = allow_final_forced_revise
    base["reviewer_model"] = reviewer_model
    base["coder_mode"] = coder_mode
    base["cleanup_mode"] = cleanup_mode
    return base


def _parse_int_csv(value: str, option_name: str) -> tuple[int, ...]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return ()
    try:
        return tuple(int(part) for part in parts)
    except ValueError as exc:
        raise RuntimeError(f"{option_name} must be a comma-separated integer list") from exc


def _parse_provider_dag_plan_cli_args(args: list[str]) -> dict[str, object]:
    options = _parse_provider_dag_poc_cli_args(args)
    return {
        "repo": options["repo"],
        "run_root": options["run_root"],
        "label": options["label"],
        "max_attempts": options["max_attempts"],
        "force_reviewer_revise_attempts": options["force_reviewer_revise_attempts"],
        "allow_final_forced_revise": options["allow_final_forced_revise"],
        "reviewer_model": options["reviewer_model"],
        "coder_mode": options["coder_mode"],
    }


def _parse_provider_dag_orchestrate_cli_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError("Usage: tau provider-dag-orchestrate <dag-spec> [options]")
    dag_spec = Path(args[0])
    options = _parse_provider_dag_poc_cli_args(args[1:])
    return {
        "dag_spec": dag_spec,
        "repo": options["repo"],
        "receipt_timeout_seconds": options["receipt_timeout_seconds"],
        "herdr_workstation": options["herdr_workstation"],
        "herdr_bin": options["herdr_bin"],
        "session": options["session"],
        "install_integrations": options["install_integrations"],
        "cleanup_mode": options["cleanup_mode"],
    }


def _parse_provider_dag_inspect_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau provider-dag-inspect <run-dir>")
    return Path(args[0])


def _parse_orchestration_evidence_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau orchestration-evidence <provider-dag-run-dir>")
    return Path(args[0])


def _run_dag_cli_command(args: list[str], *, command_name: str) -> dict[str, object]:
    options = _parse_generic_dag_run_cli_args(args, command_name=command_name)
    spec_path = Path(str(options["spec_path"]))
    if _dag_run_schema(spec_path) == DAG_CONTRACT_SCHEMA:
        try:
            return run_project_dag_contract(
                contract_path=spec_path,
                receipt_dir=options.get("receipt_dir"),
                agents_root=Path(str(options["agents_root"])),
                command_spec_root=options.get("command_spec_root"),
                scheduler=str(options["scheduler"]),
                security_mode=(
                    str(options["security_mode"])
                    if options.get("security_mode") is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            return dag_contract_error_payload(
                contract_path=spec_path,
                receipt_dir=options.get("receipt_dir"),
                error=str(exc),
                scheduler=str(options["scheduler"]),
            )
    with redirect_stderr(io.StringIO()):
        return run_generic_dag(
            spec_path=spec_path,
            resume=bool(options["resume"]),
        )


def _dispatch_workflows_cli(args: list[str]) -> tuple[dict[str, Any], bool]:
    if not args:
        raise RuntimeError("Usage: tau workflows <list|describe|run>")
    subcommand = args[0]
    remaining = args[1:]
    if subcommand == "list":
        if remaining not in ([], ["--json"]):
            raise RuntimeError("Usage: tau workflows list [--json]")
        return workflow_catalog_payload(), remaining == ["--json"]
    if subcommand == "describe":
        json_output = "--json" in remaining
        positional = [item for item in remaining if item != "--json"]
        if len(positional) != 1:
            raise RuntimeError("Usage: tau workflows describe <workflow-id> [--json]")
        return get_workflow(positional[0]).public_payload(), json_output
    if subcommand == "approve":
        approval_packet: Path | None = None
        positional: list[str] = []
        index = 0
        while index < len(remaining):
            arg = remaining[index]
            index += 1
            if arg == "--approval-packet":
                if index >= len(remaining):
                    raise RuntimeError("--approval-packet requires a value")
                approval_packet = Path(remaining[index])
                index += 1
            elif arg.startswith("--approval-packet="):
                approval_packet = Path(arg.partition("=")[2])
            elif arg == "--last":
                positional.append(arg)
            elif arg.startswith("-"):
                raise RuntimeError(f"unknown workflows approve option: {arg}")
            else:
                positional.append(arg)
        if positional == ["--last"]:
            run_dir = _resolve_last_run_dir()
        elif len(positional) == 1:
            run_dir = Path(positional[0])
        else:
            raise RuntimeError(
                "Usage: tau workflows approve <run-dir>|--last "
                "[--approval-packet <approval.json>]"
            )
        payload = approve_packaged_workflow(
            run_dir=run_dir,
            approval_packet=approval_packet,
        )
        _record_workflow_run(payload)
        return dict(payload), True
    if subcommand == "resume":
        if remaining == ["--last"]:
            run_dir = _resolve_last_run_dir()
        elif len(remaining) == 1:
            run_dir = Path(remaining[0])
        else:
            raise RuntimeError(f"Usage: tau workflows {subcommand} <run-dir>|--last")
        payload = resume_packaged_workflow(run_dir=run_dir)
        _record_workflow_run(payload)
        return dict(payload), True
    if subcommand == "repair":
        approval_packet: Path | None = None
        node_id: str | None = None
        positional = []
        index = 0
        while index < len(remaining):
            arg = remaining[index]
            index += 1
            if arg == "--node":
                if index >= len(remaining):
                    raise RuntimeError("--node requires a value")
                node_id = remaining[index]
                index += 1
            elif arg.startswith("--node="):
                node_id = arg.partition("=")[2]
            elif arg == "--approval-packet":
                if index >= len(remaining):
                    raise RuntimeError("--approval-packet requires a value")
                approval_packet = Path(remaining[index])
                index += 1
            elif arg.startswith("--approval-packet="):
                approval_packet = Path(arg.partition("=")[2])
            elif arg == "--last":
                positional.append(arg)
            elif arg.startswith("-"):
                raise RuntimeError(f"unknown workflows repair option: {arg}")
            else:
                positional.append(arg)
        if positional == ["--last"]:
            run_dir = _resolve_last_run_dir()
        elif len(positional) == 1:
            run_dir = Path(positional[0])
        else:
            raise RuntimeError(
                "Usage: tau workflows repair <run-dir> --node <node-id> "
                "[--approval-packet <approval.json>]"
            )
        if node_id is None:
            raise RuntimeError(
                "Usage: tau workflows repair <run-dir>|--last --node <node-id> "
                "[--approval-packet <approval.json>]"
            )
        payload = repair_durable_repository_qualification(
            run_dir=run_dir,
            node_id=node_id,
            approval_packet=approval_packet,
        )
        _record_workflow_run(payload)
        return (
            dict(payload),
            True,
        )
    if subcommand != "run":
        raise RuntimeError(f"unknown workflows subcommand: {subcommand}")
    if not remaining:
        raise RuntimeError("Usage: tau workflows run <workflow-id> [options]")
    workflow_id = remaining[0]
    if workflow_id not in {
        "approved-release-bundle",
        "durable-repository-qualification",
        "repository-readiness",
        "repository-evidence-map",
        "tau-operator-reference",
    }:
        raise RuntimeError(f"unknown workflow_id: {workflow_id}")
    values: dict[str, str] = {}
    flags: set[str] = set()
    index = 1
    value_options = {
        "--repo",
        "--goal",
        "--required-workflow",
        "--run-dir",
        "--publish-path",
        "--viewer-hold-seconds",
        "--step-delay-seconds",
    }
    flag_options = {
        "--require-clean",
        "--require-tests",
        "--open-viewer",
        "--no-browser-open",
        "--inject-test-branch-failure",
    }
    while index < len(remaining):
        argument = remaining[index]
        if argument in flag_options:
            flags.add(argument)
            index += 1
            continue
        if argument not in value_options or index + 1 >= len(remaining):
            raise RuntimeError(f"unknown or incomplete workflows run option: {argument}")
        values[argument] = remaining[index + 1]
        index += 2
    required_options = ["--repo", "--run-dir"]
    if workflow_id in {
        "approved-release-bundle",
        "durable-repository-qualification",
        "repository-readiness",
        "repository-evidence-map",
    }:
        required_options.append("--goal")
    if workflow_id in {"approved-release-bundle", "durable-repository-qualification"}:
        required_options.append("--publish-path")
    missing = [option for option in required_options if option not in values]
    if missing:
        raise RuntimeError(f"workflows run missing required option: {missing[0]}")
    hold = values.get("--viewer-hold-seconds")
    try:
        hold_seconds = float(hold) if hold is not None else None
    except ValueError as exc:
        raise RuntimeError("--viewer-hold-seconds must be a number") from exc
    step_delay = values.get("--step-delay-seconds")
    try:
        step_delay_seconds = float(step_delay) if step_delay is not None else 0.0
    except ValueError as exc:
        raise RuntimeError("--step-delay-seconds must be a number") from exc
    if workflow_id in {"approved-release-bundle", "durable-repository-qualification"}:
        common = {
            "repo_path": Path(values["--repo"]),
            "human_goal": values["--goal"],
            "publish_path": Path(values["--publish-path"]),
            "run_dir": Path(values["--run-dir"]),
            "open_viewer": "--open-viewer" in flags,
            "browser_open": "--no-browser-open" not in flags,
            "viewer_hold_seconds": hold_seconds,
        }
        if workflow_id == "approved-release-bundle":
            payload = run_approved_release_bundle_workflow(**common)
        else:
            payload = run_durable_repository_qualification_workflow(
                **common,
                inject_test_branch_failure="--inject-test-branch-failure" in flags,
                step_delay_seconds=step_delay_seconds,
            )
    elif workflow_id == "repository-readiness":
        payload = run_repository_readiness_workflow(
            repo_path=Path(values["--repo"]),
            human_goal=values["--goal"],
            require_clean="--require-clean" in flags,
            run_dir=Path(values["--run-dir"]),
            open_viewer="--open-viewer" in flags,
            browser_open="--no-browser-open" not in flags,
            viewer_hold_seconds=hold_seconds,
        )
    elif workflow_id == "tau-operator-reference":
        payload = run_tau_operator_reference_workflow(
            repo_path=Path(values["--repo"]),
            required_workflow=values.get("--required-workflow", "tau-operator-reference"),
            run_dir=Path(values["--run-dir"]),
            open_viewer="--open-viewer" in flags,
            browser_open="--no-browser-open" not in flags,
            viewer_hold_seconds=hold_seconds,
        )
    else:
        payload = run_repository_evidence_map_workflow(
            repo_path=Path(values["--repo"]),
            human_goal=values["--goal"],
            require_tests="--require-tests" in flags,
            run_dir=Path(values["--run-dir"]),
            open_viewer="--open-viewer" in flags,
            browser_open="--no-browser-open" not in flags,
            viewer_hold_seconds=hold_seconds,
        )
    _record_workflow_run(payload)
    return dict(payload), True


def _parse_dag_plan_cli_args(args: list[str]) -> tuple[Path, Path]:
    if not args:
        raise RuntimeError("Usage: tau dag-plan <dag-spec> --out <plan.json>")
    source_path = Path(args[0])
    output_path: Path | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            output_path = Path(args[index])
        elif arg.startswith("--out="):
            output_path = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown dag-plan option: {arg}")
        index += 1
    if output_path is None:
        raise RuntimeError("--out is required")
    return source_path, output_path


def _parse_dag_template_compile_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "template": None,
        "params": None,
        "out": None,
        "receipt": None,
        "missing_out": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--template", "--params", "--out", "--receipt", "--missing-out"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--template="):
            options["template"] = arg.partition("=")[2]
        elif arg.startswith("--params="):
            options["params"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--receipt="):
            options["receipt"] = arg.partition("=")[2]
        elif arg.startswith("--missing-out="):
            options["missing_out"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown dag-template-compile option: {arg}")
        index += 1
    if not _optional_str(options.get("template")):
        raise RuntimeError("Usage: tau dag-template-compile --template <name> --params <json>")
    if not _optional_str(options.get("params")):
        raise RuntimeError("Usage: tau dag-template-compile --template <name> --params <json>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau dag-template-compile --out <dag.json>")
    if not _optional_str(options.get("receipt")):
        raise RuntimeError("Usage: tau dag-template-compile --receipt <receipt.json>")
    return options


def _parse_dag_template_describe_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {"template": None}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--template":
            index += 1
            if index >= len(args):
                raise RuntimeError("--template requires a value")
            options["template"] = args[index]
        elif arg.startswith("--template="):
            options["template"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown dag-template-describe option: {arg}")
        index += 1
    if not _optional_str(options.get("template")):
        raise RuntimeError("Usage: tau dag-template-describe --template <name>")
    return options


def _parse_dag_template_params_cli_args(
    args: list[str],
    *,
    command_name: str,
) -> dict[str, object]:
    options: dict[str, object] = {"template": None, "params": None}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--template", "--params"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--")] = args[index]
        elif arg.startswith("--template="):
            options["template"] = arg.partition("=")[2]
        elif arg.startswith("--params="):
            options["params"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown {command_name} option: {arg}")
        index += 1
    if not _optional_str(options.get("template")):
        raise RuntimeError(f"Usage: tau {command_name} --template <name> --params <json>")
    if not _optional_str(options.get("params")):
        raise RuntimeError(f"Usage: tau {command_name} --template <name> --params <json>")
    return options


def _parse_dag_template_select_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {"facts": None}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--facts":
            index += 1
            if index >= len(args):
                raise RuntimeError("--facts requires a value")
            options["facts"] = args[index]
        elif arg.startswith("--facts="):
            options["facts"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown dag-template-select option: {arg}")
        index += 1
    if not _optional_str(options.get("facts")):
        raise RuntimeError("Usage: tau dag-template-select --facts <json>")
    return options


def _parse_gs001_closure_publish_cli_args(args: list[str]) -> dict[str, object]:
    usage = (
        "Usage: tau gs001-closure-publish --repo-root <repo> --dag <dag.json> "
        "--closure-state <state.json> --terminal-receipt <terminal.json> "
        "--visual-receipt <visual.json> --out <receipt.json> "
        "[--expected-goal-hash <sha256:...>]"
    )
    value_options = {
        "--repo-root",
        "--dag",
        "--closure-state",
        "--terminal-receipt",
        "--visual-receipt",
        "--out",
        "--expected-goal-hash",
    }
    values: dict[str, str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg not in value_options or index + 1 >= len(args):
            raise RuntimeError(f"{usage}; unknown or incomplete option: {arg}")
        values[arg] = args[index + 1]
        index += 2
    missing = [
        option
        for option in (
            "--repo-root",
            "--dag",
            "--closure-state",
            "--terminal-receipt",
            "--visual-receipt",
            "--out",
        )
        if option not in values
    ]
    if missing:
        raise RuntimeError(f"{usage}; missing {missing[0]}")
    return {
        "repo_root": Path(values["--repo-root"]),
        "dag_contract_path": Path(values["--dag"]),
        "closure_state_path": Path(values["--closure-state"]),
        "terminal_receipt_path": Path(values["--terminal-receipt"]),
        "visual_receipt_path": Path(values["--visual-receipt"]),
        "output_path": Path(values["--out"]),
        "expected_goal_hash": values.get("--expected-goal-hash"),
    }


def _parse_generic_dag_run_cli_args(
    args: list[str],
    *,
    command_name: str = "dag-run",
) -> dict[str, object]:
    if not args:
        raise RuntimeError(
            f"Usage: tau {command_name} <dag-spec> [--no-resume] "
            "[--receipt-dir <dir>] [--agents-root <dir>] [--command-spec-root <dir>] "
            "[--scheduler <handoff-loop|bounded-ready-queue>] [--mode <development|secure>]"
        )
    spec_path = Path(args[0])
    resume = True
    receipt_dir: Path | None = None
    agents_root = Path(
        os.environ.get(
            "TAU_AGENT_REGISTRY_ROOT",
            "/home/graham/workspace/experiments/agent-skills/agents",
        )
    )
    command_spec_root: Path | None = None
    scheduler = "handoff-loop"
    security_mode: str | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--no-resume":
            resume = False
        elif arg == "--receipt-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-dir requires a value")
            receipt_dir = Path(args[index])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg == "--command-spec-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command-spec-root requires a value")
            command_spec_root = Path(args[index])
        elif arg == "--scheduler":
            index += 1
            if index >= len(args):
                raise RuntimeError("--scheduler requires a value")
            scheduler = args[index]
        elif arg == "--mode":
            index += 1
            if index >= len(args):
                raise RuntimeError("--mode requires a value")
            security_mode = args[index]
            if security_mode not in {"development", "secure"}:
                raise RuntimeError("--mode must be development or secure")
        else:
            raise RuntimeError(f"unknown {command_name} option: {arg}")
        index += 1
    return {
        "spec_path": spec_path,
        "resume": resume,
        "receipt_dir": receipt_dir,
        "agents_root": agents_root,
        "command_spec_root": command_spec_root,
        "scheduler": scheduler,
        "security_mode": security_mode,
    }


def _parse_init_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "profile": None,
        "out": Path.cwd(),
        "force": False,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--profile":
            index += 1
            if index >= len(args):
                raise RuntimeError("--profile requires a value")
            options["profile"] = args[index]
        elif arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            options["out"] = Path(args[index])
        elif arg == "--force":
            options["force"] = True
        else:
            raise RuntimeError(f"unknown init option: {arg}")
        index += 1
    if options["profile"] is None:
        raise RuntimeError(
            "Usage: tau init --profile zero-trust|coding-zero-trust|itar-airgap "
            "[--out <dir>] [--force]"
        )
    return options


def _parse_demo_cli_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError("Usage: tau demo airgap-itar-basic|embry-sparta-airgap --out <dir>")
    options: dict[str, object] = {
        "name": args[0],
        "out": None,
        "provider_url": "http://127.0.0.1:4001",
        "model": "local-kimi-k2.6",
        "live_provider": False,
        "live_airgap_probe": False,
        "memory_url": "http://127.0.0.1:8601",
        "scillm_url": "http://127.0.0.1:4001",
        "sparta_contract_out": None,
        "timeout_s": 5.0,
    }
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in {
            "--out",
            "--provider-url",
            "--model",
            "--memory-url",
            "--scillm-url",
            "--sparta-contract-out",
            "--timeout-s",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg == "--out":
                options["out"] = Path(value)
            elif arg == "--provider-url":
                options["provider_url"] = value
            elif arg == "--model":
                options["model"] = value
            elif arg == "--memory-url":
                options["memory_url"] = value
            elif arg == "--scillm-url":
                options["scillm_url"] = value
            elif arg == "--sparta-contract-out":
                options["sparta_contract_out"] = Path(value)
            elif arg == "--timeout-s":
                options["timeout_s"] = float(value)
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        elif arg.startswith("--provider-url="):
            options["provider_url"] = arg.partition("=")[2]
        elif arg.startswith("--model="):
            options["model"] = arg.partition("=")[2]
        elif arg.startswith("--memory-url="):
            options["memory_url"] = arg.partition("=")[2]
        elif arg.startswith("--scillm-url="):
            options["scillm_url"] = arg.partition("=")[2]
        elif arg.startswith("--sparta-contract-out="):
            options["sparta_contract_out"] = Path(arg.partition("=")[2])
        elif arg.startswith("--timeout-s="):
            options["timeout_s"] = float(arg.partition("=")[2])
        elif arg == "--live-provider":
            options["live_provider"] = True
        elif arg == "--live-airgap-probe":
            options["live_airgap_probe"] = True
        else:
            raise RuntimeError(f"unknown demo option: {arg}")
        index += 1
    if options["out"] is None:
        raise RuntimeError("Usage: tau demo airgap-itar-basic|embry-sparta-airgap --out <dir>")
    if float(options["timeout_s"]) <= 0:
        raise RuntimeError("--timeout-s must be positive")
    if options["name"] == "airgap-itar-basic":
        options.pop("memory_url")
        options.pop("scillm_url")
        options.pop("sparta_contract_out")
        options.pop("timeout_s")
    elif options["name"] == "embry-sparta-airgap":
        options.pop("provider_url")
        options.pop("live_provider")
        options.pop("live_airgap_probe")
    return options


def _parse_project_cli_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError("Usage: tau project check-spine --spine <json> --out <receipt>")
    subcommand = args[0]
    if subcommand != "check-spine":
        raise RuntimeError(f"unsupported project subcommand: {subcommand}")
    options: dict[str, object] = {"subcommand": subcommand, "spine": None, "out": None}
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in {"--spine", "--out"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--")] = args[index]
        elif arg.startswith("--spine="):
            options["spine"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown project check-spine option: {arg}")
        index += 1
    if not _optional_str(options.get("spine")):
        raise RuntimeError("Usage: tau project check-spine --spine <json> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau project check-spine --spine <json> --out <receipt>")
    return options


def _parse_zero_trust_doctor_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "policy_profile": None,
        "data_boundary": None,
        "dag_contract": None,
        "receipt": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--policy-profile":
            index += 1
            if index >= len(args):
                raise RuntimeError("--policy-profile requires a value")
            options["policy_profile"] = Path(args[index])
        elif arg == "--data-boundary":
            index += 1
            if index >= len(args):
                raise RuntimeError("--data-boundary requires a value")
            options["data_boundary"] = Path(args[index])
        elif arg == "--dag-contract":
            index += 1
            if index >= len(args):
                raise RuntimeError("--dag-contract requires a value")
            options["dag_contract"] = Path(args[index])
        elif arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            options["receipt"] = Path(args[index])
        else:
            raise RuntimeError(f"unknown zero-trust-doctor option: {arg}")
        index += 1
    if options["policy_profile"] is None:
        raise RuntimeError(
            "Usage: tau zero-trust-doctor --policy-profile <policy.json> "
            "[--data-boundary <boundary.json>] [--dag-contract <dag.json>] "
            "[--receipt <receipt.json>]"
        )
    return options


def _dag_run_schema(spec_path: Path) -> str | None:
    try:
        payload = load_dag_contract_payload(spec_path)
    except OSError, json.JSONDecodeError, RuntimeError:
        return None
    return str(payload.get("schema")) if isinstance(payload.get("schema"), str) else None


def _parse_dag_signals_cli_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError("Usage: tau dag-signals <dag-receipt-or-run-dir> [--receipt <path>]")
    source = Path(args[0])
    receipt_path: Path | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt_path = Path(args[index])
        else:
            raise RuntimeError(f"unknown dag-signals option: {arg}")
        index += 1
    return {"source": source, "receipt_path": receipt_path}


def _parse_evidence_validate_cli_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError(
            "Usage: tau evidence-validate <evidence-manifest.json> [--receipt <path>]"
        )
    manifest = Path(args[0])
    receipt: Path | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt = Path(args[index])
        else:
            raise RuntimeError(f"unknown evidence-validate option: {arg}")
        index += 1
    return {"manifest": manifest, "receipt": receipt}


def _parse_proof_index_cli_args(args: list[str]) -> dict[str, object]:
    if not args or args[0] != "build":
        raise RuntimeError(
            "Usage: tau proof-index build <proofs-dir> --out <index.jsonl> "
            "[--receipt <receipt.json>]"
        )
    if len(args) < 2:
        raise RuntimeError(
            "Usage: tau proof-index build <proofs-dir> --out <index.jsonl> "
            "[--receipt <receipt.json>]"
        )
    proofs_dir = Path(args[1])
    output_path: Path | None = None
    receipt_path: Path | None = None
    index = 2
    while index < len(args):
        arg = args[index]
        if arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            output_path = Path(args[index])
        elif arg.startswith("--out="):
            output_path = Path(arg.partition("=")[2])
        elif arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt_path = Path(args[index])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown proof-index option: {arg}")
        index += 1
    if output_path is None:
        raise RuntimeError("--out is required")
    return {"proofs_dir": proofs_dir, "output_path": output_path, "receipt_path": receipt_path}


def _parse_dag_expansion_validate_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "dag_contract": None,
        "proposal": None,
        "receipt": None,
        "preview": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--dag-contract", "--proposal", "--receipt", "--preview"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = Path(args[index])
        else:
            raise RuntimeError(f"unknown dag-expansion-validate option: {arg}")
        index += 1
    missing = [key for key in ("dag_contract", "proposal", "receipt") if options[key] is None]
    if missing:
        raise RuntimeError(
            "Usage: tau dag-expansion-validate --dag-contract <dag-contract.json|yaml> "
            "--proposal <dag-expansion-proposal.json|yaml> "
            "--receipt <dag-expansion-validation-receipt.json> "
            "[--preview <expanded-dag.preview.json>]"
        )
    return options


def _parse_dag_expansion_policy_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "validation_receipt": None,
        "signal_receipt": None,
        "receipt": None,
        "require_clean_signal": False,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--validation-receipt", "--signal-receipt", "--receipt"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = Path(args[index])
        elif arg == "--require-clean-signal":
            options["require_clean_signal"] = True
        else:
            raise RuntimeError(f"unknown dag-expansion-policy option: {arg}")
        index += 1
    missing = [key for key in ("validation_receipt", "receipt") if options[key] is None]
    if missing:
        raise RuntimeError(
            "Usage: tau dag-expansion-policy "
            "--validation-receipt <dag-expansion-validation-receipt.json> "
            "--receipt <dag-expansion-policy-receipt.json> "
            "[--signal-receipt <dag-signal-receipt.json>] [--require-clean-signal]"
        )
    return options


def _parse_dag_expansion_apply_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "validation_receipt": None,
        "policy_receipt": None,
        "out": None,
        "receipt": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--validation-receipt", "--policy-receipt", "--out", "--receipt"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = Path(args[index])
        else:
            raise RuntimeError(f"unknown dag-expansion-apply option: {arg}")
        index += 1
    missing = [key for key in ("validation_receipt", "out", "receipt") if options[key] is None]
    if missing:
        raise RuntimeError(
            "Usage: tau dag-expansion-apply "
            "--validation-receipt <dag-expansion-validation-receipt.json> "
            "--out <expanded-dag.json> --receipt <dag-expansion-apply-receipt.json> "
            "[--policy-receipt <dag-expansion-policy-receipt.json>]"
        )
    return options


def _parse_dag_branch_locks_validate_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "dag_contract": None,
        "locks": None,
        "receipt": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--dag-contract", "--locks", "--receipt"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = Path(args[index])
        else:
            raise RuntimeError(f"unknown dag-branch-locks-validate option: {arg}")
        index += 1
    missing = [key for key in ("dag_contract", "locks", "receipt") if options[key] is None]
    if missing:
        raise RuntimeError(
            "Usage: tau dag-branch-locks-validate --dag-contract <dag-contract.json|yaml> "
            "--locks <branch-locks.json|yaml> --receipt <dag-branch-lock-validation-receipt.json>"
        )
    return options


def _parse_dag_motif_validate_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "dag_contract": None,
        "motif": None,
        "receipt": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--dag-contract", "--motif", "--receipt"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = Path(args[index])
        else:
            raise RuntimeError(f"unknown dag-motif-validate option: {arg}")
        index += 1
    missing = [key for key in ("dag_contract", "motif", "receipt") if options[key] is None]
    if missing:
        raise RuntimeError(
            "Usage: tau dag-motif-validate --dag-contract <dag-contract.json|yaml> "
            "--motif <dag-motif.json|yaml> --receipt <dag-motif-validation-receipt.json>"
        )
    return options


def _parse_dag_route_memory_candidates_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "signal_receipt": None,
        "receipt": None,
        "min_confidence": 1.0,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--signal-receipt", "--receipt", "--min-confidence"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            if key == "min_confidence":
                try:
                    options[key] = float(args[index])
                except ValueError as exc:
                    raise RuntimeError("--min-confidence must be a number") from exc
            else:
                options[key] = Path(args[index])
        else:
            raise RuntimeError(f"unknown dag-route-memory-candidates option: {arg}")
        index += 1
    missing = [key for key in ("signal_receipt", "receipt") if options[key] is None]
    if missing:
        raise RuntimeError(
            "Usage: tau dag-route-memory-candidates "
            "--signal-receipt <dag-signal-receipt.json> "
            "--receipt <dag-route-memory-candidate-receipt.json> "
            "[--min-confidence <0..1>]"
        )
    return options


def _parse_dag_route_memory_sync_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "candidate_receipt": None,
        "receipt": None,
        "collection": "tau_route_memory",
        "memory_url": "http://127.0.0.1:8601",
        "apply": False,
        "approval_receipt": None,
        "memory_auth_token": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--candidate-receipt",
            "--receipt",
            "--collection",
            "--memory-url",
            "--memory-auth-token",
            "--approval-receipt",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            if key in {"candidate_receipt", "receipt", "approval_receipt"}:
                options[key] = Path(args[index])
            else:
                options[key] = args[index]
        elif arg == "--apply":
            options["apply"] = True
        else:
            raise RuntimeError(f"unknown dag-route-memory-sync option: {arg}")
        index += 1
    missing = [key for key in ("candidate_receipt", "receipt") if options[key] is None]
    if missing:
        raise RuntimeError(
            "Usage: tau dag-route-memory-sync "
            "--candidate-receipt <dag-route-memory-candidate-receipt.json> "
            "--receipt <dag-route-memory-sync-receipt.json> "
            "[--collection <collection>] [--memory-url <url>] "
            "[--apply --approval-receipt <approval-gate-receipt.json> "
            "--memory-auth-token <token>]"
        )
    return options


def _parse_memory_intent_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "query": None,
        "out": None,
        "memory_url": None,
        "scope": "tau",
        "app": "tau",
        "fast": True,
        "goal_hash": None,
        "target": None,
        "timeout_seconds": 15.0,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--query",
            "--out",
            "--memory-url",
            "--scope",
            "--app",
            "--goal-hash",
            "--target-json",
            "--timeout-seconds",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            _set_memory_acquisition_option(options, arg, args[index])
        elif arg.startswith("--query="):
            options["query"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--memory-url="):
            options["memory_url"] = arg.partition("=")[2]
        elif arg.startswith("--scope="):
            options["scope"] = arg.partition("=")[2]
        elif arg.startswith("--app="):
            options["app"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--target-json="):
            options["target"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-seconds="):
            options["timeout_seconds"] = float(arg.partition("=")[2])
        elif arg == "--no-fast":
            options["fast"] = False
        else:
            raise RuntimeError(f"unknown memory-intent option: {arg}")
        index += 1
    if not _optional_str(options.get("query")):
        raise RuntimeError("Usage: tau memory-intent --query <text> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau memory-intent --query <text> --out <receipt>")
    return options


def _parse_evidence_case_create_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "intent": None,
        "out": None,
        "memory_url": None,
        "question": None,
        "scope": "tau",
        "app": "tau",
        "goal_hash": None,
        "target": None,
        "timeout_seconds": 15.0,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--intent",
            "--out",
            "--memory-url",
            "--question",
            "--scope",
            "--app",
            "--goal-hash",
            "--target-json",
            "--timeout-seconds",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            _set_memory_acquisition_option(options, arg, args[index])
        elif arg.startswith("--intent="):
            options["intent"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--memory-url="):
            options["memory_url"] = arg.partition("=")[2]
        elif arg.startswith("--question="):
            options["question"] = arg.partition("=")[2]
        elif arg.startswith("--scope="):
            options["scope"] = arg.partition("=")[2]
        elif arg.startswith("--app="):
            options["app"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--target-json="):
            options["target"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-seconds="):
            options["timeout_seconds"] = float(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown evidence-case-create option: {arg}")
        index += 1
    if not _optional_str(options.get("intent")):
        raise RuntimeError("Usage: tau evidence-case-create --intent <json> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau evidence-case-create --intent <json> --out <receipt>")
    return options


def _parse_skill_chain_recall_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "query": None,
        "out": None,
        "memory_url": None,
        "scope": "tau",
        "app": "tau",
        "k": 5,
        "goal_hash": None,
        "target": None,
        "fallback_skills": None,
        "timeout_seconds": 15.0,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--query",
            "--out",
            "--memory-url",
            "--scope",
            "--app",
            "--k",
            "--goal-hash",
            "--target-json",
            "--fallback-skills-json",
            "--timeout-seconds",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            _set_skill_chain_recall_option(options, arg, args[index])
        elif arg.startswith("--query="):
            options["query"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--memory-url="):
            options["memory_url"] = arg.partition("=")[2]
        elif arg.startswith("--scope="):
            options["scope"] = arg.partition("=")[2]
        elif arg.startswith("--app="):
            options["app"] = arg.partition("=")[2]
        elif arg.startswith("--k="):
            options["k"] = int(arg.partition("=")[2])
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--target-json="):
            options["target"] = arg.partition("=")[2]
        elif arg.startswith("--fallback-skills-json="):
            options["fallback_skills"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-seconds="):
            options["timeout_seconds"] = float(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown skill-chain-recall option: {arg}")
        index += 1
    if not _optional_str(options.get("query")):
        raise RuntimeError("Usage: tau skill-chain-recall --query <text> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau skill-chain-recall --query <text> --out <receipt>")
    return options


def _parse_tool_chain_recall_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "query": None,
        "out": None,
        "memory_url": None,
        "scope": "tau",
        "app": "tau",
        "k": 5,
        "goal_hash": None,
        "target": None,
        "timeout_seconds": 15.0,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--query",
            "--out",
            "--memory-url",
            "--scope",
            "--app",
            "--k",
            "--goal-hash",
            "--target-json",
            "--timeout-seconds",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            _set_tool_chain_recall_option(options, arg, args[index])
        elif arg.startswith("--query="):
            options["query"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--memory-url="):
            options["memory_url"] = arg.partition("=")[2]
        elif arg.startswith("--scope="):
            options["scope"] = arg.partition("=")[2]
        elif arg.startswith("--app="):
            options["app"] = arg.partition("=")[2]
        elif arg.startswith("--k="):
            options["k"] = int(arg.partition("=")[2])
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--target-json="):
            options["target"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-seconds="):
            options["timeout_seconds"] = float(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown tool-chain-recall option: {arg}")
        index += 1
    if not _optional_str(options.get("query")):
        raise RuntimeError("Usage: tau tool-chain-recall --query <text> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau tool-chain-recall --query <text> --out <receipt>")
    return options


def _set_tool_chain_recall_option(options: dict[str, object], arg: str, value: str) -> None:
    key = arg.removeprefix("--").replace("-", "_")
    if arg == "--target-json":
        key = "target"
    if arg == "--timeout-seconds":
        options[key] = float(value)
    elif arg == "--k":
        options[key] = int(value)
    else:
        options[key] = value


def _set_skill_chain_recall_option(options: dict[str, object], arg: str, value: str) -> None:
    key = arg.removeprefix("--").replace("-", "_")
    if arg == "--target-json":
        key = "target"
    if arg == "--fallback-skills-json":
        key = "fallback_skills"
    if arg == "--timeout-seconds":
        options[key] = float(value)
    elif arg == "--k":
        options[key] = int(value)
    else:
        options[key] = value


def _set_memory_acquisition_option(options: dict[str, object], arg: str, value: str) -> None:
    key = arg.removeprefix("--").replace("-", "_")
    if arg == "--target-json":
        key = "target"
    if arg == "--timeout-seconds":
        options[key] = float(value)
    else:
        options[key] = value


def _parse_generic_dag_inspect_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau dag-inspect <run-dir>")
    return Path(args[0])


def _parse_generic_dag_resume_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau dag-resume <run-dir>")
    return Path(args[0])


def _parse_dag_retention_expire_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "root": None,
        "archive_dir": None,
        "keep_count": None,
        "older_than_days": None,
        "receipt": None,
        "dry_run": False,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--root",
            "--archive-dir",
            "--keep-count",
            "--older-than-days",
            "--receipt",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            if key in {"keep_count", "older_than_days"}:
                options[key] = _parse_non_negative_number(args[index], flag=arg)
            else:
                options[key] = args[index]
        elif arg.startswith("--root="):
            options["root"] = arg.partition("=")[2]
        elif arg.startswith("--archive-dir="):
            options["archive_dir"] = arg.partition("=")[2]
        elif arg.startswith("--keep-count="):
            options["keep_count"] = _parse_non_negative_number(
                arg.partition("=")[2], flag="--keep-count"
            )
        elif arg.startswith("--older-than-days="):
            options["older_than_days"] = _parse_non_negative_number(
                arg.partition("=")[2], flag="--older-than-days"
            )
        elif arg.startswith("--receipt="):
            options["receipt"] = arg.partition("=")[2]
        elif arg == "--dry-run":
            options["dry_run"] = True
        elif arg.startswith("-"):
            raise RuntimeError(f"unknown dag-retention-expire option: {arg}")
        else:
            raise RuntimeError(f"unexpected dag-retention-expire argument: {arg}")
        index += 1
    if not _optional_str(options.get("root")) or not _optional_str(
        options.get("archive_dir")
    ):
        raise RuntimeError(
            "Usage: tau dag-retention-expire --root <dir> --archive-dir <dir> "
            "[--keep-count N] [--older-than-days DAYS] [--receipt <path>] [--dry-run]"
        )
    if options["keep_count"] is None and options["older_than_days"] is None:
        raise RuntimeError("dag-retention-expire requires --keep-count or --older-than-days")
    return options


def _parse_non_negative_number(value: str, *, flag: str) -> int | float:
    try:
        parsed: int | float
        parsed = int(value) if flag == "--keep-count" else float(value)
    except ValueError as exc:
        raise RuntimeError(f"{flag} requires a non-negative number") from exc
    if parsed < 0:
        raise RuntimeError(f"{flag} requires a non-negative number")
    return parsed


def _parse_dag_reconcile_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "run_dir": None,
        "decision": None,
        "operator_id": None,
        "reason": None,
        "receipt": None,
        "run_id": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--decision", "--operator", "--reason", "--receipt", "--run-id"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            if arg == "--operator":
                options["operator_id"] = args[index]
            else:
                options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--decision="):
            options["decision"] = arg.partition("=")[2]
        elif arg.startswith("--operator="):
            options["operator_id"] = arg.partition("=")[2]
        elif arg.startswith("--reason="):
            options["reason"] = arg.partition("=")[2]
        elif arg.startswith("--receipt="):
            options["receipt"] = arg.partition("=")[2]
        elif arg.startswith("--run-id="):
            options["run_id"] = arg.partition("=")[2]
        elif arg.startswith("-"):
            raise RuntimeError(f"unknown dag-reconcile option: {arg}")
        elif options["run_dir"] is None:
            options["run_dir"] = arg
        else:
            raise RuntimeError(f"unexpected dag-reconcile argument: {arg}")
        index += 1
    if not _optional_str(options.get("run_dir")):
        raise RuntimeError(
            "Usage: tau dag-reconcile <run-dir> --decision <reconcile|abandon> "
            "--operator <id> --reason <text> [--run-id <id>] [--receipt <path>]"
        )
    if not _optional_str(options.get("decision")):
        raise RuntimeError("dag-reconcile requires --decision <reconcile|abandon>")
    if not _optional_str(options.get("operator_id")):
        raise RuntimeError("dag-reconcile requires --operator <id>")
    if not _optional_str(options.get("reason")):
        raise RuntimeError("dag-reconcile requires --reason <text>")
    return options


def _write_dag_reconciliation_decision_receipt(
    *,
    run_dir: Path,
    decision: str,
    operator_id: str,
    reason: str,
    receipt_path: Path | None,
    run_id: str | None,
) -> dict[str, Any]:
    resolved_run_dir = run_dir.expanduser().resolve()
    store_path = resolved_run_dir / "dag-run.sqlite3"
    if not store_path.is_file():
        raise RuntimeError(f"dag run store not found: {store_path}")
    resolved_receipt = (
        receipt_path.expanduser().resolve()
        if receipt_path is not None
        else resolved_run_dir / "reconciliation-decision.json"
    )
    with SqliteDagRunStore(store_path) as store:
        selected_run_id = run_id
        if selected_run_id is None:
            records = store.reconciliation_required_runs()
            if not records:
                raise RuntimeError("no RECONCILIATION_REQUIRED run found")
            if len(records) > 1:
                raise RuntimeError("multiple RECONCILIATION_REQUIRED runs found; pass --run-id")
            selected_run_id = records[0].run_id
        try:
            receipt = store.resolve_reconciliation_required_run(
                run_id=selected_run_id,
                decision=decision,
                operator_id=operator_id,
                reason=reason,
            )
        except DagRunStoreError as exc:
            raise RuntimeError(str(exc)) from exc
    payload = {
        **receipt,
        "run_dir": str(resolved_run_dir),
        "run_store_path": str(store_path),
        "receipt_path": str(resolved_receipt),
    }
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    resolved_receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _parse_dag_clear_lease_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "run_dir": None,
        "run_id": None,
        "operator_id": None,
        "reason": None,
        "receipt": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--run-id", "--operator", "--reason", "--receipt"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            if arg == "--operator":
                options["operator_id"] = args[index]
            else:
                options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--run-id="):
            options["run_id"] = arg.partition("=")[2]
        elif arg.startswith("--operator="):
            options["operator_id"] = arg.partition("=")[2]
        elif arg.startswith("--reason="):
            options["reason"] = arg.partition("=")[2]
        elif arg.startswith("--receipt="):
            options["receipt"] = arg.partition("=")[2]
        elif arg.startswith("-"):
            raise RuntimeError(f"unknown dag-clear-lease option: {arg}")
        elif options["run_dir"] is None:
            options["run_dir"] = arg
        else:
            raise RuntimeError(f"unexpected dag-clear-lease argument: {arg}")
        index += 1
    if not _optional_str(options.get("run_dir")):
        raise RuntimeError(
            "Usage: tau dag-clear-lease <run-dir> --run-id <id> "
            "--operator <id> --reason <text> [--receipt <path>]"
        )
    if not _optional_str(options.get("run_id")):
        raise RuntimeError("dag-clear-lease requires --run-id <id>")
    if not _optional_str(options.get("operator_id")):
        raise RuntimeError("dag-clear-lease requires --operator <id>")
    if not _optional_str(options.get("reason")):
        raise RuntimeError("dag-clear-lease requires --reason <text>")
    return options


def _write_dag_clear_lease_receipt(
    *,
    run_dir: Path,
    run_id: str,
    operator_id: str,
    reason: str,
    receipt_path: Path | None,
) -> dict[str, Any]:
    resolved_run_dir = run_dir.expanduser().resolve()
    store_path = resolved_run_dir / "dag-run.sqlite3"
    if not store_path.is_file():
        raise RuntimeError(f"dag run store not found: {store_path}")
    resolved_receipt = (
        receipt_path.expanduser().resolve()
        if receipt_path is not None
        else resolved_run_dir / "stale-lease-clear.json"
    )
    with SqliteDagRunStore(store_path) as store:
        try:
            receipt = store.clear_stale_lease(
                run_id=run_id,
                operator_id=operator_id,
                reason=reason,
            )
        except DagRunStoreError as exc:
            raise RuntimeError(str(exc)) from exc
    payload = {
        **receipt,
        "run_dir": str(resolved_run_dir),
        "run_store_path": str(store_path),
        "receipt_path": str(resolved_receipt),
    }
    resolved_receipt.parent.mkdir(parents=True, exist_ok=True)
    resolved_receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _parse_generic_provider_dag_node_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "repo": Path("."),
        "label": "tau-generic-provider-dag-node",
        "max_attempts": 1,
        "receipt_timeout_seconds": 120.0,
        "herdr_workstation": None,
        "herdr_bin": "herdr",
        "session": None,
        "install_integrations": False,
        "cleanup_mode": "dry-run",
        "work_order_path": None,
    }
    required: dict[str, object | None] = {
        "node_id": None,
        "receipt_path": None,
        "provider_run_root": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--node-id",
            "--receipt-path",
            "--provider-run-root",
            "--repo",
            "--label",
            "--work-order-path",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            value: object = args[index]
            if key in {"receipt_path", "provider_run_root", "repo", "work_order_path"}:
                value = Path(str(value))
            if key in required:
                required[key] = value
            else:
                options[key] = value
        elif arg in {"--max-attempts", "--receipt-timeout-seconds", "--herdr-bin", "--session"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            value: object = args[index]
            if key == "max_attempts":
                value = int(str(value))
            elif key == "receipt_timeout_seconds":
                value = float(str(value))
            options[key] = value
        elif arg == "--herdr-workstation":
            index += 1
            if index >= len(args):
                raise RuntimeError("--herdr-workstation requires a value")
            options["herdr_workstation"] = Path(args[index])
        elif arg == "--install-integrations":
            options["install_integrations"] = True
        elif arg == "--no-install-integrations":
            options["install_integrations"] = False
        elif arg == "--cleanup-mode":
            index += 1
            if index >= len(args):
                raise RuntimeError("--cleanup-mode requires a value")
            options["cleanup_mode"] = args[index]
        else:
            raise RuntimeError(f"unknown generic-provider-dag-node option: {arg}")
        index += 1
    missing = [key for key, value in required.items() if value is None]
    if missing:
        raise RuntimeError(
            "Usage: tau generic-provider-dag-node --node-id <id> "
            "--receipt-path <path> --provider-run-root <dir> [options]; "
            f"missing {', '.join(missing)}"
        )
    return {**options, **required}


def _parse_dag_stress_poc_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "run_root": Path("experiments/goal-locked-subagents/proofs/dag-stress-poc"),
        "label": "tau-dag-stress-poc",
        "max_attempts": 3,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--run-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-root requires a value")
            options["run_root"] = Path(args[index])
        elif arg.startswith("--run-root="):
            options["run_root"] = Path(arg.partition("=")[2])
        elif arg == "--label":
            index += 1
            if index >= len(args):
                raise RuntimeError("--label requires a value")
            options["label"] = args[index]
        elif arg.startswith("--label="):
            options["label"] = arg.partition("=")[2]
        elif arg == "--max-attempts":
            index += 1
            if index >= len(args):
                raise RuntimeError("--max-attempts requires a value")
            options["max_attempts"] = int(args[index])
        elif arg.startswith("--max-attempts="):
            options["max_attempts"] = int(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown dag-stress-poc option: {arg}")
        index += 1
    return options


def _parse_dag_stress_inspect_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau dag-stress-inspect <run-dir>")
    return Path(args[0])


def _parse_dag_stress_campaign_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "run_root": Path("experiments/goal-locked-subagents/proofs/dag-stress-campaign"),
        "label": "tau-dag-stress-campaign",
        "max_budget": 5,
        "repetitions": 3,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--run-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-root requires a value")
            options["run_root"] = Path(args[index])
        elif arg.startswith("--run-root="):
            options["run_root"] = Path(arg.partition("=")[2])
        elif arg == "--label":
            index += 1
            if index >= len(args):
                raise RuntimeError("--label requires a value")
            options["label"] = args[index]
        elif arg.startswith("--label="):
            options["label"] = arg.partition("=")[2]
        elif arg == "--max-budget":
            index += 1
            if index >= len(args):
                raise RuntimeError("--max-budget requires a value")
            options["max_budget"] = int(args[index])
        elif arg.startswith("--max-budget="):
            options["max_budget"] = int(arg.partition("=")[2])
        elif arg == "--repetitions":
            index += 1
            if index >= len(args):
                raise RuntimeError("--repetitions requires a value")
            options["repetitions"] = int(args[index])
        elif arg.startswith("--repetitions="):
            options["repetitions"] = int(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown dag-stress-campaign option: {arg}")
        index += 1
    return options


def _parse_dag_stress_campaign_inspect_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau dag-stress-campaign-inspect <run-dir>")
    return Path(args[0])


def _parse_media_explainer_smoke_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "run_root": Path("experiments/goal-locked-subagents/proofs/media-explainer-smoke"),
        "label": "tau-media-explainer-smoke",
        "work_item": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--run-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-root requires a value")
            options["run_root"] = Path(args[index])
        elif arg.startswith("--run-root="):
            options["run_root"] = Path(arg.partition("=")[2])
        elif arg == "--label":
            index += 1
            if index >= len(args):
                raise RuntimeError("--label requires a value")
            options["label"] = args[index]
        elif arg.startswith("--label="):
            options["label"] = arg.partition("=")[2]
        elif arg == "--work-item":
            index += 1
            if index >= len(args):
                raise RuntimeError("--work-item requires a value")
            options["work_item"] = Path(args[index])
        elif arg.startswith("--work-item="):
            options["work_item"] = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown media-explainer-smoke option: {arg}")
        index += 1
    return options


def _parse_media_explainer_inspect_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau media-explainer-inspect <run-dir>")
    return Path(args[0])


def _parse_herdr_cleanup_cli_args(args: list[str]) -> dict[str, object]:
    if not args or args[0] not in {"audit", "dry-run", "apply", "gc"}:
        raise RuntimeError(
            "Usage: tau herdr-cleanup audit|dry-run|apply --run-dir <run-dir> "
            "[--workspace-lease <lease.json>] "
            "[--session-ownership <ownership.json>] [--herdr-bin herdr] "
            "[--session default] "
            "[--include-current-workspace]\n"
            "       tau herdr-cleanup gc --run-dir <receipt-dir> "
            "[--apply --approval-receipt <receipt.json>] [--herdr-bin herdr] "
            "[--session default] "
            "[--include-current-workspace]"
        )
    mode = args[0]
    run_dir: Path | None = None
    herdr_bin = "herdr"
    session = "default"
    include_current_workspace = False
    workspace_lease_path: Path | None = None
    session_ownership_path: Path | None = None
    approval_receipt_path: Path | None = None
    apply_gc = False
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--run-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-dir requires a value")
            run_dir = Path(args[index])
        elif arg.startswith("--run-dir="):
            run_dir = Path(arg.partition("=")[2])
        elif arg == "--herdr-bin":
            index += 1
            if index >= len(args):
                raise RuntimeError("--herdr-bin requires a value")
            herdr_bin = args[index]
        elif arg.startswith("--herdr-bin="):
            herdr_bin = arg.partition("=")[2]
        elif arg == "--session":
            index += 1
            if index >= len(args):
                raise RuntimeError("--session requires a value")
            session = args[index]
        elif arg.startswith("--session="):
            session = arg.partition("=")[2]
        elif arg == "--workspace-lease":
            index += 1
            if index >= len(args):
                raise RuntimeError("--workspace-lease requires a value")
            workspace_lease_path = Path(args[index])
        elif arg.startswith("--workspace-lease="):
            workspace_lease_path = Path(arg.partition("=")[2])
        elif arg == "--session-ownership":
            index += 1
            if index >= len(args):
                raise RuntimeError("--session-ownership requires a value")
            session_ownership_path = Path(args[index])
        elif arg.startswith("--session-ownership="):
            session_ownership_path = Path(arg.partition("=")[2])
        elif arg == "--approval-receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--approval-receipt requires a value")
            approval_receipt_path = Path(args[index])
        elif arg.startswith("--approval-receipt="):
            approval_receipt_path = Path(arg.partition("=")[2])
        elif arg == "--include-current-workspace":
            include_current_workspace = True
        elif arg == "--apply" and mode == "gc":
            apply_gc = True
        else:
            raise RuntimeError(f"unknown herdr-cleanup option: {arg}")
        index += 1
    if run_dir is None:
        raise RuntimeError("--run-dir is required")
    if not session.strip():
        raise RuntimeError("--session must be a non-empty string")
    return {
        "run_dir": run_dir,
        "mode": mode,
        "apply": apply_gc,
        "herdr_bin": herdr_bin,
        "session": session,
        "include_current_workspace": include_current_workspace,
        "workspace_lease_path": workspace_lease_path,
        "session_ownership_path": session_ownership_path,
        "approval_receipt_path": approval_receipt_path,
        "gc": mode == "gc",
    }


def _parse_approval_gate_check_cli_args(args: list[str]) -> dict[str, object]:
    approval_packet: Path | None = None
    requested_action = ""
    run_dir = Path("experiments/goal-locked-subagents/proofs/approval-gates")
    output: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--approval-packet":
            index += 1
            if index >= len(args):
                raise RuntimeError("--approval-packet requires a value")
            approval_packet = Path(args[index])
        elif arg.startswith("--approval-packet="):
            approval_packet = Path(arg.partition("=")[2])
        elif arg == "--requested-action":
            index += 1
            if index >= len(args):
                raise RuntimeError("--requested-action requires a value")
            requested_action = args[index]
        elif arg.startswith("--requested-action="):
            requested_action = arg.partition("=")[2]
        elif arg == "--run-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-dir requires a value")
            run_dir = Path(args[index])
        elif arg.startswith("--run-dir="):
            run_dir = Path(arg.partition("=")[2])
        elif arg == "--output":
            index += 1
            if index >= len(args):
                raise RuntimeError("--output requires a value")
            output = Path(args[index])
        elif arg.startswith("--output="):
            output = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown approval-gate-check option: {arg}")
        index += 1
    if approval_packet is None:
        raise RuntimeError("--approval-packet is required")
    if not requested_action:
        raise RuntimeError("--requested-action is required")
    return {
        "approval_packet": approval_packet,
        "requested_action": requested_action,
        "run_dir": run_dir,
        "output": output,
    }


def _parse_permission_request_cli_args(args: list[str]) -> dict[str, object]:
    action = ""
    resources: list[str] = []
    source_node = ""
    run_dir = Path("experiments/goal-locked-subagents/proofs/permissions")
    output: Path | None = None
    session_id: str | None = None
    request_id: str | None = None
    mode: str | None = None
    proposed_save_rule: str | None = None
    denied = False
    reason: str | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--action":
            index += 1
            if index >= len(args):
                raise RuntimeError("--action requires a value")
            action = args[index]
        elif arg.startswith("--action="):
            action = arg.partition("=")[2]
        elif arg == "--resource":
            index += 1
            if index >= len(args):
                raise RuntimeError("--resource requires a value")
            resources.append(args[index])
        elif arg.startswith("--resource="):
            resources.append(arg.partition("=")[2])
        elif arg == "--source-node":
            index += 1
            if index >= len(args):
                raise RuntimeError("--source-node requires a value")
            source_node = args[index]
        elif arg.startswith("--source-node="):
            source_node = arg.partition("=")[2]
        elif arg == "--run-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-dir requires a value")
            run_dir = Path(args[index])
        elif arg.startswith("--run-dir="):
            run_dir = Path(arg.partition("=")[2])
        elif arg == "--output":
            index += 1
            if index >= len(args):
                raise RuntimeError("--output requires a value")
            output = Path(args[index])
        elif arg.startswith("--output="):
            output = Path(arg.partition("=")[2])
        elif arg == "--session":
            index += 1
            if index >= len(args):
                raise RuntimeError("--session requires a value")
            session_id = args[index]
        elif arg.startswith("--session="):
            session_id = arg.partition("=")[2]
        elif arg == "--request-id":
            index += 1
            if index >= len(args):
                raise RuntimeError("--request-id requires a value")
            request_id = args[index]
        elif arg.startswith("--request-id="):
            request_id = arg.partition("=")[2]
        elif arg == "--mode":
            index += 1
            if index >= len(args):
                raise RuntimeError("--mode requires a value")
            mode = args[index]
        elif arg.startswith("--mode="):
            mode = arg.partition("=")[2]
        elif arg == "--save-rule":
            index += 1
            if index >= len(args):
                raise RuntimeError("--save-rule requires a value")
            proposed_save_rule = args[index]
        elif arg.startswith("--save-rule="):
            proposed_save_rule = arg.partition("=")[2]
        elif arg == "--deny":
            denied = True
        elif arg == "--reason":
            index += 1
            if index >= len(args):
                raise RuntimeError("--reason requires a value")
            reason = args[index]
        elif arg.startswith("--reason="):
            reason = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown permission-request option: {arg}")
        index += 1
    if not action:
        raise RuntimeError("--action is required")
    if not resources:
        raise RuntimeError("--resource is required")
    if not source_node:
        raise RuntimeError("--source-node is required")
    return {
        "action": action,
        "resources": resources,
        "source_node": source_node,
        "run_dir": run_dir,
        "output": output,
        "session_id": session_id,
        "request_id": request_id,
        "mode": mode,
        "proposed_save_rule": proposed_save_rule,
        "denied": denied,
        "reason": reason,
    }


def _parse_permission_reply_cli_args(args: list[str]) -> dict[str, object]:
    request_receipt: Path | None = None
    reply = ""
    output: Path | None = None
    actor_id: str | None = None
    scope: str | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--request":
            index += 1
            if index >= len(args):
                raise RuntimeError("--request requires a value")
            request_receipt = Path(args[index])
        elif arg.startswith("--request="):
            request_receipt = Path(arg.partition("=")[2])
        elif arg == "--reply":
            index += 1
            if index >= len(args):
                raise RuntimeError("--reply requires a value")
            reply = args[index]
        elif arg.startswith("--reply="):
            reply = arg.partition("=")[2]
        elif arg == "--output":
            index += 1
            if index >= len(args):
                raise RuntimeError("--output requires a value")
            output = Path(args[index])
        elif arg.startswith("--output="):
            output = Path(arg.partition("=")[2])
        elif arg == "--actor":
            index += 1
            if index >= len(args):
                raise RuntimeError("--actor requires a value")
            actor_id = args[index]
        elif arg.startswith("--actor="):
            actor_id = arg.partition("=")[2]
        elif arg == "--scope":
            index += 1
            if index >= len(args):
                raise RuntimeError("--scope requires a value")
            scope = args[index]
        elif arg.startswith("--scope="):
            scope = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown permission-reply option: {arg}")
        index += 1
    if request_receipt is None:
        raise RuntimeError("--request is required")
    if not reply:
        raise RuntimeError("--reply is required")
    return {
        "request_receipt": request_receipt,
        "reply": reply,
        "output": output,
        "actor_id": actor_id,
        "scope": scope,
    }


def _parse_run_status_cli_args(args: list[str]) -> Path:
    if args == ["--last"]:
        return _resolve_last_run_dir()
    if len(args) != 1:
        raise RuntimeError("Usage: tau run-status <run-dir>|--last")
    return Path(args[0])


def _parse_dag_viewer_link_cli_args(args: list[str]) -> Path:
    if args == ["--last"]:
        return _resolve_last_run_dir()
    if len(args) != 1:
        raise RuntimeError("Usage: tau dag-viewer-link <run-dir>|--last")
    return Path(args[0])


def _parse_compliance_package_cli_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError("Usage: tau compliance-package <run-dir> --out <package-dir> [--force]")
    options: dict[str, object] = {
        "run_dir": Path(args[0]),
        "out": None,
        "force": False,
    }
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            options["out"] = Path(args[index])
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        elif arg == "--force":
            options["force"] = True
        else:
            raise RuntimeError(f"unknown compliance-package option: {arg}")
        index += 1
    if options["out"] is None:
        raise RuntimeError("Usage: tau compliance-package <run-dir> --out <package-dir> [--force]")
    return options


def _parse_actor_manifest_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "run_id": None,
        "actors": [],
        "out": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--run-id":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-id requires a value")
            options["run_id"] = args[index]
        elif arg.startswith("--run-id="):
            options["run_id"] = arg.partition("=")[2]
        elif arg == "--actor":
            index += 1
            if index >= len(args):
                raise RuntimeError("--actor requires a value")
            cast_actors = options["actors"]
            if isinstance(cast_actors, list):
                cast_actors.append(args[index])
        elif arg.startswith("--actor="):
            cast_actors = options["actors"]
            if isinstance(cast_actors, list):
                cast_actors.append(arg.partition("=")[2])
        elif arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            options["out"] = Path(args[index])
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown actor-manifest option: {arg}")
        index += 1
    if not options["run_id"]:
        raise RuntimeError("Usage: tau actor-manifest --run-id <id> --actor <id:type:roles>")
    if not options["actors"]:
        raise RuntimeError("Usage: tau actor-manifest --run-id <id> --actor <id:type:roles>")
    return options


def _parse_environment_manifest_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "run_id": None,
        "network_policy": "unknown",
        "provider_access": "unknown",
        "mounted_paths": [],
        "secrets_visible": [],
        "tool_versions": {},
        "policy_profile": None,
        "data_boundary": None,
        "out": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--run-id":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-id requires a value")
            options["run_id"] = args[index]
        elif arg.startswith("--run-id="):
            options["run_id"] = arg.partition("=")[2]
        elif arg == "--network-policy":
            index += 1
            if index >= len(args):
                raise RuntimeError("--network-policy requires a value")
            options["network_policy"] = args[index]
        elif arg.startswith("--network-policy="):
            options["network_policy"] = arg.partition("=")[2]
        elif arg == "--provider-access":
            index += 1
            if index >= len(args):
                raise RuntimeError("--provider-access requires a value")
            options["provider_access"] = args[index]
        elif arg.startswith("--provider-access="):
            options["provider_access"] = arg.partition("=")[2]
        elif arg == "--mounted-path":
            index += 1
            if index >= len(args):
                raise RuntimeError("--mounted-path requires a value")
            _append_option(options, "mounted_paths", args[index])
        elif arg.startswith("--mounted-path="):
            _append_option(options, "mounted_paths", arg.partition("=")[2])
        elif arg == "--secret-visible":
            index += 1
            if index >= len(args):
                raise RuntimeError("--secret-visible requires a value")
            _append_option(options, "secrets_visible", args[index])
        elif arg.startswith("--secret-visible="):
            _append_option(options, "secrets_visible", arg.partition("=")[2])
        elif arg == "--tool-version":
            index += 1
            if index >= len(args):
                raise RuntimeError("--tool-version requires name=value")
            _set_tool_version(options, args[index])
        elif arg.startswith("--tool-version="):
            _set_tool_version(options, arg.partition("=")[2])
        elif arg == "--policy-profile":
            index += 1
            if index >= len(args):
                raise RuntimeError("--policy-profile requires a value")
            options["policy_profile"] = args[index]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg == "--data-boundary":
            index += 1
            if index >= len(args):
                raise RuntimeError("--data-boundary requires a value")
            options["data_boundary"] = args[index]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            options["out"] = Path(args[index])
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown environment-manifest option: {arg}")
        index += 1
    if not options["run_id"]:
        raise RuntimeError("Usage: tau environment-manifest --run-id <id>")
    return options


def _parse_sign_receipt_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "receipt": None,
        "key": None,
        "out": None,
        "actor_manifest": None,
        "environment_manifest": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            options["receipt"] = Path(args[index])
        elif arg.startswith("--receipt="):
            options["receipt"] = Path(arg.partition("=")[2])
        elif arg == "--key":
            index += 1
            if index >= len(args):
                raise RuntimeError("--key requires a value")
            options["key"] = Path(args[index])
        elif arg.startswith("--key="):
            options["key"] = Path(arg.partition("=")[2])
        elif arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            options["out"] = Path(args[index])
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        elif arg == "--actor-manifest":
            index += 1
            if index >= len(args):
                raise RuntimeError("--actor-manifest requires a value")
            options["actor_manifest"] = Path(args[index])
        elif arg.startswith("--actor-manifest="):
            options["actor_manifest"] = Path(arg.partition("=")[2])
        elif arg == "--environment-manifest":
            index += 1
            if index >= len(args):
                raise RuntimeError("--environment-manifest requires a value")
            options["environment_manifest"] = Path(args[index])
        elif arg.startswith("--environment-manifest="):
            options["environment_manifest"] = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown sign-receipt option: {arg}")
        index += 1
    if options["receipt"] is None or options["key"] is None:
        raise RuntimeError("Usage: tau sign-receipt --receipt <json> --key <key> [--out <json>]")
    return options


def _parse_verify_signed_receipt_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "signed_receipt": None,
        "key": None,
        "out": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--signed-receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--signed-receipt requires a value")
            options["signed_receipt"] = Path(args[index])
        elif arg.startswith("--signed-receipt="):
            options["signed_receipt"] = Path(arg.partition("=")[2])
        elif arg == "--key":
            index += 1
            if index >= len(args):
                raise RuntimeError("--key requires a value")
            options["key"] = Path(args[index])
        elif arg.startswith("--key="):
            options["key"] = Path(arg.partition("=")[2])
        elif arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            options["out"] = Path(args[index])
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown verify-signed-receipt option: {arg}")
        index += 1
    if options["signed_receipt"] is None or options["key"] is None:
        raise RuntimeError("Usage: tau verify-signed-receipt --signed-receipt <json> --key <key>")
    return options


def _parse_sandbox_run_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "policy_profile": None,
        "data_boundary": None,
        "out": None,
        "timeout_seconds": 30.0,
        "backend": "bwrap",
        "image": None,
        "stdin_file": None,
        "work_dir": None,
        "goal_hash": None,
        "work_order_sha256": None,
        "command": [],
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            options["command"] = args[index + 1 :]
            break
        if arg == "--policy-profile":
            index += 1
            if index >= len(args):
                raise RuntimeError("--policy-profile requires a value")
            options["policy_profile"] = Path(args[index])
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = Path(arg.partition("=")[2])
        elif arg == "--data-boundary":
            index += 1
            if index >= len(args):
                raise RuntimeError("--data-boundary requires a value")
            options["data_boundary"] = Path(args[index])
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = Path(arg.partition("=")[2])
        elif arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            options["out"] = Path(args[index])
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        elif arg == "--timeout-seconds":
            index += 1
            if index >= len(args):
                raise RuntimeError("--timeout-seconds requires a value")
            options["timeout_seconds"] = float(args[index])
        elif arg.startswith("--timeout-seconds="):
            options["timeout_seconds"] = float(arg.partition("=")[2])
        elif arg == "--backend":
            index += 1
            if index >= len(args):
                raise RuntimeError("--backend requires a value")
            options["backend"] = args[index]
        elif arg.startswith("--backend="):
            options["backend"] = arg.partition("=")[2]
        elif arg == "--image":
            index += 1
            if index >= len(args):
                raise RuntimeError("--image requires a value")
            options["image"] = args[index]
        elif arg.startswith("--image="):
            options["image"] = arg.partition("=")[2]
        elif arg == "--stdin-file":
            index += 1
            if index >= len(args):
                raise RuntimeError("--stdin-file requires a value")
            options["stdin_file"] = Path(args[index])
        elif arg.startswith("--stdin-file="):
            options["stdin_file"] = Path(arg.partition("=")[2])
        elif arg == "--work-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--work-dir requires a value")
            options["work_dir"] = Path(args[index])
        elif arg.startswith("--work-dir="):
            options["work_dir"] = Path(arg.partition("=")[2])
        elif arg == "--goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--goal-hash requires a value")
            options["goal_hash"] = args[index]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg == "--work-order-sha256":
            index += 1
            if index >= len(args):
                raise RuntimeError("--work-order-sha256 requires a value")
            options["work_order_sha256"] = args[index]
        elif arg.startswith("--work-order-sha256="):
            options["work_order_sha256"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown sandbox-run option: {arg}")
        index += 1
    if options["policy_profile"] is None or options["data_boundary"] is None:
        raise RuntimeError(
            "Usage: tau sandbox-run --policy-profile <policy.json> "
            "--data-boundary <boundary.json> [--out <receipt.json>] -- <command...>"
        )
    if not options["command"]:
        raise RuntimeError("sandbox-run requires a command after --")
    timeout = float(options["timeout_seconds"])
    if timeout <= 0:
        raise RuntimeError("--timeout-seconds must be positive")
    return options


def _parse_report_cli_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError("Usage: tau report <run-dir> --out <report.html> [--force]")
    options: dict[str, object] = {
        "run_dir": Path(args[0]),
        "out": None,
        "force": False,
    }
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            options["out"] = Path(args[index])
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        elif arg == "--force":
            options["force"] = True
        else:
            raise RuntimeError(f"unknown report option: {arg}")
        index += 1
    if options["out"] is None:
        raise RuntimeError("Usage: tau report <run-dir> --out <report.html> [--force]")
    return options


def _parse_serve_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 8768,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--host":
            index += 1
            if index >= len(args):
                raise RuntimeError("--host requires a value")
            options["host"] = args[index]
        elif arg.startswith("--host="):
            options["host"] = arg.partition("=")[2]
        elif arg == "--port":
            index += 1
            if index >= len(args):
                raise RuntimeError("--port requires a value")
            options["port"] = int(args[index])
        elif arg.startswith("--port="):
            options["port"] = int(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown serve option: {arg}")
        index += 1
    if not isinstance(options["host"], str) or not options["host"]:
        raise RuntimeError("--host must be non-empty")
    if int(options["port"]) < 1 or int(options["port"]) > 65535:
        raise RuntimeError("--port must be between 1 and 65535")
    return options


def _append_option(options: dict[str, object], key: str, value: str) -> None:
    current = options[key]
    if isinstance(current, list):
        current.append(value)


def _set_tool_version(options: dict[str, object], value: str) -> None:
    name, separator, version = value.partition("=")
    if not separator or not name or not version:
        raise RuntimeError("--tool-version requires name=value")
    current = options["tool_versions"]
    if isinstance(current, dict):
        current[name] = version


def _parse_dag_fail_closed_registry_args(args: list[str]) -> Path | None:
    output_path: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            output_path = Path(args[index])
        elif arg.startswith("--out="):
            output_path = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown dag-fail-closed-registry option: {arg}")
        index += 1
    return output_path


def _parse_course_correction_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "trigger": None,
        "out": None,
        "run_id": None,
        "dag_id": None,
        "goal_hash": None,
        "target": None,
        "node_id": None,
        "agent": None,
        "attempt": None,
        "observed_state": None,
        "observed_artifact": None,
        "reason": None,
        "stop_reason": None,
        "error": [],
        "mocked": False,
        "live": False,
        "provider_live": False,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--trigger",
            "--out",
            "--run-id",
            "--dag-id",
            "--goal-hash",
            "--target-json",
            "--node-id",
            "--agent",
            "--attempt",
            "--observed-state-json",
            "--observed-artifact",
            "--reason",
            "--stop-reason",
            "--error",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            _set_course_correction_option(options, arg, args[index])
        elif arg.startswith("--trigger="):
            options["trigger"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--run-id="):
            options["run_id"] = arg.partition("=")[2]
        elif arg.startswith("--dag-id="):
            options["dag_id"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--target-json="):
            options["target"] = arg.partition("=")[2]
        elif arg.startswith("--node-id="):
            options["node_id"] = arg.partition("=")[2]
        elif arg.startswith("--agent="):
            options["agent"] = arg.partition("=")[2]
        elif arg.startswith("--attempt="):
            options["attempt"] = int(arg.partition("=")[2])
        elif arg.startswith("--observed-state-json="):
            options["observed_state"] = arg.partition("=")[2]
        elif arg.startswith("--observed-artifact="):
            options["observed_artifact"] = arg.partition("=")[2]
        elif arg.startswith("--reason="):
            options["reason"] = arg.partition("=")[2]
        elif arg.startswith("--stop-reason="):
            options["stop_reason"] = arg.partition("=")[2]
        elif arg.startswith("--error="):
            _append_option(options, "error", arg.partition("=")[2])
        elif arg == "--mocked":
            options["mocked"] = True
        elif arg == "--live":
            options["live"] = True
        elif arg == "--provider-live":
            options["provider_live"] = True
        else:
            raise RuntimeError(f"unknown course-correction option: {arg}")
        index += 1
    if not _optional_str(options.get("trigger")):
        raise RuntimeError("Usage: tau course-correction --trigger <code> --out <receipt.json>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau course-correction --trigger <code> --out <receipt.json>")
    return options


def _parse_code_patch_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "patch": None,
        "repo": ".",
        "out": None,
        "goal_hash": None,
        "policy_profile": None,
        "data_boundary": None,
        "zero_trust": False,
        "dry_run": False,
        "run_id": None,
        "dag_id": None,
        "node_id": None,
        "agent": None,
        "attempt": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--patch",
            "--repo",
            "--out",
            "--goal-hash",
            "--policy-profile",
            "--data-boundary",
            "--run-id",
            "--dag-id",
            "--node-id",
            "--agent",
            "--attempt",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = int(args[index]) if key == "attempt" else args[index]
        elif arg.startswith("--patch="):
            options["patch"] = arg.partition("=")[2]
        elif arg.startswith("--repo="):
            options["repo"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg.startswith("--run-id="):
            options["run_id"] = arg.partition("=")[2]
        elif arg.startswith("--dag-id="):
            options["dag_id"] = arg.partition("=")[2]
        elif arg.startswith("--node-id="):
            options["node_id"] = arg.partition("=")[2]
        elif arg.startswith("--agent="):
            options["agent"] = arg.partition("=")[2]
        elif arg.startswith("--attempt="):
            options["attempt"] = int(arg.partition("=")[2])
        elif arg == "--zero-trust":
            options["zero_trust"] = True
        elif arg == "--dry-run":
            options["dry_run"] = True
        else:
            raise RuntimeError(f"unknown code-patch option: {arg}")
        index += 1
    if not _optional_str(options.get("patch")):
        raise RuntimeError("Usage: tau code-patch --patch <patch.json> [--repo <repo>]")
    return options


def _parse_review_findings_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "findings": None,
        "out": None,
        "goal_hash": None,
        "zero_trust": False,
        "policy_profile": None,
        "data_boundary": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--findings",
            "--out",
            "--goal-hash",
            "--policy-profile",
            "--data-boundary",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = args[index]
        elif arg.startswith("--findings="):
            options["findings"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg == "--zero-trust":
            options["zero_trust"] = True
        else:
            raise RuntimeError(f"unknown review-findings option: {arg}")
        index += 1
    if not _optional_str(options.get("findings")):
        raise RuntimeError("Usage: tau review-findings --findings <findings.json>")
    return options


def _parse_lsp_diagnostics_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "workspace": ".",
        "out": None,
        "required": False,
        "zero_trust": False,
        "policy_profile": None,
        "data_boundary": None,
        "goal_hash": None,
        "baseline_receipt": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--workspace",
            "--out",
            "--policy-profile",
            "--data-boundary",
            "--goal-hash",
            "--baseline-receipt",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--workspace="):
            options["workspace"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--baseline-receipt="):
            options["baseline_receipt"] = arg.partition("=")[2]
        elif arg == "--required":
            options["required"] = True
        elif arg == "--zero-trust":
            options["zero_trust"] = True
        else:
            raise RuntimeError(f"unknown lsp-diagnostics option: {arg}")
        index += 1
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau lsp-diagnostics --workspace <path> --out <receipt>")
    return options


def _parse_lsp_symbols_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "workspace": ".",
        "query": None,
        "out": None,
        "zero_trust": False,
        "policy_profile": None,
        "data_boundary": None,
        "goal_hash": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--workspace",
            "--query",
            "--out",
            "--policy-profile",
            "--data-boundary",
            "--goal-hash",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--workspace="):
            options["workspace"] = arg.partition("=")[2]
        elif arg.startswith("--query="):
            options["query"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg == "--zero-trust":
            options["zero_trust"] = True
        else:
            raise RuntimeError(f"unknown lsp-symbols option: {arg}")
        index += 1
    if not _optional_str(options.get("query")):
        raise RuntimeError(
            "Usage: tau lsp-symbols --workspace <path> --query <symbol> --out <receipt>"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            "Usage: tau lsp-symbols --workspace <path> --query <symbol> --out <receipt>"
        )
    return options


def _parse_lsp_rename_plan_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "workspace": ".",
        "symbol": None,
        "new_name": None,
        "out": None,
        "zero_trust": False,
        "policy_profile": None,
        "data_boundary": None,
        "goal_hash": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--workspace",
            "--symbol",
            "--new-name",
            "--out",
            "--policy-profile",
            "--data-boundary",
            "--goal-hash",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--workspace="):
            options["workspace"] = arg.partition("=")[2]
        elif arg.startswith("--symbol="):
            options["symbol"] = arg.partition("=")[2]
        elif arg.startswith("--new-name="):
            options["new_name"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg == "--zero-trust":
            options["zero_trust"] = True
        else:
            raise RuntimeError(f"unknown lsp-rename-plan option: {arg}")
        index += 1
    if not _optional_str(options.get("symbol")) or not _optional_str(options.get("new_name")):
        raise RuntimeError(
            "Usage: tau lsp-rename-plan --symbol <symbol> --new-name <name> --out <receipt>"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            "Usage: tau lsp-rename-plan --symbol <symbol> --new-name <name> --out <receipt>"
        )
    return options


def _parse_test_run_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "repo": ".",
        "out": None,
        "command": [],
        "tested_paths": [],
        "timeout_s": 120,
        "zero_trust": False,
        "policy_profile": None,
        "data_boundary": None,
        "goal_hash": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--repo",
            "--out",
            "--command",
            "--tested-path",
            "--timeout-s",
            "--policy-profile",
            "--data-boundary",
            "--goal-hash",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            if arg == "--command":
                command = options["command"]
                if isinstance(command, list):
                    command.append(args[index])
            elif arg == "--tested-path":
                tested_paths = options["tested_paths"]
                if isinstance(tested_paths, list):
                    tested_paths.append(args[index])
            else:
                options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--repo="):
            options["repo"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--command="):
            command = options["command"]
            if isinstance(command, list):
                command.append(arg.partition("=")[2])
        elif arg.startswith("--tested-path="):
            tested_paths = options["tested_paths"]
            if isinstance(tested_paths, list):
                tested_paths.append(arg.partition("=")[2])
        elif arg.startswith("--timeout-s="):
            options["timeout_s"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg == "--zero-trust":
            options["zero_trust"] = True
        else:
            raise RuntimeError(f"unknown test-run option: {arg}")
        index += 1
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau test-run --repo <repo> --out <receipt>")
    if not options["command"]:
        options["command"] = [sys.executable, "-m", "pytest", "-q"]
    try:
        options["timeout_s"] = int(str(options["timeout_s"]))
    except ValueError as exc:
        raise RuntimeError("--timeout-s must be an integer") from exc
    return options


def _parse_commit_plan_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "repo": ".",
        "out": None,
        "apply": False,
        "zero_trust": False,
        "policy_profile": None,
        "data_boundary": None,
        "goal_hash": None,
        "evidence_receipts": [],
        "approval_receipt": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--repo",
            "--out",
            "--policy-profile",
            "--data-boundary",
            "--goal-hash",
            "--evidence-receipt",
            "--approval-receipt",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            if arg == "--evidence-receipt":
                options["evidence_receipts"].append(args[index])
            else:
                options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--repo="):
            options["repo"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--evidence-receipt="):
            options["evidence_receipts"].append(arg.partition("=")[2])
        elif arg.startswith("--approval-receipt="):
            options["approval_receipt"] = arg.partition("=")[2]
        elif arg == "--apply":
            options["apply"] = True
        elif arg == "--zero-trust":
            options["zero_trust"] = True
        else:
            raise RuntimeError(f"unknown commit-plan option: {arg}")
        index += 1
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau commit-plan --repo <repo> --out <receipt>")
    return options


def _parse_orchestration_reliability_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "run_dir": None,
        "dag_receipt": None,
        "out": None,
        "required_receipts": [],
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--run-dir", "--dag-receipt", "--out", "--required-receipt"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            if arg == "--required-receipt":
                required = options["required_receipts"]
                if isinstance(required, list):
                    required.append(args[index])
            else:
                options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--run-dir="):
            options["run_dir"] = arg.partition("=")[2]
        elif arg.startswith("--dag-receipt="):
            options["dag_receipt"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--required-receipt="):
            required = options["required_receipts"]
            if isinstance(required, list):
                required.append(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown orchestration-reliability option: {arg}")
        index += 1
    if not _optional_str(options.get("run_dir")) and not _optional_str(options.get("dag_receipt")):
        raise RuntimeError(
            "Usage: tau orchestration-reliability "
            "(--run-dir <dir> | --dag-receipt <receipt>) --out <receipt>"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            "Usage: tau orchestration-reliability "
            "(--run-dir <dir> | --dag-receipt <receipt>) --out <receipt>"
        )
    return options


def _parse_worker_validate_cli_args(args: list[str], *, command: str) -> dict[str, object]:
    options: dict[str, object] = {
        "work_order": None,
        "result": None,
        "out": None,
        "launch_receipt": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--work-order", "--result", "--out", "--launch-receipt"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--work-order="):
            options["work_order"] = arg.partition("=")[2]
        elif arg.startswith("--result="):
            options["result"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--launch-receipt="):
            options["launch_receipt"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown {command} option: {arg}")
        index += 1
    if not _optional_str(options.get("work_order")):
        raise RuntimeError(
            f"Usage: tau {command} --work-order <json> --result <json> --out <receipt>"
        )
    if not _optional_str(options.get("result")):
        raise RuntimeError(
            f"Usage: tau {command} --work-order <json> --result <json> --out <receipt>"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            f"Usage: tau {command} --work-order <json> --result <json> --out <receipt>"
        )
    return options


def _parse_omp_worker_launch_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "work_order": None,
        "out": None,
        "caller_skill": "tau",
        "apply": False,
        "omp_bin": "omp",
        "timeout_s": 600,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--work-order", "--out", "--caller-skill", "--omp-bin", "--timeout-s"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = int(args[index]) if key == "timeout_s" else args[index]
        elif arg.startswith("--work-order="):
            options["work_order"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--caller-skill="):
            options["caller_skill"] = arg.partition("=")[2]
        elif arg.startswith("--omp-bin="):
            options["omp_bin"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-s="):
            options["timeout_s"] = int(arg.partition("=")[2])
        elif arg == "--apply":
            options["apply"] = True
        else:
            raise RuntimeError(f"unknown omp-worker-launch option: {arg}")
        index += 1
    if not _optional_str(options.get("work_order")):
        raise RuntimeError("Usage: tau omp-worker-launch --work-order <json> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau omp-worker-launch --work-order <json> --out <receipt>")
    return options


def _parse_omp_worker_doctor_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "out": None,
        "omp_bin": "omp",
        "timeout_s": 10,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--out", "--omp-bin", "--timeout-s"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = int(args[index]) if key == "timeout_s" else args[index]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--omp-bin="):
            options["omp_bin"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-s="):
            options["timeout_s"] = int(arg.partition("=")[2])
        else:
            raise RuntimeError(f"unknown omp-worker-doctor option: {arg}")
        index += 1
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau omp-worker-doctor --out <receipt>")
    return options


def _parse_scillm_worker_launch_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "work_order": None,
        "out": None,
        "scillm_base_url": "http://localhost:4001",
        "caller_skill": "tau",
        "apply": False,
        "auth_token": None,
        "request_timeout_s": 600,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--work-order",
            "--out",
            "--scillm-base-url",
            "--caller-skill",
            "--auth-token",
            "--request-timeout-s",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = int(args[index]) if key == "request_timeout_s" else args[index]
        elif arg.startswith("--work-order="):
            options["work_order"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--scillm-base-url="):
            options["scillm_base_url"] = arg.partition("=")[2]
        elif arg.startswith("--caller-skill="):
            options["caller_skill"] = arg.partition("=")[2]
        elif arg.startswith("--auth-token="):
            options["auth_token"] = arg.partition("=")[2]
        elif arg.startswith("--request-timeout-s="):
            options["request_timeout_s"] = int(arg.partition("=")[2])
        elif arg == "--apply":
            options["apply"] = True
        else:
            raise RuntimeError(f"unknown scillm-worker-launch option: {arg}")
        index += 1
    if not _optional_str(options.get("work_order")):
        raise RuntimeError("Usage: tau scillm-worker-launch --work-order <json> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau scillm-worker-launch --work-order <json> --out <receipt>")
    return options


def _parse_scillm_chat_review_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "request": None,
        "out": None,
        "response_out": None,
        "scillm_base_url": "http://localhost:4001",
        "caller_skill": "tau",
        "apply": False,
        "auth_token": None,
        "request_timeout_s": 120,
        "timeout_diagnosis_mode": "off",
        "timeout_diagnosis_timeout_s": 30,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--request",
            "--out",
            "--response-out",
            "--scillm-base-url",
            "--caller-skill",
            "--auth-token",
            "--request-timeout-s",
            "--timeout-diagnosis-mode",
            "--timeout-diagnosis-timeout-s",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = (
                int(args[index])
                if key in {"request_timeout_s", "timeout_diagnosis_timeout_s"}
                else args[index]
            )
        elif arg.startswith("--request="):
            options["request"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--response-out="):
            options["response_out"] = arg.partition("=")[2]
        elif arg.startswith("--scillm-base-url="):
            options["scillm_base_url"] = arg.partition("=")[2]
        elif arg.startswith("--caller-skill="):
            options["caller_skill"] = arg.partition("=")[2]
        elif arg.startswith("--auth-token="):
            options["auth_token"] = arg.partition("=")[2]
        elif arg.startswith("--request-timeout-s="):
            options["request_timeout_s"] = int(arg.partition("=")[2])
        elif arg.startswith("--timeout-diagnosis-mode="):
            options["timeout_diagnosis_mode"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-diagnosis-timeout-s="):
            options["timeout_diagnosis_timeout_s"] = int(arg.partition("=")[2])
        elif arg == "--apply":
            options["apply"] = True
        else:
            raise RuntimeError(f"unknown scillm-chat-review option: {arg}")
        index += 1
    if not _optional_str(options.get("request")):
        raise RuntimeError("Usage: tau scillm-chat-review --request <json> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau scillm-chat-review --request <json> --out <receipt>")
    return options


def _parse_pdf_lab_second_pass_review_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "contract": None,
        "out": None,
        "artifact_root": None,
        "scillm_base_url": "http://localhost:4001",
        "caller_skill": "pdf-lab",
        "apply": False,
        "auth_token": None,
        "request_timeout_s": 900,
        "timeout_diagnosis_mode": "live_canary",
        "timeout_diagnosis_timeout_s": 30,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--contract",
            "--out",
            "--artifact-root",
            "--scillm-base-url",
            "--caller-skill",
            "--auth-token",
            "--request-timeout-s",
            "--timeout-diagnosis-mode",
            "--timeout-diagnosis-timeout-s",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = (
                int(args[index])
                if key in {"request_timeout_s", "timeout_diagnosis_timeout_s"}
                else args[index]
            )
        elif arg.startswith("--contract="):
            options["contract"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--artifact-root="):
            options["artifact_root"] = arg.partition("=")[2]
        elif arg.startswith("--scillm-base-url="):
            options["scillm_base_url"] = arg.partition("=")[2]
        elif arg.startswith("--caller-skill="):
            options["caller_skill"] = arg.partition("=")[2]
        elif arg.startswith("--auth-token="):
            options["auth_token"] = arg.partition("=")[2]
        elif arg.startswith("--request-timeout-s="):
            options["request_timeout_s"] = int(arg.partition("=")[2])
        elif arg.startswith("--timeout-diagnosis-mode="):
            options["timeout_diagnosis_mode"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-diagnosis-timeout-s="):
            options["timeout_diagnosis_timeout_s"] = int(arg.partition("=")[2])
        elif arg == "--apply":
            options["apply"] = True
        else:
            raise RuntimeError(f"unknown pdf-lab-second-pass-review option: {arg}")
        index += 1
    if not _optional_str(options.get("contract")):
        raise RuntimeError(
            "Usage: tau pdf-lab-second-pass-review --contract <json> --out <receipt>"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            "Usage: tau pdf-lab-second-pass-review --contract <json> --out <receipt>"
        )
    return options


def _parse_debug_session_receipt_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "session": None,
        "out": None,
        "required": False,
        "zero_trust": False,
        "policy_profile": None,
        "data_boundary": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--session", "--out", "--goal-hash", "--policy-profile", "--data-boundary"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--session="):
            options["session"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg == "--required":
            options["required"] = True
        elif arg == "--zero-trust":
            options["zero_trust"] = True
        else:
            raise RuntimeError(f"unknown debug-session-receipt option: {arg}")
        index += 1
    if not _optional_str(options.get("session")):
        raise RuntimeError("Usage: tau debug-session-receipt --session <json> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau debug-session-receipt --session <json> --out <receipt>")
    return options


def _parse_github_read_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "uri": None,
        "out": None,
        "zero_trust": False,
        "policy_profile": None,
        "data_boundary": None,
        "goal_hash": None,
        "execute": False,
        "gh_bin": "gh",
        "timeout_s": 30,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--uri",
            "--out",
            "--policy-profile",
            "--data-boundary",
            "--goal-hash",
            "--gh-bin",
            "--timeout-s",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--uri="):
            options["uri"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--gh-bin="):
            options["gh_bin"] = arg.partition("=")[2]
        elif arg.startswith("--timeout-s="):
            options["timeout_s"] = arg.partition("=")[2]
        elif arg == "--zero-trust":
            options["zero_trust"] = True
        elif arg == "--execute":
            options["execute"] = True
        else:
            raise RuntimeError(f"unknown github-read option: {arg}")
        index += 1
    if not _optional_str(options.get("uri")):
        raise RuntimeError("Usage: tau github-read --uri <github-uri> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau github-read --uri <github-uri> --out <receipt>")
    return options


def _parse_herdr_observation_gate_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "snapshot": None,
        "out": None,
        "expected_receipt": None,
        "expected_workspace_id": None,
        "expected_pane_id": None,
        "expected_terminal_id": None,
        "run_id": None,
        "dag_id": None,
        "goal_hash": None,
        "node_id": None,
        "agent": None,
        "attempt": None,
        "receipt_timeout_seconds": None,
        "receipt_overdue": False,
        "mocked": False,
        "live": True,
        "provider_live": False,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--snapshot",
            "--out",
            "--expected-receipt",
            "--expected-workspace-id",
            "--expected-pane-id",
            "--expected-terminal-id",
            "--run-id",
            "--dag-id",
            "--goal-hash",
            "--node-id",
            "--agent",
            "--attempt",
            "--receipt-timeout-seconds",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            _set_herdr_observation_gate_option(options, arg, args[index])
        elif arg.startswith("--snapshot="):
            options["snapshot"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--expected-receipt="):
            options["expected_receipt"] = arg.partition("=")[2]
        elif arg.startswith("--expected-workspace-id="):
            options["expected_workspace_id"] = arg.partition("=")[2]
        elif arg.startswith("--expected-pane-id="):
            options["expected_pane_id"] = arg.partition("=")[2]
        elif arg.startswith("--expected-terminal-id="):
            options["expected_terminal_id"] = arg.partition("=")[2]
        elif arg.startswith("--run-id="):
            options["run_id"] = arg.partition("=")[2]
        elif arg.startswith("--dag-id="):
            options["dag_id"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--node-id="):
            options["node_id"] = arg.partition("=")[2]
        elif arg.startswith("--agent="):
            options["agent"] = arg.partition("=")[2]
        elif arg.startswith("--attempt="):
            options["attempt"] = int(arg.partition("=")[2])
        elif arg.startswith("--receipt-timeout-seconds="):
            options["receipt_timeout_seconds"] = float(arg.partition("=")[2])
        elif arg == "--receipt-overdue":
            options["receipt_overdue"] = True
        elif arg == "--mocked":
            options["mocked"] = True
            options["live"] = False
        elif arg == "--live":
            options["live"] = True
        elif arg == "--provider-live":
            options["provider_live"] = True
        else:
            raise RuntimeError(f"unknown herdr-observation-gate option: {arg}")
        index += 1
    if not _optional_str(options.get("snapshot")):
        raise RuntimeError("Usage: tau herdr-observation-gate --snapshot <json> --out <json>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau herdr-observation-gate --snapshot <json> --out <json>")
    return options


def _parse_project_profile_validate_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {"profile": None, "out": None, "registry": None}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--profile", "--out", "--registry"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--")] = args[index]
        elif arg.startswith("--profile="):
            options["profile"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--registry="):
            options["registry"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown project-profile-validate option: {arg}")
        index += 1
    if not _optional_str(options.get("profile")):
        raise RuntimeError("Usage: tau project-profile-validate --profile <json> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau project-profile-validate --profile <json> --out <receipt>")
    return options


def _parse_skill_capability_registry_validate_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "registry": None,
        "out": None,
        "skills_root": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--registry", "--out", "--skills-root"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--registry="):
            options["registry"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--skills-root="):
            options["skills_root"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown skill-capability-registry-validate option: {arg}")
        index += 1
    if not _optional_str(options.get("registry")):
        raise RuntimeError(
            "Usage: tau skill-capability-registry-validate "
            "--registry <json> --out <receipt> [--skills-root <dir>]"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            "Usage: tau skill-capability-registry-validate "
            "--registry <json> --out <receipt> [--skills-root <dir>]"
        )
    return options


def _parse_skill_capability_registry_default_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {"out": None}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            options["out"] = args[index]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown skill-capability-registry-default option: {arg}")
        index += 1
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau skill-capability-registry-default --out <registry.json>")
    return options


def _parse_skill_invocation_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "request": None,
        "out": None,
        "repo_root": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--request", "--out", "--repo-root"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--request="):
            options["request"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--repo-root="):
            options["repo_root"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown skill-invocation option: {arg}")
        index += 1
    if not _optional_str(options.get("request")):
        raise RuntimeError(
            "Usage: tau skill-invocation --request <json> --out <receipt> [--repo-root <dir>]"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            "Usage: tau skill-invocation --request <json> --out <receipt> [--repo-root <dir>]"
        )
    return options


def _parse_debugger_skill_adapter_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "proof": None,
        "out": None,
        "debug_session_out": None,
        "repo_root": None,
        "goal_hash": None,
        "zero_trust": False,
        "policy_profile": None,
        "data_boundary": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--proof",
            "--out",
            "--debug-session-out",
            "--repo-root",
            "--goal-hash",
            "--policy-profile",
            "--data-boundary",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--proof="):
            options["proof"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--debug-session-out="):
            options["debug_session_out"] = arg.partition("=")[2]
        elif arg.startswith("--repo-root="):
            options["repo_root"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        elif arg == "--zero-trust":
            options["zero_trust"] = True
        else:
            raise RuntimeError(f"unknown debugger-skill-adapter option: {arg}")
        index += 1
    if not _optional_str(options.get("proof")):
        raise RuntimeError(
            "Usage: tau debugger-skill-adapter --proof <json> --out <receipt> "
            "--debug-session-out <receipt> [--repo-root <dir>]"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            "Usage: tau debugger-skill-adapter --proof <json> --out <receipt> "
            "--debug-session-out <receipt> [--repo-root <dir>]"
        )
    if not _optional_str(options.get("debug_session_out")):
        raise RuntimeError(
            "Usage: tau debugger-skill-adapter --proof <json> --out <receipt> "
            "--debug-session-out <receipt> [--repo-root <dir>]"
        )
    return options


def _parse_code_runner_skill_adapter_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "result": None,
        "out": None,
        "repo_root": None,
        "goal_hash": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--result", "--out", "--repo-root", "--goal-hash"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--result="):
            options["result"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--repo-root="):
            options["repo_root"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown code-runner-skill-adapter option: {arg}")
        index += 1
    if not _optional_str(options.get("result")):
        raise RuntimeError(
            "Usage: tau code-runner-skill-adapter --result <json> --out <receipt> --repo-root <dir>"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            "Usage: tau code-runner-skill-adapter --result <json> --out <receipt> --repo-root <dir>"
        )
    if not _optional_str(options.get("repo_root")):
        raise RuntimeError(
            "Usage: tau code-runner-skill-adapter --result <json> --out <receipt> --repo-root <dir>"
        )
    return options


def _parse_review_code_skill_adapter_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "review": None,
        "out": None,
        "repo_root": None,
        "goal_hash": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--review", "--out", "--repo-root", "--goal-hash"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--review="):
            options["review"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--repo-root="):
            options["repo_root"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown review-code-skill-adapter option: {arg}")
        index += 1
    if not _optional_str(options.get("review")):
        raise RuntimeError(
            "Usage: tau review-code-skill-adapter --review <json> --out <receipt> --repo-root <dir>"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            "Usage: tau review-code-skill-adapter --review <json> --out <receipt> --repo-root <dir>"
        )
    if not _optional_str(options.get("repo_root")):
        raise RuntimeError(
            "Usage: tau review-code-skill-adapter --review <json> --out <receipt> --repo-root <dir>"
        )
    return options


def _parse_evidence_case_skill_adapter_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "case": None,
        "out": None,
        "repo_root": None,
        "goal_hash": None,
        "policy_profile": None,
        "data_boundary": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--case",
            "--out",
            "--repo-root",
            "--goal-hash",
            "--policy-profile",
            "--data-boundary",
            "--result",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = "case" if arg == "--result" else arg.removeprefix("--").replace("-", "_")
            options[key] = args[index]
        elif arg.startswith("--case=") or arg.startswith("--result="):
            options["case"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--repo-root="):
            options["repo_root"] = arg.partition("=")[2]
        elif arg.startswith("--goal-hash="):
            options["goal_hash"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown evidence-case-skill-adapter option: {arg}")
        index += 1
    if not _optional_str(options.get("case")):
        raise RuntimeError(
            "Usage: tau evidence-case-skill-adapter --case <json> --out <receipt> --repo-root <dir>"
        )
    if not _optional_str(options.get("out")):
        raise RuntimeError(
            "Usage: tau evidence-case-skill-adapter --case <json> --out <receipt> --repo-root <dir>"
        )
    if not _optional_str(options.get("repo_root")):
        raise RuntimeError(
            "Usage: tau evidence-case-skill-adapter --case <json> --out <receipt> --repo-root <dir>"
        )
    return options


def _read_optional_json_object(value: object) -> dict[str, object] | None:
    if not _optional_str(value):
        return None
    path = Path(str(value)).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return payload


def _parse_dag_view_cli_args(args: list[str], *, command: str) -> dict[str, object]:
    options: dict[str, object] = {
        "run_dir": None,
        "run_id": None,
        "after_sequence": 0,
        "limit": 200,
        "last": False,
    }
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--last":
            options["last"] = True
            index += 1
            continue
        if argument in {"--run-dir", "--run-id", "--after-sequence", "--limit", "--output"}:
            if index + 1 >= len(args):
                raise RuntimeError(f"{argument} requires a value")
            value = args[index + 1]
            if argument == "--run-dir":
                options["run_dir"] = value
            elif argument == "--run-id":
                options["run_id"] = value
            elif argument == "--after-sequence":
                try:
                    options["after_sequence"] = int(value)
                except ValueError as exc:
                    raise RuntimeError("dag_viewer_event_range_invalid") from exc
            elif argument == "--limit":
                try:
                    options["limit"] = int(value)
                except ValueError as exc:
                    raise RuntimeError("dag_viewer_event_range_invalid") from exc
            elif value != "-":
                raise RuntimeError("Child A supports --output - only")
            index += 2
            continue
        raise RuntimeError(f"unknown {command} option: {argument}")
    if options["last"] and options["run_dir"] is not None:
        raise RuntimeError("--last cannot be combined with --run-dir")
    if options["last"]:
        options["run_dir"] = _resolve_last_run_dir()
    if options["run_dir"] is None:
        raise RuntimeError(
            f"Usage: tau {command} --run-dir <run-dir>|--last [--run-id <run-id>]"
        )
    if int(options["after_sequence"]) < 0 or not 1 <= int(options["limit"]) <= 5000:
        raise RuntimeError("dag_viewer_event_range_invalid")
    return options


def _parse_dag_view_serve_cli_args(
    args: list[str], *, command: str = "dag-view-serve"
) -> dict[str, object]:
    options: dict[str, object] = {
        "run_dir": None,
        "run_id": None,
        "host": "127.0.0.1",
        "port": 0,
        "open": command == "dag-view" and sys.stdout.isatty(),
        "last": False,
    }
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--json":
            index += 1
            continue
        if argument == "--last":
            options["last"] = True
            index += 1
            continue
        if argument in {"--open", "--no-open"}:
            options["open"] = argument == "--open"
            index += 1
            continue
        if argument not in {"--run-dir", "--run-id", "--host", "--port"}:
            raise RuntimeError(f"unknown {command} option: {argument}")
        if index + 1 >= len(args):
            raise RuntimeError(f"{argument} requires a value")
        value = args[index + 1]
        if argument == "--run-dir":
            options["run_dir"] = value
        elif argument == "--run-id":
            options["run_id"] = value
        elif argument == "--host":
            options["host"] = value
        else:
            try:
                options["port"] = int(value)
            except ValueError as exc:
                raise RuntimeError("dag_viewer_port_invalid") from exc
        index += 2
    if options["last"] and options["run_dir"] is not None:
        raise RuntimeError("--last cannot be combined with --run-dir")
    if options["last"]:
        options["run_dir"] = _resolve_last_run_dir()
    if options["run_dir"] is None:
        raise RuntimeError(
            f"Usage: tau {command} --run-dir <run-dir>|--last [--run-id <run-id>]"
        )
    return options


def _parse_research_skill_adapter_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "report": None,
        "query_safety": None,
        "out": None,
        "repo_root": None,
        "method": "dogpile",
        "source_type": "web",
        "classification": "design_input",
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--report",
            "--query-safety",
            "--out",
            "--repo-root",
            "--method",
            "--source-type",
            "--classification",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--report="):
            options["report"] = arg.partition("=")[2]
        elif arg.startswith("--query-safety="):
            options["query_safety"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--repo-root="):
            options["repo_root"] = arg.partition("=")[2]
        elif arg.startswith("--method="):
            options["method"] = arg.partition("=")[2]
        elif arg.startswith("--source-type="):
            options["source_type"] = arg.partition("=")[2]
        elif arg.startswith("--classification="):
            options["classification"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown research-skill-adapter option: {arg}")
        index += 1
    for key in ("report", "query_safety", "out", "repo_root"):
        if not _optional_str(options.get(key)):
            raise RuntimeError(
                "Usage: tau research-skill-adapter --report <json> "
                "--query-safety <receipt> --out <receipt> --repo-root <dir>"
            )
    return options


def _set_herdr_observation_gate_option(
    options: dict[str, object],
    arg: str,
    value: str,
) -> None:
    key = arg.removeprefix("--").replace("-", "_")
    if arg in {"--attempt"}:
        options[key] = int(value)
    elif arg == "--receipt-timeout-seconds":
        options[key] = float(value)
    else:
        options[key] = value


def _set_course_correction_option(
    options: dict[str, object],
    arg: str,
    value: str,
) -> None:
    key = arg.removeprefix("--").replace("-", "_")
    if arg == "--target-json":
        key = "target"
    elif arg == "--observed-state-json":
        key = "observed_state"
    elif arg == "--observed-artifact":
        key = "observed_artifact"
    elif arg == "--error":
        _append_option(options, "error", value)
        return
    if arg == "--attempt":
        options[key] = int(value)
    else:
        options[key] = value


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        return int(value)
    return None


def _json_object_option(value: object, *, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return parsed


def _json_array_option(value: object, *, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label} must be a JSON array: {exc}") from exc
    else:
        return []
    if not isinstance(parsed, list):
        raise RuntimeError(f"{label} must be a JSON array")
    objects: list[dict[str, Any]] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise RuntimeError(f"{label}[{index}] must be a JSON object")
        objects.append(item)
    return objects


def _read_optional_json_object(value: object) -> dict[str, Any] | None:
    path_text = _optional_str(value)
    if path_text is None:
        return None
    path = Path(path_text).expanduser().resolve()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{path} root must be a JSON object")
    return parsed


def _parse_tui_proof_cli_args(args: list[str]) -> dict[str, str | Path]:
    output_dir = Path(".tmp/tui-proof")
    prompt = DEFAULT_TUI_PROOF_PROMPT
    run_id = DEFAULT_TUI_PROOF_RUN_ID
    route = "COMPLIANCE"
    next_agent = "reviewer"
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--out-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("Usage: tau tui-proof [--out-dir DIR]")
            output_dir = Path(args[index])
        elif arg.startswith("--out-dir="):
            output_dir = Path(arg.partition("=")[2])
        elif arg == "--prompt":
            index += 1
            if index >= len(args):
                raise RuntimeError("Usage: tau tui-proof [--prompt TEXT]")
            prompt = args[index]
        elif arg.startswith("--prompt="):
            prompt = arg.partition("=")[2]
        elif arg == "--run-id":
            index += 1
            if index >= len(args):
                raise RuntimeError("Usage: tau tui-proof [--run-id RUN_ID]")
            run_id = args[index]
        elif arg.startswith("--run-id="):
            run_id = arg.partition("=")[2]
        elif arg == "--route":
            index += 1
            if index >= len(args):
                raise RuntimeError("Usage: tau tui-proof [--route ROUTE]")
            route = args[index]
        elif arg.startswith("--route="):
            route = arg.partition("=")[2]
        elif arg == "--next-agent":
            index += 1
            if index >= len(args):
                raise RuntimeError("Usage: tau tui-proof [--next-agent AGENT]")
            next_agent = args[index]
        elif arg.startswith("--next-agent="):
            next_agent = arg.partition("=")[2]
        else:
            raise RuntimeError(f"Unknown tui-proof option: {arg}")
        index += 1
    if not prompt.strip():
        raise RuntimeError("--prompt must not be empty")
    if not run_id.strip():
        raise RuntimeError("--run-id must not be empty")
    if not route.strip():
        raise RuntimeError("--route must not be empty")
    if not next_agent.strip():
        raise RuntimeError("--next-agent must not be empty")
    return {
        "output_dir": output_dir,
        "prompt": prompt,
        "run_id": run_id,
        "route": route,
        "next_agent": next_agent,
    }


def _parse_browser_cdp_proof_cli_args(args: list[str]) -> dict[str, str | Path | bool | None]:
    output_dir = Path(".tmp/browser-cdp-proof")
    run_id = DEFAULT_BROWSER_PROOF_RUN_ID
    surf_bin: Path | None = None
    keep_tab = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--out-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("Usage: tau browser-cdp-proof [--out-dir DIR]")
            output_dir = Path(args[index])
        elif arg.startswith("--out-dir="):
            output_dir = Path(arg.partition("=")[2])
        elif arg == "--run-id":
            index += 1
            if index >= len(args):
                raise RuntimeError("Usage: tau browser-cdp-proof [--run-id RUN_ID]")
            run_id = args[index]
        elif arg.startswith("--run-id="):
            run_id = arg.partition("=")[2]
        elif arg == "--surf-bin":
            index += 1
            if index >= len(args):
                raise RuntimeError("Usage: tau browser-cdp-proof [--surf-bin PATH]")
            surf_bin = Path(args[index])
        elif arg.startswith("--surf-bin="):
            surf_bin = Path(arg.partition("=")[2])
        elif arg == "--keep-tab":
            keep_tab = True
        else:
            raise RuntimeError(f"Unknown browser-cdp-proof option: {arg}")
        index += 1
    if not run_id.strip():
        raise RuntimeError("--run-id must not be empty")
    return {
        "output_dir": output_dir,
        "run_id": run_id,
        "surf_bin": surf_bin,
        "keep_tab": keep_tab,
    }


def _parse_human_goal_change_bridge_cli_args(
    args: list[str],
) -> tuple[Path, str | None, bool, Path, Path, Path | None]:
    if not args:
        raise RuntimeError(
            "Usage: tau human-goal-change-bridge <human-goal-change.json> "
            "--handoff-out <start-handoff.json> --receipt <receipt.json> "
            "[--active-goal-hash <hash>] [--trusted-human] [--agents-root <dir>]"
        )
    goal_change_path: Path | None = None
    active_goal_hash: str | None = None
    trusted_human = False
    handoff_out: Path | None = None
    receipt_path: Path | None = None
    agents_root: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--trusted-human":
            trusted_human = True
        elif arg == "--handoff-out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--handoff-out requires a value")
            handoff_out = Path(args[index])
        elif arg.startswith("--handoff-out="):
            handoff_out = Path(arg.partition("=")[2])
        elif arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt_path = Path(args[index])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        elif arg.startswith("-"):
            raise RuntimeError(f"Unknown human-goal-change-bridge option: {arg}")
        elif goal_change_path is None:
            goal_change_path = Path(arg)
        else:
            raise RuntimeError(f"Unexpected human-goal-change-bridge argument: {arg}")
        index += 1

    if goal_change_path is None:
        raise RuntimeError("human-goal-change-bridge requires <human-goal-change.json>")
    if handoff_out is None:
        raise RuntimeError("human-goal-change-bridge requires --handoff-out <start-handoff.json>")
    if receipt_path is None:
        raise RuntimeError("human-goal-change-bridge requires --receipt <receipt.json>")
    return (
        goal_change_path,
        active_goal_hash,
        trusted_human,
        handoff_out,
        receipt_path,
        agents_root,
    )


def _parse_handoff_project_cli_args(
    args: list[str],
) -> tuple[Path, str | None, Path | None, Path | None]:
    if not args:
        raise RuntimeError(
            "Usage: tau handoff-project <handoff.json> "
            "[--active-goal-hash <hash>] [--receipt <receipt.json>]"
        )
    handoff_path = Path(args[0])
    active_goal_hash: str | None = None
    receipt_path: Path | None = None
    agents_root: Path | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt_path = Path(args[index])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown handoff-project option: {arg}")
        index += 1
    return handoff_path, active_goal_hash, receipt_path, agents_root


def _parse_handoff_github_transport_cli_args(
    args: list[str],
) -> tuple[Path, str | None, Path | None, Path | None, bool, Path | None]:
    if not args:
        raise RuntimeError(
            "Usage: tau handoff-github-transport <handoff.json> "
            "[--active-goal-hash <hash>] [--agents-root <dir>] "
            "[--receipt <receipt.json>] [--apply] "
            "[--github-apply-policy-receipt <receipt.json>]"
        )
    handoff_path = Path(args[0])
    active_goal_hash: str | None = None
    receipt_path: Path | None = None
    agents_root: Path | None = None
    apply_github = False
    github_apply_policy_receipt: Path | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt_path = Path(args[index])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        elif arg == "--github-apply-policy-receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--github-apply-policy-receipt requires a value")
            github_apply_policy_receipt = Path(args[index])
        elif arg.startswith("--github-apply-policy-receipt="):
            github_apply_policy_receipt = Path(arg.partition("=")[2])
        elif arg == "--apply":
            apply_github = True
        else:
            raise RuntimeError(f"Unknown handoff-github-transport option: {arg}")
        index += 1
    return (
        handoff_path,
        active_goal_hash,
        receipt_path,
        agents_root,
        apply_github,
        github_apply_policy_receipt,
    )


def _parse_github_redact_projection_args(args: list[str]) -> tuple[Path, Path, Path | None]:
    if not args:
        raise RuntimeError(
            "Usage: tau github-redact-projection --projection <projection.json> "
            "--out <redacted-projection.json> [--receipt <receipt.json>]"
        )
    projection_path: Path | None = None
    output_path: Path | None = None
    receipt_path: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--projection", "--out", "--receipt"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = Path(args[index])
            if arg == "--projection":
                projection_path = value
            elif arg == "--out":
                output_path = value
            else:
                receipt_path = value
        elif arg.startswith("--projection="):
            projection_path = Path(arg.partition("=")[2])
        elif arg.startswith("--out="):
            output_path = Path(arg.partition("=")[2])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown github-redact-projection option: {arg}")
        index += 1
    if projection_path is None:
        raise RuntimeError("--projection requires a value")
    if output_path is None:
        raise RuntimeError("--out requires a value")
    return projection_path, output_path, receipt_path


def _parse_github_apply_policy_check_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError(
            "Usage: tau github-apply-policy-check --projection <projection.json> "
            "--policy <policy.json> --receipt <receipt.json> "
            "[--approval-receipt <approval-receipt.json>] "
            "[--redaction-receipt <redaction-receipt.json>] [--preflight-ready]"
        )
    projection_path: Path | None = None
    policy_path: Path | None = None
    receipt_path: Path | None = None
    approval_receipt_path: Path | None = None
    redaction_receipt_path: Path | None = None
    preflight_ready = False
    index = 0
    path_options = {
        "--projection",
        "--policy",
        "--receipt",
        "--approval-receipt",
        "--redaction-receipt",
    }
    while index < len(args):
        arg = args[index]
        if arg in path_options:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = Path(args[index])
            if arg == "--projection":
                projection_path = value
            elif arg == "--policy":
                policy_path = value
            elif arg == "--receipt":
                receipt_path = value
            elif arg == "--approval-receipt":
                approval_receipt_path = value
            else:
                redaction_receipt_path = value
        elif arg.startswith("--projection="):
            projection_path = Path(arg.partition("=")[2])
        elif arg.startswith("--policy="):
            policy_path = Path(arg.partition("=")[2])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        elif arg.startswith("--approval-receipt="):
            approval_receipt_path = Path(arg.partition("=")[2])
        elif arg.startswith("--redaction-receipt="):
            redaction_receipt_path = Path(arg.partition("=")[2])
        elif arg == "--preflight-ready":
            preflight_ready = True
        else:
            raise RuntimeError(f"Unknown github-apply-policy-check option: {arg}")
        index += 1
    if projection_path is None:
        raise RuntimeError("--projection requires a value")
    if policy_path is None:
        raise RuntimeError("--policy requires a value")
    if receipt_path is None:
        raise RuntimeError("--receipt requires a value")
    return {
        "projection_path": projection_path,
        "policy_path": policy_path,
        "receipt_path": receipt_path,
        "approval_receipt_path": approval_receipt_path,
        "redaction_receipt_path": redaction_receipt_path,
        "preflight_ready": preflight_ready,
    }


def _parse_research_source_receipt_args(args: list[str]) -> tuple[Path, Path]:
    if not args:
        raise RuntimeError(
            "Usage: tau research-source-receipt --source <source-packet.json> "
            "--receipt <receipt.json>"
        )
    source_path: Path | None = None
    receipt_path: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--source", "--receipt"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            if arg == "--source":
                source_path = Path(args[index])
            else:
                receipt_path = Path(args[index])
        elif arg.startswith("--source="):
            source_path = Path(arg.partition("=")[2])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown research-source-receipt option: {arg}")
        index += 1
    if source_path is None:
        raise RuntimeError("--source requires a value")
    if receipt_path is None:
        raise RuntimeError("--receipt requires a value")
    return source_path, receipt_path


def _parse_research_query_gate_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError(
            "Usage: tau research-query-gate --query <query> --method <method> "
            "--policy-profile <policy.json> --data-boundary <boundary.json> "
            "--receipt <receipt.json> [--authorization <auth.json>] "
            "[--controlled-artifact <path> ...]"
        )
    options: dict[str, object] = {
        "query": None,
        "method": "brave-search",
        "policy_profile_path": None,
        "data_boundary_path": None,
        "authorization_path": None,
        "controlled_artifact_paths": [],
        "receipt_path": None,
    }
    path_keys = {
        "--policy-profile": "policy_profile_path",
        "--data-boundary": "data_boundary_path",
        "--authorization": "authorization_path",
        "--receipt": "receipt_path",
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--query",
            "--method",
            "--policy-profile",
            "--data-boundary",
            "--authorization",
            "--controlled-artifact",
            "--receipt",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg == "--controlled-artifact":
                artifacts = options["controlled_artifact_paths"]
                if not isinstance(artifacts, list):
                    raise RuntimeError("internal controlled artifact parser error")
                artifacts.append(Path(value))
            elif arg in path_keys:
                options[path_keys[arg]] = Path(value)
            else:
                options[arg.removeprefix("--").replace("-", "_")] = value
        elif any(
            arg.startswith(f"{flag}=")
            for flag in (
                "--query",
                "--method",
                "--policy-profile",
                "--data-boundary",
                "--authorization",
                "--controlled-artifact",
                "--receipt",
            )
        ):
            key, _, value = arg.partition("=")
            if key == "--controlled-artifact":
                artifacts = options["controlled_artifact_paths"]
                if not isinstance(artifacts, list):
                    raise RuntimeError("internal controlled artifact parser error")
                artifacts.append(Path(value))
            elif key in path_keys:
                options[path_keys[key]] = Path(value)
            else:
                options[key.removeprefix("--").replace("-", "_")] = value
        else:
            raise RuntimeError(f"Unknown research-query-gate option: {arg}")
        index += 1

    query = options["query"]
    if not isinstance(query, str) or not query.strip():
        raise RuntimeError("--query requires a non-empty value")
    method = options["method"]
    if not isinstance(method, str) or not method.strip():
        raise RuntimeError("--method requires a non-empty value")
    for key, flag in {
        "policy_profile_path": "--policy-profile",
        "data_boundary_path": "--data-boundary",
        "receipt_path": "--receipt",
    }.items():
        if not isinstance(options.get(key), Path):
            raise RuntimeError(f"{flag} requires a value")
    return options


def _parse_itar_access_preflight_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError(
            "Usage: tau itar-access-preflight --actor-manifest <actor.json> "
            "--data-boundary <boundary.json> --receipt <receipt.json> "
            "[--approval-packet <approval.json>] [--required-boundary ITAR]"
        )
    options: dict[str, object] = {
        "actor_manifest_path": None,
        "data_boundary_path": None,
        "approval_packet_path": None,
        "receipt_path": None,
        "required_boundary": "ITAR",
    }
    path_keys = {
        "--actor-manifest": "actor_manifest_path",
        "--data-boundary": "data_boundary_path",
        "--approval-packet": "approval_packet_path",
        "--receipt": "receipt_path",
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--actor-manifest",
            "--data-boundary",
            "--approval-packet",
            "--receipt",
            "--required-boundary",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg in path_keys:
                options[path_keys[arg]] = Path(value)
            else:
                options["required_boundary"] = value
        elif any(
            arg.startswith(f"{flag}=")
            for flag in (
                "--actor-manifest",
                "--data-boundary",
                "--approval-packet",
                "--receipt",
                "--required-boundary",
            )
        ):
            key, _, value = arg.partition("=")
            if key in path_keys:
                options[path_keys[key]] = Path(value)
            else:
                options["required_boundary"] = value
        else:
            raise RuntimeError(f"Unknown itar-access-preflight option: {arg}")
        index += 1
    for key, flag in {
        "actor_manifest_path": "--actor-manifest",
        "data_boundary_path": "--data-boundary",
        "receipt_path": "--receipt",
    }.items():
        if not isinstance(options.get(key), Path):
            raise RuntimeError(f"{flag} requires a value")
    if (
        not isinstance(options["required_boundary"], str)
        or not options["required_boundary"].strip()
    ):
        raise RuntimeError("--required-boundary requires a non-empty value")
    return options


def _parse_itar_contract_review_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "clause": None,
        "policy_profile": None,
        "data_boundary": None,
        "out": None,
        "contract_clause_id": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--clause",
            "--policy-profile",
            "--data-boundary",
            "--out",
            "--contract-clause-id",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg == "--clause":
                options["clause"] = Path(value)
            elif arg == "--policy-profile":
                options["policy_profile"] = Path(value)
            elif arg == "--data-boundary":
                options["data_boundary"] = Path(value)
            elif arg == "--out":
                options["out"] = Path(value)
            elif arg == "--contract-clause-id":
                options["contract_clause_id"] = value
        elif arg.startswith("--clause="):
            options["clause"] = Path(arg.partition("=")[2])
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = Path(arg.partition("=")[2])
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = Path(arg.partition("=")[2])
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        elif arg.startswith("--contract-clause-id="):
            options["contract_clause_id"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"Unknown itar-contract-review option: {arg}")
        index += 1
    missing = [
        name
        for name in ("clause", "policy_profile", "data_boundary", "out")
        if options[name] is None
    ]
    if missing:
        raise RuntimeError(
            "Usage: tau itar-contract-review --clause <clause.txt> "
            "--policy-profile <policy.json> --data-boundary <boundary.json> --out <receipt>"
        )
    return options


def _parse_sparta_posture_export_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "run_dir": None,
        "out": None,
        "program": "synthetic-f36",
        "system": "sparta-explorer",
        "demo": True,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--run-dir", "--out", "--program", "--system"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg == "--run-dir":
                options["run_dir"] = Path(value)
            elif arg == "--out":
                options["out"] = Path(value)
            elif arg == "--program":
                options["program"] = value
            elif arg == "--system":
                options["system"] = value
        elif arg.startswith("--run-dir="):
            options["run_dir"] = Path(arg.partition("=")[2])
        elif arg.startswith("--out="):
            options["out"] = Path(arg.partition("=")[2])
        elif arg.startswith("--program="):
            options["program"] = arg.partition("=")[2]
        elif arg.startswith("--system="):
            options["system"] = arg.partition("=")[2]
        elif arg == "--not-demo":
            options["demo"] = False
        else:
            raise RuntimeError(f"Unknown sparta-posture-export option: {arg}")
        index += 1
    if options["run_dir"] is None or options["out"] is None:
        raise RuntimeError("Usage: tau sparta-posture-export --run-dir <dir> --out <json>")
    return options


def _parse_compliance_package_validate_args(args: list[str]) -> dict[str, object]:
    if not args:
        raise RuntimeError(
            "Usage: tau compliance-package-validate <package-dir> "
            "--receipt <receipt.json> [--policy itar-local-only]"
        )
    options: dict[str, object] = {
        "package_dir": Path(args[0]),
        "receipt_path": None,
        "policy": "itar-local-only",
    }
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in {"--receipt", "--policy"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            if arg == "--receipt":
                options["receipt_path"] = Path(args[index])
            else:
                options["policy"] = args[index]
        elif arg.startswith("--receipt="):
            options["receipt_path"] = Path(arg.partition("=")[2])
        elif arg.startswith("--policy="):
            options["policy"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"Unknown compliance-package-validate option: {arg}")
        index += 1
    if not isinstance(options["receipt_path"], Path):
        raise RuntimeError("--receipt requires a value")
    if not isinstance(options["policy"], str) or not options["policy"].strip():
        raise RuntimeError("--policy requires a non-empty value")
    return options


def _parse_zero_trust_redteam_args(args: list[str]) -> Path:
    run_dir: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--run-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-dir requires a value")
            run_dir = Path(args[index])
        elif arg.startswith("--run-dir="):
            run_dir = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown zero-trust-redteam option: {arg}")
        index += 1
    if run_dir is None:
        raise RuntimeError("Usage: tau zero-trust-redteam --run-dir <dir>")
    return run_dir


def _parse_orchestration_redteam_args(args: list[str]) -> Path:
    run_dir: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--run-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-dir requires a value")
            run_dir = Path(args[index])
        elif arg.startswith("--run-dir="):
            run_dir = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown orchestration-redteam option: {arg}")
        index += 1
    if run_dir is None:
        raise RuntimeError("Usage: tau orchestration-redteam --run-dir <dir>")
    return run_dir


def _parse_skill_composition_redteam_args(args: list[str]) -> Path:
    run_dir: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--run-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--run-dir requires a value")
            run_dir = Path(args[index])
        elif arg.startswith("--run-dir="):
            run_dir = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown skill-composition-redteam option: {arg}")
        index += 1
    if run_dir is None:
        raise RuntimeError("Usage: tau skill-composition-redteam --run-dir <dir>")
    return run_dir


def _parse_docker_sandbox_check_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "image": None,
        "command": [],
        "receipt_path": None,
        "backend": "docker",
        "network": "none",
        "user": "65532:65532",
        "read_only_rootfs": True,
        "cap_drop": ["ALL"],
        "no_new_privileges": True,
        "privileged": False,
        "host_network": False,
        "docker_socket_mounted": False,
        "mounts": [],
        "execute": False,
        "timeout_seconds": 30,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--image", "--receipt", "--backend", "--network", "--user", "--mount"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg == "--receipt":
                options["receipt_path"] = Path(value)
            elif arg == "--mount":
                mounts = options["mounts"]
                if not isinstance(mounts, list):
                    raise RuntimeError("internal mount parser error")
                mounts.append(value)
            else:
                options[arg.removeprefix("--").replace("-", "_")] = value
        elif arg == "--command":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command requires at least one value")
            options["command"] = args[index:]
            break
        elif arg == "--privileged":
            options["privileged"] = True
        elif arg == "--execute":
            options["execute"] = True
        elif arg in {"--timeout", "--timeout-seconds"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            try:
                options["timeout_seconds"] = int(args[index])
            except ValueError as exc:
                raise RuntimeError(f"{arg} must be an integer") from exc
        elif arg == "--host-network":
            options["host_network"] = True
            options["network"] = "host"
        elif arg == "--docker-socket-mounted":
            options["docker_socket_mounted"] = True
        elif arg == "--no-read-only-rootfs":
            options["read_only_rootfs"] = False
        elif arg == "--allow-new-privileges":
            options["no_new_privileges"] = False
        elif arg == "--no-cap-drop-all":
            options["cap_drop"] = []
        else:
            raise RuntimeError(f"Unknown docker-sandbox-check option: {arg}")
        index += 1
    if not isinstance(options["image"], str) or not options["image"].strip():
        raise RuntimeError("--image requires a non-empty value")
    if not isinstance(options["receipt_path"], Path):
        raise RuntimeError("--receipt requires a value")
    command = options["command"]
    if not isinstance(command, list) or not command:
        raise RuntimeError("--command requires at least one value")
    return options


def _parse_generated_ticket_github_create_cli_args(
    args: list[str],
) -> tuple[Path, str | None, Path | None, Path | None, bool, Path | None]:
    if not args:
        raise RuntimeError(
            "Usage: tau generated-ticket-github-create <ticket.json> "
            "[--active-goal-hash <hash>] [--agents-root <dir>] "
            "[--receipt <receipt.json>] [--dedupe-preflight <receipt.json>] [--apply]"
        )
    ticket_path = Path(args[0])
    active_goal_hash: str | None = None
    receipt_path: Path | None = None
    agents_root: Path | None = None
    dedupe_preflight_path: Path | None = None
    apply_github = False
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt_path = Path(args[index])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        elif arg == "--dedupe-preflight":
            index += 1
            if index >= len(args):
                raise RuntimeError("--dedupe-preflight requires a value")
            dedupe_preflight_path = Path(args[index])
        elif arg.startswith("--dedupe-preflight="):
            dedupe_preflight_path = Path(arg.partition("=")[2])
        elif arg == "--apply":
            apply_github = True
        else:
            raise RuntimeError(f"Unknown generated-ticket-github-create option: {arg}")
        index += 1
    return (
        ticket_path,
        active_goal_hash,
        receipt_path,
        agents_root,
        apply_github,
        dedupe_preflight_path,
    )


def _parse_handoff_command_loop_github_transport_args(
    args: list[str],
) -> tuple[Path, Path | None, bool]:
    if not args:
        raise RuntimeError(
            "Usage: tau handoff-command-loop-github-transport <command-loop-receipt.json> "
            "[--receipt <receipt.json>] [--apply]"
        )
    loop_receipt_path = Path(args[0])
    receipt_path: Path | None = None
    apply_github = False
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt_path = Path(args[index])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        elif arg == "--apply":
            apply_github = True
        else:
            raise RuntimeError(f"Unknown handoff-command-loop-github-transport option: {arg}")
        index += 1
    return loop_receipt_path, receipt_path, apply_github


def _parse_goal_guardian_reconciliation_github_transport_args(
    args: list[str],
) -> tuple[Path, Path | None, bool]:
    if not args:
        raise RuntimeError(
            "Usage: tau goal-guardian-reconciliation-github-transport "
            "<reconciliation-receipt.json> [--receipt <receipt.json>] [--apply]"
        )
    reconciliation_receipt_path = Path(args[0])
    receipt_path: Path | None = None
    apply_github = False
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt_path = Path(args[index])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        elif arg == "--apply":
            apply_github = True
        else:
            raise RuntimeError(
                f"Unknown goal-guardian-reconciliation-github-transport option: {arg}"
            )
        index += 1
    return reconciliation_receipt_path, receipt_path, apply_github


def _parse_handoff_command_loop_reconciliation_github_transport_args(
    args: list[str],
) -> tuple[Path, Path | None, bool]:
    if not args:
        raise RuntimeError(
            "Usage: tau handoff-command-loop-reconciliation-github-transport "
            "<command-loop-receipt.json> [--receipt <receipt.json>] [--apply]"
        )
    loop_receipt_path = Path(args[0])
    receipt_path: Path | None = None
    apply_github = False
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt_path = Path(args[index])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        elif arg == "--apply":
            apply_github = True
        else:
            raise RuntimeError(
                f"Unknown handoff-command-loop-reconciliation-github-transport option: {arg}"
            )
        index += 1
    return loop_receipt_path, receipt_path, apply_github


def _parse_goal_guardian_ticket_source_github_fetch_args(
    args: list[str],
) -> tuple[str, Path, Path | None, bool, str, int]:
    if not args:
        raise RuntimeError(
            "Usage: tau goal-guardian-ticket-source-github-fetch <repo> "
            "--out <ticket-source.json> [--receipt <receipt.json>] [--execute] "
            "[--state open|closed|all] [--limit <n>]"
        )
    repo = args[0]
    output_path: Path | None = None
    receipt_path: Path | None = None
    execute = False
    state = "open"
    limit = 100
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--out":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out requires a value")
            output_path = Path(args[index])
        elif arg.startswith("--out="):
            output_path = Path(arg.partition("=")[2])
        elif arg == "--receipt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt requires a value")
            receipt_path = Path(args[index])
        elif arg.startswith("--receipt="):
            receipt_path = Path(arg.partition("=")[2])
        elif arg == "--execute":
            execute = True
        elif arg == "--state":
            index += 1
            if index >= len(args):
                raise RuntimeError("--state requires a value")
            state = args[index]
        elif arg.startswith("--state="):
            state = arg.partition("=")[2]
        elif arg == "--limit":
            index += 1
            if index >= len(args):
                raise RuntimeError("--limit requires a value")
            limit = _parse_positive_int(args[index], "--limit")
        elif arg.startswith("--limit="):
            limit = _parse_positive_int(arg.partition("=")[2], "--limit")
        else:
            raise RuntimeError(f"Unknown goal-guardian-ticket-source-github-fetch option: {arg}")
        index += 1
    if output_path is None:
        raise RuntimeError("--out is required")
    return repo, output_path, receipt_path, execute, state, limit


def _parse_handoff_chain_cli_args(
    args: list[str],
) -> tuple[list[Path], str | None, Path, Path | None]:
    if not args:
        raise RuntimeError(
            "Usage: tau handoff-chain-dry-run <handoff.json>... "
            "--receipt-dir <dir> [--active-goal-hash <hash>]"
        )
    handoff_paths: list[Path] = []
    active_goal_hash: str | None = None
    receipt_dir: Path | None = None
    agents_root: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--receipt-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-dir requires a value")
            receipt_dir = Path(args[index])
        elif arg.startswith("--receipt-dir="):
            receipt_dir = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        elif arg.startswith("-"):
            raise RuntimeError(f"Unknown handoff-chain-dry-run option: {arg}")
        else:
            handoff_paths.append(Path(arg))
        index += 1
    if not handoff_paths:
        raise RuntimeError("handoff-chain-dry-run requires at least one handoff JSON file")
    if receipt_dir is None:
        raise RuntimeError("handoff-chain-dry-run requires --receipt-dir <dir>")
    return handoff_paths, active_goal_hash, receipt_dir, agents_root


def _parse_handoff_loop_cli_args(
    args: list[str],
) -> tuple[Path, Path, str | None, Path, int, Path | None]:
    start_path: Path | None = None
    responses_dir: Path | None = None
    active_goal_hash: str | None = None
    receipt_dir: Path | None = None
    agents_root: Path | None = None
    max_steps = 5
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--start":
            index += 1
            if index >= len(args):
                raise RuntimeError("--start requires a value")
            start_path = Path(args[index])
        elif arg.startswith("--start="):
            start_path = Path(arg.partition("=")[2])
        elif arg == "--responses-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--responses-dir requires a value")
            responses_dir = Path(args[index])
        elif arg.startswith("--responses-dir="):
            responses_dir = Path(arg.partition("=")[2])
        elif arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--receipt-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-dir requires a value")
            receipt_dir = Path(args[index])
        elif arg.startswith("--receipt-dir="):
            receipt_dir = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        elif arg == "--max-steps":
            index += 1
            if index >= len(args):
                raise RuntimeError("--max-steps requires a value")
            max_steps = _parse_positive_int(args[index], "--max-steps")
        elif arg.startswith("--max-steps="):
            max_steps = _parse_positive_int(arg.partition("=")[2], "--max-steps")
        else:
            raise RuntimeError(f"Unknown handoff-loop-dry-run option: {arg}")
        index += 1
    if start_path is None:
        raise RuntimeError("handoff-loop-dry-run requires --start <handoff.json>")
    if responses_dir is None:
        raise RuntimeError("handoff-loop-dry-run requires --responses-dir <dir>")
    if receipt_dir is None:
        raise RuntimeError("handoff-loop-dry-run requires --receipt-dir <dir>")
    return start_path, responses_dir, active_goal_hash, receipt_dir, max_steps, agents_root


def _parse_handoff_dispatch_cli_args(
    args: list[str],
) -> tuple[Path, Path, str | None, Path, Path | None]:
    start_path: Path | None = None
    responses_dir: Path | None = None
    active_goal_hash: str | None = None
    receipt_dir: Path | None = None
    agents_root: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--start":
            index += 1
            if index >= len(args):
                raise RuntimeError("--start requires a value")
            start_path = Path(args[index])
        elif arg.startswith("--start="):
            start_path = Path(arg.partition("=")[2])
        elif arg == "--responses-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--responses-dir requires a value")
            responses_dir = Path(args[index])
        elif arg.startswith("--responses-dir="):
            responses_dir = Path(arg.partition("=")[2])
        elif arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--receipt-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-dir requires a value")
            receipt_dir = Path(args[index])
        elif arg.startswith("--receipt-dir="):
            receipt_dir = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown handoff-dispatch-once option: {arg}")
        index += 1
    if start_path is None:
        raise RuntimeError("handoff-dispatch-once requires --start <handoff.json>")
    if responses_dir is None:
        raise RuntimeError("handoff-dispatch-once requires --responses-dir <dir>")
    if receipt_dir is None:
        raise RuntimeError("handoff-dispatch-once requires --receipt-dir <dir>")
    return start_path, responses_dir, active_goal_hash, receipt_dir, agents_root


def _parse_handoff_dispatch_command_cli_args(
    args: list[str],
) -> tuple[Path, Path, str | None, Path, Path | None]:
    start_path: Path | None = None
    command_spec: Path | None = None
    active_goal_hash: str | None = None
    receipt_dir: Path | None = None
    agents_root: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--start":
            index += 1
            if index >= len(args):
                raise RuntimeError("--start requires a value")
            start_path = Path(args[index])
        elif arg.startswith("--start="):
            start_path = Path(arg.partition("=")[2])
        elif arg == "--command-spec":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command-spec requires a value")
            command_spec = Path(args[index])
        elif arg.startswith("--command-spec="):
            command_spec = Path(arg.partition("=")[2])
        elif arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--receipt-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-dir requires a value")
            receipt_dir = Path(args[index])
        elif arg.startswith("--receipt-dir="):
            receipt_dir = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown handoff-dispatch-command option: {arg}")
        index += 1
    if start_path is None:
        raise RuntimeError("handoff-dispatch-command requires --start <handoff.json>")
    if command_spec is None:
        raise RuntimeError("handoff-dispatch-command requires --command-spec <command.json>")
    if receipt_dir is None:
        raise RuntimeError("handoff-dispatch-command requires --receipt-dir <dir>")
    return start_path, command_spec, active_goal_hash, receipt_dir, agents_root


def _parse_handoff_dispatch_agent_command_cli_args(
    args: list[str],
) -> tuple[Path, str | None, Path, Path, Path | None]:
    start_path: Path | None = None
    active_goal_hash: str | None = None
    receipt_dir: Path | None = None
    agents_root: Path | None = None
    command_spec_root: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--start":
            index += 1
            if index >= len(args):
                raise RuntimeError("--start requires a value")
            start_path = Path(args[index])
        elif arg.startswith("--start="):
            start_path = Path(arg.partition("=")[2])
        elif arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--receipt-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-dir requires a value")
            receipt_dir = Path(args[index])
        elif arg.startswith("--receipt-dir="):
            receipt_dir = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        elif arg == "--command-spec-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command-spec-root requires a value")
            command_spec_root = Path(args[index])
        elif arg.startswith("--command-spec-root="):
            command_spec_root = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown handoff-dispatch-agent-command option: {arg}")
        index += 1
    if start_path is None:
        raise RuntimeError("handoff-dispatch-agent-command requires --start <handoff.json>")
    if receipt_dir is None:
        raise RuntimeError("handoff-dispatch-agent-command requires --receipt-dir <dir>")
    if agents_root is None:
        raise RuntimeError("handoff-dispatch-agent-command requires --agents-root <dir>")
    return start_path, active_goal_hash, receipt_dir, agents_root, command_spec_root


def _parse_handoff_command_loop_cli_args(
    args: list[str],
) -> tuple[Path, str | None, Path, Path, Path | None, Path | None, Path | None, int]:
    start_path: Path | None = None
    active_goal_hash: str | None = None
    receipt_dir: Path | None = None
    agents_root: Path | None = None
    command_spec_root: Path | None = None
    command_policy_path: Path | None = None
    goal_guardian_ticket_source: Path | None = None
    max_steps = 5
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--start":
            index += 1
            if index >= len(args):
                raise RuntimeError("--start requires a value")
            start_path = Path(args[index])
        elif arg.startswith("--start="):
            start_path = Path(arg.partition("=")[2])
        elif arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--receipt-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-dir requires a value")
            receipt_dir = Path(args[index])
        elif arg.startswith("--receipt-dir="):
            receipt_dir = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        elif arg == "--command-spec-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command-spec-root requires a value")
            command_spec_root = Path(args[index])
        elif arg.startswith("--command-spec-root="):
            command_spec_root = Path(arg.partition("=")[2])
        elif arg == "--command-policy":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command-policy requires a value")
            command_policy_path = Path(args[index])
        elif arg.startswith("--command-policy="):
            command_policy_path = Path(arg.partition("=")[2])
        elif arg == "--goal-guardian-ticket-source":
            index += 1
            if index >= len(args):
                raise RuntimeError("--goal-guardian-ticket-source requires a value")
            goal_guardian_ticket_source = Path(args[index])
        elif arg.startswith("--goal-guardian-ticket-source="):
            goal_guardian_ticket_source = Path(arg.partition("=")[2])
        elif arg == "--max-steps":
            index += 1
            if index >= len(args):
                raise RuntimeError("--max-steps requires a value")
            max_steps = _parse_positive_int(args[index], "--max-steps")
        elif arg.startswith("--max-steps="):
            max_steps = _parse_positive_int(arg.partition("=")[2], "--max-steps")
        else:
            raise RuntimeError(f"Unknown handoff-command-loop option: {arg}")
        index += 1
    if start_path is None:
        raise RuntimeError("handoff-command-loop requires --start <handoff.json>")
    if receipt_dir is None:
        raise RuntimeError("handoff-command-loop requires --receipt-dir <dir>")
    if agents_root is None:
        raise RuntimeError("handoff-command-loop requires --agents-root <dir>")
    return (
        start_path,
        active_goal_hash,
        receipt_dir,
        agents_root,
        command_spec_root,
        command_policy_path,
        goal_guardian_ticket_source,
        max_steps,
    )


def _parse_goal_cli_args(args: list[str]) -> dict[str, object]:
    if not args or args[0] != "run":
        raise RuntimeError("Usage: tau goal run --start <handoff.json> --timeout-s <seconds>")
    return _parse_goal_run_cli_args(args[1:])


def _parse_goal_run_cli_args(args: list[str]) -> dict[str, object]:
    start_path: Path | None = None
    goal_helper_path: Path | None = None
    active_goal_hash: str | None = None
    receipt_dir: Path | None = None
    agents_root: Path | None = None
    command_spec_root: Path | None = None
    command_policy_path: Path | None = None
    timeout_s: float | None = None
    max_steps_per_tick = 1
    max_ticks: int | None = None
    poll_interval_s = 0.0
    until_complete = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--until-complete":
            until_complete = True
        elif arg == "--start":
            index += 1
            if index >= len(args):
                raise RuntimeError("--start requires a value")
            start_path = Path(args[index])
        elif arg.startswith("--start="):
            start_path = Path(arg.partition("=")[2])
        elif arg == "--goal-helper":
            index += 1
            if index >= len(args):
                raise RuntimeError("--goal-helper requires a value")
            goal_helper_path = Path(args[index])
        elif arg.startswith("--goal-helper="):
            goal_helper_path = Path(arg.partition("=")[2])
        elif arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--receipt-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-dir requires a value")
            receipt_dir = Path(args[index])
        elif arg.startswith("--receipt-dir="):
            receipt_dir = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        elif arg == "--command-spec-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command-spec-root requires a value")
            command_spec_root = Path(args[index])
        elif arg.startswith("--command-spec-root="):
            command_spec_root = Path(arg.partition("=")[2])
        elif arg == "--command-policy":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command-policy requires a value")
            command_policy_path = Path(args[index])
        elif arg.startswith("--command-policy="):
            command_policy_path = Path(arg.partition("=")[2])
        elif arg == "--timeout-s":
            index += 1
            if index >= len(args):
                raise RuntimeError("--timeout-s requires a value")
            timeout_s = _parse_positive_float(args[index], "--timeout-s")
        elif arg.startswith("--timeout-s="):
            timeout_s = _parse_positive_float(arg.partition("=")[2], "--timeout-s")
        elif arg in {"--tick-max-steps", "--max-steps-per-tick"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            max_steps_per_tick = _parse_positive_int(args[index], arg)
        elif arg.startswith("--tick-max-steps="):
            max_steps_per_tick = _parse_positive_int(arg.partition("=")[2], "--tick-max-steps")
        elif arg.startswith("--max-steps-per-tick="):
            max_steps_per_tick = _parse_positive_int(arg.partition("=")[2], "--max-steps-per-tick")
        elif arg == "--max-ticks":
            index += 1
            if index >= len(args):
                raise RuntimeError("--max-ticks requires a value")
            max_ticks = _parse_positive_int(args[index], "--max-ticks")
        elif arg.startswith("--max-ticks="):
            max_ticks = _parse_positive_int(arg.partition("=")[2], "--max-ticks")
        elif arg == "--poll-interval-s":
            index += 1
            if index >= len(args):
                raise RuntimeError("--poll-interval-s requires a value")
            poll_interval_s = _parse_non_negative_float(args[index], "--poll-interval-s")
        elif arg.startswith("--poll-interval-s="):
            poll_interval_s = _parse_non_negative_float(arg.partition("=")[2], "--poll-interval-s")
        else:
            raise RuntimeError(f"Unknown goal run option: {arg}")
        index += 1
    if not until_complete:
        raise RuntimeError("goal run requires --until-complete")
    if start_path is None:
        raise RuntimeError("goal run requires --start <handoff.json>")
    if receipt_dir is None:
        raise RuntimeError("goal run requires --receipt-dir <dir>")
    if agents_root is None:
        raise RuntimeError("goal run requires --agents-root <dir>")
    if timeout_s is None:
        raise RuntimeError("goal run requires --timeout-s <seconds>")
    return {
        "start_path": start_path,
        "goal_helper_path": goal_helper_path,
        "active_goal_hash": active_goal_hash,
        "receipt_dir": receipt_dir,
        "agent_registry_root": agents_root,
        "command_spec_root": command_spec_root,
        "command_policy_path": command_policy_path,
        "timeout_s": timeout_s,
        "max_steps_per_tick": max_steps_per_tick,
        "max_ticks": max_ticks,
        "poll_interval_s": poll_interval_s,
    }


def _parse_handoff_agent_adapter_cli_args(args: list[str]) -> dict[str, str | None]:
    options: dict[str, str | None] = {
        "result_status": "COMPLETED",
        "result_summary": None,
        "next_agent": "human",
        "next_executor": "human",
        "next_reason": "Human review is required after this bounded adapter response.",
        "required_evidence": "Human accepts, redirects, or requests another bounded subagent.",
        "stop_condition": "Human posts a schema-valid handoff or goal decision.",
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--result-status",
            "--result-summary",
            "--next-agent",
            "--next-executor",
            "--next-reason",
            "--required-evidence",
            "--stop-condition",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif any(
            arg.startswith(f"{flag}=")
            for flag in (
                "--result-status",
                "--result-summary",
                "--next-agent",
                "--next-executor",
                "--next-reason",
                "--required-evidence",
                "--stop-condition",
            )
        ):
            key, _, value = arg.partition("=")
            options[key.removeprefix("--").replace("-", "_")] = value
        else:
            raise RuntimeError(f"Unknown handoff-agent-adapter option: {arg}")
        index += 1
    return options


def _parse_self_fix_cli_args(args: list[str]) -> dict[str, object]:
    if not args or args[0] not in {"tick", "poll"}:
        raise RuntimeError(
            "Usage: tau self-fix tick --repo <owner/repo> --issue <number>, "
            "tau self-fix poll --repo <owner/repo>, "
            "or tau self-fix coder-reviewer-loop --request <text> --target-file <path> "
            "--find-text <text> --replace-text <text> --verification-command <cmd>"
        )
    if args[0] == "poll":
        return _parse_self_fix_poll_cli_args(args[1:])
    repo: str | None = None
    issue: int | None = None
    receipt_dir: Path | None = None
    agents_root = Path("/home/graham/workspace/experiments/agent-skills/agents")
    command_spec_root: Path | None = None
    active_goal_hash: str | None = None
    memory_base_url = "http://127.0.0.1:8601"
    scillm_base_url = "http://127.0.0.1:4001"
    model = "gpt-5.5"
    repo_root = Path.cwd()
    max_steps = 3
    repair = False
    apply_github = False
    required_labels = [
        "agent-work",
        "agent:coder",
        "tau-harness",
        "route:backend_python_or_skill_runtime",
    ]
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--repo":
            index += 1
            if index >= len(args):
                raise RuntimeError("--repo requires a value")
            repo = args[index]
        elif arg.startswith("--repo="):
            repo = arg.partition("=")[2]
        elif arg == "--issue":
            index += 1
            if index >= len(args):
                raise RuntimeError("--issue requires a value")
            issue = _parse_positive_int(args[index], "--issue")
        elif arg.startswith("--issue="):
            issue = _parse_positive_int(arg.partition("=")[2], "--issue")
        elif arg == "--receipt-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-dir requires a value")
            receipt_dir = Path(args[index])
        elif arg.startswith("--receipt-dir="):
            receipt_dir = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        elif arg == "--command-spec-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command-spec-root requires a value")
            command_spec_root = Path(args[index])
        elif arg.startswith("--command-spec-root="):
            command_spec_root = Path(arg.partition("=")[2])
        elif arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--memory-base-url":
            index += 1
            if index >= len(args):
                raise RuntimeError("--memory-base-url requires a value")
            memory_base_url = args[index]
        elif arg.startswith("--memory-base-url="):
            memory_base_url = arg.partition("=")[2]
        elif arg == "--scillm-base-url":
            index += 1
            if index >= len(args):
                raise RuntimeError("--scillm-base-url requires a value")
            scillm_base_url = args[index]
        elif arg.startswith("--scillm-base-url="):
            scillm_base_url = arg.partition("=")[2]
        elif arg == "--model":
            index += 1
            if index >= len(args):
                raise RuntimeError("--model requires a value")
            model = args[index]
        elif arg.startswith("--model="):
            model = arg.partition("=")[2]
        elif arg == "--repo-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--repo-root requires a value")
            repo_root = Path(args[index])
        elif arg.startswith("--repo-root="):
            repo_root = Path(arg.partition("=")[2])
        elif arg == "--max-steps":
            index += 1
            if index >= len(args):
                raise RuntimeError("--max-steps requires a value")
            max_steps = _parse_positive_int(args[index], "--max-steps")
        elif arg.startswith("--max-steps="):
            max_steps = _parse_positive_int(arg.partition("=")[2], "--max-steps")
        elif arg == "--required-label":
            index += 1
            if index >= len(args):
                raise RuntimeError("--required-label requires a value")
            required_labels.append(args[index])
        elif arg.startswith("--required-label="):
            required_labels.append(arg.partition("=")[2])
        elif arg == "--repair":
            repair = True
        elif arg == "--apply-github":
            apply_github = True
        else:
            raise RuntimeError(f"Unknown self-fix tick option: {arg}")
        index += 1
    if not repo:
        raise RuntimeError("self-fix tick requires --repo <owner/repo>")
    if issue is None:
        raise RuntimeError("self-fix tick requires --issue <number>")
    if receipt_dir is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        receipt_dir = Path("experiments/goal-locked-subagents/proofs") / (
            f"self-fix-issue-{issue}-{stamp}"
        )
    return {
        "repo": repo,
        "issue": issue,
        "receipt_dir": receipt_dir,
        "agents_root": agents_root,
        "command_spec_root": command_spec_root,
        "active_goal_hash": active_goal_hash,
        "memory_base_url": memory_base_url.rstrip("/"),
        "scillm_base_url": scillm_base_url.rstrip("/"),
        "model": model,
        "repo_root": repo_root,
        "max_steps": max_steps,
        "required_labels": tuple(label for label in required_labels if label),
        "repair": repair,
        "apply_github": apply_github,
    }


def _parse_self_fix_poll_cli_args(args: list[str]) -> dict[str, object]:
    repo: str | None = None
    receipt_dir: Path | None = None
    agents_root = Path("/home/graham/workspace/experiments/agent-skills/agents")
    command_spec_root: Path | None = None
    active_goal_hash: str | None = None
    memory_base_url = "http://127.0.0.1:8601"
    scillm_base_url = "http://127.0.0.1:4001"
    model = "gpt-5.5"
    repo_root = Path.cwd()
    max_steps = 3
    issue_limit = 30
    dispatch = False
    repair = False
    apply_github = False
    required_labels = [
        "agent-work",
        "agent:coder",
        "tau-harness",
        "route:backend_python_or_skill_runtime",
    ]
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--repo":
            index += 1
            if index >= len(args):
                raise RuntimeError("--repo requires a value")
            repo = args[index]
        elif arg.startswith("--repo="):
            repo = arg.partition("=")[2]
        elif arg == "--receipt-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--receipt-dir requires a value")
            receipt_dir = Path(args[index])
        elif arg.startswith("--receipt-dir="):
            receipt_dir = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            agents_root = Path(args[index])
        elif arg.startswith("--agents-root="):
            agents_root = Path(arg.partition("=")[2])
        elif arg == "--command-spec-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command-spec-root requires a value")
            command_spec_root = Path(args[index])
        elif arg.startswith("--command-spec-root="):
            command_spec_root = Path(arg.partition("=")[2])
        elif arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            active_goal_hash = args[index]
        elif arg.startswith("--active-goal-hash="):
            active_goal_hash = arg.partition("=")[2]
        elif arg == "--memory-base-url":
            index += 1
            if index >= len(args):
                raise RuntimeError("--memory-base-url requires a value")
            memory_base_url = args[index]
        elif arg.startswith("--memory-base-url="):
            memory_base_url = arg.partition("=")[2]
        elif arg == "--scillm-base-url":
            index += 1
            if index >= len(args):
                raise RuntimeError("--scillm-base-url requires a value")
            scillm_base_url = args[index]
        elif arg.startswith("--scillm-base-url="):
            scillm_base_url = arg.partition("=")[2]
        elif arg == "--model":
            index += 1
            if index >= len(args):
                raise RuntimeError("--model requires a value")
            model = args[index]
        elif arg.startswith("--model="):
            model = arg.partition("=")[2]
        elif arg == "--repo-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--repo-root requires a value")
            repo_root = Path(args[index])
        elif arg.startswith("--repo-root="):
            repo_root = Path(arg.partition("=")[2])
        elif arg == "--max-steps":
            index += 1
            if index >= len(args):
                raise RuntimeError("--max-steps requires a value")
            max_steps = _parse_positive_int(args[index], "--max-steps")
        elif arg.startswith("--max-steps="):
            max_steps = _parse_positive_int(arg.partition("=")[2], "--max-steps")
        elif arg == "--issue-limit":
            index += 1
            if index >= len(args):
                raise RuntimeError("--issue-limit requires a value")
            issue_limit = _parse_positive_int(args[index], "--issue-limit")
        elif arg.startswith("--issue-limit="):
            issue_limit = _parse_positive_int(arg.partition("=")[2], "--issue-limit")
        elif arg == "--dispatch":
            dispatch = True
        elif arg == "--repair":
            repair = True
        elif arg == "--apply-github":
            apply_github = True
        elif arg == "--required-label":
            index += 1
            if index >= len(args):
                raise RuntimeError("--required-label requires a value")
            required_labels.append(args[index])
        elif arg.startswith("--required-label="):
            required_labels.append(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown self-fix poll option: {arg}")
        index += 1
    if not repo:
        raise RuntimeError("self-fix poll requires --repo <owner/repo>")
    if receipt_dir is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        receipt_dir = Path("experiments/goal-locked-subagents/proofs") / (f"self-fix-poll-{stamp}")
    return {
        "repo": repo,
        "receipt_dir": receipt_dir,
        "agents_root": agents_root,
        "command_spec_root": command_spec_root,
        "active_goal_hash": active_goal_hash,
        "memory_base_url": memory_base_url.rstrip("/"),
        "scillm_base_url": scillm_base_url.rstrip("/"),
        "model": model,
        "repo_root": repo_root,
        "max_steps": max_steps,
        "required_labels": tuple(label for label in required_labels if label),
        "issue_limit": issue_limit,
        "dispatch": dispatch,
        "repair": repair,
        "apply_github": apply_github,
        "_self_fix_mode": "poll",
    }


def _parse_self_fix_coder_reviewer_loop_cli_args(args: list[str]) -> dict[str, object]:
    request: str | None = None
    target_file: Path | None = None
    find_text: str | None = None
    replace_text: str | None = None
    verification_commands: list[str] = []
    receipt_dir: Path | None = None
    repo_root = Path.cwd()
    memory_base_url = "http://127.0.0.1:8601"
    scillm_base_url = "http://127.0.0.1:4001"
    model = "gpt-5.5"
    max_review_cycles = 3
    github_repo = "grahama1970/tau"
    github_target = "local-proof"
    active_goal_hash: str | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--request",
            "--target-file",
            "--find-text",
            "--replace-text",
            "--verification-command",
            "--receipt-dir",
            "--repo-root",
            "--memory-base-url",
            "--scillm-base-url",
            "--model",
            "--max-review-cycles",
            "--github-repo",
            "--github-target",
            "--active-goal-hash",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg == "--request":
                request = value
            elif arg == "--target-file":
                target_file = Path(value)
            elif arg == "--find-text":
                find_text = value
            elif arg == "--replace-text":
                replace_text = value
            elif arg == "--verification-command":
                verification_commands.append(value)
            elif arg == "--receipt-dir":
                receipt_dir = Path(value)
            elif arg == "--repo-root":
                repo_root = Path(value)
            elif arg == "--memory-base-url":
                memory_base_url = value
            elif arg == "--scillm-base-url":
                scillm_base_url = value
            elif arg == "--model":
                model = value
            elif arg == "--max-review-cycles":
                max_review_cycles = _parse_positive_int(value, "--max-review-cycles")
            elif arg == "--github-repo":
                github_repo = value
            elif arg == "--github-target":
                github_target = value
            elif arg == "--active-goal-hash":
                active_goal_hash = value
        elif any(
            arg.startswith(f"{flag}=")
            for flag in (
                "--request",
                "--target-file",
                "--find-text",
                "--replace-text",
                "--verification-command",
                "--receipt-dir",
                "--repo-root",
                "--memory-base-url",
                "--scillm-base-url",
                "--model",
                "--max-review-cycles",
                "--github-repo",
                "--github-target",
                "--active-goal-hash",
            )
        ):
            key, _, value = arg.partition("=")
            if key == "--request":
                request = value
            elif key == "--target-file":
                target_file = Path(value)
            elif key == "--find-text":
                find_text = value
            elif key == "--replace-text":
                replace_text = value
            elif key == "--verification-command":
                verification_commands.append(value)
            elif key == "--receipt-dir":
                receipt_dir = Path(value)
            elif key == "--repo-root":
                repo_root = Path(value)
            elif key == "--memory-base-url":
                memory_base_url = value
            elif key == "--scillm-base-url":
                scillm_base_url = value
            elif key == "--model":
                model = value
            elif key == "--max-review-cycles":
                max_review_cycles = _parse_positive_int(value, "--max-review-cycles")
            elif key == "--github-repo":
                github_repo = value
            elif key == "--github-target":
                github_target = value
            elif key == "--active-goal-hash":
                active_goal_hash = value
        else:
            raise RuntimeError(f"Unknown self-fix coder-reviewer-loop option: {arg}")
        index += 1

    if not isinstance(request, str) or not request.strip():
        raise RuntimeError("self-fix coder-reviewer-loop requires --request <text>")
    if target_file is None:
        raise RuntimeError("self-fix coder-reviewer-loop requires --target-file <path>")
    if find_text is None:
        raise RuntimeError("self-fix coder-reviewer-loop requires --find-text <text>")
    if replace_text is None:
        raise RuntimeError("self-fix coder-reviewer-loop requires --replace-text <text>")
    if not verification_commands:
        raise RuntimeError(
            "self-fix coder-reviewer-loop requires at least one --verification-command <cmd>"
        )
    if receipt_dir is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        receipt_dir = Path("experiments/goal-locked-subagents/proofs") / (
            f"self-fix-coder-reviewer-loop-{stamp}"
        )
    return {
        "repo_root": repo_root,
        "out_dir": receipt_dir,
        "request": request,
        "target_file": target_file,
        "find_text": find_text,
        "replace_text": replace_text,
        "verification_commands": verification_commands,
        "memory_base_url": memory_base_url.rstrip("/"),
        "scillm_base_url": scillm_base_url.rstrip("/"),
        "model": model,
        "max_review_cycles": max_review_cycles,
        "github_repo": github_repo,
        "github_target": github_target,
        "active_goal_hash": active_goal_hash,
    }


def _parse_scillm_subagent_gate_cli_args(args: list[str]) -> Path:
    summary_path: Path | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--summary":
            index += 1
            if index >= len(args):
                raise RuntimeError("--summary requires a value")
            summary_path = Path(args[index])
        elif arg.startswith("--summary="):
            summary_path = Path(arg.partition("=")[2])
        else:
            raise RuntimeError(f"Unknown scillm-subagent-gate option: {arg}")
        index += 1
    if summary_path is None:
        raise RuntimeError("scillm-subagent-gate requires --summary <summary.json>")
    return summary_path


def _parse_ticket_subagent_closure_proof_cli_args(args: list[str]) -> dict[str, Path | bool]:
    output: Path | None = None
    allow_live_filesystem = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--output":
            index += 1
            if index >= len(args):
                raise RuntimeError("--output requires a value")
            output = Path(args[index])
        elif arg.startswith("--output="):
            output = Path(arg.partition("=")[2])
        elif arg == "--allow-live-filesystem":
            allow_live_filesystem = True
        else:
            raise RuntimeError(f"Unknown ticket-subagent-closure-proof option: {arg}")
        index += 1
    if output is None:
        raise RuntimeError("ticket-subagent-closure-proof requires --output <proof.json>")
    return {
        "output": output,
        "allow_live_filesystem": allow_live_filesystem,
    }


def _parse_persona_dream_panel_proof_cli_args(
    args: list[str],
) -> dict[str, Path | str | bool | None]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    options: dict[str, Path | str] = {
        "out_dir": Path("experiments/goal-locked-subagents/proofs")
        / f"persona-dream-panel-proof-{stamp}",
        "agents_root": DEFAULT_PERSONA_DREAM_PANEL_AGENT_ROOT,
        "command_spec_root": DEFAULT_PERSONA_DREAM_PANEL_COMMAND_SPEC_ROOT,
        "active_goal_hash": DEFAULT_PERSONA_DREAM_PANEL_GOAL_HASH,
        "github_target": "issue#27",
        "panel_evidence": None,
        "panel_source": None,
        "panel_repair_work_order": None,
        "scillm_live_panel": False,
        "panel_prompt": None,
        "scillm_image_model": "gpt-image-2",
        "scillm_image_auth": "codex-oauth",
        "scillm_image_quality": "high",
        "scillm_vlm_model": "gpt-5.5",
        "scillm_base_url": "http://127.0.0.1:4001",
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--out-dir":
            index += 1
            if index >= len(args):
                raise RuntimeError("--out-dir requires a value")
            options["out_dir"] = Path(args[index])
        elif arg.startswith("--out-dir="):
            options["out_dir"] = Path(arg.partition("=")[2])
        elif arg == "--agents-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--agents-root requires a value")
            options["agents_root"] = Path(args[index])
        elif arg.startswith("--agents-root="):
            options["agents_root"] = Path(arg.partition("=")[2])
        elif arg == "--command-spec-root":
            index += 1
            if index >= len(args):
                raise RuntimeError("--command-spec-root requires a value")
            options["command_spec_root"] = Path(args[index])
        elif arg.startswith("--command-spec-root="):
            options["command_spec_root"] = Path(arg.partition("=")[2])
        elif arg == "--active-goal-hash":
            index += 1
            if index >= len(args):
                raise RuntimeError("--active-goal-hash requires a value")
            options["active_goal_hash"] = args[index]
        elif arg.startswith("--active-goal-hash="):
            options["active_goal_hash"] = arg.partition("=")[2]
        elif arg == "--github-target":
            index += 1
            if index >= len(args):
                raise RuntimeError("--github-target requires a value")
            options["github_target"] = args[index]
        elif arg.startswith("--github-target="):
            options["github_target"] = arg.partition("=")[2]
        elif arg == "--panel-evidence":
            index += 1
            if index >= len(args):
                raise RuntimeError("--panel-evidence requires a value")
            options["panel_evidence"] = Path(args[index])
        elif arg.startswith("--panel-evidence="):
            options["panel_evidence"] = Path(arg.partition("=")[2])
        elif arg == "--panel-source":
            index += 1
            if index >= len(args):
                raise RuntimeError("--panel-source requires a value")
            options["panel_source"] = Path(args[index])
        elif arg.startswith("--panel-source="):
            options["panel_source"] = Path(arg.partition("=")[2])
        elif arg == "--panel-repair-work-order":
            index += 1
            if index >= len(args):
                raise RuntimeError("--panel-repair-work-order requires a value")
            options["panel_repair_work_order"] = Path(args[index])
        elif arg.startswith("--panel-repair-work-order="):
            options["panel_repair_work_order"] = Path(arg.partition("=")[2])
        elif arg == "--scillm-live-panel":
            options["scillm_live_panel"] = True
        elif arg == "--panel-prompt":
            index += 1
            if index >= len(args):
                raise RuntimeError("--panel-prompt requires a value")
            options["panel_prompt"] = args[index]
        elif arg.startswith("--panel-prompt="):
            options["panel_prompt"] = arg.partition("=")[2]
        elif arg == "--scillm-image-model":
            index += 1
            if index >= len(args):
                raise RuntimeError("--scillm-image-model requires a value")
            options["scillm_image_model"] = args[index]
        elif arg.startswith("--scillm-image-model="):
            options["scillm_image_model"] = arg.partition("=")[2]
        elif arg == "--scillm-image-auth":
            index += 1
            if index >= len(args):
                raise RuntimeError("--scillm-image-auth requires a value")
            options["scillm_image_auth"] = args[index]
        elif arg.startswith("--scillm-image-auth="):
            options["scillm_image_auth"] = arg.partition("=")[2]
        elif arg == "--scillm-image-quality":
            index += 1
            if index >= len(args):
                raise RuntimeError("--scillm-image-quality requires a value")
            options["scillm_image_quality"] = args[index]
        elif arg.startswith("--scillm-image-quality="):
            options["scillm_image_quality"] = arg.partition("=")[2]
        elif arg == "--scillm-vlm-model":
            index += 1
            if index >= len(args):
                raise RuntimeError("--scillm-vlm-model requires a value")
            options["scillm_vlm_model"] = args[index]
        elif arg.startswith("--scillm-vlm-model="):
            options["scillm_vlm_model"] = arg.partition("=")[2]
        elif arg == "--scillm-base-url":
            index += 1
            if index >= len(args):
                raise RuntimeError("--scillm-base-url requires a value")
            options["scillm_base_url"] = args[index]
        elif arg.startswith("--scillm-base-url="):
            options["scillm_base_url"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"Unknown persona-dream-panel-proof option: {arg}")
        index += 1
    return options


def _parse_handoff_goal_guardian_adapter_cli_args(args: list[str]) -> dict[str, str | None]:
    options: dict[str, str | None] = {
        "next_agent": "project-or-harness-verifier",
        "next_executor": "local",
        "next_reason": "A verifier should check the preserved-goal handoff.",
        "required_evidence": "Verifier posts a schema-valid handoff receipt.",
        "stop_condition": "Verifier handoff is posted or Tau fails closed.",
        "ticket_source": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--next-agent",
            "--next-executor",
            "--next-reason",
            "--required-evidence",
            "--stop-condition",
            "--ticket-source",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif any(
            arg.startswith(f"{flag}=")
            for flag in (
                "--next-agent",
                "--next-executor",
                "--next-reason",
                "--required-evidence",
                "--stop-condition",
                "--ticket-source",
            )
        ):
            key, _, value = arg.partition("=")
            options[key.removeprefix("--").replace("-", "_")] = value
        else:
            raise RuntimeError(f"Unknown handoff-goal-guardian-adapter option: {arg}")
        index += 1
    return options


def _parse_external_research_receipt_cli_args(
    args: list[str],
) -> dict[str, str | Path | list[str] | None]:
    options: dict[str, str | Path | list[str] | None] = {
        "query": None,
        "method": "brave-search",
        "summary": None,
        "sources": [],
        "output": None,
        "retrieved_at": None,
        "from_brave": None,
        "count": "5",
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--query",
            "--method",
            "--summary",
            "--source",
            "--output",
            "--retrieved-at",
            "--count",
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg == "--source":
                sources = options["sources"]
                if not isinstance(sources, list):
                    raise RuntimeError("internal source parser error")
                sources.append(value)
            elif arg == "--output":
                options["output"] = Path(value)
            else:
                options[arg.removeprefix("--").replace("-", "_")] = value
        elif arg == "--from-brave":
            options["from_brave"] = "true"
        elif any(
            arg.startswith(f"{flag}=")
            for flag in (
                "--query",
                "--method",
                "--summary",
                "--source",
                "--output",
                "--retrieved-at",
                "--count",
            )
        ):
            key, _, value = arg.partition("=")
            if key == "--source":
                sources = options["sources"]
                if not isinstance(sources, list):
                    raise RuntimeError("internal source parser error")
                sources.append(value)
            elif key == "--output":
                options["output"] = Path(value)
            else:
                options[key.removeprefix("--").replace("-", "_")] = value
        else:
            raise RuntimeError(f"Unknown external-research-receipt option: {arg}")
        index += 1

    query = options["query"]
    if not isinstance(query, str) or not query.strip():
        raise RuntimeError("--query requires a non-empty value")
    sources = options["sources"]
    from_brave = options["from_brave"] == "true"
    if not from_brave and (not isinstance(sources, list) or not sources):
        raise RuntimeError("at least one --source title|url value is required")
    method = options["method"]
    if not isinstance(method, str) or not method.strip():
        raise RuntimeError("--method requires a non-empty value")
    return options


def _parse_subagent_receipt_from_handoff_cli_args(args: list[str]) -> dict[str, str | Path | None]:
    options: dict[str, str | Path | None] = {
        "run_id": None,
        "subagent": None,
        "actor_type": "tau",
        "ticket": None,
        "output": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--run-id", "--subagent", "--actor-type", "--ticket", "--output"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            value = args[index]
            if arg == "--output":
                options["output"] = Path(value)
            else:
                options[arg.removeprefix("--").replace("-", "_")] = value
        elif any(
            arg.startswith(f"{flag}=")
            for flag in {"--run-id", "--subagent", "--actor-type", "--ticket", "--output"}
        ):
            key, _, value = arg.partition("=")
            if key == "--output":
                options["output"] = Path(value)
            else:
                options[key.removeprefix("--").replace("-", "_")] = value
        else:
            raise RuntimeError(f"Unknown subagent-receipt-from-handoff option: {arg}")
        index += 1
    run_id = options["run_id"]
    subagent = options["subagent"]
    actor_type = options["actor_type"]
    if not isinstance(run_id, str) or not run_id.strip():
        raise RuntimeError("--run-id requires a non-empty value")
    if not isinstance(subagent, str) or not subagent.strip():
        raise RuntimeError("--subagent requires a non-empty value")
    if not isinstance(actor_type, str) or not actor_type.strip():
        raise RuntimeError("--actor-type requires a non-empty value")
    return options


def _parse_positive_int(value: str, option: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{option} must be an integer") from exc
    if parsed < 1:
        raise RuntimeError(f"{option} must be at least 1")
    return parsed


def _parse_positive_float(value: str, option: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"{option} must be a number") from exc
    if parsed <= 0:
        raise RuntimeError(f"{option} must be positive")
    return parsed


def _parse_non_negative_float(value: str, option: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"{option} must be a number") from exc
    if parsed < 0:
        raise RuntimeError(f"{option} must be non-negative")
    return parsed


def _resolve_export_destination(
    output_path: Path | None,
    *,
    session_path: Path,
    format: str,
) -> Path:
    if output_path is None:
        return default_session_export_artifact_path(
            session_path,
            destination_dir=Path.cwd(),
            format=format,
        )
    if output_path.suffix:
        return output_path
    return default_session_export_artifact_path(
        session_path,
        destination_dir=output_path,
        format=format,
    )


def _resolve_export_source(
    session_ref: str,
    session_manager: SessionManager | None = None,
) -> tuple[Path, str]:
    candidate_path = Path(session_ref).expanduser()
    if candidate_path.exists():
        if candidate_path.is_dir():
            raise RuntimeError(f"Session export source is a directory: {candidate_path}")
        return candidate_path, f"Tau session {candidate_path.stem}"

    manager = session_manager or SessionManager()
    record = manager.get_session(session_ref)
    if record is None:
        raise RuntimeError(f"Unknown session or file: {session_ref}")

    title = record.title or f"Tau session {record.id}"
    return record.path, title


def render_provider_settings(
    settings: ProviderSettings,
    *,
    credential_reader: CredentialReader | None = None,
) -> None:
    """Render configured providers for the CLI."""
    for provider in settings.providers:
        marker = "*" if provider.name == settings.default_provider else " "
        models = ",".join(provider.models)
        typer.echo(
            f"{marker}\t{provider.name}\t{provider_kind(provider)}\t"
            f"{provider.default_model}\t{models}\t{provider.api_key_env}\t"
            f"{_provider_credential_status(provider, credential_reader=credential_reader)}\t"
            f"{provider.base_url}\t{provider.timeout_seconds:g}s\t"
            f"retries={provider.max_retries}\t"
            f"retry_delay={provider.max_retry_delay_seconds:g}s"
        )


def _provider_credential_status(
    provider: ProviderConfig,
    *,
    credential_reader: CredentialReader | None,
) -> str:
    if provider.credential_name and credential_reader is not None:
        if provider_kind(provider) == "openai-codex":
            get_oauth = getattr(credential_reader, "get_oauth", None)
            if get_oauth is not None and get_oauth(provider.credential_name) is not None:
                return f"stored:{provider.credential_name}"
        elif credential_reader.get(provider.credential_name):
            return f"stored:{provider.credential_name}"
    if provider_kind(provider) == "anthropic" and environ.get(ANTHROPIC_AUTH_TOKEN_ENV):
        return f"env:{ANTHROPIC_AUTH_TOKEN_ENV}"
    if environ.get(provider.api_key_env):
        return f"env:{provider.api_key_env}"
    return "missing"


def serve_loop_receipt_command(
    run_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Serve one Tau Loop2 receipt run directory until interrupted."""

    resolved = run_dir.expanduser().resolve()
    if not resolved.exists():
        raise RuntimeError(f"Loop2 receipt run directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise RuntimeError(f"Loop2 receipt run path is not a directory: {resolved}")

    server = create_loop_receipt_monitor_server(resolved, host=host, port=port)
    actual_host, actual_port = server.server_address
    typer.echo(
        f"Serving Tau Loop2 receipt run {resolved.name} at "
        f"http://{actual_host}:{actual_port}/api/loop2/runs/{resolved.name}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("Stopping Tau Loop2 receipt monitor.", err=True)
    finally:
        server.server_close()


def validate_loop_receipt_command(
    run_dir: Path,
    *,
    loop2_src: Path | None = None,
) -> bool:
    """Validate one Tau Loop2 receipt run directory against Loop2 contracts."""

    resolved = run_dir.expanduser().resolve()
    result = validate_loop_receipt_with_loop2_contracts(resolved, loop2_src=loop2_src)
    payload = {
        "schema": "tau.loop_receipt.validation.v1",
        "run_dir": str(resolved),
        "ok": result.ok,
        "checked_artifacts": list(result.checked_artifacts),
        "errors": list(result.errors),
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return result.ok


def validate_loop2_contract_command(
    contract_path: Path,
    *,
    loop2_src: Path | None = None,
) -> bool:
    """Validate one Loop2 repair-node contract file."""

    resolved = contract_path.expanduser().resolve()
    result = validate_loop2_contract_file(resolved, loop2_src=loop2_src)
    payload = {
        "schema": "tau.loop2_contract.validation.v1",
        "contract": str(resolved),
        "ok": result.ok,
        "checked_artifacts": list(result.checked_artifacts),
        "errors": list(result.errors),
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return result.ok


def validate_native_loop2_run_command(
    run_dir: Path,
    *,
    loop2_src: Path | None = None,
) -> bool:
    """Validate a native Loop2 runner artifact directory."""

    resolved = run_dir.expanduser().resolve()
    result = validate_native_loop2_run_with_contracts(resolved, loop2_src=loop2_src)
    payload = {
        "schema": "tau.native_loop2_run.validation.v1",
        "run_dir": str(resolved),
        "ok": result.ok,
        "checked_artifacts": list(result.checked_artifacts),
        "errors": list(result.errors),
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return result.ok


async def run_loop2_contract_command(
    contract_path: Path,
    model: str | None,
    output: PrintOutputMode = PrintOutputMode.text,
    provider_name: str | None = None,
    loop2_src: Path | None = None,
    scillm_doctor_receipt: Path | None = None,
) -> bool:
    """Run one Tau print-mode transaction from a Loop2 repair-node contract."""

    resolved = contract_path.expanduser().resolve()
    validation = validate_loop2_contract_file(resolved, loop2_src=loop2_src)
    if not validation.ok:
        payload = {
            "schema": "tau.loop2_contract_run.v1",
            "contract": str(resolved),
            "ok": False,
            "errors": list(validation.errors),
            "mocked": provider_name in {None, "fake"},
            "live": provider_name not in {None, "fake"},
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return False

    contract = _load_loop2_contract(resolved)
    backend = str(contract.get("backend") or "fixture")
    if backend != "fixture":
        runner = _loop2_runner_from_src(loop2_src)
        if runner is not None:
            doctor_errors = _scillm_doctor_receipt_errors(scillm_doctor_receipt)
            if doctor_errors:
                payload = {
                    "schema": "tau.loop2_contract_run.v1",
                    "contract": str(resolved),
                    "ok": False,
                    "run_dir": "",
                    "node_id": str(contract.get("node_id") or ""),
                    "mocked": provider_name in {None, "fake"},
                    "live": provider_name not in {None, "fake"},
                    "checks": list(contract.get("checks") or ()),
                    "delegated": True,
                    "runner": str(runner),
                    "scillm_doctor_receipt": (
                        str(scillm_doctor_receipt.expanduser().resolve())
                        if scillm_doctor_receipt is not None
                        else ""
                    ),
                    "errors": doctor_errors,
                }
                typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                return False
            materialization_errors = _scillm_materialization_preflight_errors(contract)
            if materialization_errors:
                payload = {
                    "schema": "tau.loop2_contract_run.v1",
                    "contract": str(resolved),
                    "ok": False,
                    "run_dir": "",
                    "node_id": str(contract.get("node_id") or ""),
                    "mocked": provider_name in {None, "fake"},
                    "live": provider_name not in {None, "fake"},
                    "checks": list(contract.get("checks") or ()),
                    "delegated": True,
                    "runner": str(runner),
                    "scillm_doctor_receipt": (
                        str(scillm_doctor_receipt.expanduser().resolve())
                        if scillm_doctor_receipt is not None
                        else ""
                    ),
                    "errors": materialization_errors,
                }
                typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                return False
            with tempfile.TemporaryDirectory(prefix="tau-loop2-contract-") as temp_dir:
                prepared_contract_path, contract_preparation = (
                    _prepare_delegated_scillm_contract_for_runner(
                        resolved,
                        contract,
                        temp_dir=Path(temp_dir),
                    )
                )
                prepared_contract = _load_loop2_contract(prepared_contract_path)
                scillm_auth_preflight = await _scillm_proxy_auth_preflight(
                    prepared_contract,
                )
                if scillm_auth_preflight["ok"] is not True:
                    payload = {
                        "schema": "tau.loop2_contract_run.v1",
                        "contract": str(resolved),
                        "ok": False,
                        "run_dir": "",
                        "node_id": str(contract.get("node_id") or ""),
                        "mocked": provider_name in {None, "fake"},
                        "live": provider_name not in {None, "fake"},
                        "checks": list(contract.get("checks") or ()),
                        "delegated": True,
                        "runner": str(runner),
                        "contract_preparation": contract_preparation,
                        "scillm_auth_preflight": scillm_auth_preflight,
                        "errors": list(scillm_auth_preflight.get("errors") or ()),
                    }
                    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
                    return False
                return await _run_loop2_runner_contract(
                    runner,
                    prepared_contract_path,
                    display_contract_path=resolved,
                    contract_preparation=contract_preparation,
                    scillm_auth_preflight=scillm_auth_preflight,
                    provider_name=provider_name,
                    loop2_src=loop2_src,
                )
        payload = {
            "schema": "tau.loop2_contract_run.v1",
            "contract": str(resolved),
            "ok": False,
            "run_dir": "",
            "node_id": str(contract.get("node_id") or ""),
            "mocked": provider_name in {None, "fake"},
            "live": provider_name not in {None, "fake"},
            "checks": list(contract.get("checks") or ()),
            "errors": [
                "tau loop2-run currently supports backend=fixture only; "
                f"backend={backend} requires --loop2-src pointing at the Loop2 runner"
            ],
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return False
    receipt = _loop_receipt_config_from_contract(contract, provider_name=provider_name)
    before = _loop2_run_dirs(receipt.root_dir)
    if backend == "fixture":
        ok = await _run_fixture_loop2_print_mode(
            prompt=str(contract["objective"]),
            model=model or "fake",
            cwd=Path(str(contract["repo"])),
            output=output,
            loop_receipt=receipt,
        )
    else:
        ok = await run_openai_print_mode(
            contract["objective"],
            model,
            Path(str(contract["repo"])),
            output,
            provider_name,
            receipt,
        )
    after = _loop2_run_dirs(receipt.root_dir)
    created = [path for path in after if path not in before]
    run_dir = created[-1] if created else (after[-1] if after else None)
    receipt_validation = {
        "ran": False,
        "ok": None,
        "checked_artifacts": [],
        "errors": ["no run directory was created"],
    }
    if run_dir is not None:
        validation = validate_loop_receipt_with_loop2_contracts(run_dir, loop2_src=loop2_src)
        receipt_validation = {
            "ran": True,
            "ok": validation.ok,
            "checked_artifacts": list(validation.checked_artifacts),
            "errors": list(validation.errors),
        }
    command_ok = ok and receipt_validation["ok"] is True
    payload = {
        "schema": "tau.loop2_contract_run.v1",
        "contract": str(resolved),
        "ok": command_ok,
        "run_dir": str(run_dir) if run_dir is not None else "",
        "node_id": receipt.node_id,
        "mocked": receipt.mocked,
        "live": receipt.live,
        "checks": list(receipt.checks),
        "receipt_validation": receipt_validation,
        "errors": [] if command_ok else list(receipt_validation["errors"]),
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return command_ok


async def _run_fixture_loop2_print_mode(
    *,
    prompt: str,
    model: str,
    cwd: Path,
    output: PrintOutputMode,
    loop_receipt: LoopReceiptConfig,
) -> bool:
    provider = FakeProvider(
        [
            [
                ProviderResponseStartEvent(model=model),
                ProviderResponseEndEvent(
                    message=AssistantMessage(content="Fixture loop complete.")
                ),
            ]
        ]
    )
    with redirect_stdout(io.StringIO()):
        return await run_print_mode(
            prompt=prompt,
            model=model,
            cwd=cwd,
            provider=provider,
            output=output,
            provider_name="fixture",
            loop_receipt=loop_receipt,
        )


def _scillm_doctor_receipt_errors(receipt_path: Path | None) -> list[str]:
    if receipt_path is None:
        return ["delegated Scillm loop2 runs require --loop2-scillm-doctor-receipt"]
    resolved = receipt_path.expanduser().resolve()
    if not resolved.exists():
        return [f"Scillm doctor receipt does not exist: {resolved}"]
    try:
        receipt = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Scillm doctor receipt is unreadable: {exc}"]
    if not isinstance(receipt, dict):
        return ["Scillm doctor receipt must be a JSON object"]
    if receipt.get("schema") != "scillm.project_agent_sanity.v1":
        return [f"Scillm doctor receipt schema mismatch: {receipt.get('schema')!r}"]
    if receipt.get("status") != "PASS":
        reason = receipt.get("reason")
        return [f"Scillm doctor receipt status is {receipt.get('status')!r}: {reason}"]
    if receipt.get("mocked") is not False or receipt.get("live") is not True:
        return ["Scillm doctor receipt must be mocked:false and live:true"]
    return []


def _scillm_materialization_preflight_errors(contract: dict[str, object]) -> list[str]:
    repo_value = contract.get("repo")
    if not isinstance(repo_value, str) or not repo_value:
        return ["delegated Scillm loop2 runs require contract.repo"]
    repo = Path(repo_value).expanduser().resolve()
    if not repo.exists():
        return [f"delegated Scillm loop2 repo does not exist: {repo}"]
    if not repo.is_dir():
        return [f"delegated Scillm loop2 repo is not a directory: {repo}"]
    blocked_roots = (Path("/tmp"), Path("/var/tmp"))
    for root in blocked_roots:
        try:
            repo.relative_to(root)
        except ValueError:
            continue
        return [
            "delegated Scillm loop2 repo is not materializable by the OpenCode "
            f"worker from {root}: {repo}. Move the repair repo under the project "
            "workspace before running live loop2."
        ]
    return []


def _prepare_delegated_scillm_contract_for_runner(
    contract_path: Path,
    contract: dict[str, object],
    *,
    temp_dir: Path,
) -> tuple[Path, dict[str, object]]:
    api_key = environ.get("SCILLM_API_KEY")
    preparation: dict[str, object] = {
        "schema": "tau.loop2_contract_preparation.v1",
        "ran": False,
        "auth_source": "contract",
        "execution_contract": str(contract_path),
        "redacted_keys": [],
    }
    if not api_key:
        return contract_path, preparation

    prepared = dict(contract)
    scillm_config = prepared.get("scillm")
    if not isinstance(scillm_config, dict):
        return contract_path, preparation
    prepared_scillm = dict(scillm_config)
    if prepared_scillm.get("api_key") == api_key:
        return contract_path, preparation

    prepared_scillm["api_key"] = api_key
    prepared["scillm"] = prepared_scillm
    prepared_path = temp_dir / contract_path.name
    prepared_path.write_text(
        json.dumps(prepared, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return prepared_path, {
        "schema": "tau.loop2_contract_preparation.v1",
        "ran": True,
        "auth_source": "env:SCILLM_API_KEY",
        "execution_contract": str(prepared_path),
        "redacted_keys": ["contract.scillm.api_key"],
    }


async def _scillm_proxy_auth_preflight(contract: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "tau.scillm_proxy_auth_preflight.v1",
        "ran": True,
        "ok": False,
        "base_url": "",
        "endpoint": "/v1/scillm/loop2/capabilities",
        "caller_skill": "tau",
        "status_code": None,
        "errors": [],
    }
    scillm_config = contract.get("scillm")
    if not isinstance(scillm_config, dict):
        payload["errors"] = ["contract.scillm must be an object for auth preflight"]
        return payload
    base_url = scillm_config.get("base_url")
    api_key = scillm_config.get("api_key")
    if not isinstance(base_url, str) or not base_url:
        payload["errors"] = ["contract.scillm.base_url must be a non-empty string"]
        return payload
    payload["base_url"] = base_url.rstrip("/")
    if not isinstance(api_key, str) or not api_key or api_key.startswith("<redacted"):
        payload["errors"] = [
            "contract.scillm.api_key is missing or redacted; set SCILLM_API_KEY "
            "for delegated Scillm loop2 runs"
        ]
        return payload
    try:
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=10.0) as client:
            response = await client.get(
                "/v1/scillm/loop2/capabilities",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "X-Caller-Skill": "tau",
                },
            )
    except httpx.HTTPError as exc:
        payload["errors"] = [f"Scillm proxy auth preflight request failed: {exc}"]
        return payload
    payload["status_code"] = response.status_code
    if response.status_code != 200:
        payload["errors"] = [f"Scillm proxy auth preflight failed with HTTP {response.status_code}"]
        return payload
    try:
        body = response.json()
    except ValueError:
        payload["errors"] = ["Scillm proxy auth preflight returned non-JSON response"]
        return payload
    if not isinstance(body, dict) or body.get("schema") != "scillm.loop2.capabilities.v1":
        payload["errors"] = ["Scillm proxy auth preflight returned unexpected capabilities"]
        return payload
    payload["ok"] = True
    return payload


def _sanitize_delegated_loop2_run_artifacts(run_dir: Path) -> dict[str, object]:
    redacted_keys = _redact_delegated_loop2_run_secrets(run_dir)
    filtered = _filter_delegated_changed_files(run_dir)
    changed_artifacts = sorted({*redacted_keys.values(), *filtered.keys()})
    artifact_path = run_dir / "tau-sanitization.json"
    payload: dict[str, object] = {
        "schema": "tau.loop2_delegated_artifact_sanitization.v1",
        "ran": True,
        "artifact": str(artifact_path),
        "run_dir": str(run_dir),
        "changed_artifacts": changed_artifacts,
        "redacted_keys": sorted(redacted_keys.keys()),
        "filtered_changed_files": sum(filtered.values()),
    }
    artifact_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _index_tau_sanitization_artifact(run_dir, artifact_path)
    return payload


def _index_tau_sanitization_artifact(run_dir: Path, artifact_path: Path) -> None:
    final_receipt_path = run_dir / "final-receipt.json"
    if not final_receipt_path.exists():
        return
    try:
        final_receipt = json.loads(final_receipt_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return
    if not isinstance(final_receipt, dict):
        return
    artifacts = final_receipt.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
        final_receipt["artifacts"] = artifacts
    if artifacts.get("tau_sanitization") == str(artifact_path):
        return
    artifacts["tau_sanitization"] = str(artifact_path)
    final_receipt_path.write_text(
        json.dumps(final_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _redact_delegated_loop2_run_secrets(run_dir: Path) -> dict[str, str]:
    contract_path = run_dir / "contract.json"
    if not contract_path.exists():
        return {}
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    if not isinstance(contract, dict):
        return {}
    scillm_config = contract.get("scillm")
    if not isinstance(scillm_config, dict):
        return {}
    api_key = scillm_config.get("api_key")
    if not isinstance(api_key, str) or not api_key or api_key.startswith("<redacted"):
        return {}
    redacted = dict(contract)
    redacted_scillm = dict(scillm_config)
    redacted_scillm["api_key"] = "<redacted-scillm-api-key>"
    redacted["scillm"] = redacted_scillm
    contract_path.write_text(
        json.dumps(redacted, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"contract.scillm.api_key": "contract.json"}


def _filter_delegated_changed_files(run_dir: Path) -> dict[str, int]:
    filtered_counts: dict[str, int] = {}
    for artifact_name in ("final-receipt.json", "node-result.json"):
        artifact_path = run_dir / artifact_name
        if not artifact_path.exists():
            continue
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        changed_files = payload.get("changed_files")
        if not isinstance(changed_files, list):
            continue
        filtered = [
            item
            for item in changed_files
            if isinstance(item, str) and not _is_generated_changed_file(item)
        ]
        if filtered == changed_files:
            continue
        payload["changed_files"] = filtered
        filtered_counts[artifact_name] = len(changed_files) - len(filtered)
        artifact_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return filtered_counts


def _is_generated_changed_file(path: str) -> bool:
    parts = Path(path).parts
    return "__pycache__" in parts or ".pytest_cache" in parts or path.endswith((".pyc", ".pyo"))


async def _run_loop2_runner_contract(
    runner: Path,
    contract_path: Path,
    *,
    display_contract_path: Path | None = None,
    contract_preparation: dict[str, object] | None = None,
    scillm_auth_preflight: dict[str, object] | None = None,
    provider_name: str | None,
    loop2_src: Path | None,
) -> bool:
    reported_contract_path = display_contract_path or contract_path
    preparation = contract_preparation or {
        "schema": "tau.loop2_contract_preparation.v1",
        "ran": False,
        "auth_source": "contract",
        "execution_contract": str(contract_path),
        "redacted_keys": [],
    }
    auth_preflight = scillm_auth_preflight or {
        "schema": "tau.scillm_proxy_auth_preflight.v1",
        "ran": False,
        "ok": None,
        "errors": [],
    }
    process = await asyncio.create_subprocess_exec(
        str(runner),
        "run",
        "--contract",
        str(contract_path),
        "--json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        payload = {
            "schema": "tau.loop2_contract_run.v1",
            "contract": str(reported_contract_path),
            "ok": False,
            "run_dir": "",
            "node_id": "",
            "mocked": provider_name in {None, "fake"},
            "live": provider_name not in {None, "fake"},
            "checks": [],
            "delegated": True,
            "runner": str(runner),
            "contract_preparation": preparation,
            "scillm_auth_preflight": auth_preflight,
            "errors": [stderr.decode("utf-8", errors="replace").strip()],
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return False
    try:
        result = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Loop2 runner did not emit JSON: {runner}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Loop2 runner JSON result must be an object")
    final_receipt = str(result.get("final_receipt") or "")
    run_dir = str(Path(final_receipt).parent) if final_receipt else ""
    artifact_errors = _delegated_loop2_result_artifact_errors(result)
    native_validation = {
        "ok": False,
        "checked_artifacts": [],
        "errors": ["native validation skipped because delegated artifacts are missing"],
    }
    artifact_sanitization: dict[str, object] = {
        "schema": "tau.loop2_delegated_artifact_sanitization.v1",
        "ran": False,
        "artifact": "",
        "changed_artifacts": [],
        "redacted_keys": [],
        "filtered_changed_files": 0,
    }
    if run_dir and not artifact_errors:
        artifact_sanitization = _sanitize_delegated_loop2_run_artifacts(Path(run_dir))
        result = _load_delegated_node_result(Path(run_dir), fallback=result)
        validation = validate_native_loop2_run_with_contracts(
            Path(run_dir),
            loop2_src=loop2_src,
        )
        native_validation = {
            "ok": validation.ok,
            "checked_artifacts": list(validation.checked_artifacts),
            "errors": list(validation.errors),
        }
    native_validation_errors = [
        f"native Loop2 validation failed: {error}" for error in native_validation["errors"]
    ]
    ok = result.get("status") == "PASS" and not artifact_errors and native_validation["ok"] is True
    payload = {
        "schema": "tau.loop2_contract_run.v1",
        "contract": str(reported_contract_path),
        "ok": ok,
        "run_dir": run_dir,
        "node_id": str(result.get("node_id") or ""),
        "mocked": bool(result.get("mocked")),
        "live": bool(result.get("live")),
        "checks": result.get("checks") if isinstance(result.get("checks"), list) else [],
        "delegated": True,
        "runner": str(runner),
        "contract_preparation": preparation,
        "scillm_auth_preflight": auth_preflight,
        "node_result": result,
        "native_validation": native_validation,
        "artifact_sanitization": artifact_sanitization,
        "errors": (
            []
            if ok
            else artifact_errors
            or native_validation_errors
            or [f"Loop2 runner returned status={result.get('status')}"]
        ),
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return ok


def _delegated_loop2_result_artifact_errors(result: dict[str, object]) -> list[str]:
    missing: list[str] = []
    for key in ("final_receipt", "transport_dag_evidence", "events"):
        _append_missing_cli_artifact(missing, f"node_result.{key}", result.get(key))
    final_receipt_path = result.get("final_receipt")
    if isinstance(final_receipt_path, str) and final_receipt_path:
        run_dir = Path(final_receipt_path).parent
        for name in ("contract.json", "current-state.json", "node-result.json"):
            if not (run_dir / name).exists():
                missing.append(f"run_dir.{name}")
    checks = result.get("checks")
    if isinstance(checks, list):
        for index, check in enumerate(checks, start=1):
            if isinstance(check, dict):
                _append_missing_cli_artifact(
                    missing,
                    f"node_result.checks[{index}].stdout_path",
                    check.get("stdout_path"),
                )
                _append_missing_cli_artifact(
                    missing,
                    f"node_result.checks[{index}].stderr_path",
                    check.get("stderr_path"),
                )
            else:
                missing.append(f"node_result.checks[{index}]")
    else:
        missing.append("node_result.checks")
    return [f"missing delegated Loop2 artifacts: {', '.join(missing)}"] if missing else []


def _load_delegated_node_result(
    run_dir: Path,
    *,
    fallback: dict[str, object],
) -> dict[str, object]:
    node_result_path = run_dir / "node-result.json"
    try:
        loaded = json.loads(node_result_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return fallback
    return loaded if isinstance(loaded, dict) else fallback


def _append_missing_cli_artifact(missing: list[str], label: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        missing.append(label)
        return
    if not Path(value).exists():
        missing.append(f"{label}={value}")


def _loop2_runner_from_src(loop2_src: Path | None) -> Path | None:
    if loop2_src is None:
        return None
    runner = loop2_src.expanduser().resolve().parent / "run.sh"
    if runner.exists() and runner.is_file():
        return runner
    return None


def _load_loop2_contract(contract_path: Path) -> dict[str, object]:
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Unable to read Loop2 contract: {contract_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Loop2 contract is not valid JSON: {contract_path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Loop2 contract must be a JSON object")
    return payload


def _loop_receipt_config_from_contract(
    contract: dict[str, object],
    *,
    provider_name: str | None = None,
) -> LoopReceiptConfig:
    repo = Path(str(contract["repo"])).expanduser()
    run_root = Path(str(contract.get("run_root") or ".loop2/runs")).expanduser()
    if not run_root.is_absolute():
        run_root = repo / run_root
    backend = str(contract.get("backend") or "fixture")
    backend_config: dict[str, object] = {}
    if backend == "scillm" and isinstance(contract.get("scillm"), dict):
        backend_config["scillm"] = contract["scillm"]
    mocked = provider_name in {None, "fake"}
    return LoopReceiptConfig(
        root_dir=run_root,
        node_id=str(contract["node_id"]),
        allowed_globs=tuple(str(item) for item in contract["allowed_globs"]),
        required_changed_globs=tuple(
            str(item) for item in contract.get("required_changed_globs", ())
        ),
        checks=tuple(str(item) for item in contract["checks"]),
        max_attempts=int(contract.get("max_attempts") or 1),
        backend=backend,
        backend_config=backend_config or None,
        mocked=mocked,
        live=not mocked,
    )


def _loop2_run_dirs(root_dir: Path) -> list[Path]:
    if not root_dir.exists():
        return []
    return sorted(path for path in root_dir.iterdir() if path.is_dir())


def inspect_loop_receipt_command(
    run_dir: Path,
    *,
    loop2_src: Path | None = None,
    include_validation: bool = False,
) -> bool:
    """Print a fail-closed JSON summary for one Tau Loop2 receipt run directory."""

    resolved = run_dir.expanduser().resolve()
    summary = loop_receipt_summary(resolved)
    validation_ok = True
    loop2_contract_validation = {
        "ran": False,
        "ok": None,
        "validator": None,
        "checked_artifacts": [],
        "errors": ["not run; pass --loop2-inspect-validate to validate Loop2 contracts"],
    }
    summary = {
        **summary,
        "loop2_contract_validation": loop2_contract_validation,
    }
    if include_validation:
        if _inspect_summary_is_delegated_native_loop2(summary):
            validator = "native_loop2"
            validation = validate_native_loop2_run_with_contracts(resolved, loop2_src=loop2_src)
        else:
            validator = "tau_receipt"
            validation = validate_loop_receipt_with_loop2_contracts(
                resolved,
                loop2_src=loop2_src,
            )
        validation_ok = validation.ok
        loop2_contract_validation = {
            "ran": True,
            "ok": validation.ok,
            "validator": validator,
            "checked_artifacts": list(validation.checked_artifacts),
            "errors": list(validation.errors),
        }
        summary = {
            **summary,
            "loop2_contract_validation": loop2_contract_validation,
        }
    summary = {
        **summary,
        "tau_delegation": _tau_delegation_inspect_summary(
            summary,
            loop2_contract_validation=loop2_contract_validation,
        ),
    }
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
    return bool(summary.get("found")) and validation_ok


def _inspect_summary_is_delegated_native_loop2(summary: dict[str, object]) -> bool:
    return isinstance(summary.get("tau_sanitization"), dict)


def _tau_delegation_inspect_summary(
    summary: dict[str, object],
    *,
    loop2_contract_validation: dict[str, object],
) -> dict[str, object]:
    artifacts = summary.get("artifacts")
    tau_sanitization = summary.get("tau_sanitization")
    has_sidecar = isinstance(tau_sanitization, dict)
    checked_artifacts = loop2_contract_validation.get("checked_artifacts")
    if not isinstance(checked_artifacts, list):
        checked_artifacts = []
    validation_ran = loop2_contract_validation.get("ran") is True
    payload: dict[str, object] = {
        "schema": "tau.loop2_delegation.inspect.v1",
        "delegated": has_sidecar,
        "tau_sanitization_present": has_sidecar,
        "tau_sanitization_artifact": "",
        "changed_artifacts": [],
        "redacted_keys": [],
        "filtered_changed_files": 0,
        "validation_checked_tau_sanitization": (
            "tau_sanitization" in checked_artifacts if validation_ran else None
        ),
    }
    if isinstance(artifacts, dict):
        artifact = artifacts.get("tau_sanitization")
        if isinstance(artifact, str):
            payload["tau_sanitization_artifact"] = artifact
    if has_sidecar:
        changed_artifacts = tau_sanitization.get("changed_artifacts")
        redacted_keys = tau_sanitization.get("redacted_keys")
        filtered_changed_files = tau_sanitization.get("filtered_changed_files")
        if isinstance(changed_artifacts, list):
            payload["changed_artifacts"] = [
                item for item in changed_artifacts if isinstance(item, str)
            ]
        if isinstance(redacted_keys, list):
            payload["redacted_keys"] = [item for item in redacted_keys if isinstance(item, str)]
        if isinstance(filtered_changed_files, int):
            payload["filtered_changed_files"] = filtered_changed_files
    return payload


def check_loop_receipt_monitor_command(run_dir: Path) -> bool:
    """Validate the read-only Loop2 monitor endpoints for one Tau receipt run."""

    resolved = run_dir.expanduser().resolve()
    result = check_loop_receipt_monitor_contract(resolved)
    payload = {
        "schema": "tau.loop2_monitor_check.v1",
        "run_dir": str(resolved),
        "ok": result.ok,
        "checked_endpoints": list(result.checked_endpoints),
        "errors": list(result.errors),
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return result.ok


def emit_loop_peer_command(
    run_dir: Path,
    *,
    switchboard_url: str,
    target_harness: str,
    monitor_base_url: str | None,
) -> bool:
    """Emit one Tau peer handoff through pi-mono switchboard."""

    resolved = run_dir.expanduser().resolve()
    result = emit_loop_peer_to_switchboard(
        resolved,
        switchboard_url=switchboard_url,
        target_harness=target_harness,
        monitor_base_url=monitor_base_url,
    )
    payload = {
        "schema": "tau.loop_peer_switchboard_emit.v1",
        "run_dir": str(resolved),
        "ok": result.ok,
        "switchboard_url": result.switchboard_url,
        "status_code": result.status_code,
        "request": result.request,
        "response": result.response,
        "errors": list(result.errors),
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return result.ok


def check_loop2_scillm_doctor_command(receipt_path: Path) -> bool:
    """Validate a Scillm doctor receipt before delegated Loop2 Scillm runs."""

    resolved = receipt_path.expanduser().resolve()
    errors = _scillm_doctor_receipt_errors(resolved)
    payload = {
        "schema": "tau.loop2_scillm_doctor_check.v1",
        "receipt": str(resolved),
        "ok": not errors,
        "errors": errors,
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return not errors


def backfill_loop_receipt_artifacts_command(run_dir: Path) -> bool:
    """Backfill missing standard artifact paths in one final receipt."""

    payload = backfill_loop_receipt_artifact_index(run_dir)
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return bool(payload.get("ok"))


def human_goal_change_bridge_command(
    goal_change_path: Path,
    *,
    active_goal_hash: str | None,
    trusted_human: bool,
    handoff_out: Path,
    receipt_path: Path,
    agents_root: Path | None,
) -> bool:
    """Bridge a trusted human goal-change packet into a normal start handoff."""

    resolved_goal_change = goal_change_path.expanduser().resolve()
    payload = _load_json_object(resolved_goal_change, label="human goal change")
    receipt = write_human_goal_change_bridge_receipt(
        payload,
        receipt_path.expanduser().resolve(),
        handoff_path=handoff_out.expanduser().resolve(),
        active_goal_hash=active_goal_hash,
        trusted_human=trusted_human,
        source=str(resolved_goal_change),
        agent_registry_root=agents_root,
    )
    typer.echo(json.dumps(receipt, indent=2, sort_keys=True))
    return bool(receipt.get("ok"))


def project_agent_handoff_command(
    handoff_path: Path,
    *,
    active_goal_hash: str | None,
    receipt_path: Path | None,
    agents_root: Path | None,
) -> bool:
    """Print a non-mutating GitHub projection for one Tau agent handoff."""

    resolved = handoff_path.expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Agent handoff is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Agent handoff root must be a JSON object")

    if receipt_path is not None:
        projection = write_agent_handoff_projection_receipt(
            payload,
            receipt_path.expanduser().resolve(),
            active_goal_hash=active_goal_hash,
            agent_registry_root=agents_root,
        )
    else:
        projection = project_agent_handoff(
            payload,
            active_goal_hash=active_goal_hash,
            agent_registry_root=agents_root,
        )
    typer.echo(json.dumps(projection.as_dict(), indent=2, sort_keys=True))
    return projection.ok


def transport_agent_handoff_to_github_command(
    handoff_path: Path,
    *,
    active_goal_hash: str | None,
    receipt_path: Path | None,
    agents_root: Path | None,
    apply_github: bool,
    github_apply_policy_receipt: Path | None = None,
) -> bool:
    """Render or apply GitHub transport for one validated handoff."""

    payload = _load_json_object(handoff_path, label="agent handoff")
    projection = project_agent_handoff(
        payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agents_root,
    )
    if not projection.ok:
        transport_receipt = {
            "schema": "tau.github_handoff_transport_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dry_run": not apply_github,
            "applied": False,
            "target": projection.target,
            "commands": [],
            "command_results": [],
            "receipt_path": str(receipt_path.expanduser().resolve()) if receipt_path else None,
            "errors": list(projection.errors),
        }
        if receipt_path is not None:
            resolved = receipt_path.expanduser().resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(
                json.dumps(transport_receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        typer.echo(json.dumps(transport_receipt, indent=2, sort_keys=True))
        return False

    policy_errors = _github_apply_policy_receipt_errors(
        projection=projection.as_dict(),
        apply_github=apply_github,
        github_apply_policy_receipt=github_apply_policy_receipt,
    )
    if policy_errors:
        transport_receipt = {
            "schema": "tau.github_handoff_transport_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "dry_run": False,
            "applied": False,
            "target": projection.target,
            "commands": [],
            "command_results": [],
            "preflight_results": [],
            "receipt_path": str(receipt_path.expanduser().resolve()) if receipt_path else None,
            "errors": policy_errors,
        }
        if receipt_path is not None:
            resolved = receipt_path.expanduser().resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(
                json.dumps(transport_receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        typer.echo(json.dumps(transport_receipt, indent=2, sort_keys=True))
        return False

    transport = transport_handoff_projection_to_github(
        projection.as_dict(),
        apply=apply_github,
        receipt_path=receipt_path,
    )
    typer.echo(json.dumps(transport.as_dict(), indent=2, sort_keys=True))
    return transport.ok


def _github_apply_policy_receipt_errors(
    *,
    projection: dict[str, Any],
    apply_github: bool,
    github_apply_policy_receipt: Path | None,
) -> list[str]:
    if not apply_github:
        return []
    if github_apply_policy_receipt is None:
        return [
            "GitHub --apply requires --github-apply-policy-receipt "
            "with a PASS tau.github_apply_policy_receipt.v1 receipt."
        ]
    receipt = _load_json_object(github_apply_policy_receipt, label="GitHub apply policy receipt")
    errors: list[str] = []
    if receipt.get("schema") != "tau.github_apply_policy_receipt.v1":
        errors.append(
            "GitHub apply policy receipt schema must be tau.github_apply_policy_receipt.v1"
        )
    if receipt.get("ok") is not True or receipt.get("status") != "PASS":
        errors.append("GitHub apply policy receipt must be PASS")
    if receipt.get("target") != projection.get("target"):
        errors.append("GitHub apply policy receipt target must match the handoff projection target")
    failed_checks = receipt.get("failed_checks")
    if isinstance(failed_checks, list) and failed_checks:
        errors.append("GitHub apply policy receipt has failed_checks")
    receipt_errors = receipt.get("errors")
    if isinstance(receipt_errors, list) and receipt_errors:
        errors.append("GitHub apply policy receipt has errors")
    required_actions = set(_github_projection_action_names(projection))
    receipt_actions = receipt.get("actions")
    receipt_action_set = (
        {str(action) for action in receipt_actions} if isinstance(receipt_actions, list) else set()
    )
    if required_actions and not required_actions.issubset(receipt_action_set):
        missing = sorted(required_actions - receipt_action_set)
        errors.append(f"GitHub apply policy receipt is missing actions: {missing}")
    requirements = receipt.get("requirements")
    if not isinstance(requirements, dict) or not all(
        requirements.get(key) is True for key in ("approval_packet", "preflight", "redaction")
    ):
        errors.append(
            "GitHub apply policy receipt must show approval, preflight, and redaction gates"
        )
    return errors


def _github_projection_action_names(projection: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    comment = projection.get("comment")
    if isinstance(comment, dict) and str(comment.get("body") or "").strip():
        actions.append("comment")
    labels = projection.get("labels")
    if isinstance(labels, dict):
        add = labels.get("add")
        remove = labels.get("remove")
        if (isinstance(add, list) and add) or (isinstance(remove, list) and remove):
            actions.append("label")
    return actions


def transport_generated_ticket_to_github_command(
    ticket_path: Path,
    *,
    active_goal_hash: str | None,
    receipt_path: Path | None,
    agents_root: Path | None,
    apply_github: bool,
    dedupe_preflight_path: Path | None = None,
) -> bool:
    """Render or apply GitHub issue creation for one validated generated ticket."""

    resolved = ticket_path.expanduser().resolve()
    try:
        payload = load_generated_ticket(resolved)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Generated ticket is unreadable: {resolved}: {exc}") from exc

    validation = validate_generated_ticket(
        payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agents_root,
    )
    github = payload.get("github")
    repo = github.get("repo") if isinstance(github, dict) else None
    if not validation.ok or validation.github_create is None or not isinstance(repo, str):
        errors = list(validation.errors)
        if not isinstance(repo, str) or not repo.strip():
            errors.append("github.repo must be a non-empty string")
        transport_receipt = {
            "schema": "tau.github_generated_ticket_transport_receipt.v1",
            "ok": False,
            "dry_run": not apply_github,
            "applied": False,
            "target": {"repo": repo, "target": "new"} if isinstance(repo, str) else None,
            "commands": [],
            "receipt_path": str(receipt_path.expanduser().resolve()) if receipt_path else None,
            "errors": errors,
        }
        if receipt_path is not None:
            _write_json_receipt(receipt_path, transport_receipt)
        typer.echo(json.dumps(transport_receipt, indent=2, sort_keys=True))
        return False

    dedupe_projection: dict[str, Any] | None = None
    if dedupe_preflight_path is not None:
        dedupe_projection = _load_json_object(
            dedupe_preflight_path,
            label="generated-ticket dedupe preflight receipt",
        )
    transport = transport_generated_ticket_to_github(
        repo=repo,
        github_create=validation.github_create,
        dedupe_projection=dedupe_projection,
        require_dedupe_preflight=apply_github,
        apply=apply_github,
        receipt_path=receipt_path,
    )
    typer.echo(json.dumps(transport.as_dict(), indent=2, sort_keys=True))
    return transport.ok


def transport_handoff_command_loop_terminal_to_github_command(
    loop_receipt_path: Path,
    *,
    receipt_path: Path | None,
    apply_github: bool,
) -> bool:
    """Render GitHub transport commands for a command-loop terminal handoff."""

    payload = _load_json_object(loop_receipt_path, label="command loop receipt")
    transport = transport_command_loop_terminal_to_github(
        payload,
        apply=apply_github,
        receipt_path=receipt_path,
    )
    typer.echo(json.dumps(transport.as_dict(), indent=2, sort_keys=True))
    return transport.ok


def transport_goal_guardian_reconciliation_to_github_command(
    reconciliation_receipt_path: Path,
    *,
    receipt_path: Path | None,
    apply_github: bool,
) -> bool:
    """Render GitHub transport commands for a goal-guardian reconciliation receipt."""

    payload = _load_json_object(
        reconciliation_receipt_path,
        label="goal guardian reconciliation receipt",
    )
    transport = transport_goal_guardian_reconciliation_to_github(
        payload,
        apply=apply_github,
        receipt_path=receipt_path,
    )
    typer.echo(json.dumps(transport.as_dict(), indent=2, sort_keys=True))
    return transport.ok


def transport_handoff_command_loop_reconciliation_to_github_command(
    loop_receipt_path: Path,
    *,
    receipt_path: Path | None,
    apply_github: bool,
) -> bool:
    """Render GitHub transport for a goal-guardian receipt inside a loop receipt."""

    loop_receipt_resolved = loop_receipt_path.expanduser().resolve()
    loop_receipt = _load_json_object(loop_receipt_resolved, label="command loop receipt")
    reconciliation_path = _goal_guardian_reconciliation_artifact_from_loop(
        loop_receipt,
        loop_receipt_path=loop_receipt_resolved,
    )
    reconciliation_receipt = _load_json_object(
        reconciliation_path,
        label="goal guardian reconciliation receipt",
    )
    ticket_source_path = _goal_guardian_ticket_source_from_reconciliation(reconciliation_receipt)
    transport = transport_goal_guardian_reconciliation_to_github(
        reconciliation_receipt,
        apply=apply_github,
    )
    payload = {
        "schema": "tau.github_command_loop_reconciliation_transport_receipt.v1",
        "ok": transport.ok,
        "dry_run": transport.dry_run,
        "applied": transport.applied,
        "source_loop_receipt_path": str(loop_receipt_resolved),
        "reconciliation_receipt_path": str(reconciliation_path),
        "ticket_source_path": ticket_source_path,
        "transport": transport.as_dict(),
        "errors": list(transport.errors),
    }
    if receipt_path is not None:
        payload["receipt_path"] = str(receipt_path.expanduser().resolve())
        _write_json_receipt(receipt_path, payload)
    else:
        payload["receipt_path"] = None
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return transport.ok


def goal_guardian_ticket_source_github_fetch_command(
    repo: str,
    *,
    output_path: Path,
    receipt_path: Path | None,
    execute: bool,
    state: str,
    limit: int,
) -> bool:
    """Render or run a read-only GitHub issue-list fetch for goal-guardian."""

    result = fetch_goal_guardian_ticket_source_from_github(
        repo=repo,
        output_path=output_path,
        execute=execute,
        state=state,
        limit=limit,
        receipt_path=receipt_path,
    )
    typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return result.ok


def _goal_guardian_reconciliation_artifact_from_loop(
    loop_receipt: dict[str, object],
    *,
    loop_receipt_path: Path,
) -> Path:
    if loop_receipt.get("schema") != "tau.agent_handoff_command_loop_receipt.v1":
        raise RuntimeError(
            "command loop receipt schema must be tau.agent_handoff_command_loop_receipt.v1"
        )
    if loop_receipt.get("ok") is not True:
        raise RuntimeError("command loop receipt must be ok before reconciliation GitHub transport")
    artifact = _find_goal_guardian_reconciliation_artifact(loop_receipt.get("artifacts"))
    if artifact is None:
        dispatches = loop_receipt.get("dispatches")
        if isinstance(dispatches, list):
            for dispatch in dispatches:
                if isinstance(dispatch, dict):
                    artifact = _find_goal_guardian_reconciliation_artifact(
                        dispatch.get("artifacts")
                    )
                    if artifact is not None:
                        break
    if artifact is None:
        raise RuntimeError("command loop receipt lacks goal-guardian reconciliation artifact")
    path = Path(artifact).expanduser()
    if not path.is_absolute():
        path = loop_receipt_path.parent / path
    resolved = path.resolve()
    if not resolved.is_file():
        raise RuntimeError(f"goal-guardian reconciliation artifact does not exist: {resolved}")
    return resolved


def _find_goal_guardian_reconciliation_artifact(artifacts: object) -> str | None:
    if not isinstance(artifacts, list):
        return None
    for artifact in artifacts:
        if isinstance(artifact, str) and artifact.endswith(
            "goal-guardian-reconciliation-receipt.json"
        ):
            return artifact
    return None


def _goal_guardian_ticket_source_from_reconciliation(
    reconciliation_receipt: dict[str, object],
) -> str | None:
    reconciliation = reconciliation_receipt.get("open_ticket_reconciliation")
    if not isinstance(reconciliation, dict):
        return None
    source = reconciliation.get("source")
    return source if isinstance(source, str) and source.strip() else None


def project_agent_handoff_chain_command(
    handoff_paths: list[Path],
    *,
    active_goal_hash: str | None,
    receipt_dir: Path,
    agents_root: Path | None,
) -> bool:
    """Write a dry-run chain receipt for local handoff routing continuity."""

    payloads: list[dict[str, object]] = []
    for handoff_path in handoff_paths:
        resolved = handoff_path.expanduser().resolve()
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Agent handoff is unreadable: {resolved}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Agent handoff root must be a JSON object: {resolved}")
        payloads.append(payload)

    chain = write_agent_handoff_chain_receipt(
        payloads,
        receipt_dir.expanduser().resolve(),
        active_goal_hash=active_goal_hash,
        agent_registry_root=agents_root,
    )
    typer.echo(json.dumps(chain.as_dict(), indent=2, sort_keys=True))
    return chain.ok


def project_agent_handoff_loop_command(
    start_path: Path,
    *,
    responses_dir: Path,
    active_goal_hash: str | None,
    receipt_dir: Path,
    max_steps: int,
    agents_root: Path | None,
) -> bool:
    """Write a dry-run loop receipt by following next_agent response files."""

    start_payload = _load_json_object(start_path, label="start handoff")
    response_payloads = _load_handoff_response_dir(responses_dir)
    loop = write_agent_handoff_loop_receipt(
        start_payload,
        response_payloads,
        receipt_dir.expanduser().resolve(),
        active_goal_hash=active_goal_hash,
        agent_registry_root=agents_root,
        max_steps=max_steps,
    )
    typer.echo(json.dumps(loop.as_dict(), indent=2, sort_keys=True))
    return loop.ok


def project_agent_handoff_dispatch_command(
    start_path: Path,
    *,
    responses_dir: Path,
    active_goal_hash: str | None,
    receipt_dir: Path,
    agents_root: Path | None,
) -> bool:
    """Write a one-step dispatch receipt by consuming the selected response file."""

    start_payload = _load_json_object(start_path, label="start handoff")
    response_payloads = _load_handoff_response_dir(responses_dir)
    resolved_receipt_dir = receipt_dir.expanduser().resolve()
    dispatch = write_agent_handoff_dispatch_receipt(
        start_payload,
        response_payloads,
        resolved_receipt_dir,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agents_root,
    )
    receipt_payload = _load_json_object(
        resolved_receipt_dir / "dispatch-receipt.json",
        label="dispatch receipt",
    )
    typer.echo(json.dumps(receipt_payload, indent=2, sort_keys=True))
    return dispatch.ok


def project_agent_handoff_command_dispatch_command(
    start_path: Path,
    *,
    command_spec: Path,
    active_goal_hash: str | None,
    receipt_dir: Path,
    agents_root: Path | None,
) -> bool:
    """Write a one-step dispatch receipt by running a bounded command."""

    start_payload = _load_json_object(start_path, label="start handoff")
    spec = _load_command_dispatch_spec(command_spec)
    resolved_receipt_dir = receipt_dir.expanduser().resolve()
    dispatch = write_agent_handoff_command_dispatch_receipt(
        start_payload,
        spec["command"],
        resolved_receipt_dir,
        timeout_s=spec["timeout_s"],
        cwd=spec["cwd"],
        active_goal_hash=active_goal_hash,
        agent_registry_root=agents_root,
    )
    receipt_payload = _load_json_object(
        resolved_receipt_dir / "dispatch-receipt.json",
        label="dispatch receipt",
    )
    typer.echo(json.dumps(receipt_payload, indent=2, sort_keys=True))
    return dispatch.ok


def project_agent_handoff_agent_command_dispatch_command(
    start_path: Path,
    *,
    active_goal_hash: str | None,
    receipt_dir: Path,
    agents_root: Path,
    command_spec_root: Path | None = None,
) -> bool:
    """Write a one-step dispatch receipt using the selected agent registry command."""

    start_payload = _load_json_object(start_path, label="start handoff")
    start_projection = project_agent_handoff(
        start_payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agents_root,
    )
    if not start_projection.ok:
        dispatch = write_agent_handoff_command_dispatch_receipt(
            start_payload,
            [],
            receipt_dir.expanduser().resolve(),
            active_goal_hash=active_goal_hash,
            agent_registry_root=agents_root,
        )
        typer.echo(json.dumps(dispatch.as_dict(), indent=2, sort_keys=True))
        return False
    selected_agent = start_projection.next_agent
    if selected_agent is None:
        raise RuntimeError("start handoff did not select a next agent")
    try:
        spec = load_agent_dispatch_command_spec(
            agents_root,
            selected_agent,
            command_spec_root=command_spec_root,
        )
    except ValueError as exc:
        resolved_receipt_dir = receipt_dir.expanduser().resolve()
        resolved_receipt_dir.mkdir(parents=True, exist_ok=True)
        start_receipt_path = resolved_receipt_dir / "start-handoff.receipt.json"
        start_projection_payload = start_projection.as_dict()
        start_receipt_path.write_text(
            json.dumps(start_projection_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        receipt_payload = {
            "schema": TAU_AGENT_HANDOFF_DISPATCH_RECEIPT_SCHEMA,
            "ok": False,
            "status": "BLOCKED",
            "selected_agent": selected_agent,
            "stop_reason": "missing_agent_command_spec",
            "mocked": False,
            "live": False,
            "runner": "agent-registry-command",
            "start_projection": start_projection_payload,
            "response_projection": None,
            "command_results": [],
            "receipt_dir": str(resolved_receipt_dir),
            "artifacts": [str(start_receipt_path)],
            "errors": [str(exc)],
        }
        (resolved_receipt_dir / "dispatch-receipt.json").write_text(
            json.dumps(receipt_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        typer.echo(json.dumps(receipt_payload, indent=2, sort_keys=True))
        return False
    dispatch = write_agent_handoff_command_dispatch_receipt(
        start_payload,
        spec["command"],
        receipt_dir.expanduser().resolve(),
        timeout_s=spec["timeout_s"],
        cwd=spec["cwd"],
        active_goal_hash=active_goal_hash,
        agent_registry_root=agents_root,
    )
    typer.echo(json.dumps(dispatch.as_dict(), indent=2, sort_keys=True))
    return dispatch.ok


def project_agent_handoff_command_loop_command(
    start_path: Path,
    *,
    active_goal_hash: str | None,
    receipt_dir: Path,
    agents_root: Path,
    command_spec_root: Path | None,
    command_policy_path: Path | None,
    goal_guardian_ticket_source: Path | None,
    max_steps: int,
) -> bool:
    """Write a command-backed loop receipt using selected agent registry commands."""

    start_payload = _load_json_object(start_path, label="start handoff")
    loop = write_agent_handoff_command_loop_receipt(
        start_payload,
        receipt_dir.expanduser().resolve(),
        agent_registry_root=agents_root,
        command_spec_root=command_spec_root,
        command_policy_path=command_policy_path,
        active_goal_hash=active_goal_hash,
        goal_guardian_ticket_source=goal_guardian_ticket_source,
        max_steps=max_steps,
    )
    typer.echo(json.dumps(loop.as_dict(), indent=2, sort_keys=True))
    return loop.ok


def project_agent_scillm_subagent_gate_command(summary_path: Path) -> bool:
    """Validate that Scillm subagent loop summaries only pass completed substrates."""

    result = validate_scillm_subagent_loop_summary(summary_path)
    typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return result.ok


def project_agent_ticket_subagent_closure_proof_command(
    *,
    output: Path,
    allow_live_filesystem: bool,
) -> dict[str, object]:
    """Write the code-ticket closure evidence proof receipt."""

    return write_ticket_subagent_closure_proof(
        output,
        allow_live_filesystem=allow_live_filesystem,
    )


def project_agent_persona_dream_panel_proof_command(
    *,
    out_dir: Path,
    agents_root: Path,
    command_spec_root: Path,
    active_goal_hash: str,
    github_target: str,
    panel_evidence: Path | None,
    panel_source: Path | None,
    panel_repair_work_order: Path | None,
    scillm_live_panel: bool,
    panel_prompt: str | None,
    scillm_image_model: str,
    scillm_image_auth: str,
    scillm_image_quality: str,
    scillm_vlm_model: str,
    scillm_base_url: str,
) -> bool:
    """Run the local one-panel persona-dream command-loop proof."""

    manifest = write_persona_dream_panel_proof(
        out_dir,
        agents_root=agents_root,
        command_spec_root=command_spec_root,
        active_goal_hash=active_goal_hash,
        github_target=github_target,
        panel_evidence=panel_evidence,
        panel_source=panel_source,
        panel_repair_work_order=panel_repair_work_order,
        scillm_live_panel=scillm_live_panel,
        panel_prompt=panel_prompt,
        scillm_image_model=scillm_image_model,
        scillm_image_auth=scillm_image_auth,
        scillm_image_quality=scillm_image_quality,
        scillm_vlm_model=scillm_vlm_model,
        scillm_base_url=scillm_base_url,
    )
    typer.echo(json.dumps(manifest, indent=2, sort_keys=True))
    return bool(manifest.get("ok")) and (
        manifest.get("first_blocker") is not None
        or manifest.get("dry_run_one_scene_kling_request") is not None
    )


def _fetch_github_issue(*, repo: str, issue: int) -> tuple[dict[str, object], dict[str, object]]:
    gh_path = which("gh")
    if gh_path is None:
        raise RuntimeError("self-fix tick requires the gh CLI on PATH")
    command = [
        gh_path,
        "issue",
        "view",
        str(issue),
        "--repo",
        repo,
        "--json",
        "number,title,body,state,labels,comments,url,createdAt,updatedAt,author",
    ]
    started_at = datetime.now(UTC)
    completed = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
    duration_seconds = (datetime.now(UTC) - started_at).total_seconds()
    fetch = {
        "command": command,
        "exit_code": completed.returncode,
        "duration_seconds": duration_seconds,
        "stderr": completed.stderr.strip(),
    }
    if completed.returncode != 0:
        fetch["ok"] = False
        raise RuntimeError(f"gh issue view failed for {repo}#{issue}: {completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        fetch["ok"] = False
        raise RuntimeError(
            f"gh issue view returned invalid JSON for {repo}#{issue}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        fetch["ok"] = False
        raise RuntimeError(f"gh issue view returned non-object JSON for {repo}#{issue}")
    payload.update(_fetch_github_issue_rest_metadata(repo=repo, issue=issue))
    fetch["ok"] = True
    return payload, fetch


def _fetch_github_issue_rest_metadata(*, repo: str, issue: int) -> dict[str, object]:
    gh_path = which("gh")
    if gh_path is None:
        return {}
    owner_repo = repo.strip("/")
    command = [gh_path, "api", f"repos/{owner_repo}/issues/{issue}"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
    if completed.returncode != 0:
        return {"securityMetadataFetchError": completed.stderr.strip()}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"securityMetadataFetchError": "gh api issue JSON parse failed"}
    if not isinstance(payload, dict):
        return {"securityMetadataFetchError": "gh api issue returned non-object JSON"}
    metadata: dict[str, object] = {}
    association = payload.get("author_association")
    if isinstance(association, str):
        metadata["authorAssociation"] = association
    updated_at = payload.get("updated_at")
    if isinstance(updated_at, str):
        metadata["restUpdatedAt"] = updated_at
    user = payload.get("user")
    if isinstance(user, dict) and isinstance(user.get("login"), str):
        metadata["authorLogin"] = user["login"]
    return metadata


def _fetch_github_open_issues(
    *,
    repo: str,
    limit: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    gh_path = which("gh")
    if gh_path is None:
        raise RuntimeError("self-fix poll requires the gh CLI on PATH")
    command = [
        gh_path,
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        str(limit),
        "--json",
        "number,title,body,state,labels,comments,url,createdAt,updatedAt,author",
    ]
    started_at = datetime.now(UTC)
    completed = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
    duration_seconds = (datetime.now(UTC) - started_at).total_seconds()
    fetch = {
        "command": command,
        "exit_code": completed.returncode,
        "duration_seconds": duration_seconds,
        "stderr": completed.stderr.strip(),
    }
    if completed.returncode != 0:
        fetch["ok"] = False
        raise RuntimeError(f"gh issue list failed for {repo}: {completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        fetch["ok"] = False
        raise RuntimeError(f"gh issue list returned invalid JSON for {repo}: {exc}") from exc
    if not isinstance(payload, list):
        fetch["ok"] = False
        raise RuntimeError(f"gh issue list returned non-list JSON for {repo}")
    issues: list[dict[str, object]] = []
    for item in payload:
        if isinstance(item, dict):
            issues.append(item)
    fetch["ok"] = True
    fetch["issue_count"] = len(issues)
    return issues, fetch


def _issue_labels(issue_payload: dict[str, object]) -> set[str]:
    labels = issue_payload.get("labels")
    if not isinstance(labels, list):
        return set()
    names: set[str] = set()
    for label in labels:
        if isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
        elif isinstance(label, str) and label.strip():
            names.add(label.strip())
    return names


def _issue_text(issue_payload: dict[str, object]) -> str:
    lines = [
        f"title: {issue_payload.get('title', '')}",
        f"state: {issue_payload.get('state', '')}",
        "",
        str(issue_payload.get("body") or ""),
    ]
    comments = issue_payload.get("comments")
    if isinstance(comments, list) and comments:
        lines.append("")
        lines.append("recent_comments:")
        for comment in comments[-3:]:
            if not isinstance(comment, dict):
                continue
            author = comment.get("author")
            author_login = ""
            if isinstance(author, dict) and isinstance(author.get("login"), str):
                author_login = author["login"]
            body = str(comment.get("body") or "")
            lines.append(f"- {author_login}: {body[:1200]}")
    return "\n".join(lines).strip()


def _self_fix_eligibility(
    issue_labels: set[str],
    required_labels: tuple[str, ...],
) -> dict[str, object]:
    required = {label for label in required_labels if label}
    matched = sorted(issue_labels & required)
    eligible = bool(matched)
    return {
        "eligible": eligible,
        "policy": "any_required_label_match",
        "required_labels_any": sorted(required),
        "matched_labels": matched,
        "issue_labels": sorted(issue_labels),
        "reason": (
            "issue has at least one configured self-fix routing label"
            if eligible
            else "issue has no configured self-fix routing labels"
        ),
    }


def _issue_body_edited_after_routing_label(
    *,
    repo: str,
    issue: int,
    routing_labels: set[str],
) -> dict[str, object]:
    if not routing_labels:
        return {"ok": True, "edited_after_routing_label": False, "reason": "no_routing_labels"}
    gh_path = which("gh")
    if gh_path is None:
        return {"ok": False, "edited_after_routing_label": True, "error": "gh_missing"}
    command = [gh_path, "api", f"repos/{repo.strip('/')}/issues/{issue}/events", "--paginate"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
    if completed.returncode != 0:
        return {
            "ok": False,
            "edited_after_routing_label": True,
            "error": completed.stderr.strip() or "issue_events_fetch_failed",
        }
    try:
        events = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "edited_after_routing_label": True,
            "error": "issue_events_json_parse_failed",
        }
    if not isinstance(events, list):
        return {
            "ok": False,
            "edited_after_routing_label": True,
            "error": "issue_events_non_list",
        }
    latest_route_label: str | None = None
    latest_route_label_at: str | None = None
    latest_body_edit_at: str | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        created_at = event.get("created_at")
        if not isinstance(created_at, str):
            continue
        label = event.get("label")
        label_name = label.get("name") if isinstance(label, dict) else None
        if event.get("event") == "labeled" and label_name in routing_labels:
            if latest_route_label_at is None or created_at > latest_route_label_at:
                latest_route_label_at = created_at
                latest_route_label = label_name
        elif event.get("event") == "edited" and (
            latest_body_edit_at is None or created_at > latest_body_edit_at
        ):
            latest_body_edit_at = created_at
    edited_after = bool(
        latest_route_label_at is not None
        and latest_body_edit_at is not None
        and latest_body_edit_at > latest_route_label_at
    )
    return {
        "ok": not edited_after,
        "edited_after_routing_label": edited_after,
        "latest_routing_label": latest_route_label,
        "latest_routing_label_at": latest_route_label_at,
        "latest_body_edit_at": latest_body_edit_at,
    }


def _self_fix_issue_ref(issue_payload: dict[str, object]) -> dict[str, object]:
    return {
        "number": issue_payload.get("number"),
        "title": issue_payload.get("title"),
        "url": issue_payload.get("url"),
        "state": issue_payload.get("state"),
        "labels": sorted(_issue_labels(issue_payload)),
        "updated_at": issue_payload.get("updatedAt"),
    }


def _memory_post_json(
    *,
    client: httpx.Client,
    path: str,
    payload: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    started_at = datetime.now(UTC)
    try:
        response = client.post(path, json=payload)
    except httpx.HTTPError as exc:
        duration_seconds = (datetime.now(UTC) - started_at).total_seconds()
        return (
            {},
            {
                "ok": False,
                "path": path,
                "error": str(exc),
                "duration_seconds": duration_seconds,
            },
        )
    duration_seconds = (datetime.now(UTC) - started_at).total_seconds()
    call = {
        "ok": response.status_code < 400,
        "path": path,
        "status_code": response.status_code,
        "duration_seconds": duration_seconds,
    }
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = {"raw": response.text}
        call["ok"] = False
        call["error"] = "response was not JSON"
    if not isinstance(body, dict):
        body = {"value": body}
    return body, call


def _self_fix_memory_preflight(
    *,
    memory_base_url: str,
    query: str,
    receipt_dir: Path,
) -> dict[str, object]:
    resolved_receipt_dir = receipt_dir.expanduser().resolve()
    query_excerpt = query[:4000]
    with httpx.Client(base_url=memory_base_url, timeout=15.0) as client:
        intent_payload, intent_call = _memory_post_json(
            client=client,
            path="/intent",
            payload={
                "q": query_excerpt,
                "scope": "tau",
                "app": "tau",
                "fast": True,
            },
        )
        recall_payload, recall_call = _memory_post_json(
            client=client,
            path="/recall",
            payload={
                "q": query_excerpt,
                "scope": "tau",
                "k": 5,
            },
        )
    _write_json_object(resolved_receipt_dir / "memory-intent.json", intent_payload)
    _write_json_object(resolved_receipt_dir / "memory-recall.json", recall_payload)
    recall_items = recall_payload.get("items")
    if not isinstance(recall_items, list):
        recall_items = recall_payload.get("results")
    recall_count = len(recall_items) if isinstance(recall_items, list) else 0
    action = intent_payload.get("action") or intent_payload.get("intent")
    return {
        "ok": bool(intent_call["ok"] and recall_call["ok"]),
        "mocked": False,
        "live": True,
        "memory_base_url": memory_base_url,
        "intent_call": intent_call,
        "recall_call": recall_call,
        "intent_action": action if isinstance(action, str) else None,
        "recall_count": recall_count,
        "artifacts": {
            "intent": str(resolved_receipt_dir / "memory-intent.json"),
            "recall": str(resolved_receipt_dir / "memory-recall.json"),
        },
    }


def _self_fix_goal_hash(*, repo: str, issue: int, issue_text: str) -> str:
    digest = hashlib.sha256(f"{repo}#{issue}\n{issue_text}".encode()).hexdigest()
    return f"sha256:{digest}"


def _self_fix_goal_helper_packet(
    *,
    repo: str,
    issue: int,
    issue_payload: dict[str, object],
    goal_hash: str,
    memory_preflight: dict[str, object],
    eligible: object,
) -> dict[str, object]:
    return {
        "schema": "tau.goal_helper.v1",
        "mocked": False,
        "live": True,
        "created_at": datetime.now(UTC).isoformat(),
        "source": {
            "repo": repo,
            "issue": issue,
            "url": issue_payload.get("url"),
            "title": issue_payload.get("title"),
        },
        "goal": {
            "goal_id": f"goal-tau-self-fix-issue-{issue}",
            "goal_version": 1,
            "goal_hash": goal_hash,
            "immutable_goal": (
                "Run one bounded Tau self-fix intake tick for the selected GitHub issue "
                "without mutating code or GitHub state in this slice."
            ),
        },
        "primary_proof": "tau self-fix tick writes self-fix-receipt.json for the live issue.",
        "completion_criteria": [
            "Live GitHub issue is fetched through gh.",
            "Memory /intent and /recall are called before subagent dispatch.",
            "A tau.agent_handoff.v1 start handoff is written.",
            "The bounded command-loop writes a command-loop receipt.",
            "The final self-fix receipt lists explicit non-claims.",
        ],
        "allowed_scope": [
            "Issue intake.",
            "Memory-first preflight.",
            "Goal-helper packet generation.",
            "Start handoff generation.",
            "Bounded local coder/reviewer command-loop dispatch.",
        ],
        "forbidden_drift": [
            "Do not edit application code as part of this intake proof.",
            "Do not mutate GitHub labels or comments in this slice.",
            "Do not claim autonomous repair, cron, GitHub Actions, rollback, "
            "or Scillm quality unless separately proven.",
        ],
        "retry_budget": {
            "max_live_attempts_before_escalation": 2,
            "escalation": (
                "Use WebGPT/create-architecture or ask the human if the live proof "
                "cannot be produced."
            ),
        },
        "stop_condition": (
            "Stop after one command-loop receipt, a human route, or a fail-closed receipt; "
            "do not continue into code mutation in this slice."
        ),
        "eligible_for_dispatch": bool(eligible),
        "memory_first": {
            "ok": memory_preflight.get("ok"),
            "intent_artifact": memory_preflight.get("artifacts", {}).get("intent")
            if isinstance(memory_preflight.get("artifacts"), dict)
            else None,
            "recall_artifact": memory_preflight.get("artifacts", {}).get("recall")
            if isinstance(memory_preflight.get("artifacts"), dict)
            else None,
        },
    }


def _self_fix_start_handoff(
    *,
    repo: str,
    issue: int,
    issue_payload: dict[str, object],
    goal_hash: str,
    memory_preflight: dict[str, object],
    goal_helper_path: Path,
) -> dict[str, object]:
    title = str(issue_payload.get("title") or f"Issue #{issue}")
    url = issue_payload.get("url")
    artifacts = [
        str(goal_helper_path.expanduser().resolve()),
    ]
    memory_artifacts = memory_preflight.get("artifacts")
    if isinstance(memory_artifacts, dict):
        for value in memory_artifacts.values():
            if isinstance(value, str):
                artifacts.append(value)
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": repo,
            "target": f"issue#{issue}",
            "url": url,
        },
        "goal": {
            "goal_id": f"goal-tau-self-fix-issue-{issue}",
            "goal_version": 1,
            "goal_hash": goal_hash,
        },
        "previous_subagent": "human",
        "context": {
            "summary": (
                f"Live GitHub issue #{issue} was selected for a bounded Tau self-fix "
                f"intake tick: {title}"
            ),
            "artifacts": artifacts,
        },
        "result": {
            "status": "REQUESTED",
            "summary": (
                "Human requested Tau to start the coder/reviewer self-fix loop for this issue."
            ),
            "evidence": [
                str(goal_helper_path.expanduser().resolve()),
            ],
        },
        "rationale": (
            "The issue has a self-fix routing label and Memory-first preflight has produced "
            "artifacts, so the next bounded actor should be coder."
        ),
        "next_agent": {
            "name": "coder",
            "executor": "local",
            "reason": (
                "Coder should perform the first bounded implementation analysis for the issue."
            ),
        },
        "required_evidence": [
            "Coder emits a schema-valid tau.agent_handoff.v1 handoff.",
            "Reviewer emits a schema-valid tau.agent_handoff.v1 handoff.",
            "Any code mutation in a later slice starts from a checkpoint commit "
            "and records rollback status.",
        ],
        "stop_condition": (
            "Stop when reviewer routes to human/PASS, the command-loop hits max steps, "
            "or any dispatch fails closed."
        ),
    }


def project_agent_self_fix_tick_command(
    *,
    repo: str,
    issue: int,
    receipt_dir: Path,
    agents_root: Path,
    command_spec_root: Path | None,
    active_goal_hash: str | None,
    memory_base_url: str,
    scillm_base_url: str = "http://127.0.0.1:4001",
    model: str = "gpt-5.5",
    repo_root: Path | None = None,
    max_steps: int = 3,
    required_labels: tuple[str, ...] = (),
    repair: bool = False,
    apply_github: bool = False,
) -> bool:
    """Run one bounded self-fix issue tick."""

    resolved_receipt_dir = receipt_dir.expanduser().resolve()
    resolved_receipt_dir.mkdir(parents=True, exist_ok=True)
    issue_payload, issue_fetch = _fetch_github_issue(repo=repo, issue=issue)
    _write_json_object(resolved_receipt_dir / "issue.json", issue_payload)
    issue_text = _issue_text(issue_payload)
    issue_labels = _issue_labels(issue_payload)
    eligibility = _self_fix_eligibility(issue_labels, required_labels)
    body_edit_gate = _issue_body_edited_after_routing_label(
        repo=repo,
        issue=issue,
        routing_labels=set(eligibility.get("matched_labels", [])),
    )
    issue_payload["bodyEditedAfterRoutingLabel"] = bool(
        body_edit_gate.get("edited_after_routing_label")
    )
    issue_payload["bodyEditAfterRoutingLabelGate"] = body_edit_gate
    memory_preflight = _self_fix_memory_preflight(
        memory_base_url=memory_base_url,
        query=issue_text,
        receipt_dir=resolved_receipt_dir,
    )
    goal_hash = active_goal_hash or _self_fix_goal_hash(
        repo=repo,
        issue=issue,
        issue_text=issue_text,
    )
    goal_helper = _self_fix_goal_helper_packet(
        repo=repo,
        issue=issue,
        issue_payload=issue_payload,
        goal_hash=goal_hash,
        memory_preflight=memory_preflight,
        eligible=eligibility["eligible"],
    )
    _write_json_object(resolved_receipt_dir / "goal-helper.json", goal_helper)
    start_handoff = _self_fix_start_handoff(
        repo=repo,
        issue=issue,
        issue_payload=issue_payload,
        goal_hash=goal_hash,
        memory_preflight=memory_preflight,
        goal_helper_path=resolved_receipt_dir / "goal-helper.json",
    )
    _write_json_object(resolved_receipt_dir / "start-handoff.json", start_handoff)

    loop_payload: dict[str, object] | None = None
    repair_payload: dict[str, object] | None = None
    loop_ok = False
    if eligibility["eligible"] and memory_preflight["ok"] and repair:
        repair_payload = run_ticket_repair(
            repo=repo,
            issue_payload=issue_payload,
            repo_root=repo_root or Path.cwd(),
            receipt_dir=resolved_receipt_dir / "ticket-repair",
            memory_base_url=memory_base_url,
            scillm_base_url=scillm_base_url,
            model=model,
            active_goal_hash=goal_hash,
            apply_github=apply_github,
        )
        loop_payload = {
            "schema": "tau.self_fix_command_loop_bypassed_for_repair.v1",
            "ok": bool(repair_payload.get("ok")),
            "reason": "repair_request_contract_selected",
        }
        loop_ok = bool(repair_payload.get("ok"))
    elif eligibility["eligible"] and memory_preflight["ok"]:
        loop = write_agent_handoff_command_loop_receipt(
            start_handoff,
            resolved_receipt_dir / "command-loop",
            agent_registry_root=agents_root,
            command_spec_root=command_spec_root,
            active_goal_hash=goal_hash,
            max_steps=max_steps,
        )
        loop_payload = loop.as_dict()
        loop_ok = loop.ok
    else:
        loop_payload = {
            "schema": "tau.self_fix_loop_skipped.v1",
            "ok": False,
            "reason": "eligibility_or_memory_preflight_failed",
        }

    receipt = {
        "schema": "tau.self_fix_tick_receipt.v1",
        "ok": bool(eligibility["eligible"] and memory_preflight["ok"] and loop_ok),
        "mocked": False,
        "live": True,
        "scope": (
            "One bounded Tau self-fix intake tick: GitHub issue fetch, Memory-first "
            "preflight, goal-helper/start handoff generation, and command-loop dispatch."
        ),
        "repo": repo,
        "issue": {
            "number": issue,
            "url": issue_payload.get("url"),
            "title": issue_payload.get("title"),
            "state": issue_payload.get("state"),
            "labels": sorted(issue_labels),
        },
        "issue_fetch": issue_fetch,
        "eligibility": eligibility,
        "memory_preflight": memory_preflight,
        "goal_hash": goal_hash,
        "artifacts": {
            "issue": str(resolved_receipt_dir / "issue.json"),
            "memory_intent": str(resolved_receipt_dir / "memory-intent.json"),
            "memory_recall": str(resolved_receipt_dir / "memory-recall.json"),
            "goal_helper": str(resolved_receipt_dir / "goal-helper.json"),
            "start_handoff": str(resolved_receipt_dir / "start-handoff.json"),
            "command_loop_receipt": str(
                resolved_receipt_dir / "command-loop" / "command-loop-receipt.json"
            ),
            "ticket_repair_receipt": str(
                resolved_receipt_dir / "ticket-repair" / "ticket-repair-receipt.json"
            )
            if repair
            else None,
        },
        "command_loop": loop_payload,
        "ticket_repair": repair_payload,
        "checkpoint": {
            "required_before_mutation": True,
            "mutation_attempted": bool(repair),
            "status": "handled_by_ticket_repair" if repair else "not_applicable_for_intake_slice",
        },
        "claims": {
            "proves": [
                "Tau can fetch the selected GitHub issue through gh.",
                "Tau can run Memory-first intent/recall before dispatch.",
                "Tau can generate a goal-helper packet and start handoff from the issue.",
                (
                    "Tau can route a repair-contract issue into the streaming coder/reviewer "
                    "repair path."
                    if repair
                    else "Tau can invoke the existing command-loop for eligible issues."
                ),
            ],
            "does_not_prove": [
                "Autonomous code mutation." if not repair else "Unbounded autonomous repair.",
                "Scillm-backed coder/reviewer semantic quality unless command specs call Scillm.",
                "GitHub Actions event wiring.",
                "Cron recovery.",
                "Rollback after a real failed code mutation.",
            ],
        },
    }
    _write_json_object(resolved_receipt_dir / "self-fix-receipt.json", receipt)
    typer.echo(json.dumps(receipt, indent=2, sort_keys=True))
    return bool(receipt["ok"])


def project_agent_self_fix_poll_command(
    *,
    repo: str,
    receipt_dir: Path,
    agents_root: Path,
    command_spec_root: Path | None,
    active_goal_hash: str | None,
    memory_base_url: str,
    scillm_base_url: str = "http://127.0.0.1:4001",
    model: str = "gpt-5.5",
    repo_root: Path | None = None,
    max_steps: int = 3,
    required_labels: tuple[str, ...] = (),
    issue_limit: int = 30,
    dispatch: bool = False,
    repair: bool = False,
    apply_github: bool = False,
) -> bool:
    """Poll live GitHub issues and optionally dispatch exactly one eligible issue."""

    resolved_receipt_dir = receipt_dir.expanduser().resolve()
    resolved_receipt_dir.mkdir(parents=True, exist_ok=True)
    issues, issue_fetch = _fetch_github_open_issues(repo=repo, limit=issue_limit)
    _write_json_object(resolved_receipt_dir / "open-issues.json", issues)

    candidates: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for issue_payload in issues:
        labels = _issue_labels(issue_payload)
        eligibility = _self_fix_eligibility(labels, required_labels)
        ref = _self_fix_issue_ref(issue_payload)
        ref["eligibility"] = eligibility
        number = issue_payload.get("number")
        if eligibility["eligible"] and isinstance(number, int):
            candidates.append(ref)
        else:
            skipped.append(ref)

    selected = candidates[0] if candidates else None
    dispatch_receipt_dir = None
    dispatch_ok: bool | None = None
    if selected is not None and dispatch:
        number = selected.get("number")
        if not isinstance(number, int):
            raise RuntimeError("selected issue has no integer number")
        dispatch_receipt_dir = resolved_receipt_dir / f"issue-{number}"
        dispatch_ok = project_agent_self_fix_tick_command(
            repo=repo,
            issue=number,
            receipt_dir=dispatch_receipt_dir,
            agents_root=agents_root,
            command_spec_root=command_spec_root,
            active_goal_hash=active_goal_hash,
            memory_base_url=memory_base_url,
            scillm_base_url=scillm_base_url,
            model=model,
            repo_root=repo_root,
            max_steps=max_steps,
            required_labels=required_labels,
            repair=repair,
            apply_github=apply_github,
        )

    status = "IDLE" if selected is None else ("DISPATCHED" if dispatch else "READY")
    proves = [
        "Tau can poll the live GitHub issue queue through gh.",
        "Tau can apply the configured one-ticket eligibility rule.",
    ]
    if selected is None:
        proves.append("Tau writes a deterministic idle receipt when no eligible issue exists.")
    elif dispatch:
        proves.append("Tau dispatches exactly one selected eligible issue.")
        if repair:
            proves.append("Tau can route the selected issue into the contract-backed repair path.")
    else:
        proves.append("Tau reports the first selected eligible issue without dispatch.")

    does_not_prove = [
        "Unbounded autonomous operation.",
    ]
    if not dispatch:
        does_not_prove.append(
            "A code repair unless dispatch_requested is true and the nested tick receipt proves it."
        )
        does_not_prove.append("GitHub issue closure.")

    receipt = {
        "schema": "tau.self_fix_poll_receipt.v1",
        "ok": bool(issue_fetch["ok"] and (dispatch_ok is not False)),
        "status": status,
        "mocked": False,
        "live": True,
        "repo": repo,
        "issue_limit": issue_limit,
        "dispatch_requested": dispatch,
        "repair_requested": repair,
        "apply_github": apply_github,
        "issue_fetch": issue_fetch,
        "open_issue_count": len(issues),
        "eligible_issue_count": len(candidates),
        "selected_issue": selected,
        "candidate_issues": candidates,
        "skipped_issues": skipped,
        "artifacts": {
            "open_issues": str(resolved_receipt_dir / "open-issues.json"),
            "dispatch_receipt_dir": str(dispatch_receipt_dir) if dispatch_receipt_dir else None,
            "dispatch_receipt": str(dispatch_receipt_dir / "self-fix-receipt.json")
            if dispatch_receipt_dir
            else None,
        },
        "claims": {
            "proves": proves,
            "does_not_prove": does_not_prove,
        },
    }
    _write_json_object(resolved_receipt_dir / "self-fix-poll-receipt.json", receipt)
    typer.echo(json.dumps(receipt, indent=2, sort_keys=True))
    return bool(receipt["ok"])


def project_agent_handoff_adapter_command(
    *,
    result_status: str | None,
    result_summary: str | None,
    next_agent: str | None,
    next_executor: str | None,
    next_reason: str | None,
    required_evidence: str | None,
    stop_condition: str | None,
) -> dict[str, object]:
    """Emit one schema-valid Tau handoff response from stdin for registry command adapters."""

    try:
        start_payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"stdin handoff JSON is unreadable: {exc}") from exc
    if not isinstance(start_payload, dict):
        raise RuntimeError("stdin handoff JSON root must be an object")
    github = start_payload.get("github")
    goal = start_payload.get("goal")
    context = start_payload.get("context")
    next_payload = start_payload.get("next_agent")
    if not isinstance(github, dict):
        raise RuntimeError("stdin handoff missing github object")
    if not isinstance(goal, dict):
        raise RuntimeError("stdin handoff missing goal object")
    if not isinstance(context, dict):
        raise RuntimeError("stdin handoff missing context object")
    if not isinstance(next_payload, dict):
        raise RuntimeError("stdin handoff missing next_agent object")
    previous_subagent = environ.get("TAU_HANDOFF_SELECTED_AGENT")
    if not previous_subagent:
        previous_subagent = str(next_payload.get("name") or "")
    if not previous_subagent:
        raise RuntimeError("selected agent is missing")

    resolved_result_status = result_status or "COMPLETED"
    resolved_next_agent = next_agent or "human"
    resolved_next_executor = next_executor or "human"
    resolved_next_reason = next_reason or "Human review is required after this bounded response."
    resolved_required_evidence = (
        required_evidence or "Human accepts, redirects, or requests another bounded subagent."
    )
    resolved_stop_condition = stop_condition or "Human posts a schema-valid handoff or decision."
    summary = (
        result_summary
        or f"{previous_subagent} consumed the handoff through the Tau registry command adapter."
    )
    artifacts = context.get("artifacts") if isinstance(context.get("artifacts"), list) else []
    return {
        "schema": "tau.agent_handoff.v1",
        "github": github,
        "goal": goal,
        "previous_subagent": previous_subagent,
        "context": {
            "summary": f"Registry command adapter handled route for {previous_subagent}.",
            "artifacts": artifacts,
        },
        "result": {
            "status": resolved_result_status,
            "summary": summary,
            "evidence": ["tau handoff-agent-adapter emitted this schema-valid response from stdin"],
        },
        "rationale": (
            f"{previous_subagent} completed one bounded adapter turn; "
            "routing follows the configured next agent."
        ),
        "next_agent": {
            "name": resolved_next_agent,
            "executor": resolved_next_executor,
            "reason": resolved_next_reason,
        },
        "required_evidence": [resolved_required_evidence],
        "stop_condition": resolved_stop_condition,
    }


def project_agent_handoff_research_auditor_adapter_command() -> dict[str, object]:
    """Emit a research-auditor handoff that refuses unapproved external research."""

    start_payload = _read_stdin_handoff()
    github = _required_mapping(start_payload, "github", "stdin handoff")
    goal = _required_mapping(start_payload, "goal", "stdin handoff")
    context = _required_mapping(start_payload, "context", "stdin handoff")
    authorization = context.get("research_authorization")
    artifacts = context.get("artifacts") if isinstance(context.get("artifacts"), list) else []
    previous_subagent = environ.get("TAU_HANDOFF_SELECTED_AGENT") or "research-auditor"
    if previous_subagent != "research-auditor":
        raise RuntimeError(
            "handoff-research-auditor-adapter may only run for selected agent research-auditor"
        )

    if not _research_authorized(authorization):
        return {
            "schema": "tau.agent_handoff.v1",
            "github": github,
            "goal": goal,
            "previous_subagent": "research-auditor",
            "context": {
                "summary": (
                    "Research auditor refused fresh external research because the handoff "
                    "did not include context.research_authorization.approved=true."
                ),
                "artifacts": artifacts,
            },
            "result": {
                "status": "REFUSED",
                "summary": (
                    "Fresh external research was not authorized; no Brave/WebGPT call was made."
                ),
                "evidence": [
                    (
                        "research-auditor checked context.research_authorization.approved "
                        "and found no approval"
                    )
                ],
            },
            "rationale": (
                "Tau must not perform fresh web research from a RESEARCH intent unless the "
                "handoff explicitly authorizes the external research lane."
            ),
            "next_agent": {
                "name": "human",
                "executor": "human",
                "reason": (
                    "Human must approve a schema-valid fresh research route before Tau calls "
                    "Brave Search, WebGPT, or another external research lane."
                ),
            },
            "required_evidence": [
                (
                    "Human posts a handoff with context.research_authorization.approved=true "
                    "and a named research method."
                )
            ],
            "stop_condition": "Human route is posted.",
        }

    method = _research_authorization_method(authorization)
    receipt_path = _research_authorization_receipt_path(authorization)
    if not receipt_path:
        return {
            "schema": "tau.agent_handoff.v1",
            "github": github,
            "goal": goal,
            "previous_subagent": "research-auditor",
            "context": {
                "summary": (
                    f"Research auditor accepted authorization for {method}, but no external "
                    "research receipt was attached."
                ),
                "artifacts": artifacts,
            },
            "result": {
                "status": "NEEDS_AGENT",
                "summary": (
                    f"Fresh research lane {method} is authorized, but no external research "
                    "receipt has been produced."
                ),
                "evidence": [
                    "context.research_authorization.approved=true",
                    f"context.research_authorization.method={method}",
                    "context.research_authorization.receipt_path missing",
                ],
            },
            "rationale": (
                "Authorization alone is not research evidence; Tau must receive a durable "
                "external research receipt before routing to review."
            ),
            "next_agent": {
                "name": "human",
                "executor": "human",
                "reason": (
                    f"Human must dispatch the actual {method} research executor or attach "
                    "a schema-valid external research receipt."
                ),
            },
            "required_evidence": [
                f"External research receipt for {method} with sources and retrieval timestamp."
            ],
            "stop_condition": "Human route is posted.",
        }

    receipt, receipt_errors = _load_external_research_receipt(receipt_path, method)
    if receipt_errors:
        return {
            "schema": "tau.agent_handoff.v1",
            "github": github,
            "goal": goal,
            "previous_subagent": "research-auditor",
            "context": {
                "summary": (
                    f"Research auditor refused {method} results because the attached external "
                    "research receipt was invalid."
                ),
                "artifacts": [*artifacts, receipt_path],
            },
            "result": {
                "status": "REFUSED",
                "summary": "Attached external research receipt failed validation.",
                "evidence": [f"receipt_error:{error}" for error in receipt_errors],
            },
            "rationale": (
                "Tau cannot route fresh research to review unless the external research "
                "receipt is durable and schema-valid."
            ),
            "next_agent": {
                "name": "human",
                "executor": "human",
                "reason": (
                    "Human must attach a corrected external research receipt or stop the route."
                ),
            },
            "required_evidence": [
                f"Corrected external research receipt for {method} with non-empty sources."
            ],
            "stop_condition": "Human route is posted.",
        }

    source_count = len(receipt.get("sources", [])) if isinstance(receipt, dict) else 0
    return {
        "schema": "tau.agent_handoff.v1",
        "github": github,
        "goal": goal,
        "previous_subagent": "research-auditor",
        "context": {
            "summary": (
                f"Research auditor accepted a schema-valid {method} receipt with "
                f"{source_count} source(s)."
            ),
            "artifacts": [*artifacts, receipt_path],
        },
        "result": {
            "status": "COMPLETED",
            "summary": (
                f"Fresh research lane {method} produced a schema-valid external research receipt."
            ),
            "evidence": [
                "context.research_authorization.approved=true",
                f"context.research_authorization.method={method}",
                f"context.research_authorization.receipt_path={receipt_path}",
                f"external_research_receipt.sources={source_count}",
            ],
        },
        "rationale": (
            "A durable external research receipt is attached, so a reviewer can inspect "
            "the sources without weakening the Memory-first proof boundary."
        ),
        "next_agent": {
            "name": "reviewer",
            "executor": "either",
            "reason": "Reviewer should inspect the external research receipt before Tau answers.",
        },
        "required_evidence": [f"Reviewer receipt over {receipt_path} and its cited sources."],
        "stop_condition": "Reviewer posts a schema-valid receipt.",
    }


def project_agent_external_research_receipt_command(
    *,
    query: str,
    method: str,
    summary: str | None,
    sources: list[str],
    output: Path | None,
    retrieved_at: str | None,
    from_brave: str | None = None,
    count: str | None = None,
) -> dict[str, object]:
    """Create a durable external research receipt from explicit source evidence."""

    normalized_query = query.strip()
    normalized_method = method.strip()
    if not normalized_query:
        raise RuntimeError("--query requires a non-empty value")
    if not normalized_method:
        raise RuntimeError("--method requires a non-empty value")
    if from_brave == "true":
        parsed_sources = _brave_search_sources(normalized_query, count=count)
        if summary is None:
            summary = f"Brave Search returned {len(parsed_sources)} source(s) for review."
        normalized_method = "brave-search"
    else:
        parsed_sources = [_parse_external_research_source(source) for source in sources]
    if not parsed_sources:
        raise RuntimeError("at least one --source title|url value is required")
    receipt = {
        "schema": "tau.external_research_receipt.v1",
        "method": normalized_method,
        "query": normalized_query,
        "retrieved_at": (
            retrieved_at.strip()
            if isinstance(retrieved_at, str) and retrieved_at.strip()
            else datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ),
        "summary": (
            summary.strip()
            if isinstance(summary, str) and summary.strip()
            else f"{len(parsed_sources)} explicit source(s) were attached for review."
        ),
        "sources": parsed_sources,
    }
    _, errors = _validate_external_research_receipt_payload(receipt, normalized_method)
    if errors:
        raise RuntimeError("; ".join(errors))
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def project_agent_subagent_receipt_from_handoff_command(
    *,
    run_id: str,
    subagent: str,
    actor_type: str | None,
    ticket: str | None,
    output: Path | None,
) -> dict[str, object]:
    """Convert a completed Tau handoff response into a subagent receipt artifact."""

    handoff = _read_stdin_handoff()
    if handoff.get("schema") != "tau.agent_handoff.v1":
        raise RuntimeError("stdin handoff schema must be tau.agent_handoff.v1")
    goal = _required_mapping(handoff, "goal", "stdin handoff")
    context = _required_mapping(handoff, "context", "stdin handoff")
    result = _required_mapping(handoff, "result", "stdin handoff")
    next_agent = _required_mapping(handoff, "next_agent", "stdin handoff")
    status = result.get("status")
    allowed_statuses = {
        "PASS",
        "COMPLETED",
        "NEEDS_CHANGES",
        "BLOCKED",
        "INSUFFICIENT_EVIDENCE",
        "REFUSED",
    }
    if status not in allowed_statuses:
        raise RuntimeError(f"handoff result.status {status!r} cannot become subagent receipt")
    goal_id = goal.get("goal_id")
    goal_version = goal.get("goal_version")
    goal_hash = goal.get("goal_hash")
    if not isinstance(goal_id, str) or not goal_id.strip():
        raise RuntimeError("handoff goal.goal_id must be non-empty")
    if not isinstance(goal_version, int) or goal_version < 1:
        raise RuntimeError("handoff goal.goal_version must be a positive integer")
    if not isinstance(goal_hash, str) or not goal_hash.strip():
        raise RuntimeError("handoff goal.goal_hash must be non-empty")
    rationale = handoff.get("rationale")
    stop_condition = handoff.get("stop_condition")
    if not isinstance(rationale, str) or not rationale.strip():
        raise RuntimeError("handoff rationale must be non-empty")
    if not isinstance(stop_condition, str) or not stop_condition.strip():
        raise RuntimeError("handoff stop_condition must be non-empty")
    next_name = next_agent.get("name")
    next_reason = next_agent.get("reason")
    next_executor = next_agent.get("executor")
    if not isinstance(next_name, str) or not next_name.strip():
        raise RuntimeError("handoff next_agent.name must be non-empty")
    if not isinstance(next_reason, str) or not next_reason.strip():
        raise RuntimeError("handoff next_agent.reason must be non-empty")
    if next_executor not in {"local", "github-actions", "either", "human"}:
        raise RuntimeError(
            "handoff next_agent.executor must be local, github-actions, either, or human"
        )
    artifacts = context.get("artifacts") if isinstance(context.get("artifacts"), list) else []
    evidence = result.get("evidence") if isinstance(result.get("evidence"), list) else []
    commands_run = (
        result.get("commands_run") if isinstance(result.get("commands_run"), list) else []
    )
    github = handoff.get("github") if isinstance(handoff.get("github"), dict) else {}
    resolved_ticket = ticket or str(github.get("target") or "")
    receipt = {
        "schema": "tau.subagent_receipt.v1",
        "goal": {
            "goal_id": goal_id,
            "goal_version": goal_version,
            "goal_hash": goal_hash,
            "immutable_goal_preserved": True,
        },
        "context": {
            "run_id": run_id.strip(),
            "ticket": resolved_ticket,
            "subagent": subagent.strip(),
            "actor_type": (actor_type or "tau").strip(),
            "artifacts_read": artifacts,
            "assumptions": [],
            "unknowns": [],
        },
        "result": {
            "status": status,
            "summary": str(result.get("summary") or ""),
            "artifacts": artifacts,
            "commands_run": commands_run,
            "mocked": False,
            "live": True,
        },
        "rationale": rationale,
        "evidence": evidence,
        "next": {
            "subagent": next_name,
            "reason": next_reason,
            "executor": next_executor,
        },
        "stop_condition": stop_condition,
    }
    receipt_errors = _validate_subagent_receipt_payload(receipt)
    if receipt_errors:
        raise RuntimeError("; ".join(receipt_errors))
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def project_agent_handoff_goal_guardian_adapter_command(
    *,
    next_agent: str | None,
    next_executor: str | None,
    next_reason: str | None,
    required_evidence: str | None,
    stop_condition: str | None,
    ticket_source: str | None = None,
) -> dict[str, object]:
    """Emit a goal-guardian handoff only when the active goal hash is preserved."""

    try:
        start_payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"stdin handoff JSON is unreadable: {exc}") from exc
    if not isinstance(start_payload, dict):
        raise RuntimeError("stdin handoff JSON root must be an object")
    github = start_payload.get("github")
    goal = start_payload.get("goal")
    context = start_payload.get("context")
    if not isinstance(github, dict):
        raise RuntimeError("stdin handoff missing github object")
    if not isinstance(goal, dict):
        raise RuntimeError("stdin handoff missing goal object")
    if not isinstance(context, dict):
        raise RuntimeError("stdin handoff missing context object")

    active_goal_hash = environ.get("TAU_HANDOFF_ACTIVE_GOAL_HASH")
    goal_hash = goal.get("goal_hash")
    if not isinstance(active_goal_hash, str) or not active_goal_hash.strip():
        raise RuntimeError("TAU_HANDOFF_ACTIVE_GOAL_HASH is required")
    if goal_hash != active_goal_hash:
        raise RuntimeError("goal-guardian refused stale or changed goal hash")

    human_goal_change = context.get("human_goal_change")
    if isinstance(human_goal_change, dict):
        return _project_agent_goal_guardian_reconciliation_handoff(
            github=github,
            goal=goal,
            context=context,
            human_goal_change=human_goal_change,
            ticket_source=ticket_source,
        )

    resolved_next_agent = next_agent or "project-or-harness-verifier"
    resolved_next_executor = next_executor or "local"
    resolved_next_reason = (
        next_reason or "The preserved-goal handoff should be checked by a verifier."
    )
    resolved_required_evidence = required_evidence or "Verifier posts a schema-valid receipt."
    resolved_stop_condition = stop_condition or "Verifier handoff is posted or Tau fails closed."
    artifacts = context.get("artifacts") if isinstance(context.get("artifacts"), list) else []
    return {
        "schema": "tau.agent_handoff.v1",
        "github": github,
        "goal": goal,
        "previous_subagent": "goal-guardian",
        "context": {
            "summary": "Goal guardian verified that the handoff preserved the active goal hash.",
            "artifacts": artifacts,
        },
        "result": {
            "status": "PASS",
            "summary": "Active goal hash was preserved.",
            "evidence": ["TAU_HANDOFF_ACTIVE_GOAL_HASH matched handoff.goal.goal_hash"],
        },
        "rationale": (
            "Goal preservation passed, so the next bounded agent can continue "
            "without a human goal amendment."
        ),
        "next_agent": {
            "name": resolved_next_agent,
            "executor": resolved_next_executor,
            "reason": resolved_next_reason,
        },
        "required_evidence": [resolved_required_evidence],
        "stop_condition": resolved_stop_condition,
    }


def _read_stdin_handoff() -> dict[str, object]:
    try:
        start_payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"stdin handoff JSON is unreadable: {exc}") from exc
    if not isinstance(start_payload, dict):
        raise RuntimeError("stdin handoff JSON root must be an object")
    return start_payload


def _required_mapping(payload: dict[str, object], key: str, label: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} missing {key} object")
    return value


def _research_authorized(value: object) -> bool:
    return isinstance(value, dict) and value.get("approved") is True


def _research_authorization_method(value: object) -> str:
    if not isinstance(value, dict):
        return "unknown"
    method = value.get("method")
    if isinstance(method, str) and method.strip():
        return method.strip()
    return "external-research"


def _research_authorization_receipt_path(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    receipt_path = value.get("receipt_path")
    if isinstance(receipt_path, str) and receipt_path.strip():
        return receipt_path.strip()
    return None


def _load_external_research_receipt(
    receipt_path: str,
    method: str,
) -> tuple[dict[str, object], list[str]]:
    path = Path(receipt_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"unreadable:{receipt_path}:{exc}"]
    return _validate_external_research_receipt_payload(payload, method)


def _validate_external_research_receipt_payload(
    payload: object,
    method: str,
) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {}, ["receipt root must be a JSON object"]
    if payload.get("schema") != "tau.external_research_receipt.v1":
        errors.append("schema must be tau.external_research_receipt.v1")
    receipt_method = payload.get("method")
    if receipt_method != method:
        errors.append(f"method must equal {method}")
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        errors.append("query must be a non-empty string")
    retrieved_at = payload.get("retrieved_at")
    if not isinstance(retrieved_at, str) or not retrieved_at.strip():
        errors.append("retrieved_at must be a non-empty string")
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("summary must be a non-empty string")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sources must be a non-empty list")
    else:
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"sources[{index}] must be an object")
                continue
            title = source.get("title")
            url = source.get("url")
            if not isinstance(title, str) or not title.strip():
                errors.append(f"sources[{index}].title must be a non-empty string")
            if not isinstance(url, str) or not url.strip():
                errors.append(f"sources[{index}].url must be a non-empty string")
    return payload, errors


def _parse_external_research_source(value: str) -> dict[str, str]:
    title, separator, url = value.partition("|")
    if not separator:
        raise RuntimeError("--source must use title|url format")
    title = title.strip()
    url = url.strip()
    if not title:
        raise RuntimeError("--source title must be non-empty")
    if not url:
        raise RuntimeError("--source url must be non-empty")
    return {"title": title, "url": url}


def _validate_subagent_receipt_payload(payload: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["receipt root must be a JSON object"]
    if payload.get("schema") != "tau.subagent_receipt.v1":
        errors.append("schema must be tau.subagent_receipt.v1")
    goal = payload.get("goal")
    context = payload.get("context")
    result = payload.get("result")
    next_route = payload.get("next")
    if not isinstance(goal, dict):
        errors.append("goal must be an object")
    else:
        for key in ("goal_id", "goal_hash"):
            value = goal.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"goal.{key} must be a non-empty string")
        if not isinstance(goal.get("goal_version"), int) or goal.get("goal_version") < 1:
            errors.append("goal.goal_version must be a positive integer")
        if not isinstance(goal.get("immutable_goal_preserved"), bool):
            errors.append("goal.immutable_goal_preserved must be boolean")
    if not isinstance(context, dict):
        errors.append("context must be an object")
    else:
        for key in ("run_id", "subagent", "actor_type"):
            value = context.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"context.{key} must be a non-empty string")
        if context.get("actor_type") not in {
            "human",
            "webgpt",
            "subagent",
            "github-actions",
            "local-cron",
            "tau",
        }:
            errors.append("context.actor_type must be a supported actor type")
    if not isinstance(result, dict):
        errors.append("result must be an object")
    else:
        if result.get("status") not in {
            "PASS",
            "COMPLETED",
            "NEEDS_CHANGES",
            "BLOCKED",
            "INSUFFICIENT_EVIDENCE",
            "REFUSED",
        }:
            errors.append("result.status must be a supported status")
        summary = result.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            errors.append("result.summary must be a non-empty string")
        for key in ("mocked", "live"):
            if not isinstance(result.get(key), bool):
                errors.append(f"result.{key} must be boolean")
    rationale = payload.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        errors.append("rationale must be a non-empty string")
    if not isinstance(payload.get("evidence"), list):
        errors.append("evidence must be a list")
    if not isinstance(next_route, dict):
        errors.append("next must be an object")
    else:
        for key in ("subagent", "reason"):
            value = next_route.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"next.{key} must be a non-empty string")
        if next_route.get("executor") not in {"local", "github-actions", "either", "human"}:
            errors.append("next.executor must be local, github-actions, either, or human")
    stop_condition = payload.get("stop_condition")
    if not isinstance(stop_condition, str) or not stop_condition.strip():
        errors.append("stop_condition must be a non-empty string")
    errors.extend(validate_subagent_code_ticket_closure(payload))
    return errors


def _brave_search_sources(query: str, *, count: str | None) -> list[dict[str, str]]:
    result_count = _parse_positive_int(count or "5", "--count")
    command = [
        "bash",
        "-lc",
        (
            "source ~/.zshrc >/dev/null 2>&1 || true; "
            "/home/graham/workspace/experiments/agent-skills/skills/brave-search/run.sh "
            f"web {json.dumps(query)} --count {result_count} --json"
        ),
    ]
    process = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
        env=dict(os.environ),
        timeout=90,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip()
        raise RuntimeError(f"Brave Search failed with exit code {process.returncode}: {detail}")
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Brave Search returned unreadable JSON: {exc}") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not results:
        raise RuntimeError("Brave Search returned no results")
    sources: list[dict[str, str]] = []
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            continue
        title = result.get("title")
        url = result.get("url")
        if not isinstance(title, str) or not title.strip():
            title = result.get("description")
        if not isinstance(title, str) or not title.strip():
            title = f"Brave result {index + 1}"
        if not isinstance(url, str) or not url.strip():
            continue
        sources.append({"title": title.strip(), "url": url.strip()})
    if not sources:
        raise RuntimeError("Brave Search returned no usable result URLs")
    return sources


def _project_agent_goal_guardian_reconciliation_handoff(
    *,
    github: dict[str, object],
    goal: dict[str, object],
    context: dict[str, object],
    human_goal_change: dict[str, object],
    ticket_source: str | None,
) -> dict[str, object]:
    artifacts = context.get("artifacts") if isinstance(context.get("artifacts"), list) else []
    receipt = _goal_guardian_reconciliation_receipt(
        goal=goal,
        github=github,
        human_goal_change=human_goal_change,
        source_artifacts=artifacts,
        ticket_source=ticket_source,
    )
    artifact_path = _write_goal_guardian_reconciliation_receipt(receipt)
    output_artifacts = list(artifacts)
    if artifact_path is not None:
        output_artifacts.append(str(artifact_path))
    receipt_ref = str(artifact_path) if artifact_path is not None else "embedded receipt"
    return {
        "schema": "tau.agent_handoff.v1",
        "github": github,
        "goal": goal,
        "previous_subagent": "goal-guardian",
        "context": {
            "summary": "Goal guardian reconciled a trusted human goal-change request.",
            "artifacts": output_artifacts,
            "goal_guardian_reconciliation": receipt,
        },
        "result": {
            "status": "REQUIRES_HUMAN_GOAL_VERSION",
            "summary": "Human goal-change request requires a human-authored goal version.",
            "evidence": [
                f"goal-guardian reconciliation receipt: {receipt_ref}",
            ],
        },
        "rationale": (
            "Only a human may create or accept a new immutable goal version. "
            "Goal guardian recorded the proposed new goal and stopped before "
            "routing to a non-human agent."
        ),
        "next_agent": {
            "name": "human",
            "executor": "human",
            "reason": "Human must create or reject the next immutable goal version.",
        },
        "required_evidence": [
            "Human posts a schema-valid goal decision or new goal capsule.",
            "Goal guardian reconciliation receipt remains attached as evidence.",
        ],
        "stop_condition": "Human accepts, rejects, or rewrites the proposed goal change.",
    }


def _goal_guardian_reconciliation_receipt(
    *,
    goal: dict[str, object],
    github: dict[str, object],
    human_goal_change: dict[str, object],
    source_artifacts: list[object],
    ticket_source: str | None,
) -> dict[str, object]:
    new_goal = human_goal_change.get("new_goal")
    if not isinstance(new_goal, dict):
        new_goal = {}
    open_ticket_reconciliation = _goal_guardian_open_ticket_reconciliation(
        goal=goal,
        ticket_source=ticket_source,
    )
    return {
        "schema": "tau.goal_guardian_reconciliation_receipt.v1",
        "ok": True,
        "dry_run": True,
        "goal": goal,
        "github": github,
        "decision": "REQUIRES_HUMAN_GOAL_VERSION",
        "new_goal": new_goal,
        "source_schema": human_goal_change.get("schema"),
        "source": human_goal_change.get("source"),
        "source_artifacts": [item for item in source_artifacts if isinstance(item, str)],
        "open_ticket_reconciliation": open_ticket_reconciliation,
        "next_agent": "human",
        "errors": [],
    }


def _goal_guardian_open_ticket_reconciliation(
    *,
    goal: dict[str, object],
    ticket_source: str | None,
) -> dict[str, object]:
    source_path = ticket_source or environ.get("TAU_GOAL_GUARDIAN_TICKET_SOURCE")
    if not isinstance(source_path, str) or not source_path.strip():
        return {
            "status": "not_started",
            "reason": "No authoritative open-ticket source was provided to this bounded adapter.",
            "source": None,
            "counts": {"keep": 0, "close": 0, "migrate": 0, "regenerate": 0},
            "keep": [],
            "close": [],
            "migrate": [],
            "regenerate": [],
        }

    resolved = Path(source_path).expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"goal-guardian ticket source unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("goal-guardian ticket source root must be an object")
    if payload.get("schema") != "tau.goal_guardian_ticket_source.v1":
        raise RuntimeError(
            "goal-guardian ticket source schema must be tau.goal_guardian_ticket_source.v1"
        )
    tickets = payload.get("tickets")
    if not isinstance(tickets, list):
        raise RuntimeError("goal-guardian ticket source tickets must be a list")

    buckets: dict[str, list[dict[str, object]]] = {
        "keep": [],
        "close": [],
        "migrate": [],
        "regenerate": [],
    }
    current_goal_hash = goal.get("goal_hash")
    for index, ticket in enumerate(tickets):
        if not isinstance(ticket, dict):
            raise RuntimeError(f"goal-guardian ticket source tickets[{index}] must be an object")
        bucket = _classify_goal_guardian_ticket(ticket, current_goal_hash=current_goal_hash)
        buckets[bucket].append(_goal_guardian_ticket_ref(ticket))

    return {
        "status": "classified",
        "reason": "Classified tickets from authoritative local ticket source.",
        "source": str(resolved),
        "source_schema": payload.get("schema"),
        "counts": {name: len(items) for name, items in buckets.items()},
        **buckets,
    }


def _classify_goal_guardian_ticket(
    ticket: dict[str, object],
    *,
    current_goal_hash: object,
) -> str:
    explicit = ticket.get("reconciliation")
    if isinstance(explicit, str) and explicit in {"keep", "close", "migrate", "regenerate"}:
        return explicit
    status = ticket.get("status")
    if isinstance(status, str) and status.lower() not in {"open", "opened"}:
        return "close"
    ticket_goal_hash = ticket.get("goal_hash")
    if (
        isinstance(ticket_goal_hash, str)
        and ticket_goal_hash
        and ticket_goal_hash != current_goal_hash
    ):
        return "regenerate"
    labels = ticket.get("labels")
    label_set = (
        {item for item in labels if isinstance(item, str)} if isinstance(labels, list) else set()
    )
    if "goal-change" in label_set or "ticket:goal" in label_set:
        return "migrate"
    if "next:human" in label_set or "agent-blocked" in label_set:
        return "keep"
    return "migrate"


def _goal_guardian_ticket_ref(ticket: dict[str, object]) -> dict[str, object]:
    ref: dict[str, object] = {}
    for field in ("id", "kind", "number", "title", "url", "goal_hash", "reconciliation"):
        value = ticket.get(field)
        if isinstance(value, (str, int, bool)) or value is None:
            ref[field] = value
    labels = ticket.get("labels")
    if isinstance(labels, list):
        ref["labels"] = [item for item in labels if isinstance(item, str)]
    return ref


def _write_goal_guardian_reconciliation_receipt(
    receipt: dict[str, object],
) -> Path | None:
    artifact_root = environ.get("TAU_HANDOFF_COMMAND_ARTIFACT_DIR")
    if not isinstance(artifact_root, str) or not artifact_root.strip():
        return None
    path = Path(artifact_root).expanduser().resolve() / "goal-guardian-reconciliation-receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _load_command_dispatch_spec(path: Path) -> dict[str, object]:
    payload = _load_json_object(path, label="handoff command spec")
    try:
        return validate_command_dispatch_spec(payload)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def _load_handoff_response_dir(responses_dir: Path) -> dict[str, dict[str, object]]:
    resolved = responses_dir.expanduser().resolve()
    if not resolved.exists():
        raise RuntimeError(f"handoff response directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise RuntimeError(f"handoff response path is not a directory: {resolved}")
    responses: dict[str, dict[str, object]] = {}
    for path in sorted(resolved.glob("*.json")):
        payload = _load_json_object(path, label="handoff response")
        previous_subagent = payload.get("previous_subagent")
        if not isinstance(previous_subagent, str) or not previous_subagent.strip():
            raise RuntimeError(f"handoff response missing previous_subagent: {path}")
        responses[path.stem] = payload
    return responses


def _load_json_object(path: Path, *, label: str) -> dict[str, object]:
    resolved = path.expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be a JSON object: {resolved}")
    return payload


def _write_json_receipt(path: Path, payload: dict[str, object]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_json_object(path: Path, payload: object) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def loop2_sanity_command(
    *,
    root_dir: Path,
    repo: Path,
    loop2_src: Path | None = None,
) -> bool:
    """Create and check one fixture Tau Loop2 receipt run."""

    payload = run_loop2_sanity(root_dir=root_dir, repo=repo, loop2_src=loop2_src)
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return bool(payload.get("ok"))


def tui_proof_command(
    *,
    output_dir: Path,
    prompt: str,
    run_id: str,
    route: str,
    next_agent: str,
) -> bool:
    """Render a fixture-backed Textual TUI Memory-stage proof."""

    payload = render_textual_tui_memory_stage_proof(
        output_dir=output_dir,
        prompt=prompt,
        run_id=run_id,
        route=route,
        next_agent=next_agent,
    )
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return bool(payload.get("ok"))


def browser_cdp_proof_command(
    *,
    output_dir: Path,
    run_id: str,
    surf_bin: Path | None,
    keep_tab: bool,
) -> bool:
    """Render a local Tau proof page through Surf and write screenshot proof."""

    payload = write_browser_cdp_proof(
        output_dir=output_dir,
        run_id=run_id,
        surf_bin=surf_bin,
        keep_tab=keep_tab,
    )
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    return bool(payload.get("ok"))


async def run_openai_print_mode(
    prompt: str,
    model: str | None,
    cwd: Path,
    output: PrintOutputMode = PrintOutputMode.text,
    provider_name: str | None = None,
    loop_receipt: LoopReceiptConfig | None = None,
    thinking_level: ThinkingLevel | None = None,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    session_manager: SessionManager | None = None,
    session_name: str | None = None,
    no_session: bool = False,
    exact_session_id: str | None = None,
    session_dir: Path | None = None,
    default_project_trust: DefaultProjectTrust | None = None,
    no_context_files: bool = False,
    tool_allowlist: tuple[str, ...] | None = None,
    tool_denylist: tuple[str, ...] = (),
    no_tools: bool = False,
    no_builtin_tools: bool = False,
    no_skills: bool = False,
    no_prompt_templates: bool = False,
    no_themes: bool = False,
    no_extensions: bool = False,
    skill_paths: tuple[Path, ...] = (),
    prompt_template_paths: tuple[Path, ...] = (),
    theme_paths: tuple[Path, ...] = (),
    extension_paths: tuple[Path, ...] = (),
    extension_flag_values: Mapping[str, bool | str] | None = None,
) -> bool:
    """Run print mode with the OpenAI-compatible provider configured from the environment."""
    settings = load_provider_settings()
    selection = resolve_provider_selection(settings, provider_name=provider_name, model=model)
    startup_thinking_level = thinking_level or DEFAULT_THINKING_LEVEL
    provider = create_model_provider(
        selection.provider,
        model=selection.model,
        thinking_level=startup_thinking_level,
    )
    manager = None if no_session else session_manager or _session_manager_from_dir(session_dir)
    record: CodingSessionRecord | None = None
    if manager is not None:
        try:
            record = manager.create_session(
                cwd=cwd,
                model=selection.model,
                title=session_name,
                session_id=exact_session_id,
            )
        except TypeError:
            record = manager.create_session(cwd=cwd, model=selection.model)
    try:
        return await run_print_mode(
            prompt=prompt,
            model=selection.model,
            cwd=record.cwd if record is not None else cwd,
            provider=provider,
            output=output,
            storage=jsonl_session_storage(record.path) if record is not None else None,
            session_id=record.id if record is not None else None,
            session_manager=manager,
            provider_name=selection.provider.name,
            provider_settings=settings,
            runtime_provider_config=selection.provider,
            loop_receipt=loop_receipt,
            thinking_level=startup_thinking_level,
            custom_system_prompt=custom_system_prompt,
            append_system_prompt=append_system_prompt,
            default_project_trust=default_project_trust,
            discover_context_files=not no_context_files,
            tool_allowlist=tool_allowlist,
            tool_denylist=tool_denylist,
            no_tools=no_tools,
            no_builtin_tools=no_builtin_tools,
            discover_skills=not no_skills,
            discover_prompt_templates=not no_prompt_templates,
            discover_themes=not no_themes,
            discover_extensions=not no_extensions,
            skill_paths=skill_paths,
            prompt_template_paths=prompt_template_paths,
            theme_paths=theme_paths,
            extension_paths=extension_paths,
            extension_flag_values=extension_flag_values,
        )
    finally:
        await provider.aclose()


async def run_print_mode(
    *,
    prompt: str,
    model: str,
    cwd: Path,
    provider: ModelProvider,
    output: PrintOutputMode = PrintOutputMode.text,
    resource_paths: TauResourcePaths | None = None,
    storage: SessionStorage | None = None,
    session_id: str | None = None,
    session_manager: SessionManager | None = None,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    provider_settings: ProviderSettings | None = None,
    runtime_provider_config: ProviderConfig | None = None,
    loop_receipt: LoopReceiptConfig | None = None,
    thinking_level: ThinkingLevel = DEFAULT_THINKING_LEVEL,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    default_project_trust: DefaultProjectTrust | None = None,
    discover_context_files: bool = True,
    tool_allowlist: tuple[str, ...] | None = None,
    tool_denylist: tuple[str, ...] = (),
    no_tools: bool = False,
    no_builtin_tools: bool = False,
    discover_skills: bool = True,
    discover_prompt_templates: bool = True,
    discover_themes: bool = True,
    discover_extensions: bool = True,
    skill_paths: tuple[Path, ...] = (),
    prompt_template_paths: tuple[Path, ...] = (),
    theme_paths: tuple[Path, ...] = (),
    extension_paths: tuple[Path, ...] = (),
    extension_flag_values: Mapping[str, bool | str] | None = None,
) -> bool:
    """Run one non-interactive prompt and print streamed events.

    Returns False when the agent emits a non-recoverable error so CLI callers
    can fail non-interactive runs while still rendering the error message.
    """
    tui_settings = load_tui_settings(_tui_settings_paths(resource_paths))
    project_tui_settings = load_project_tui_settings(cwd, _tui_settings_paths(resource_paths))
    effective_default_project_trust = default_project_trust or tui_settings.default_project_trust
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model=model,
            cwd=cwd,
            storage=storage or _MemorySessionStorage(),
            resource_paths=resource_paths,
            session_id=session_id,
            session_manager=session_manager,
            provider_name=provider_name,
            provider_settings=provider_settings,
            runtime_provider_config=runtime_provider_config,
            thinking_level=thinking_level,
            custom_system_prompt=custom_system_prompt,
            append_system_prompt=append_system_prompt,
            default_project_trust=effective_default_project_trust,
            shell_path=tui_settings.shell_path,
            shell_command_prefix=tui_settings.shell_command_prefix,
            auto_resize_images=tui_settings.auto_resize_images,
            disabled_resource_paths=_effective_tui_disabled_resource_paths(
                tui_settings.disabled_resource_paths,
                project_tui_settings.disabled_resource_paths,
            ),
            loop_receipt=loop_receipt,
            discover_context_files=discover_context_files,
            tool_allowlist=tool_allowlist,
            tool_denylist=tool_denylist,
            no_tools=no_tools,
            no_builtin_tools=no_builtin_tools,
            discover_skills=discover_skills,
            discover_prompt_templates=discover_prompt_templates,
            discover_themes=discover_themes,
            discover_extensions=discover_extensions,
            skill_paths=skill_paths,
            prompt_template_paths=prompt_template_paths,
            theme_paths=theme_paths,
            extension_paths=extension_paths,
            extension_flag_values=extension_flag_values or {},
        )
    )
    renderer = create_event_renderer(output)
    try:
        terminal_command = parse_terminal_command(prompt)
        if terminal_command is not None:
            result = await session.run_terminal_command(
                terminal_command.command,
                add_to_context=terminal_command.add_to_context,
            )
            typer.echo(_format_terminal_command_result(result))
            return result.ok
        async for event in session.prompt(prompt):
            renderer.render(event)
        return renderer.finish()
    finally:
        await session.aclose()


class _MemorySessionStorage:
    """Append-only in-memory storage for direct print-mode tests."""

    def __init__(self) -> None:
        self.entries: list[SessionEntry] = []

    async def append(self, entry: SessionEntry) -> None:
        self.entries.append(entry)

    async def read_all(self) -> list[SessionEntry]:
        return list(self.entries)


def _tui_settings_paths(resource_paths: TauResourcePaths | None) -> TauPaths | None:
    if resource_paths is None:
        return None
    if resource_paths.paths is not None:
        return resource_paths.paths
    return TauPaths(
        home=resource_paths.root,
        agents_home=resource_paths.agents_root or Path.home() / ".agents",
    )


def _effective_tui_disabled_resource_paths(
    user_paths: Sequence[str],
    project_paths: Sequence[str],
) -> tuple[Path, ...]:
    paths = [*(Path(path) for path in user_paths), *(Path(path) for path in project_paths)]
    return tuple(dict.fromkeys(path.expanduser().resolve(strict=False) for path in paths))


def _format_terminal_command_result(result: TerminalCommandResult) -> str:
    context_status = "added to context" if result.added_to_context else "not added to context"
    return f"$ {result.command}\n[{context_status}]\n{result.output}"


def _loop_receipt_config_from_cli(
    *,
    root: Path | None,
    node_id: str,
    allowed_globs: list[str] | None,
    required_changed_globs: list[str] | None,
    checks: list[str] | None,
    provider_name: str | None = None,
) -> LoopReceiptConfig | None:
    if root is None:
        if checks:
            raise RuntimeError("--loop2-check requires --loop2-receipt-root")
        if allowed_globs:
            raise RuntimeError("--loop2-allowed-glob requires --loop2-receipt-root")
        if required_changed_globs:
            raise RuntimeError("--loop2-required-changed-glob requires --loop2-receipt-root")
        return None
    selected_checks = tuple(checks or ())
    if not selected_checks:
        raise RuntimeError("--loop2-receipt-root requires at least one --loop2-check")
    mocked = provider_name in {None, "fake"}
    return LoopReceiptConfig(
        root_dir=root,
        node_id=node_id,
        allowed_globs=tuple(allowed_globs or ("**/*",)),
        required_changed_globs=tuple(required_changed_globs or ()),
        checks=selected_checks,
        mocked=mocked,
        live=not mocked,
    )
