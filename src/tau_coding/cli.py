"""Command-line entry point for Tau."""

import asyncio
import io
import json
import tempfile
from contextlib import redirect_stdout
from os import environ
from pathlib import Path
from typing import Annotated

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
from tau_coding.credentials import FileCredentialStore
from tau_coding.generated_ticket import (
    project_agent_handoff,
    write_agent_handoff_chain_receipt,
    write_agent_handoff_loop_receipt,
    write_agent_handoff_projection_receipt,
)
from tau_coding.github_handoff import transport_handoff_projection_to_github
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
from tau_coding.provider_runtime import create_model_provider
from tau_coding.rendering import PrintOutputMode, create_event_renderer
from tau_coding.resources import TauResourcePaths
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
from tau_coding.tui import run_tui_app

app = typer.Typer(
    name="tau",
    help="Tau coding-agent harness.",
    add_completion=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)


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
            handoff_path, active_goal_hash, receipt_path, agents_root, apply_github = (
                _parse_handoff_github_transport_cli_args(positional_args[1:])
            )
            ok = transport_agent_handoff_to_github_command(
                handoff_path,
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
) -> tuple[Path, str | None, Path | None, Path | None, bool]:
    if not args:
        raise RuntimeError(
            "Usage: tau handoff-github-transport <handoff.json> "
            "[--active-goal-hash <hash>] [--agents-root <dir>] "
            "[--receipt <receipt.json>] [--apply]"
        )
    handoff_path = Path(args[0])
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
            raise RuntimeError(f"Unknown handoff-github-transport option: {arg}")
        index += 1
    return handoff_path, active_goal_hash, receipt_path, agents_root, apply_github


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
        payload["errors"] = [
            f"Scillm proxy auth preflight failed with HTTP {response.status_code}"
        ]
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
    return (
        "__pycache__" in parts
        or ".pytest_cache" in parts
        or path.endswith((".pyc", ".pyo"))
    )


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
        f"native Loop2 validation failed: {error}"
        for error in native_validation["errors"]
    ]
    ok = (
        result.get("status") == "PASS"
        and not artifact_errors
        and native_validation["ok"] is True
    )
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
            "dry_run": not apply_github,
            "applied": False,
            "target": projection.target,
            "commands": [],
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

    transport = transport_handoff_projection_to_github(
        projection.as_dict(),
        apply=apply_github,
        receipt_path=receipt_path,
    )
    typer.echo(json.dumps(transport.as_dict(), indent=2, sort_keys=True))
    return transport.ok


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
