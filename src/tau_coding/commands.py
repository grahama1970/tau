"""Slash command registry for Tau coding sessions."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from tau_agent.messages import AgentMessage, AssistantMessage, ToolResultMessage, UserMessage
from tau_agent.tools import AgentTool
from tau_coding.approval_gate import (
    ALLOWED_ACTIONS,
    APPROVAL_GATE_RECEIPT_SCHEMA,
    APPROVAL_PACKET_SCHEMA,
)
from tau_coding.credentials import credentials_path
from tau_coding.paths import TauPaths
from tau_coding.permission_receipts import (
    ALLOWED_PERMISSION_REPLIES,
    PERMISSION_REPLY_RECEIPT_SCHEMA,
    PERMISSION_REQUEST_RECEIPT_SCHEMA,
)
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.provider_catalog import BUILTIN_PROVIDER_CATALOG, builtin_provider_entry
from tau_coding.provider_config import provider_settings_path
from tau_coding.reload import CodingReloadSummary, ReloadCategorySummary
from tau_coding.resources import ResourceDiagnostic, TauResourcePaths
from tau_coding.session_manager import CodingSessionRecord, SessionManager
from tau_coding.skills import Skill
from tau_coding.system_prompt import ProjectContextFile
from tau_coding.thinking import normalize_thinking_level
from tau_coding.trust import ProjectTrustStore
from tau_coding.workflows.catalog import get_workflow, list_workflows

BUILTIN_TUI_THEME_NAMES = ("tau-dark", "tau-light", "high-contrast")
SCILLM_DEFAULT_BASE_URL = "http://localhost:4001"
SCILLM_AUTH_ENV_NAMES = ("SCILLM_API_KEY", "SCILLM_PROXY_KEY", "LITELLM_MASTER_KEY")


class CommandSession(Protocol):
    """Session attributes available to slash-command handlers."""

    @property
    def cwd(self) -> Path: ...

    @property
    def model(self) -> str: ...

    @property
    def provider_name(self) -> str: ...

    @property
    def available_models(self) -> Sequence[str]: ...

    @property
    def available_providers(self) -> Sequence[str]: ...

    @property
    def tools(self) -> Sequence[AgentTool]: ...

    @property
    def skills(self) -> Sequence[Skill]: ...

    @property
    def prompt_templates(self) -> Sequence[PromptTemplate]: ...

    @property
    def context_files(self) -> Sequence[ProjectContextFile]: ...

    @property
    def context_token_estimate(self) -> int: ...

    @property
    def auto_compact_token_threshold(self) -> int | None: ...

    @property
    def context_window_tokens(self) -> int: ...

    @property
    def thinking_level(self) -> str: ...

    @property
    def available_thinking_levels(self) -> Sequence[str]: ...

    @property
    def resource_diagnostics(self) -> Sequence[ResourceDiagnostic]: ...

    @property
    def system_prompt(self) -> str: ...

    @property
    def session_id(self) -> str | None: ...

    @property
    def session_title(self) -> str | None: ...

    @property
    def session_manager(self) -> SessionManager | None: ...

    @property
    def session_path(self) -> Path | None: ...

    @property
    def messages(self) -> Sequence[AgentMessage]: ...

    def set_model(self, model: str) -> None: ...

    def reload(self) -> CodingReloadSummary: ...

    def reload_provider_settings(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of handling a coding-session slash command."""

    handled: bool
    exit_requested: bool = False
    clear_requested: bool = False
    new_session_requested: bool = False
    clone_session_requested: bool = False
    compact_summary: str | None = None
    copy_last_message_requested: bool = False
    export_requested: bool = False
    export_destination: Path | None = None
    export_format: str | None = None
    import_requested: bool = False
    import_path: Path | None = None
    share_requested: bool = False
    resume_session_id: str | None = None
    resume_picker_requested: bool = False
    prompts_picker_requested: bool = False
    tree_picker_requested: bool = False
    fork_picker_requested: bool = False
    login_picker_requested: bool = False
    login_picker_query: str | None = None
    login_provider: str | None = None
    logout_picker_requested: bool = False
    logout_provider: str | None = None
    model_picker_requested: bool = False
    model_picker_query: str | None = None
    scoped_models_picker_requested: bool = False
    settings_picker_requested: bool = False
    images_picker_requested: bool = False
    trust_picker_requested: bool = False
    theme_picker_requested: bool = False
    workflow_picker_requested: bool = False
    tools_picker_requested: bool = False
    skills_picker_requested: bool = False
    thinking_level: str | None = None
    show_images: bool | None = None
    theme: str | None = None
    user_message: str | None = None
    user_message_delivery: Literal["steer", "follow_up"] = "steer"
    message: str | None = None


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Runtime context passed to slash-command handlers."""

    session: CommandSession
    registry: CommandRegistry
    text: str
    name: str
    args: str


CommandHandler = Callable[[CommandContext], CommandResult]


@dataclass(frozen=True, slots=True)
class SlashCommand:
    """A registered slash command and its user-facing metadata."""

    name: str
    description: str
    usage: str
    handler: CommandHandler
    aliases: tuple[str, ...] = ()
    search_terms: tuple[str, ...] = ()
    argument_hint: str | None = None
    argument_completions: tuple[CommandArgumentCompletion, ...] = ()
    hidden: bool = False
    source: str | None = None


@dataclass(frozen=True, slots=True)
class CommandArgumentCompletion:
    """A static argument completion for a slash command."""

    value: str
    description: str | None = None


class CommandRegistry:
    """Parse, register, list, and execute slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}
        self._aliases: dict[str, str] = {}

    def register(self, command: SlashCommand) -> None:
        """Register a slash command and its aliases."""
        name = _normalize_name(command.name)
        if name in self._commands:
            raise ValueError(f"Duplicate slash command: /{name}")
        self._commands[name] = command
        for alias in command.aliases:
            normalized_alias = _normalize_name(alias)
            if normalized_alias in self._commands or normalized_alias in self._aliases:
                raise ValueError(f"Duplicate slash command alias: /{normalized_alias}")
            self._aliases[normalized_alias] = name

    def get(self, name: str) -> SlashCommand | None:
        """Return a command by name or alias."""
        normalized = _normalize_name(name)
        command_name = self._aliases.get(normalized, normalized)
        return self._commands.get(command_name)

    def list_commands(self, *, include_hidden: bool = False) -> tuple[SlashCommand, ...]:
        """Return registered commands sorted by name."""
        return tuple(
            self._commands[name]
            for name in sorted(self._commands)
            if include_hidden or not self._commands[name].hidden
        )

    def copy(self) -> CommandRegistry:
        """Return an independent copy of this command registry."""
        copied = CommandRegistry()
        copied._commands = dict(self._commands)
        copied._aliases = dict(self._aliases)
        return copied

    def execute(self, session: CommandSession, text: str) -> CommandResult:
        """Execute a slash command, or return unhandled for ordinary prompts."""
        stripped = text.strip()
        if not stripped.startswith("/"):
            return CommandResult(handled=False)

        if stripped.startswith("/skill:"):
            return CommandResult(handled=False)

        name, args = _parse_command(stripped)
        if not name:
            return CommandResult(handled=False)

        command = self.get(name)
        if command is None and name == "scoped" and args.lower() == "models":
            command = self.get("scoped-models")
            name = "scoped-models"
            args = ""
        if command is None:
            return CommandResult(handled=False)

        return command.handler(
            CommandContext(session=session, registry=self, text=stripped, name=name, args=args)
        )


def create_default_command_registry() -> CommandRegistry:
    """Create Tau's built-in slash command registry."""
    registry = CommandRegistry()
    registry.register(
        SlashCommand(
            name="quit",
            usage="/quit",
            description="Exit the current session.",
            handler=_exit_command,
        )
    )
    registry.register(
        SlashCommand(
            name="new",
            usage="/new",
            description="Start a new session.",
            handler=_new_command,
            search_terms=("clear", "reset"),
        )
    )
    registry.register(
        SlashCommand(
            name="compact",
            usage="/compact [instructions]",
            description="Summarize and compact active context.",
            handler=_compact_command,
        )
    )
    registry.register(
        SlashCommand(
            name="changelog",
            usage="/changelog",
            description="Show local changelog entries.",
            handler=_changelog_command,
            search_terms=("release", "version", "news"),
        )
    )
    registry.register(
        SlashCommand(
            name="config",
            usage="/config",
            description="Show Tau's editable config and resource locations.",
            handler=_config_command,
            search_terms=("settings", "resources", "packages", "paths"),
        )
    )
    registry.register(
        SlashCommand(
            name="clone",
            usage="/clone",
            description="Duplicate the current session at the current position.",
            handler=_clone_command,
            search_terms=("duplicate", "fork", "branch"),
        )
    )
    registry.register(
        SlashCommand(
            name="copy",
            usage="/copy",
            description="Copy the last agent message to the clipboard.",
            handler=_copy_command,
            search_terms=("clipboard", "assistant", "message"),
        )
    )
    registry.register(
        SlashCommand(
            name="images",
            usage="/images [on|off]",
            description="Choose whether tool images render inline in the TUI.",
            handler=_images_command,
            search_terms=("show", "hide", "inline", "terminal"),
        )
    )
    registry.register(
        SlashCommand(
            name="debug",
            usage="/debug",
            description="Write an interactive TUI diagnostic log.",
            handler=_debug_command,
            search_terms=("diagnostics", "runtime", "layout", "screen"),
        )
    )
    registry.register(
        SlashCommand(
            name="arminsayshi",
            usage="/arminsayshi",
            description="Render Pi's Armin easter egg.",
            handler=_armin_says_hi_command,
            hidden=True,
        )
    )
    registry.register(
        SlashCommand(
            name="dementedelves",
            usage="/dementedelves",
            description="Render Pi's Earendil announcement easter egg.",
            handler=_demented_delves_command,
            hidden=True,
        )
    )
    registry.register(
        SlashCommand(
            name="export",
            usage="/export [--format html|jsonl] [destination]",
            description="Export the current session.",
            handler=_export_command,
        )
    )
    registry.register(
        SlashCommand(
            name="import",
            usage="/import <path.jsonl>",
            description="Import and resume a session from a JSONL file.",
            handler=_import_command,
            search_terms=("jsonl", "restore"),
        )
    )
    registry.register(
        SlashCommand(
            name="prompts",
            usage="/prompts",
            description="Choose a loaded prompt template.",
            handler=_prompts_command,
            search_terms=("templates", "picker"),
        )
    )
    registry.register(
        SlashCommand(
            name="permissions",
            usage="/permissions",
            description="Show Tau permission and approval receipt commands.",
            handler=_permissions_command,
            aliases=("approvals",),
            search_terms=("approval", "approve", "reject", "gate", "human"),
        )
    )
    registry.register(
        SlashCommand(
            name="fork",
            usage="/fork",
            description="Create a new fork from a previous user message.",
            handler=_fork_command,
            search_terms=("branch", "history", "user"),
        )
    )
    registry.register(
        SlashCommand(
            name="session",
            usage="/session",
            description="Show session info and stats.",
            handler=_status_command,
            search_terms=("info",),
        )
    )
    registry.register(
        SlashCommand(
            name="skills",
            usage="/skills",
            description="Browse and insert a loaded skill.",
            handler=_skills_command,
            search_terms=("skill", "picker", "search"),
        )
    )
    registry.register(
        SlashCommand(
            name="skill",
            usage="/skill:<name> [request]",
            description="Expand a loaded skill into your prompt.",
            handler=_skill_command,
            search_terms=("skills",),
        )
    )
    registry.register(
        SlashCommand(
            name="hotkeys",
            usage="/hotkeys",
            description="Show common keyboard shortcuts.",
            handler=_hotkeys_command,
            search_terms=("keys", "shortcuts", "bindings"),
        )
    )
    registry.register(
        SlashCommand(
            name="resources",
            usage="/resources",
            description="Show loaded context, skills, prompts, and tools.",
            handler=_resources_command,
            search_terms=("context", "skills", "prompts", "tools"),
        )
    )
    registry.register(
        SlashCommand(
            name="workflows",
            usage="/workflows",
            description="List packaged canonical Tau workflows and launch commands.",
            handler=_workflows_command,
            search_terms=("canonical", "dag", "viewer", "launch", "progress"),
        )
    )
    registry.register(
        SlashCommand(
            name="tools",
            usage="/tools",
            description="Browse tools available to the active session.",
            handler=_tools_command,
            search_terms=("capabilities", "reference"),
        )
    )
    registry.register(
        SlashCommand(
            name="reload",
            usage="/reload",
            description="Reload local resources and project context.",
            handler=_reload_command,
        )
    )
    registry.register(
        SlashCommand(
            name="resume",
            usage="/resume [session-id]",
            description="Resume a previous session.",
            handler=_resume_command,
            search_terms=("history", "previous"),
        )
    )
    registry.register(
        SlashCommand(
            name="tree",
            usage="/tree",
            description="Branch from a previous session entry.",
            handler=_tree_command,
            search_terms=("branch", "history", "fork"),
        )
    )
    registry.register(
        SlashCommand(
            name="name",
            usage="/name <name>",
            description="Rename the current session.",
            handler=_name_command,
            search_terms=("rename", "title"),
        )
    )
    registry.register(
        SlashCommand(
            name="model",
            usage="/model",
            description="Choose the active model.",
            handler=_model_command,
            argument_hint="<provider/model>",
        )
    )
    registry.register(
        SlashCommand(
            name="scoped-models",
            usage="/scoped-models",
            description="Choose models available to quick-cycle with Ctrl+P.",
            handler=_scoped_models_command,
            search_terms=("scope", "quick", "cycle", "ctrl+p"),
        )
    )
    registry.register(
        SlashCommand(
            name="scillm",
            usage="/scillm [base-url]",
            description="Show Tau's SciLLM proxy surface and receipt commands.",
            handler=_scillm_command,
            search_terms=("local", "provider", "proxy", "oauth", "opencode", "llm"),
            argument_hint="<base-url>",
        )
    )
    registry.register(
        SlashCommand(
            name="thinking",
            usage="/thinking [level]",
            description="Show or set the active thinking level.",
            handler=_thinking_command,
            search_terms=("reasoning", "effort"),
            argument_hint="<level>",
        )
    )
    registry.register(
        SlashCommand(
            name="settings",
            usage="/settings",
            description="Open durable TUI settings.",
            handler=_settings_command,
            search_terms=("preferences", "config", "theme"),
        )
    )
    registry.register(
        SlashCommand(
            name="share",
            usage="/share",
            description="Share the current session as a secret GitHub gist.",
            handler=_share_command,
            search_terms=("gist", "url"),
        )
    )
    registry.register(
        SlashCommand(
            name="system",
            usage="/system",
            description="Show the active system prompt without saving it.",
            handler=_system_command,
            search_terms=("prompt", "instructions"),
        )
    )
    registry.register(
        SlashCommand(
            name="theme",
            usage="/theme [name]",
            description="Show or set the TUI theme.",
            handler=_theme_command,
            search_terms=("light", "dark", "contrast"),
        )
    )
    registry.register(
        SlashCommand(
            name="trust",
            usage="/trust",
            description="Save a project trust decision.",
            handler=_trust_command,
            search_terms=("project", "resources"),
        )
    )
    registry.register(
        SlashCommand(
            name="login",
            usage="/login [provider]",
            description="Save an API key for a built-in provider.",
            handler=_login_command,
            argument_hint="<provider>",
        )
    )
    registry.register(
        SlashCommand(
            name="logout",
            usage="/logout [provider]",
            description="Remove saved credentials for a built-in provider.",
            handler=_logout_command,
        )
    )
    return registry


def _help_command(context: CommandContext) -> CommandResult:
    lines = ["Available commands:"]
    for command in context.registry.list_commands():
        lines.append(f"{command.usage}\t{command.description}")
    return CommandResult(handled=True, message="\n".join(lines))


def _exit_command(context: CommandContext) -> CommandResult:
    return CommandResult(handled=True, exit_requested=True, message="Exiting session.")


def _new_command(context: CommandContext) -> CommandResult:
    return CommandResult(handled=True, new_session_requested=True)


def _changelog_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /changelog")
    return CommandResult(handled=True, message=_load_changelog_text(context.session.cwd))


def _config_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /config")
    return CommandResult(handled=True, message=_format_config_map(context.session))


def _clone_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /clone")
    return CommandResult(handled=True, clone_session_requested=True)


def _compact_command(context: CommandContext) -> CommandResult:
    return CommandResult(
        handled=True,
        compact_summary=context.args.strip(),
    )


def _copy_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /copy")
    return CommandResult(handled=True, copy_last_message_requested=True)


def _debug_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /debug")
    return CommandResult(
        handled=True,
        message="Use /debug in the interactive TUI to write a runtime diagnostic log.",
    )


_ARMIN_SAYS_HI_ART = (
    "        ▄▄▄▄",
    "         ▀▄ ▀▄",
    "           █  ▀▄▄",
    "     ▄▀▀▀▀▀     █",
    "     █▄   ▄▄▄▄▀▀▀▀▄",
    "   ▄▀  ▀▀▀  ▄▄▄▄▀▀ █",
    "   █              ▄█▄",
    "   ▀▄   ▄▄▄▀▀▀▀▀▀▀▀▄█  ▄▄▄▄▄",
    " ▄▄▄▀▀▀▀▄▄▄▄▄▄▄▄▄▄█▄ ▄▀   ▄ ▀▄",
    " █▄▄▄▄        ▀▀▀▀  █     ▀█ █",
    "     ██ ▄█      █   █ ▄      █",
    "     ▀█████▄▄▄▄███▄▄▀▄▀▄▄▄  ▄▀",
    "       ▀██████▀▀▀▀   ▀▄▄▄▄▄▀",
    "         ▀████████▀  ▄▄ ▄▄▄",
    "           ▀█████▀  ██▀ ██▄",
    "                    ██  ▄▄▄",
    "                     █  ▀█▀",
    "                      ▀▀▀",
)


def _armin_says_hi_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /arminsayshi")
    return CommandResult(handled=True, message="\n".join((*_ARMIN_SAYS_HI_ART, "ARMIN SAYS HI")))


def _demented_delves_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /dementedelves")
    lines = [
        "+----------------------------------------+",
        "| pi has joined Earendil                 |",
        "+----------------------------------------+",
        "",
        "Read the blog post:",
        "https://mariozechner.at/posts/2026-04-08-ive-sold-out/",
    ]
    return CommandResult(handled=True, message="\n".join(lines))


def _export_command(context: CommandContext) -> CommandResult:
    try:
        export_format, destination = _parse_export_args(context.args)
    except ValueError as exc:
        return CommandResult(handled=True, message=str(exc))
    return CommandResult(
        handled=True,
        export_requested=True,
        export_destination=destination,
        export_format=export_format,
    )


def _import_command(context: CommandContext) -> CommandResult:
    try:
        import_path = _parse_import_args(context.args)
    except ValueError as exc:
        return CommandResult(handled=True, message=str(exc))
    return CommandResult(
        handled=True,
        import_requested=True,
        import_path=import_path,
    )


def _status_command(context: CommandContext) -> CommandResult:
    session = context.session
    context_usage = getattr(session, "context_usage", None)
    session_path = getattr(session, "session_path", None)
    messages = tuple(getattr(session, "messages", ()))
    user_messages = sum(isinstance(message, UserMessage) for message in messages)
    assistant_messages = sum(isinstance(message, AssistantMessage) for message in messages)
    tool_results = sum(isinstance(message, ToolResultMessage) for message in messages)
    tool_calls = sum(
        len(message.tool_calls) for message in messages if isinstance(message, AssistantMessage)
    )
    lines = ["Session Info", ""]
    if session.session_title:
        lines.append(f"Name: {session.session_title}")
    lines.extend(
        (
            f"File: {session_path if session_path is not None else 'In-memory'}",
            f"ID: {session.session_id if session.session_id is not None else 'In-memory'}",
            f"CWD: {session.cwd}",
            "",
            "Model",
            f"Provider: {session.provider_name}",
            f"Current: {session.model}",
            "",
            "Messages",
            f"Total: {len(messages):,}",
            f"User: {user_messages:,}",
            f"Assistant: {assistant_messages:,}",
            f"Tools: {tool_calls:,} calls, {tool_results:,} results",
            "",
            "Tokens",
            f"Estimated context: {session.context_token_estimate:,}",
            f"Context window: {session.context_window_tokens:,}",
        )
    )
    if context_usage is not None:
        lines.extend(
            (
                f"  System: {context_usage.system_tokens:,}",
                f"  Messages: {context_usage.message_tokens:,}",
                f"  Tools: {context_usage.tool_tokens:,}",
            )
        )
    lines.extend(
        (
            "",
            "Resources",
            f"Tools: {len(session.tools):,}",
            f"Skills: {len(session.skills):,}",
            f"Prompt templates: {len(session.prompt_templates):,}",
            f"Context files: {len(session.context_files):,}",
            f"Diagnostics: {len(session.resource_diagnostics):,}",
        )
    )
    lines.extend(_thinking_status_lines(session))
    if session.auto_compact_token_threshold is not None:
        lines.append(f"Auto compact threshold: {session.auto_compact_token_threshold:,}")
    return CommandResult(handled=True, message="\n".join(lines))


def _hotkeys_command(context: CommandContext) -> CommandResult:
    lines = [
        "Common keyboard shortcuts:",
        "- Enter: submit prompt",
        "- Shift+Enter/Ctrl+J: insert newline",
        "- Alt+Enter: queue follow-up while running",
        "- Esc: cancel active run",
        "- Ctrl+K: open slash-command completions",
        "- Ctrl+R: open session picker",
        "- Ctrl+L: open model picker",
        "- Ctrl+P / Shift+Ctrl+P: cycle scoped models",
        "- Double Esc: open the session tree or configured double-escape action",
        '- Session picker: type text, re:<pattern> regex, or "phrase" exact',
        "- Session picker: Tab scope, Ctrl+N named, Ctrl+P path, Ctrl+S sort "
        "(recent/name/fuzzy)",
        "- Session picker: Ctrl+R/F2 rename, Ctrl+D delete",
        "- Session picker: Ctrl+Backspace delete when search is empty",
        "- Optional Pi session keys in ~/.tau/tui.json: session_new, session_tree, "
        "session_fork, session_resume",
        "- Tree picker: Ctrl+O cycles filters, Shift+Ctrl+O cycles backward",
        "- Tree picker: Shift+L edits labels, Shift+T toggles label timestamps",
        "- Shift+Tab: cycle thinking mode",
        "- Ctrl+T: toggle thinking tokens",
        "- Ctrl+O: collapse or expand tool output",
        "- Ctrl+C: clear prompt input",
        "- Ctrl+X: copy last assistant message",
        "- Ctrl+D: quit",
    ]
    extension_shortcuts = getattr(context.session, "extension_shortcut_sources", {})
    if isinstance(extension_shortcuts, dict) and extension_shortcuts:
        lines.extend(("", "Extension shortcuts:"))
        for key, value in sorted(extension_shortcuts.items()):
            extension_name, description = value
            lines.append(f"- {key}: {description} (extension:{extension_name})")
    return CommandResult(handled=True, message="\n".join(lines))


def _permissions_command(context: CommandContext) -> CommandResult:
    actions = "\n".join(f"- {action}" for action in sorted(ALLOWED_ACTIONS))
    replies = "|".join(ALLOWED_PERMISSION_REPLIES)
    lines = [
        "Tau permission receipts:",
        "",
        "Create pending request:",
        (
            "uv run tau permission-request --action <action> --resource <resource> "
            "--source-node <node-id> --run-dir <run-dir>"
        ),
        "",
        "Record human reply:",
        (
            "uv run tau permission-reply --request <permission-request.json> "
            f"--reply <{replies}> --actor human:<id>"
        ),
        "",
        "Check approval packet:",
        (
            "uv run tau approval-gate-check --approval-packet <approval.json> "
            "--requested-action <action> --run-dir <run-dir>"
        ),
        "",
        "Allowed actions:",
        actions,
        "",
        "Schemas:",
        f"- {PERMISSION_REQUEST_RECEIPT_SCHEMA}",
        f"- {PERMISSION_REPLY_RECEIPT_SCHEMA}",
        f"- {APPROVAL_PACKET_SCHEMA}",
        f"- {APPROVAL_GATE_RECEIPT_SCHEMA}",
        "",
        "These commands write receipts only; they do not execute mutations.",
    ]
    return CommandResult(handled=True, message="\n".join(lines))


def _workflows_command(context: CommandContext) -> CommandResult:
    workflow_id = context.args.strip()
    if workflow_id:
        try:
            workflow = get_workflow(workflow_id)
        except RuntimeError:
            available = ", ".join(workflow.workflow_id for workflow in list_workflows())
            return CommandResult(
                handled=True,
                message=f"Unknown workflow: {workflow_id}\nAvailable workflows: {available}",
            )
        return CommandResult(
            handled=True,
            message=_format_workflow_detail(workflow.public_payload()),
        )

    workflows = list_workflows()
    lines = [
        "Packaged canonical Tau workflows:",
        "Run from a shell with the per-workflow command shown below.",
        "Use `uv run tau workflows describe <workflow-id> --json` for the exact input contract.",
    ]
    for workflow in workflows:
        lines.extend(
            [
                "",
                f"- {workflow.workflow_id}: {workflow.title}",
                f"  topology: {workflow.topology}",
                f"  availability: {workflow.availability}",
                f"  result: {workflow.result_schema} via {workflow.result_node_id}",
                f"  describe: uv run tau workflows describe {workflow.workflow_id} --json",
                f"  run: {_format_workflow_run_command(workflow.workflow_id)}",
            ]
        )
    return CommandResult(handled=True, workflow_picker_requested=True, message="\n".join(lines))


def _format_workflow_detail(payload: dict[str, object]) -> str:
    runtime = payload.get("runtime")
    proof_boundary = payload.get("proof_boundary")
    lines = [
        f"Workflow: {payload['workflow_id']}",
        f"Title: {payload['title']}",
        f"Summary: {payload['summary']}",
        f"Topology: {payload['topology']}",
        f"Availability: {payload['availability']}",
        f"Input schema: {payload['input_schema']}",
        f"Result schema: {payload['result_schema']}",
        f"Result node: {payload['result_node_id']}",
    ]
    if isinstance(runtime, dict):
        runtime_parts = [
            f"{key}={value}" for key, value in sorted(runtime.items()) if isinstance(key, str)
        ]
        lines.append(f"Runtime: {', '.join(runtime_parts)}")
    if isinstance(proof_boundary, dict):
        proof_parts = [
            f"{key}={value}"
            for key, value in sorted(proof_boundary.items())
            if isinstance(key, str)
        ]
        lines.append(f"Proof boundary: {', '.join(proof_parts)}")
    workflow_id = str(payload["workflow_id"])
    lines.extend(
        [
            f"Describe: uv run tau workflows describe {workflow_id} --json",
            f"Run: {_format_workflow_run_command(workflow_id)}",
        ]
    )
    return "\n".join(lines)


def _format_workflow_run_command(workflow_id: str) -> str:
    parts = ["uv run tau workflows run", workflow_id, "--repo <repo>"]
    if workflow_id != "tau-operator-reference":
        parts.extend(["--goal <goal>"])
    parts.extend(["--run-dir <dir>"])
    if workflow_id in {"approved-release-bundle", "durable-repository-qualification"}:
        parts.extend(["--publish-path <publish-dir>"])
    parts.extend(["--open-viewer"])
    return " ".join(parts)


def _skills_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /skills")
    return CommandResult(handled=True, skills_picker_requested=True)


def _resources_command(context: CommandContext) -> CommandResult:
    session = context.session
    lines = [
        f"Skills: {len(session.skills)}",
        f"Prompt templates: {len(session.prompt_templates)}",
        f"Context files: {len(session.context_files)}",
        f"Tools: {len(session.tools)}",
    ]
    if session.resource_diagnostics:
        lines.append("")
        lines.extend(_format_diagnostics(session.resource_diagnostics))
    else:
        lines.append("Resource diagnostics: none")
    return CommandResult(handled=True, message="\n".join(lines))


def _tools_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /tools")
    return CommandResult(handled=True, tools_picker_requested=True)


def _reload_command(context: CommandContext) -> CommandResult:
    try:
        summary = context.session.reload()
    except ValueError as exc:
        return CommandResult(handled=True, message=f"Could not reload: {exc}")

    return CommandResult(
        handled=True,
        message=_format_reload_summary(summary),
    )


def _context_command(context: CommandContext) -> CommandResult:
    session = context.session
    if not session.context_files:
        lines = ["No project context files loaded."]
        if session.resource_diagnostics:
            lines.append("")
            lines.extend(_format_diagnostics(session.resource_diagnostics, kind="context"))
        return CommandResult(handled=True, message="\n".join(lines))

    lines = ["Active project context files:"]
    lines.extend(f"- {context_file.path}" for context_file in session.context_files)
    if session.resource_diagnostics:
        lines.append("")
        lines.extend(_format_diagnostics(session.resource_diagnostics, kind="context"))
    return CommandResult(handled=True, message="\n".join(lines))


def _skill_command(context: CommandContext) -> CommandResult:
    return CommandResult(
        handled=True,
        message="Use /skill:<name> [request] to expand a loaded skill into your prompt.",
    )


def _system_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /system")
    return CommandResult(handled=True, message=context.session.system_prompt)


def _prompts_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /prompts")
    return CommandResult(handled=True, prompts_picker_requested=True)


def _resume_command(context: CommandContext) -> CommandResult:
    if not context.args:
        return CommandResult(handled=True, resume_picker_requested=True)
    manager = context.session.session_manager
    if manager is None:
        return CommandResult(handled=True, message="Session manager is not available.")
    session_id = context.args.strip()
    if manager.get_session(session_id) is None:
        return CommandResult(handled=True, message=f"Unknown session: {session_id}")
    return CommandResult(
        handled=True,
        resume_session_id=session_id,
    )


def _tree_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /tree")
    return CommandResult(handled=True, tree_picker_requested=True)


def _fork_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /fork")
    return CommandResult(handled=True, fork_picker_requested=True)


def _settings_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /settings")
    return CommandResult(handled=True, settings_picker_requested=True)


def _images_command(context: CommandContext) -> CommandResult:
    del context
    return CommandResult(
        handled=True,
        message="Image display is an interactive TUI setting. Use /images in the TUI.",
    )


def _share_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /share")
    return CommandResult(handled=True, share_requested=True)


def _trust_command(context: CommandContext) -> CommandResult:
    if context.args:
        return CommandResult(handled=True, message="Usage: /trust")
    return CommandResult(handled=True, trust_picker_requested=True)


def _name_command(context: CommandContext) -> CommandResult:
    manager = context.session.session_manager
    session_id = context.session.session_id
    if manager is None or session_id is None:
        return CommandResult(handled=True, message="Session manager is not available.")

    record = manager.get_session(session_id)
    if record is None:
        return CommandResult(handled=True, message=f"Unknown current session: {session_id}")

    if not context.args:
        if not record.title:
            return CommandResult(handled=True, message="Usage: /name <name>")
        return CommandResult(
            handled=True,
            message=f"Session name: {record.title}",
        )

    try:
        name = _validated_session_name(context.args)
    except ValueError as exc:
        return CommandResult(handled=True, message=str(exc))

    updated = manager.touch_session(
        session_id,
        model=context.session.model,
        provider_name=context.session.provider_name,
        title=name,
    )
    if updated is None:
        return CommandResult(handled=True, message=f"Unknown current session: {session_id}")
    return CommandResult(handled=True, message=f"Session renamed: {updated.title}")


def _format_sessions(context: CommandContext) -> str:
    manager = context.session.session_manager
    if manager is None:
        return "Session manager is not available."

    records = manager.list_sessions(context.session.cwd)
    if not records:
        return "No sessions found."

    lines = ["Indexed sessions:"]
    for record in records:
        lines.append(_format_session_record(record))
    return "\n".join(lines)


def _model_command(context: CommandContext) -> CommandResult:
    refresh_error = _refresh_provider_settings(context.session)
    if refresh_error is not None:
        return refresh_error

    if context.args:
        query = context.args.strip()
        choice = _find_exact_model_choice(context.session, query)
        if choice is not None:
            model = str(cast(Any, choice).model)
            _set_model_choice(context.session, choice)
            message = f"Current model: {model}"
            daxnuts_message = daxnuts_easter_message(context.session.provider_name, model)
            if daxnuts_message is not None:
                message = f"{message}\n\n{daxnuts_message}"
            return CommandResult(handled=True, message=message)
        model = query
        available_models = set(context.session.available_models)
        if available_models and model not in available_models:
            return CommandResult(
                handled=True,
                model_picker_requested=True,
                model_picker_query=query,
            )
        context.session.set_model(model)
        message = f"Current model: {model}"
        daxnuts_message = daxnuts_easter_message(context.session.provider_name, model)
        if daxnuts_message is not None:
            message = f"{message}\n\n{daxnuts_message}"
        return CommandResult(handled=True, message=message)

    return CommandResult(handled=True, model_picker_requested=True)


def _scoped_models_command(context: CommandContext) -> CommandResult:
    refresh_error = _refresh_provider_settings(context.session)
    if refresh_error is not None:
        return refresh_error

    if context.args:
        return CommandResult(handled=True, message="Usage: /scoped-models")
    return CommandResult(handled=True, scoped_models_picker_requested=True)


def _scillm_command(context: CommandContext) -> CommandResult:
    try:
        args = shlex.split(context.args)
    except ValueError as exc:
        return CommandResult(handled=True, message=f"Could not parse /scillm arguments: {exc}")
    if len(args) > 1 or args in (["-h"], ["--help"]):
        return CommandResult(handled=True, message="Usage: /scillm [base-url]")

    configured_base_url = os.environ.get("SCILLM_BASE_URL", "").strip()
    base_url = args[0].rstrip("/") if args else (configured_base_url or SCILLM_DEFAULT_BASE_URL)
    if not base_url.startswith(("http://", "https://")):
        return CommandResult(handled=True, message="Usage: /scillm [base-url]")

    auth_lines = [
        f"- {name}: {'set' if os.environ.get(name) else 'missing'}"
        for name in SCILLM_AUTH_ENV_NAMES
    ]
    lines = [
        "SciLLM Proxy",
        "",
        f"Base URL: {base_url}",
        f"Current Tau model: {context.session.provider_name}:{context.session.model}",
        "",
        "Auth environment:",
        *auth_lines,
        "",
        "Operator checks:",
        f"- curl -s {base_url}/health/liveliness -H 'Authorization: Bearer <proxy-key>'",
        f"- curl -s {base_url}/v1/scillm/auth -H 'Authorization: Bearer <proxy-key>'",
        f"- curl -s {base_url}/v1/scillm/health -H 'Authorization: Bearer <proxy-key>'",
        "",
        "Tau receipt commands:",
        "- uv run tau scillm-worker-launch --work-order <json> --out <receipt>",
        "- uv run tau scillm-chat-review --request <json> --out <receipt>",
        "- uv run tau loop2-check-scillm-doctor <receipt.json>",
        "",
        "Notes:",
        "- /scillm is read-only; it does not call providers or mutate state.",
        "- SciLLM worker DAG nodes use /v1/scillm/opencode/runs, not direct provider APIs.",
        "- Use /model for model switching inside Tau.",
    ]
    return CommandResult(handled=True, message="\n".join(lines))


def _thinking_command(context: CommandContext) -> CommandResult:
    session = context.session
    available = tuple(session.available_thinking_levels)
    if not context.args:
        lines = _thinking_status_lines(session)
        if available:
            lines.append(f"Available modes: {', '.join(available)}")
        else:
            lines.insert(1, f"Current model: {session.provider_name}:{session.model}")
        return CommandResult(handled=True, message="\n".join(lines))

    if not available:
        message = f"Thinking controls are unavailable for {session.provider_name}:{session.model}"
        reason = _thinking_unavailable_reason(session)
        if reason:
            message = f"{message}: {reason}"
        return CommandResult(
            handled=True,
            message=message,
        )
    try:
        level = normalize_thinking_level(context.args)
    except ValueError as exc:
        return CommandResult(handled=True, message=str(exc))
    if level not in available:
        modes = ", ".join(available)
        return CommandResult(
            handled=True,
            message=(
                f"Thinking mode {level} is not available for "
                f"{session.provider_name}:{session.model}\n"
                f"Available modes: {modes}"
            ),
        )
    return CommandResult(handled=True, thinking_level=level)


def _thinking_status_lines(session: CommandSession) -> list[str]:
    if tuple(session.available_thinking_levels):
        return [f"Thinking mode: {session.thinking_level}"]
    lines = ["Thinking mode: unavailable"]
    reason = _thinking_unavailable_reason(session)
    if reason:
        lines.append(f"Thinking unavailable: {reason}")
    return lines


def _thinking_unavailable_reason(session: CommandSession) -> str | None:
    reason = getattr(session, "thinking_unavailable_reason", None)
    return reason if isinstance(reason, str) and reason else None


def _theme_command(context: CommandContext) -> CommandResult:
    if not context.args:
        return CommandResult(handled=True, theme_picker_requested=True)

    theme_setting = context.args.strip()
    if not _is_known_tui_theme_setting(theme_setting):
        themes = ", ".join((*_available_tui_theme_names(), "<light-theme>/<dark-theme>"))
        return CommandResult(
            handled=True,
            message=f"Unknown theme: {theme_setting}\nAvailable themes: {themes}",
        )
    return CommandResult(handled=True, theme=theme_setting)


def _is_known_tui_theme_setting(theme_setting: str) -> bool:
    theme_names = _available_tui_theme_names()
    if theme_setting in theme_names:
        return True
    slash_index = theme_setting.find("/")
    if slash_index < 0:
        return False
    light_theme = theme_setting[:slash_index].strip()
    dark_theme = theme_setting[slash_index + 1 :].strip()
    return light_theme in theme_names and dark_theme in theme_names


def _available_tui_theme_names() -> tuple[str, ...]:
    from tau_coding.tui.config import available_tui_theme_names

    return available_tui_theme_names()


def _login_command(context: CommandContext) -> CommandResult:
    provider_name = context.args.strip()
    if provider_name:
        entry = builtin_provider_entry(provider_name)
        if entry is None:
            return CommandResult(
                handled=True,
                login_picker_requested=True,
                login_picker_query=provider_name,
            )
        return CommandResult(handled=True, login_provider=entry.name)

    return CommandResult(handled=True, login_picker_requested=True)


def _logout_command(context: CommandContext) -> CommandResult:
    provider_name = context.args.strip()
    if provider_name:
        entry = builtin_provider_entry(provider_name)
        if entry is None:
            providers = ", ".join(entry.name for entry in BUILTIN_PROVIDER_CATALOG)
            return CommandResult(
                handled=True,
                message=(
                    f"Unknown logout provider: {provider_name}\nAvailable providers: {providers}"
                ),
            )
        return CommandResult(handled=True, logout_provider=entry.name)

    return CommandResult(handled=True, logout_picker_requested=True)


def _format_session_record(record: CodingSessionRecord) -> str:
    title = record.title or "Untitled"
    return f"- {record.id}: {title} ({record.model}) {record.cwd}"


def _format_diagnostics(
    diagnostics: Sequence[ResourceDiagnostic], *, kind: str | None = None
) -> list[str]:
    filtered = [diagnostic for diagnostic in diagnostics if kind is None or diagnostic.kind == kind]
    if not filtered:
        return ["Resource diagnostics: none"]
    lines = ["Resource diagnostics:"]
    lines.extend(f"- {diagnostic.format()}" for diagnostic in filtered)
    return lines


def _format_config_map(session: CommandSession) -> str:
    paths = TauPaths()
    resource_paths = TauResourcePaths(cwd=session.cwd, paths=paths)
    trust_path = ProjectTrustStore.from_resource_paths(resource_paths).trust_path
    loaded_extensions = getattr(session, "extensions", ())
    extension_count = len(loaded_extensions) if isinstance(loaded_extensions, Sequence) else 0
    lines = [
        "Tau Config Map",
        "",
        "Status: read-only map; Tau does not currently provide Pi's package selector TUI.",
        "",
        "Interactive commands:",
        "- /settings: edit durable TUI settings",
        "- /resources: inspect loaded context, skills, prompts, extensions, tools, diagnostics",
        "- /reload: reload local resources and project context",
        "- /trust: save project-local resource trust",
        "- /login, /logout: manage saved provider credentials",
        "- /model, /scoped-models: choose active model and Ctrl+P model scope",
        "",
        "Durable config files:",
        f"- TUI settings: {paths.home / 'tui.json'}",
        f"- Provider settings: {provider_settings_path(paths)}",
        f"- Provider credentials: {credentials_path(paths)}",
        f"- Project trust: {trust_path}",
        "",
        "Resource directories, increasing precedence:",
        "- Skills:",
        *[f"  - {path}" for path in resource_paths.skills_dirs],
        "- Prompt templates:",
        *[f"  - {path}" for path in resource_paths.prompts_dirs],
        "- Themes:",
        *[f"  - {path}" for path in resource_paths.themes_dirs],
        "- Extensions:",
        f"  - {resource_paths.extensions_dir}",
        f"  - {paths.project_tau_dir(session.cwd) / 'extensions'}",
        "",
        "Loaded resources:",
        f"- Context files: {len(session.context_files)}",
        f"- Skills: {len(session.skills)}",
        f"- Prompt templates: {len(session.prompt_templates)}",
        f"- Extensions: {extension_count}",
        f"- Resource diagnostics: {len(session.resource_diagnostics)}",
        "",
        "Boundary:",
        "- Use file edits plus /reload for resource changes; /config does not mutate state.",
        "- Missing or untrusted project resources stay visible through /resources diagnostics.",
    ]
    return "\n".join(lines)


def _refresh_provider_settings(session: CommandSession) -> CommandResult | None:
    try:
        session.reload_provider_settings()
    except ValueError as exc:
        return CommandResult(
            handled=True,
            message=f"Could not refresh provider settings: {exc}",
        )
    return None


def _format_reload_summary(summary: CodingReloadSummary) -> str:
    lines = [
        "Reloaded local coding resources and project context.",
        "Resources:",
        f"- Skills: {_format_reload_category(summary.skills)}",
        f"- Prompt templates: {_format_reload_category(summary.prompt_templates)}",
        "Context:",
        f"- Project context files: {_format_reload_category(summary.context_files)}",
        "- Next-turn system prompt: "
        + ("rebuilt" if summary.system_prompt_rebuilt else "unchanged"),
        "Diagnostics:",
        f"- Resource diagnostics: {_format_reload_category(summary.diagnostics)}",
        "Provider config:",
        "- Not refreshed by /reload; use /login or /model for provider/model settings.",
    ]
    return "\n".join(lines)


def _format_reload_category(summary: ReloadCategorySummary) -> str:
    status = "changed" if summary.changed else "unchanged"
    delta = _format_count_delta(summary.delta)
    suffix = f", {delta}" if delta is not None else ""
    return f"{summary.after} total ({status}{suffix})"


def _format_count_delta(delta: int) -> str | None:
    if delta == 0:
        return None
    return f"{delta:+d}"


def _parse_command(text: str) -> tuple[str, str]:
    command, separator, args = text[1:].partition(" ")
    return _normalize_name(command), args.strip() if separator else ""


def _parse_export_args(args: str) -> tuple[str | None, Path | None]:
    try:
        parts = shlex.split(args)
    except ValueError as exc:
        raise ValueError("Usage: /export [--format html|jsonl] [destination]") from exc
    export_format: str | None = None
    destination: Path | None = None
    index = 0
    while index < len(parts):
        part = parts[index]
        if part == "--format":
            index += 1
            if index >= len(parts):
                raise ValueError("Usage: /export [--format html|jsonl] [destination]")
            export_format = parts[index]
        elif part.startswith("--format="):
            export_format = part.partition("=")[2]
        elif part.startswith("-"):
            raise ValueError(f"Unknown export option: {part}")
        elif destination is None:
            destination = Path(part).expanduser()
        else:
            raise ValueError("Usage: /export [--format html|jsonl] [destination]")
        index += 1
    return export_format, destination


def _parse_import_args(args: str) -> Path:
    try:
        parts = shlex.split(args)
    except ValueError as exc:
        raise ValueError("Usage: /import <path.jsonl>") from exc
    if len(parts) != 1:
        raise ValueError("Usage: /import <path.jsonl>")
    return Path(parts[0]).expanduser()


def _load_changelog_text(cwd: Path) -> str:
    for path in _candidate_changelog_paths(cwd):
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return "No changelog entries found."


def _candidate_changelog_paths(cwd: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    try:
        current = cwd.resolve()
    except OSError:
        current = cwd
    for directory in (current, *current.parents):
        paths.append(directory / "CHANGELOG.md")

    package_root = Path(__file__).resolve().parents[2]
    package_changelog = package_root / "CHANGELOG.md"
    if package_changelog not in paths:
        paths.append(package_changelog)
    return tuple(paths)


def _validated_session_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("Usage: /name <name>")
    if any(char in name for char in "\r\n\t"):
        raise ValueError("Session name must be a single line.")
    return name


def daxnuts_easter_message(provider_name: str, model: str) -> str | None:
    """Return Pi's Daxnuts easter message for matching OpenCode Kimi models."""
    provider = provider_name.strip().lower()
    model_id = model.strip().lower()
    if provider == "opencode" and "kimi-k2.5" in model_id:
        return _DAXNUTS_MESSAGE
    if model_id.startswith("opencode-go/kimi-k2.5"):
        return _DAXNUTS_MESSAGE
    return None


_DAXNUTS_MESSAGE = "\n".join(
    (
        "Free Kimi K2.5 via OpenCode Zen",
        '"Powered by daxnuts"',
        "-- @thdxr",
        "",
        "Try OpenCode",
        "https://mistral.ai/news/mistral-vibe-2-0",
    )
)


def _find_exact_model_choice(session: CommandSession, query: str) -> object | None:
    normalized = query.strip().lower()
    if not normalized:
        return None
    choices = tuple(getattr(session, "available_model_choices", ()))
    for choice in choices:
        provider_name = str(getattr(choice, "provider_name", "")).strip()
        model = str(getattr(choice, "model", "")).strip()
        candidates = {
            model.lower(),
            f"{provider_name}:{model}".lower(),
            f"{provider_name}/{model}".lower(),
        }
        if normalized in candidates:
            return choice
    return None


def _set_model_choice(session: CommandSession, choice: object) -> None:
    set_model_choice = getattr(session, "set_model_choice", None)
    if callable(set_model_choice):
        set_model_choice(choice)
        return
    provider_name = str(getattr(choice, "provider_name", ""))
    model = str(getattr(choice, "model", ""))
    set_provider = getattr(session, "set_provider", None)
    if callable(set_provider) and provider_name and provider_name != session.provider_name:
        set_provider(provider_name)
    session.set_model(model)


def _normalize_name(name: str) -> str:
    return name.strip().removeprefix("/").lower()
