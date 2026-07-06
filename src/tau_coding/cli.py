"""Command-line entry point for Tau."""

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import UTC, datetime
from os import environ
from pathlib import Path
from shutil import which
from typing import Annotated, Any

import anyio
import httpx
import typer

from tau_agent import AssistantMessage
from tau_agent.session import JsonlSessionStorage, SessionEntry, SessionStorage
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
from tau_coding.approval_gate import evaluate_approval_gate
from tau_coding.browser_cdp_proof import (
    DEFAULT_BROWSER_PROOF_RUN_ID,
    DEFAULT_SURF_WRAPPER,
    write_browser_cdp_proof,
)
from tau_coding.code_patch import apply_code_patch_receipt
from tau_coding.coding_worker_adapters import (
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
from tau_coding.dag_signals import write_dag_signal_receipt
from tau_coding.dag_stress_poc import (
    inspect_dag_stress_campaign,
    inspect_dag_stress_run,
    run_dag_stress_campaign,
    run_dag_stress_poc,
)
from tau_coding.debug_session_receipt import write_debug_session_receipt
from tau_coding.docker_sandbox import write_docker_sandbox_receipt
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
    write_evidence_case_acquisition_receipt,
    write_memory_intent_acquisition_receipt,
)
from tau_coding.orchestration_evidence import build_orchestration_evidence
from tau_coding.orchestration_redteam import run_orchestration_redteam
from tau_coding.orchestration_reliability import write_orchestration_reliability_receipt
from tau_coding.package_validate import write_compliance_package_validation_receipt
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
from tau_coding.proof_index import build_proof_index
from tau_coding.provenance import (
    build_actor_manifest,
    build_environment_manifest,
    parse_actor_spec,
)
from tau_coding.provider_config import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER_NAME,
    CredentialReader,
    OpenAICompatibleProviderConfig,
    ProviderConfig,
    ProviderSettings,
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
from tau_coding.research_source_receipt import write_research_source_receipt
from tau_coding.resources import TauResourcePaths
from tau_coding.review_findings import write_review_findings_receipt
from tau_coding.run_report import write_run_report
from tau_coding.run_status import build_dag_viewer_link, build_run_status
from tau_coding.sandbox_run import run_sandboxed_command
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
from tau_coding.session_manager import CodingSessionRecord, SessionManager
from tau_coding.thinking import DEFAULT_THINKING_LEVEL
from tau_coding.traycer.cli import parse_traycer_validate_cli_args, traycer_validate_command
from tau_coding.tui import run_tui_app
from tau_coding.tui.proof import (
    DEFAULT_TUI_PROOF_PROMPT,
    DEFAULT_TUI_PROOF_RUN_ID,
    render_textual_tui_memory_stage_proof,
)
from tau_coding.visible_dag_poc import inspect_visible_dag_run, run_visible_dag_poc
from tau_coding.zero_trust_redteam import run_zero_trust_redteam

app = typer.Typer(
    name="tau",
    help="Tau coding-agent harness.",
    add_completion=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)


def doctor_command(*, repo_root: Path | None = None) -> dict[str, object]:
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
        "status": "PASS" if ok else "BLOCKED",
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
            ],
            "does_not_prove": [
                "Herdr pane readiness.",
                "Live provider/model semantic quality.",
                "Provider DAG execution.",
                "GitHub live mutation.",
                "Browser/CDP UI proof; run tau browser-cdp-proof for screenshot artifacts.",
                "Full hardening roadmap completion.",
            ],
        },
    }


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
    prompt_option: Annotated[
        str | None,
        typer.Option("--prompt", "-p", help="Prompt to run in non-interactive print mode."),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Configured provider name to use."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Model name to request from the provider."),
    ] = None,
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
    output: Annotated[
        PrintOutputMode,
        typer.Option("--output", "-o", help="Output mode for print mode."),
    ] = PrintOutputMode.text,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Resume a session id in TUI mode."),
    ] = None,
    new_session: Annotated[
        bool,
        typer.Option("--new-session", help="Create a new session in TUI mode (default)."),
    ] = False,
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
    version: Annotated[
        bool,
        typer.Option("--version", help="Show Tau's version and exit."),
    ] = False,
) -> None:
    """Run the Tau CLI."""
    if version:
        typer.echo(f"tau {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is not None:
        return

    positional_args = prompt_args or []
    command = positional_args[0] if positional_args else None
    initial_prompt = " ".join(positional_args) if positional_args else None

    if prompt_option is None and command == "sessions" and len(positional_args) == 1:
        render_session_list(SessionManager().list_sessions())
        raise typer.Exit()

    if prompt_option is None and command == "export":
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
            )
        except (RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"Exported session to {exported_path}")
        raise typer.Exit()

    if prompt_option is None and command == "providers" and len(positional_args) == 1:
        providers_command()
        raise typer.Exit()

    if prompt_option is None and command == "doctor" and len(positional_args) == 1:
        payload = doctor_command(repo_root=Path(__file__).resolve().parents[2])
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if not payload.get("ok"):
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "init":
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

    if prompt_option is None and command == "zero-trust-doctor":
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

    if prompt_option is None and command == "setup" and len(positional_args) == 1:
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

    if prompt_option is None and command == "setup-chutes" and len(positional_args) == 1:
        setup_chutes_command(
            model=model,
            timeout_seconds=setup_timeout_seconds,
            max_retries=setup_max_retries,
            max_retry_delay_seconds=setup_max_retry_delay_seconds,
            set_default=setup_default,
        )
        raise typer.Exit()

    if prompt_option is None and command == "traycer":
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

    if prompt_option is None and command == "loop2-validate":
        try:
            run_dir = _parse_loop2_run_dir_cli_args(positional_args[1:], command="loop2-validate")
            ok = validate_loop_receipt_command(run_dir, loop2_src=loop2_src)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "loop2-validate-contract":
        try:
            contract_path = _parse_loop2_contract_cli_args(positional_args[1:])
            ok = validate_loop2_contract_command(contract_path, loop2_src=loop2_src)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "loop2-validate-native":
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

    if prompt_option is None and command == "loop2-run":
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

    if prompt_option is None and command == "loop2-inspect":
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

    if prompt_option is None and command == "loop2-check-monitor":
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

    if prompt_option is None and command == "loop2-emit-peer":
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

    if prompt_option is None and command == "loop2-check-scillm-doctor":
        try:
            receipt_path = _parse_loop2_scillm_doctor_receipt_cli_args(positional_args[1:])
            ok = check_loop2_scillm_doctor_command(receipt_path)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "loop2-backfill-artifacts":
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

    if prompt_option is None and command == "loop2-sanity":
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

    if prompt_option is None and command == "tui-proof":
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

    if prompt_option is None and command == "browser-cdp-proof":
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

    if prompt_option is None and command == "visible-dag-poc":
        try:
            options = _parse_visible_dag_poc_cli_args(positional_args[1:])
            payload = run_visible_dag_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "visible-dag-inspect":
        try:
            run_dir = _parse_visible_dag_inspect_cli_args(positional_args[1:])
            payload = inspect_visible_dag_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "provider-pane-poc":
        try:
            options = _parse_provider_pane_poc_cli_args(positional_args[1:])
            payload = run_provider_pane_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "provider-pane-inspect":
        try:
            run_dir = _parse_provider_pane_inspect_cli_args(positional_args[1:])
            payload = inspect_provider_pane_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "provider-readiness-poc":
        try:
            options = _parse_provider_readiness_poc_cli_args(positional_args[1:])
            payload = run_provider_readiness_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "provider-readiness-inspect":
        try:
            run_dir = _parse_provider_readiness_inspect_cli_args(positional_args[1:])
            payload = inspect_provider_readiness_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "provider-dag-poc":
        try:
            options = _parse_provider_dag_poc_cli_args(positional_args[1:])
            payload = run_provider_dag_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "provider-dag-plan":
        try:
            options = _parse_provider_dag_plan_cli_args(positional_args[1:])
            payload = plan_provider_dag_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "provider-dag-orchestrate":
        try:
            options = _parse_provider_dag_orchestrate_cli_args(positional_args[1:])
            payload = run_provider_dag_orchestrator(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "provider-dag-inspect":
        try:
            run_dir = _parse_provider_dag_inspect_cli_args(positional_args[1:])
            payload = inspect_provider_dag_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "orchestration-evidence":
        try:
            run_dir = _parse_orchestration_evidence_cli_args(positional_args[1:])
            payload = build_orchestration_evidence(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command in {"dag-run", "run"}:
        try:
            payload = _run_dag_cli_command(positional_args[1:], command_name=str(command))
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "dag-signals":
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

    if prompt_option is None and command == "evidence-validate":
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

    if prompt_option is None and command == "dag-expansion-validate":
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

    if prompt_option is None and command == "dag-expansion-policy":
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

    if prompt_option is None and command == "dag-expansion-apply":
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

    if prompt_option is None and command == "dag-branch-locks-validate":
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

    if prompt_option is None and command == "dag-motif-validate":
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

    if prompt_option is None and command == "dag-route-memory-candidates":
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

    if prompt_option is None and command == "dag-route-memory-sync":
        try:
            options = _parse_dag_route_memory_sync_cli_args(positional_args[1:])
            payload = write_dag_route_memory_sync_receipt(
                candidate_receipt_path=Path(str(options["candidate_receipt"])),
                receipt_path=Path(str(options["receipt"])),
                collection=str(options["collection"]),
                memory_url=str(options["memory_url"]),
                apply=bool(options["apply"]),
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

    if prompt_option is None and command == "memory-intent":
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

    if prompt_option is None and command == "evidence-case-create":
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

    if prompt_option is None and command == "dag-inspect":
        try:
            run_dir = _parse_generic_dag_inspect_cli_args(positional_args[1:])
            payload = inspect_generic_dag_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "dag-resume":
        try:
            run_dir = _parse_generic_dag_resume_cli_args(positional_args[1:])
            payload = resume_generic_dag_from_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "generic-provider-dag-node":
        try:
            options = _parse_generic_provider_dag_node_cli_args(positional_args[1:])
            payload = run_generic_provider_dag_node(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("status") != "PASS":
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "dag-stress-poc":
        try:
            options = _parse_dag_stress_poc_cli_args(positional_args[1:])
            payload = run_dag_stress_poc(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "dag-stress-inspect":
        try:
            run_dir = _parse_dag_stress_inspect_cli_args(positional_args[1:])
            payload = inspect_dag_stress_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "dag-stress-campaign":
        try:
            options = _parse_dag_stress_campaign_cli_args(positional_args[1:])
            payload = run_dag_stress_campaign(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "dag-stress-campaign-inspect":
        try:
            run_dir = _parse_dag_stress_campaign_inspect_cli_args(positional_args[1:])
            payload = inspect_dag_stress_campaign(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "media-explainer-smoke":
        try:
            options = _parse_media_explainer_smoke_cli_args(positional_args[1:])
            payload = run_media_explainer_smoke(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "media-explainer-inspect":
        try:
            run_dir = _parse_media_explainer_inspect_cli_args(positional_args[1:])
            payload = inspect_media_explainer_run(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "herdr-cleanup":
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

    if prompt_option is None and command == "approval-gate-check":
        try:
            options = _parse_approval_gate_check_cli_args(positional_args[1:])
            payload = evaluate_approval_gate(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "run-status":
        try:
            run_dir = _parse_run_status_cli_args(positional_args[1:])
            payload = build_run_status(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "dag-viewer-link":
        try:
            run_dir = _parse_dag_viewer_link_cli_args(positional_args[1:])
            payload = build_dag_viewer_link(run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "compliance-package":
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

    if prompt_option is None and command == "actor-manifest":
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

    if prompt_option is None and command == "environment-manifest":
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

    if prompt_option is None and command == "sign-receipt":
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

    if prompt_option is None and command == "proof-index":
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

    if prompt_option is None and command == "verify-signed-receipt":
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

    if prompt_option is None and command == "sandbox-run":
        try:
            options = _parse_sandbox_run_cli_args(positional_args[1:])
            payload = run_sandboxed_command(
                command=[str(item) for item in options["command"]],
                policy_profile_path=Path(str(options["policy_profile"])),
                data_boundary_path=Path(str(options["data_boundary"])),
                receipt_path=Path(str(options["out"])) if options.get("out") is not None else None,
                timeout_seconds=float(options["timeout_seconds"]),
                backend=str(options["backend"]),
                image=_optional_str(options.get("image")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "report":
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

    if prompt_option is None and command == "serve":
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

    if prompt_option is None and command == "dag-fail-closed-registry":
        try:
            output_path = _parse_dag_fail_closed_registry_args(positional_args[1:])
            payload = write_fail_closed_registry_receipt(output_path=output_path)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "course-correction":
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

    if prompt_option is None and command == "code-patch":
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
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if prompt_option is None and command == "review-findings":
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

    if prompt_option is None and command == "lsp-diagnostics":
        try:
            options = _parse_lsp_diagnostics_cli_args(positional_args[1:])
            payload = write_lsp_diagnostics_receipt(
                workspace=Path(str(options["workspace"])),
                output_path=Path(str(options["out"])),
                required=bool(options["required"]),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if prompt_option is None and command == "lsp-symbols":
        try:
            options = _parse_lsp_symbols_cli_args(positional_args[1:])
            payload = write_lsp_symbol_receipt(
                workspace=Path(str(options["workspace"])),
                query=str(options["query"]),
                output_path=Path(str(options["out"])),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if prompt_option is None and command == "lsp-rename-plan":
        try:
            options = _parse_lsp_rename_plan_cli_args(positional_args[1:])
            payload = write_lsp_rename_plan_receipt(
                workspace=Path(str(options["workspace"])),
                symbol=str(options["symbol"]),
                new_name=str(options["new_name"]),
                output_path=Path(str(options["out"])),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if prompt_option is None and command == "commit-plan":
        try:
            options = _parse_commit_plan_cli_args(positional_args[1:])
            payload = write_commit_plan_receipt(
                repo=Path(str(options["repo"])),
                output_path=Path(str(options["out"])),
                apply=bool(options["apply"]),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if prompt_option is None and command == "orchestration-reliability":
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

    if prompt_option is None and command == "omp-worker-validate":
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

    if prompt_option is None and command == "scillm-worker-validate":
        try:
            options = _parse_worker_validate_cli_args(
                positional_args[1:],
                command="scillm-worker-validate",
            )
            payload = write_scillm_worker_receipt(
                work_order_path=Path(str(options["work_order"])),
                result_path=Path(str(options["result"])),
                output_path=Path(str(options["out"])),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if prompt_option is None and command == "omp-worker-launch":
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

    if prompt_option is None and command == "scillm-worker-launch":
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

    if prompt_option is None and command == "debug-session-receipt":
        try:
            options = _parse_debug_session_receipt_cli_args(positional_args[1:])
            payload = write_debug_session_receipt(
                session_path=Path(str(options["session"])),
                output_path=Path(str(options["out"])),
                required=bool(options["required"]),
                zero_trust=bool(options["zero_trust"]),
                policy_profile=_read_optional_json_object(options.get("policy_profile")),
                data_boundary=_read_optional_json_object(options.get("data_boundary")),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(0 if payload.get("ok") is True else 1)

    if prompt_option is None and command == "github-read":
        try:
            options = _parse_github_read_cli_args(positional_args[1:])
            payload = write_github_read_receipt(
                uri=str(options["uri"]),
                output_path=Path(str(options["out"])),
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

    if prompt_option is None and command == "herdr-observation-gate":
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

    if prompt_option is None and command == "project-profile-validate":
        try:
            options = _parse_project_profile_validate_cli_args(positional_args[1:])
            payload = write_project_profile_validation_receipt(
                profile_path=Path(str(options["profile"])),
                output_path=Path(str(options["out"])),
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(1 if payload.get("ok") is not True else 0)

    if prompt_option is None and command == "loop2-serve":
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

    if prompt_option is None and command == "human-goal-change-bridge":
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

    if prompt_option is None and command == "handoff-project":
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

    if prompt_option is None and command == "handoff-github-transport":
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

    if prompt_option is None and command == "github-redact-projection":
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

    if prompt_option is None and command == "github-apply-policy-check":
        try:
            options = _parse_github_apply_policy_check_args(positional_args[1:])
            payload = write_github_apply_policy_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "research-source-receipt":
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

    if prompt_option is None and command == "research-query-gate":
        try:
            options = _parse_research_query_gate_args(positional_args[1:])
            payload = write_research_query_safety_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "itar-access-preflight":
        try:
            options = _parse_itar_access_preflight_args(positional_args[1:])
            payload = write_itar_access_preflight_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "compliance-package-validate":
        try:
            options = _parse_compliance_package_validate_args(positional_args[1:])
            payload = write_compliance_package_validation_receipt(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "zero-trust-redteam":
        try:
            run_dir = _parse_zero_trust_redteam_args(positional_args[1:])
            payload = run_zero_trust_redteam(run_dir=run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "orchestration-redteam":
        try:
            run_dir = _parse_orchestration_redteam_args(positional_args[1:])
            payload = run_orchestration_redteam(run_dir=run_dir)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        if payload.get("ok") is not True:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command in {"docker-sandbox-check", "docker-sandbox-run"}:
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

    if prompt_option is None and command == "generated-ticket-github-create":
        try:
            ticket_path, active_goal_hash, receipt_path, agents_root, apply_github = (
                _parse_generated_ticket_github_create_cli_args(positional_args[1:])
            )
            ok = transport_generated_ticket_to_github_command(
                ticket_path,
                active_goal_hash=active_goal_hash,
                receipt_path=receipt_path,
                agents_root=agents_root,
                apply_github=apply_github,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "handoff-command-loop-github-transport":
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

    if prompt_option is None and command == "goal-guardian-reconciliation-github-transport":
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

    if prompt_option is None and command == "handoff-command-loop-reconciliation-github-transport":
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

    if prompt_option is None and command == "goal-guardian-ticket-source-github-fetch":
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

    if prompt_option is None and command == "handoff-chain-dry-run":
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

    if prompt_option is None and command == "handoff-loop-dry-run":
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

    if prompt_option is None and command == "handoff-dispatch-once":
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

    if prompt_option is None and command == "handoff-dispatch-command":
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

    if prompt_option is None and command == "handoff-dispatch-agent-command":
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

    if prompt_option is None and command == "handoff-command-loop":
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

    if prompt_option is None and command == "self-fix":
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

    if prompt_option is None and command == "scillm-subagent-gate":
        try:
            summary_path = _parse_scillm_subagent_gate_cli_args(positional_args[1:])
            ok = project_agent_scillm_subagent_gate_command(summary_path)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "persona-dream-panel-proof":
        try:
            options = _parse_persona_dream_panel_proof_cli_args(positional_args[1:])
            ok = project_agent_persona_dream_panel_proof_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if not ok:
            raise typer.Exit(1)
        raise typer.Exit()

    if prompt_option is None and command == "handoff-agent-adapter":
        try:
            options = _parse_handoff_agent_adapter_cli_args(positional_args[1:])
            payload = project_agent_handoff_adapter_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if prompt_option is None and command == "handoff-goal-guardian-adapter":
        try:
            options = _parse_handoff_goal_guardian_adapter_cli_args(positional_args[1:])
            payload = project_agent_handoff_goal_guardian_adapter_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if prompt_option is None and command == "handoff-research-auditor-adapter":
        try:
            payload = project_agent_handoff_research_auditor_adapter_command()
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if prompt_option is None and command == "external-research-receipt":
        try:
            options = _parse_external_research_receipt_cli_args(positional_args[1:])
            payload = project_agent_external_research_receipt_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if prompt_option is None and command == "subagent-receipt-from-handoff":
        try:
            options = _parse_subagent_receipt_from_handoff_cli_args(positional_args[1:])
            payload = project_agent_subagent_receipt_from_handoff_command(**options)
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit()

    if prompt_option is None:
        try:
            anyio.run(
                run_openai_tui,
                model,
                cwd or Path.cwd(),
                resume,
                new_session,
                provider,
                auto_compact_threshold,
                initial_prompt,
            )
        except RuntimeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        raise typer.Exit()

    prompt = prompt_option
    if prompt is None:
        raise AssertionError("prompt option should be set outside TUI mode")

    try:
        loop_receipt = _loop_receipt_config_from_cli(
            root=loop2_receipt_root,
            node_id=loop2_node_id,
            allowed_globs=loop2_allowed_globs,
            required_changed_globs=loop2_required_changed_globs,
            checks=loop2_checks,
            provider_name=provider,
        )
        ok = anyio.run(
            run_openai_print_mode,
            prompt,
            model,
            cwd or Path.cwd(),
            output,
            provider,
            loop_receipt,
        )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not ok:
        raise typer.Exit(1)


async def run_openai_tui(
    model: str | None,
    cwd: Path,
    session_id: str | None = None,
    new_session: bool = False,
    provider_name: str | None = None,
    auto_compact_token_threshold: int | None = None,
    initial_prompt: str | None = None,
) -> None:
    """Run the Textual TUI with the default OpenAI-compatible provider."""
    await run_tui_app(
        model=model,
        cwd=cwd,
        session_id=session_id,
        new_session=new_session,
        provider_name=provider_name,
        auto_compact_token_threshold=auto_compact_token_threshold,
        initial_prompt=initial_prompt,
    )


def render_session_list(records: list[CodingSessionRecord]) -> None:
    """Render indexed sessions for the CLI."""
    if not records:
        typer.echo("No sessions found.")
        return

    for record in records:
        title = record.title or "Untitled"
        typer.echo(f"{record.id}\t{title}\t{record.model}\t{record.cwd}")


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
            )
        except RuntimeError as exc:
            return dag_contract_error_payload(
                contract_path=spec_path,
                receipt_dir=options.get("receipt_dir"),
                error=str(exc),
                scheduler=str(options["scheduler"]),
            )
    return run_generic_dag(
        spec_path=spec_path,
        resume=bool(options["resume"]),
    )


def _parse_generic_dag_run_cli_args(
    args: list[str],
    *,
    command_name: str = "dag-run",
) -> dict[str, object]:
    if not args:
        raise RuntimeError(
            f"Usage: tau {command_name} <dag-spec> [--no-resume] "
            "[--receipt-dir <dir>] [--agents-root <dir>] [--command-spec-root <dir>] "
            "[--scheduler <handoff-loop|bounded-ready-queue>]"
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
        raise RuntimeError("Usage: tau init --profile zero-trust [--out <dir>] [--force]")
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
    except (OSError, json.JSONDecodeError, RuntimeError):
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
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--candidate-receipt",
            "--receipt",
            "--collection",
            "--memory-url",
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
            "[--apply --approval-receipt <approval-gate-receipt.json>]"
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
            "[--include-current-workspace]\n"
            "       tau herdr-cleanup gc --run-dir <receipt-dir> "
            "[--apply --approval-receipt <receipt.json>] [--herdr-bin herdr] "
            "[--include-current-workspace]"
        )
    mode = args[0]
    run_dir: Path | None = None
    herdr_bin = "herdr"
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
    return {
        "run_dir": run_dir,
        "mode": mode,
        "apply": apply_gc,
        "herdr_bin": herdr_bin,
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


def _parse_run_status_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau run-status <run-dir>")
    return Path(args[0])


def _parse_dag_viewer_link_cli_args(args: list[str]) -> Path:
    if len(args) != 1:
        raise RuntimeError("Usage: tau dag-viewer-link <run-dir>")
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
        }:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            key = arg.removeprefix("--").replace("-", "_")
            options[key] = args[index]
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
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--workspace", "--out", "--policy-profile", "--data-boundary"}:
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
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--workspace", "--query", "--out", "--policy-profile", "--data-boundary"}:
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


def _parse_commit_plan_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "repo": ".",
        "out": None,
        "apply": False,
        "zero_trust": False,
        "policy_profile": None,
        "data_boundary": None,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--repo", "--out", "--policy-profile", "--data-boundary"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--repo="):
            options["repo"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        elif arg.startswith("--policy-profile="):
            options["policy_profile"] = arg.partition("=")[2]
        elif arg.startswith("--data-boundary="):
            options["data_boundary"] = arg.partition("=")[2]
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
    if not _optional_str(options.get("run_dir")) and not _optional_str(
        options.get("dag_receipt")
    ):
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
    options: dict[str, object] = {"work_order": None, "result": None, "out": None}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--work-order", "--result", "--out"}:
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


def _parse_scillm_worker_launch_cli_args(args: list[str]) -> dict[str, object]:
    options: dict[str, object] = {
        "work_order": None,
        "out": None,
        "scillm_base_url": "http://localhost:4001",
        "caller_skill": "tau",
        "apply": False,
        "auth_token": None,
        "request_timeout_s": 650,
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
        if arg in {"--session", "--out", "--policy-profile", "--data-boundary"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--").replace("-", "_")] = args[index]
        elif arg.startswith("--session="):
            options["session"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
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
    options: dict[str, object] = {"profile": None, "out": None}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--profile", "--out"}:
            index += 1
            if index >= len(args):
                raise RuntimeError(f"{arg} requires a value")
            options[arg.removeprefix("--")] = args[index]
        elif arg.startswith("--profile="):
            options["profile"] = arg.partition("=")[2]
        elif arg.startswith("--out="):
            options["out"] = arg.partition("=")[2]
        else:
            raise RuntimeError(f"unknown project-profile-validate option: {arg}")
        index += 1
    if not _optional_str(options.get("profile")):
        raise RuntimeError("Usage: tau project-profile-validate --profile <json> --out <receipt>")
    if not _optional_str(options.get("out")):
        raise RuntimeError("Usage: tau project-profile-validate --profile <json> --out <receipt>")
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
) -> tuple[Path, str | None, Path | None, Path | None, bool]:
    if not args:
        raise RuntimeError(
            "Usage: tau generated-ticket-github-create <ticket.json> "
            "[--active-goal-hash <hash>] [--agents-root <dir>] "
            "[--receipt <receipt.json>] [--apply]"
        )
    ticket_path = Path(args[0])
    active_goal_hash: str | None = None
    receipt_path: Path | None = None
    agents_root: Path | None = None
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
        elif arg == "--apply":
            apply_github = True
        else:
            raise RuntimeError(f"Unknown generated-ticket-github-create option: {arg}")
        index += 1
    return ticket_path, active_goal_hash, receipt_path, agents_root, apply_github


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
    except (OSError, json.JSONDecodeError):
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
    except (OSError, json.JSONDecodeError):
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
        except (OSError, json.JSONDecodeError):
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
    except (OSError, json.JSONDecodeError):
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

    transport = transport_generated_ticket_to_github(
        repo=repo,
        github_create=validation.github_create,
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
    dispatch = write_agent_handoff_dispatch_receipt(
        start_payload,
        response_payloads,
        receipt_dir.expanduser().resolve(),
        active_goal_hash=active_goal_hash,
        agent_registry_root=agents_root,
    )
    typer.echo(json.dumps(dispatch.as_dict(), indent=2, sort_keys=True))
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
    fetch["ok"] = True
    return payload, fetch


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
    digest = hashlib.sha256(f"{repo}#{issue}\n{issue_text}".encode("utf-8")).hexdigest()
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
    session_manager: SessionManager | None = None,
) -> bool:
    """Run print mode with the OpenAI-compatible provider configured from the environment."""
    settings = load_provider_settings()
    selection = resolve_provider_selection(settings, provider_name=provider_name, model=model)
    provider = create_model_provider(
        selection.provider,
        model=selection.model,
        thinking_level=DEFAULT_THINKING_LEVEL,
    )
    manager = session_manager or SessionManager()
    record = manager.create_session(cwd=cwd, model=selection.model)
    try:
        return await run_print_mode(
            prompt=prompt,
            model=selection.model,
            cwd=record.cwd,
            provider=provider,
            output=output,
            storage=jsonl_session_storage(record.path),
            session_id=record.id,
            session_manager=manager,
            provider_name=selection.provider.name,
            provider_settings=settings,
            runtime_provider_config=selection.provider,
            loop_receipt=loop_receipt,
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
) -> bool:
    """Run one non-interactive prompt and print streamed events.

    Returns False when the agent emits a non-recoverable error so CLI callers
    can fail non-interactive runs while still rendering the error message.
    """
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
            loop_receipt=loop_receipt,
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
