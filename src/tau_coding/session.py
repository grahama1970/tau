"""Persistent coding-session wrapper built on AgentHarness."""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import os
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic, time
from typing import Any, Final, Literal, cast

from tau_agent import (
    AgentEndEvent,
    AgentEvent,
    AgentHarness,
    AgentHarnessConfig,
    AgentStartEvent,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    QueuedMessages,
    QueueMode,
    QueueUpdateEvent,
    SimpleCancellationToken,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from tau_agent.messages import AgentMessage, AssistantMessage, ToolResultMessage, UserMessage
from tau_agent.session import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    JsonlSessionStorage,
    LabelEntry,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionInfoEntry,
    SessionState,
    SessionStorage,
    ThinkingLevelChangeEntry,
)
from tau_agent.session.entries import SessionEntry
from tau_agent.session.tree import SessionTreeError, path_to_entry
from tau_agent.tools import AgentTool
from tau_ai import CancellationToken, ModelProvider, ProviderEvent
from tau_ai.events import ProviderErrorEvent, ProviderResponseEndEvent, ProviderTextDeltaEvent
from tau_coding.branch_summary import summarize_branch_messages_with_model
from tau_coding.commands import (
    CommandArgumentCompletion,
    CommandContext,
    CommandFooterUpdate,
    CommandHeaderUpdate,
    CommandNotification,
    CommandRegistry,
    CommandResult,
    CommandStatusUpdate,
    CommandWidgetPlacement,
    CommandWidgetUpdate,
    CommandWorkingIndicatorUpdate,
    SlashCommand,
    create_default_command_registry,
)
from tau_coding.context import discover_project_context_with_diagnostics
from tau_coding.context_window import (
    DEFAULT_COMPACTION_KEEP_RECENT_TOKENS,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    SUMMARIZATION_SYSTEM_PROMPT,
    ContextUsageEstimate,
    auto_compaction_threshold_for_context_window,
    build_compaction_summary_prompt,
    estimate_context_usage,
    estimate_message_tokens,
    summarize_messages_for_compaction,
)
from tau_coding.credentials import FileCredentialStore, credentials_path
from tau_coding.diagnostics import (
    AgentCallDiagnosticContext,
    AgentCallDiagnosticLogger,
    new_agent_call_run_id,
)
from tau_coding.extensions import (
    ExtensionCommand,
    ExtensionCommandContext,
    ExtensionShortcutContext,
    LoadedExtension,
    load_extension_tools,
)
from tau_coding.loop_receipt import LoopReceiptConfig, LoopReceiptRecorder
from tau_coding.paths import TauPaths
from tau_coding.prompt_templates import (
    PromptTemplate,
    expand_prompt_template_command,
    load_prompt_templates_from_paths_with_diagnostics,
    load_prompt_templates_with_diagnostics,
)
from tau_coding.provider_config import (
    ProviderConfig,
    ProviderConfigError,
    ProviderSettings,
    ScopedModelConfig,
    load_provider_settings,
    provider_default_thinking_level,
    provider_has_usable_credentials,
    provider_thinking_levels,
    provider_thinking_unavailable_reason,
    resolve_provider_selection,
    save_default_provider_model,
    set_saved_scoped_models,
    toggle_saved_scoped_model,
    upsert_provider,
)
from tau_coding.provider_runtime import ClosableModelProvider, create_model_provider
from tau_coding.reload import CodingReloadSummary, ReloadCategorySummary
from tau_coding.resources import (
    ResourceDiagnostic,
    ResourceError,
    TauResourcePaths,
    resource_paths_with_cwd,
)
from tau_coding.session_export import (
    default_session_export_artifact_path,
    export_session_artifact,
    normalize_export_format,
)
from tau_coding.session_manager import SessionManager
from tau_coding.skills import (
    Skill,
    expand_skill_command,
    load_skills_from_paths_with_diagnostics,
    load_skills_with_diagnostics,
)
from tau_coding.system_prompt import (
    BuildSystemPromptOptions,
    ProjectContextFile,
    build_system_prompt,
)
from tau_coding.thinking import (
    DEFAULT_THINKING_LEVEL,
    THINKING_LEVELS,
    ThinkingLevel,
    next_thinking_level,
    normalize_thinking_level,
)
from tau_coding.tools import (
    BUILTIN_CODING_TOOL_NAMES,
    BashEnvironment,
    create_bash_tool,
    create_coding_tools,
)
from tau_coding.trust import (
    DefaultProjectTrust,
    ProjectTrustOption,
    ProjectTrustState,
    ProjectTrustStore,
    has_trust_requiring_project_resources,
    project_trust_state,
)
from tau_coding.tui.config import (
    load_custom_tui_themes,
    load_custom_tui_themes_from_paths,
    set_custom_tui_themes,
)

StreamingBehavior = Literal["steer", "follow_up"]
InputSource = Literal["interactive", "rpc", "extension"]
ModelSelectSource = Literal["set", "cycle", "restore"]
_UNSET_LEAF_ID: Final[object] = object()
_BASH_SESSION_ENV_KEYS: Final[tuple[str, ...]] = (
    "TAU_SESSION_ID",
    "TAU_SESSION_FILE",
    "TAU_PROVIDER",
    "TAU_MODEL",
    "TAU_REASONING_LEVEL",
)


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """A selectable model and the provider that serves it."""

    provider_name: str
    model: str


@dataclass(frozen=True, slots=True)
class TerminalCommandResult:
    """Result of an input-bar terminal command."""

    command: str
    output: str
    exit_code: int | None
    ok: bool
    added_to_context: bool


@dataclass(frozen=True, slots=True)
class SessionTreeChoice:
    """One branchable entry in the active session tree."""

    entry_id: str
    label: str
    parent_entry_id: str | None = None
    active: bool = False
    is_tool_call: bool = False
    copy_text: str | None = None
    tree_label: str | None = None
    tree_label_timestamp: float | None = None


@dataclass(frozen=True, slots=True)
class SessionTreeBranchResult:
    """Result of moving the active session tree leaf."""

    message: str
    input_prefill: str | None = None


@dataclass(frozen=True, slots=True)
class TerminalCommandRequest:
    """Parsed input-bar terminal command request."""

    command: str
    add_to_context: bool


@dataclass(frozen=True, slots=True)
class SessionResources:
    """Tau-owned resources loaded around a coding session."""

    skills: tuple[Skill, ...]
    prompt_templates: tuple[PromptTemplate, ...]
    context_files: tuple[ProjectContextFile, ...]
    extensions: tuple[LoadedExtension, ...]
    extension_tools: tuple[AgentTool, ...]
    extension_provider_configs: tuple[ProviderConfig, ...]
    diagnostics: tuple[ResourceDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    """Prepared active-context entries for a compaction run."""

    replace_entry_ids: tuple[str, ...]
    messages_to_summarize: tuple[AgentMessage, ...]


@dataclass(frozen=True, slots=True)
class CodingSessionConfig:
    """Configuration for a persistent coding session."""

    provider: ModelProvider
    model: str
    storage: SessionStorage
    cwd: Path
    system: str | None = None
    custom_system_prompt: str | None = None
    append_system_prompt: str | None = None
    context_files: tuple[ProjectContextFile, ...] = ()
    skill_paths: tuple[Path, ...] = ()
    prompt_template_paths: tuple[Path, ...] = ()
    theme_paths: tuple[Path, ...] = ()
    extension_paths: tuple[Path, ...] = ()
    extension_flag_values: Mapping[str, bool | str] = field(default_factory=dict)
    discover_skills: bool = True
    discover_prompt_templates: bool = True
    discover_themes: bool = True
    discover_extensions: bool = True
    discover_context_files: bool = True
    tools: list[AgentTool] | None = None
    tool_allowlist: tuple[str, ...] | None = None
    tool_denylist: tuple[str, ...] = ()
    no_tools: bool = False
    no_builtin_tools: bool = False
    resource_paths: TauResourcePaths | None = None
    session_id: str | None = None
    session_manager: SessionManager | None = None
    command_registry: CommandRegistry | None = None
    provider_name: str = "openai"
    provider_settings: ProviderSettings | None = None
    runtime_provider_config: ProviderConfig | None = None
    auto_compact_token_threshold: int | None = None
    auto_compact_enabled: bool = True
    steering_queue_mode: QueueMode = "one_at_a_time"
    follow_up_queue_mode: QueueMode = "one_at_a_time"
    default_project_trust: DefaultProjectTrust = "ask"
    thinking_level: ThinkingLevel = DEFAULT_THINKING_LEVEL
    loop_receipt: LoopReceiptConfig | None = None
    shell_path: str | None = None
    shell_command_prefix: str | None = None
    auto_resize_images: bool = True
    extension_start_reason: str = "startup"
    extension_previous_session_file: str | None = None


class CodingSession:
    """Tau's coding-agent environment wrapper.

    `AgentHarness` owns the in-memory agent brain. `CodingSession` owns the
    coding-session environment around it: durable session entries, default coding
    tools, and a small command seam for later phases.
    """

    def __init__(
        self,
        config: CodingSessionConfig,
        *,
        state: SessionState,
        harness: AgentHarness,
        last_parent_id: str | None,
        skills: tuple[Skill, ...] = (),
        prompt_templates: tuple[PromptTemplate, ...] = (),
        context_files: tuple[ProjectContextFile, ...] = (),
        extensions: tuple[LoadedExtension, ...] = (),
        resource_diagnostics: tuple[ResourceDiagnostic, ...] = (),
        command_registry: CommandRegistry | None = None,
        base_command_registry: CommandRegistry | None = None,
        pending_initial_entries: tuple[SessionEntry, ...] = (),
    ) -> None:
        self._config = config
        self._state = state
        self._harness = harness
        self._last_parent_id = last_parent_id
        self._pending_initial_entries = pending_initial_entries
        self._skills = skills
        self._prompt_templates = prompt_templates
        self._context_files = context_files
        self._extensions = extensions
        self._runtime_extension_tool_sources: dict[str, str] = {}
        self._available_tools: list[AgentTool] = list(harness.config.tools)
        self._extension_ui_handler: Callable[..., object] | None = None
        self._extension_terminal_input_handler: Callable[..., object] | None = None
        self._extension_autocomplete_provider_handler: Callable[..., object] | None = None
        self._extension_editor_component_handler: Callable[..., object] | None = None
        self._extension_widget_component_handler: Callable[..., object] | None = None
        self._extension_chrome_component_handler: Callable[..., object] | None = None
        self._resource_diagnostics = resource_diagnostics
        self._base_command_registry = (
            base_command_registry.copy()
            if base_command_registry is not None
            else create_default_command_registry()
        )
        self._command_registry = command_registry or create_default_command_registry()
        self._provider_name = config.provider_name
        self._provider_settings = config.provider_settings
        self._runtime_provider_config = config.runtime_provider_config
        self._provider_timeout_override_seconds: float | None = None
        self._resource_paths = resource_paths_with_cwd(config.resource_paths, config.cwd)
        self._auto_compact_token_threshold = config.auto_compact_token_threshold
        self._auto_compact_enabled = config.auto_compact_enabled
        self._thinking_level = _state_thinking_level(state, config.thinking_level)
        self._shell_path = config.shell_path
        self._shell_command_prefix = config.shell_command_prefix
        self._terminal_signal: SimpleCancellationToken | None = None
        self._pending_terminal_context_messages: list[UserMessage] = []
        self._owned_providers: list[ClosableModelProvider] = []
        self._quit_shutdown_emitted = False
        self._diagnostic_logger = AgentCallDiagnosticLogger.from_paths(self._resource_paths.paths)
        self._credential_store = FileCredentialStore(
            credentials_path(self._resource_paths.paths) if self._resource_paths.paths else None
        )
        self._last_diagnostic_log_path: Path | None = None
        self._install_extension_provider_adapter(harness.config.provider)

    def _install_extension_provider_adapter(self, provider: ModelProvider) -> None:
        self._harness.config.provider = _ExtensionAwareModelProvider(
            session=self,
            provider=_base_model_provider(provider),
        )

    @classmethod
    async def load(cls, config: CodingSessionConfig) -> CodingSession:
        """Load a coding session from append-only storage."""
        entries = await config.storage.read_all()
        pending_initial_entries: tuple[SessionEntry, ...] = ()
        if not entries:
            info = SessionInfoEntry(cwd=str(config.cwd))
            model = ModelChangeEntry(parent_id=info.id, model=config.model)
            thinking = ThinkingLevelChangeEntry(
                parent_id=model.id,
                thinking_level=config.thinking_level,
            )
            entries = [info, model, thinking]
            pending_initial_entries = (info, model, thinking)
        else:
            entries = _detach_missing_parents(entries)

        linear_state = SessionState.from_entries(entries)
        latest_leaf = _latest_leaf_entry(entries)
        state = (
            SessionState.from_entries(entries, leaf_id=latest_leaf.entry_id)
            if latest_leaf is not None
            else linear_state
        )
        resource_paths = resource_paths_with_cwd(config.resource_paths, config.cwd)
        resources = _load_session_resources(
            resource_paths,
            config.context_files,
            skill_paths=config.skill_paths,
            prompt_template_paths=config.prompt_template_paths,
            theme_paths=config.theme_paths,
            extension_paths=config.extension_paths,
            extension_flag_values=config.extension_flag_values,
            discover_skills=config.discover_skills,
            discover_prompt_templates=config.discover_prompt_templates,
            discover_themes=config.discover_themes,
            discover_extensions=config.discover_extensions,
            discover_context_files=config.discover_context_files,
            default_project_trust=config.default_project_trust,
        )
        if resources.extension_provider_configs:
            config = replace(
                config,
                provider_settings=_provider_settings_with_extension_providers(
                    config.provider_settings,
                    resources.extension_provider_configs,
                ),
            )
        bash_environment_provider: dict[str, Callable[[], Mapping[str, str | None]]] = {}

        def current_bash_environment() -> Mapping[str, str | None]:
            provider = bash_environment_provider.get("provider")
            if provider is not None:
                return provider()
            return _bash_session_environment(
                session_id=config.session_id,
                storage=config.storage,
                provider_name=config.provider_name,
                model=state.model or config.model,
                thinking_level=_state_thinking_level(state, config.thinking_level),
            )

        tools = _build_session_tools(
            config,
            extension_tools=resources.extension_tools,
            bash_environment=current_bash_environment,
        )
        system = (
            config.system
            if config.system is not None
            else build_system_prompt(
                BuildSystemPromptOptions(
                    cwd=config.cwd,
                    tools=tools,
                    skills=resources.skills,
                    custom_prompt=config.custom_system_prompt,
                    append_system_prompt=config.append_system_prompt,
                    context_files=resources.context_files,
                )
            )
        )
        harness = AgentHarness(
            AgentHarnessConfig(
                provider=config.provider,
                model=state.model or config.model,
                system=system,
                tools=tools,
                steering_queue_mode=config.steering_queue_mode,
                follow_up_queue_mode=config.follow_up_queue_mode,
            ),
            messages=state.messages,
        )
        base_command_registry = (
            config.command_registry.copy()
            if config.command_registry is not None
            else create_default_command_registry()
        )
        command_registry, command_diagnostics = _command_registry_with_extensions(
            base_command_registry,
            resources.extensions,
        )
        session = cls(
            config,
            state=state,
            harness=harness,
            last_parent_id=_last_parent_id_from_state(state),
            skills=resources.skills,
            prompt_templates=resources.prompt_templates,
            context_files=resources.context_files,
            extensions=resources.extensions,
            resource_diagnostics=(*resources.diagnostics, *command_diagnostics),
            command_registry=command_registry,
            base_command_registry=base_command_registry,
            pending_initial_entries=pending_initial_entries,
        )
        session._sync_thinking_level_to_active_model()
        session._refresh_runtime_provider()
        bash_environment_provider["provider"] = session._bash_session_environment
        await session.emit_extension_event(
            {
                "type": "session_start",
                "reason": config.extension_start_reason,
                "previousSessionFile": config.extension_previous_session_file,
            }
        )
        return session

    @property
    def cwd(self) -> Path:
        """Return the session working directory."""
        return self._config.cwd

    @property
    def model(self) -> str:
        """Return the active model for this session."""
        return self._harness.config.model

    @property
    def provider_name(self) -> str:
        """Return the active provider name."""
        return self._provider_name

    @property
    def available_providers(self) -> tuple[str, ...]:
        """Return provider names Tau can call with available credentials."""
        if self._provider_settings is None:
            return (self._provider_name,)
        return tuple(provider.name for provider in self._usable_provider_configs())

    @property
    def available_models(self) -> tuple[str, ...]:
        """Return model names for the active provider when it is usable."""
        if self._provider_settings is None:
            return (self.model,)
        try:
            provider = self._provider_settings.get_provider(self._provider_name)
        except ProviderConfigError:
            return (self.model,)
        if not self._provider_is_usable(provider):
            return ()
        return provider.models

    @property
    def available_model_choices(self) -> tuple[ModelChoice, ...]:
        """Return provider/model choices Tau can call with available credentials."""
        if self._provider_settings is None:
            return (ModelChoice(provider_name=self._provider_name, model=self.model),)
        return tuple(
            ModelChoice(provider_name=provider.name, model=model)
            for provider in self._usable_provider_configs()
            for model in provider.models
        )

    @property
    def scoped_model_choices(self) -> tuple[ModelChoice, ...]:
        """Return configured quick-switch model choices that are currently usable."""
        if self._provider_settings is None:
            return ()
        available = set(self.available_model_choices)
        return tuple(
            choice
            for choice in (
                ModelChoice(provider_name=item.provider, model=item.model)
                for item in self._provider_settings.scoped_models
            )
            if choice in available
        )

    @property
    def configured_scoped_model_choices(self) -> tuple[ModelChoice, ...]:
        """Return all persisted scoped model choices, including currently unavailable entries."""
        if self._provider_settings is None:
            return ()
        return tuple(
            ModelChoice(provider_name=item.provider, model=item.model)
            for item in self._provider_settings.scoped_models
        )

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        """Return the tools available to the agent."""
        return tuple(self._harness.config.tools)

    @property
    def all_tools(self) -> tuple[AgentTool, ...]:
        """Return the full session tool universe extensions can activate."""
        return tuple(self._available_tools)

    @property
    def active_tool_names(self) -> tuple[str, ...]:
        """Return the active tool names in provider prompt order."""
        return tuple(tool.name for tool in self._harness.config.tools)

    def set_active_tools(self, tool_names: Sequence[str]) -> tuple[str, ...]:
        """Replace the active tool set from the session's available tools."""
        normalized = tuple(str(name).strip() for name in tool_names)
        if any(not name for name in normalized):
            raise ValueError("active tool names must be non-empty")
        duplicate_names = sorted({name for name in normalized if normalized.count(name) > 1})
        if duplicate_names:
            names = ", ".join(duplicate_names)
            raise ValueError(f"Duplicate active tool name(s): {names}")
        available_by_name = {tool.name: tool for tool in self._available_tools}
        missing = [name for name in normalized if name not in available_by_name]
        if missing:
            names = ", ".join(missing)
            available_names = ", ".join(sorted(available_by_name)) or "none"
            raise ValueError(
                f"Unknown active tool name(s): {names}. Available tools: {available_names}"
            )
        self._harness.config.tools = [available_by_name[name] for name in normalized]
        self._refresh_generated_system_prompt()
        return self.active_tool_names

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        """Return the restored/current transcript."""
        return self._harness.messages

    @property
    def session_path(self) -> Path | None:
        """Return the local session file path when this session is file-backed."""
        return _storage_path(self._config.storage)

    @property
    def state(self) -> SessionState:
        """Return the last replayed durable session state."""
        return self._state

    async def tree_choices(self) -> tuple[SessionTreeChoice, ...]:
        """Return branchable session entries for a tree picker."""
        entries = await self._read_session_entries()
        branch_indents = _tree_branch_indents(entries)
        tree_labels = _tree_entry_labels(entries)
        return tuple(
            SessionTreeChoice(
                entry_id=entry.id,
                label=_tree_choice_label(entry, branch_indent=branch_indents.get(entry.id, 0)),
                parent_entry_id=entry.parent_id,
                active=entry.id == self._state.active_leaf_id,
                is_tool_call=_is_tool_call_tree_entry(entry),
                copy_text=_tree_choice_copy_text(entry),
                tree_label=tree_labels.get(entry.id, (None, None))[0],
                tree_label_timestamp=tree_labels.get(entry.id, (None, None))[1],
            )
            for entry in _ordered_tree_entries(entries)
            if _is_branchable_tree_entry(entry)
        )

    async def set_tree_entry_label(self, entry_id: str, label: str | None) -> str:
        """Set or clear a human label on one tree entry."""
        entries = await self._read_session_entries()
        by_id = {entry.id: entry for entry in entries}
        target = by_id.get(entry_id)
        if target is None:
            raise ValueError(f"Unknown session entry: {entry_id}")
        if not _is_branchable_tree_entry(target):
            raise ValueError(f"Session entry cannot be labeled: {entry_id}")

        normalized = (label or "").strip()
        entry = LabelEntry(
            parent_id=self._last_parent_id,
            target_entry_id=entry_id,
            label=normalized,
        )
        await self._append_session_entry(entry)
        action = "Cleared label for" if not normalized else "Labeled"
        return f"{action} tree entry: {entry_id}"

    async def append_custom_entry(
        self,
        namespace: str,
        data: Mapping[str, Any] | None = None,
    ) -> str:
        """Append an extension/application-owned entry to the durable session."""
        normalized = str(namespace).strip()
        if not normalized:
            raise ValueError("custom entry namespace must be non-empty")
        entry_data = dict(data or {})
        entry = CustomEntry(
            parent_id=self._last_parent_id,
            namespace=normalized,
            data=entry_data,
        )
        await self._append_session_entry(entry)
        return entry.id

    async def branch_to_entry(
        self,
        entry_id: str,
        *,
        summarize: bool = False,
        custom_instructions: str | None = None,
        replace_instructions: bool = False,
    ) -> SessionTreeBranchResult:
        """Move the active leaf to a previous entry, preserving existing history."""
        entries = await self._read_session_entries()
        by_id = {entry.id: entry for entry in entries}
        if entry_id not in by_id:
            raise ValueError(f"Unknown session entry: {entry_id}")
        selected_entry = by_id[entry_id]
        if not _is_branchable_tree_entry(selected_entry):
            raise ValueError(f"Session entry cannot be branched from: {entry_id}")
        old_leaf_id = self._state.active_leaf_id
        if await self._emit_extension_before_tree(
            entries,
            target_entry_id=entry_id,
            summarize=summarize,
        ):
            return SessionTreeBranchResult(
                message="Session tree navigation cancelled by extension."
            )

        target_id: str | None = entry_id
        input_prefill: str | None = None
        summary_entry: BranchSummaryEntry | None = None
        if summarize:
            abandoned_messages = _messages_after_entry_on_active_path(
                entries,
                entry_id,
                self._last_parent_id,
            )
            if abandoned_messages:
                summary = await self._summarize_branch_messages(
                    abandoned_messages,
                    custom_instructions=custom_instructions,
                    replace_instructions=replace_instructions,
                )
                summary_entry = BranchSummaryEntry(
                    parent_id=entry_id,
                    branch_root_id=entry_id,
                    summary=summary,
                )
                await self._append_session_entry(summary_entry)
                target_id = summary_entry.id
        elif selected_entry.type == "message" and isinstance(selected_entry.message, UserMessage):
            target_id = selected_entry.parent_id
            input_prefill = selected_entry.message.content

        leaf = LeafEntry(parent_id=target_id, entry_id=target_id)
        await self._append_session_entry(leaf)
        self._last_parent_id = target_id

        await self._refresh_persisted_state(leaf_id=target_id)
        self._harness.replace_messages(self._state.messages)
        self._harness.config.model = self._state.model or self._config.model
        self._thinking_level = _state_thinking_level(self._state, self._config.thinking_level)
        self._sync_thinking_level_to_active_model()
        self._refresh_runtime_provider()
        await self._emit_extension_tree(
            old_leaf_id=old_leaf_id,
            new_leaf_id=target_id,
            summary_entry=summary_entry,
        )
        suffix = " with branch summary" if summary_entry is not None else ""
        if input_prefill is not None:
            return SessionTreeBranchResult(
                message=f"Branched session before {entry_id}.",
                input_prefill=input_prefill,
            )
        return SessionTreeBranchResult(message=f"Branched session at {target_id}{suffix}.")

    @property
    def thinking_level(self) -> ThinkingLevel:
        """Return the active thinking mode for future turns."""
        return self._thinking_level

    @property
    def available_thinking_levels(self) -> tuple[ThinkingLevel, ...]:
        """Return thinking modes supported by the active provider/model."""
        if self._provider_settings is None:
            return THINKING_LEVELS
        provider = self._active_provider_config()
        if provider is None:
            return ()
        return provider_thinking_levels(provider, model=self.model)

    @property
    def thinking_unavailable_reason(self) -> str | None:
        """Return why thinking controls are unavailable for the active model."""
        if self.available_thinking_levels:
            return None
        provider = self._active_provider_config()
        if provider is None:
            return "Active provider settings are not available"
        return provider_thinking_unavailable_reason(provider, model=self.model)

    @property
    def storage(self) -> SessionStorage:
        """Return the backing session storage."""
        return self._config.storage

    async def export(
        self,
        destination: Path | None = None,
        *,
        format: str | None = None,
    ) -> Path:
        """Export the current session to a user-facing artifact."""
        entries = await self._read_session_entries()
        session_path = _storage_path(self._config.storage)
        export_format = normalize_export_format(
            format or (destination.suffix.removeprefix(".") if destination else "html")
        )
        output_path = _resolve_export_destination(
            destination,
            cwd=self.cwd,
            session_path=session_path,
            format=export_format,
        )
        return export_session_artifact(
            entries,
            output_path,
            title=_session_export_title(self),
            source=str(session_path) if session_path is not None else self.session_id,
            format=export_format,
        )

    @property
    def skills(self) -> tuple[Skill, ...]:
        """Return loaded skills."""
        return self._skills

    @property
    def prompt_templates(self) -> tuple[PromptTemplate, ...]:
        """Return loaded prompt templates."""
        return self._prompt_templates

    @property
    def context_files(self) -> tuple[ProjectContextFile, ...]:
        """Return active project context files."""
        return self._context_files

    @property
    def extensions(self) -> tuple[LoadedExtension, ...]:
        """Return loaded Tau extensions."""
        return self._extensions

    @property
    def extension_tool_sources(self) -> dict[str, str]:
        """Return a map from extension tool name to loaded extension name."""
        sources = {
            tool.name: extension.name
            for extension in self._extensions
            for tool in extension.tools
        }
        sources.update(self._runtime_extension_tool_sources)
        return sources

    def register_extension_tool(
        self,
        tool: AgentTool,
        *,
        extension_name: str = "runtime",
    ) -> str:
        """Register an extension tool for future agent turns in this session."""
        if not isinstance(tool, AgentTool):
            raise TypeError("register_extension_tool expects an AgentTool instance")
        tool_name = tool.name.strip()
        if not tool_name:
            raise ValueError("extension tool name must be non-empty")
        if self._config.no_tools:
            raise RuntimeError("Cannot register extension tool while tools are disabled.")
        if self._config.tool_allowlist is not None and tool_name not in self._config.tool_allowlist:
            raise RuntimeError(f"Extension tool is not in the active tool allowlist: {tool_name}")
        if tool_name in self._config.tool_denylist:
            raise RuntimeError(f"Extension tool is denied by active tool settings: {tool_name}")
        if any(existing.name == tool_name for existing in self._harness.config.tools):
            raise ValueError(f"Tool already registered: {tool_name}")
        if any(existing.name == tool_name for existing in self._available_tools):
            raise ValueError(f"Tool already registered: {tool_name}")
        self._available_tools.append(tool)
        self._harness.config.tools.append(tool)
        source = extension_name.strip() or "runtime"
        self._runtime_extension_tool_sources[tool_name] = source
        self._refresh_generated_system_prompt()
        return f"Registered extension tool: {tool_name}"

    def _refresh_generated_system_prompt(self) -> None:
        if self._config.system is not None:
            return
        self._harness.config.system = build_system_prompt(
            BuildSystemPromptOptions(
                cwd=self.cwd,
                tools=self._harness.config.tools,
                skills=self._skills,
                custom_prompt=self._config.custom_system_prompt,
                append_system_prompt=self._config.append_system_prompt,
                context_files=self._context_files,
            )
        )

    @property
    def extension_shortcut_sources(self) -> dict[str, tuple[str, str]]:
        """Return extension shortcut descriptions keyed by normalized key."""
        return {
            shortcut.key: (extension.name, shortcut.description)
            for extension in self._extensions
            for shortcut in extension.shortcuts
        }

    @property
    def extension_entry_renderers(self) -> Mapping[str, Callable[..., object]]:
        """Return Pi-style custom-entry renderers keyed by custom type."""
        renderers: dict[str, Callable[..., object]] = {}
        for extension in self._extensions:
            renderers.update(extension.entry_renderers or {})
        return renderers

    @property
    def extension_message_renderers(self) -> Mapping[str, Callable[..., object]]:
        """Return Pi-style custom-message renderers keyed by custom type."""
        renderers: dict[str, Callable[..., object]] = {}
        for extension in self._extensions:
            renderers.update(extension.message_renderers or {})
        return renderers

    @property
    def system_prompt(self) -> str:
        """Return the active system prompt."""
        return self._harness.config.system

    @property
    def context_token_estimate(self) -> int:
        """Return a rough token estimate for the active provider context."""
        return self.context_usage.total_tokens

    @property
    def context_usage(self) -> ContextUsageEstimate:
        """Return structured context accounting for the active provider context."""
        return estimate_context_usage(
            system=self._harness.config.system,
            messages=self._harness.messages,
            tools=tuple(self._harness.config.tools),
        )

    @property
    def auto_compact_token_threshold(self) -> int | None:
        """Return the effective automatic compaction threshold, if any."""
        if not self._auto_compact_enabled:
            return None
        if self._auto_compact_token_threshold is not None:
            return self._auto_compact_token_threshold
        return auto_compaction_threshold_for_context_window(self.context_window_tokens)

    def set_auto_compact_enabled(self, enabled: bool) -> str:
        """Enable or disable automatic compaction for this running session."""
        self._auto_compact_enabled = enabled
        state = "enabled" if enabled else "disabled"
        return f"Auto-compact {state}."

    def set_shell_command_prefix(self, prefix: str | None) -> str:
        """Set the shell snippet prepended to future input-bar terminal commands."""
        self._shell_command_prefix = prefix
        self._config = replace(self._config, shell_command_prefix=prefix)
        state = "configured" if prefix else "cleared"
        return f"Shell command prefix {state}."

    def set_shell_path(self, path: str | None) -> str:
        """Set the shell executable used for future bash tool calls."""
        self._shell_path = path
        self._config = replace(self._config, shell_path=path)
        state = "configured" if path else "cleared"
        return f"Shell path {state}."

    @property
    def auto_resize_images(self) -> bool:
        """Return whether Tau resizes large image reads for provider compatibility."""
        return self._config.auto_resize_images

    def set_auto_resize_images(self, enabled: bool) -> str:
        """Set whether future default read-tool calls resize oversized images."""
        self._config = replace(self._config, auto_resize_images=enabled)
        if self._config.tools is None:
            self._harness.config.tools = _build_session_tools(
                self._config,
                bash_environment=self._bash_session_environment,
            )
        state = "enabled" if enabled else "disabled"
        return f"Auto-resize images {state}."

    @property
    def steering_queue_mode(self) -> QueueMode:
        """Return how queued steering messages drain into the agent loop."""
        return self._harness.steering_queue_mode

    def set_steering_queue_mode(self, mode: QueueMode) -> str:
        """Set how queued steering messages drain into the agent loop."""
        self._config = replace(self._config, steering_queue_mode=mode)
        self._harness.set_steering_queue_mode(mode)
        return f"Steering mode: {_display_queue_mode(mode)}."

    @property
    def follow_up_queue_mode(self) -> QueueMode:
        """Return how queued follow-up messages drain into the agent loop."""
        return self._harness.follow_up_queue_mode

    def set_follow_up_queue_mode(self, mode: QueueMode) -> str:
        """Set how queued follow-up messages drain into the agent loop."""
        self._config = replace(self._config, follow_up_queue_mode=mode)
        self._harness.set_follow_up_queue_mode(mode)
        return f"Follow-up mode: {_display_queue_mode(mode)}."

    def set_provider_timeout_seconds(self, timeout_seconds: float) -> str:
        """Set the active provider timeout for future model calls."""
        if timeout_seconds <= 0:
            raise ProviderConfigError("Provider timeout override must be greater than 0")
        if self._provider_timeout_override_seconds == timeout_seconds:
            return f"Provider timeout: {timeout_seconds:g}s."
        self._provider_timeout_override_seconds = timeout_seconds
        self._refresh_runtime_provider()
        return f"Provider timeout: {timeout_seconds:g}s."

    @property
    def context_window_tokens(self) -> int:
        """Return the active model's configured context window, or Tau's fallback."""
        provider = self._active_provider_config()
        if provider is None:
            return DEFAULT_CONTEXT_WINDOW_TOKENS
        return provider.context_windows.get(self.model, DEFAULT_CONTEXT_WINDOW_TOKENS)

    @property
    def command_registry(self) -> CommandRegistry:
        """Return the slash-command registry used by this session."""
        return self._command_registry

    @property
    def resource_diagnostics(self) -> tuple[ResourceDiagnostic, ...]:
        """Return non-fatal resource discovery diagnostics."""
        return self._resource_diagnostics

    async def emit_extension_event(self, event: Mapping[str, object]) -> tuple[object, ...]:
        """Emit a bounded Pi-style extension lifecycle event to registered handlers."""
        event_type = str(event.get("type") or "").strip()
        if not event_type:
            raise ValueError("extension event requires a non-empty type")
        results: list[object] = []
        diagnostics: list[ResourceDiagnostic] = []
        for extension in self._extensions:
            handlers = tuple((extension.event_handlers or {}).get(event_type, ()))
            if not handlers:
                continue
            context = ExtensionShortcutContext(
                session=self,
                key="",
                extension_name=extension.name,
            )
            for handler in handlers:
                try:
                    result = _call_extension_lifecycle_handler(handler, event, context)
                    if inspect.isawaitable(result):
                        result = await result
                    if result is not None:
                        results.append(result)
                except Exception as exc:  # noqa: BLE001 - extensions are isolated plugins
                    diagnostics.append(
                        ResourceDiagnostic(
                            kind="extension",
                            name=extension.name,
                            path=extension.path,
                            message=(
                                f"{event_type} handler failed: "
                                f"{type(exc).__name__}: {exc}"
                            ),
                            severity="error",
                        )
                    )
        if diagnostics:
            self._resource_diagnostics = (*self._resource_diagnostics, *diagnostics)
        return tuple(results)

    @property
    def session_id(self) -> str | None:
        """Return this session's manager id, if indexed."""
        return self._config.session_id

    @property
    def session_title(self) -> str | None:
        """Return this session's indexed human-friendly title, if named."""
        if self._config.session_id is None or self._config.session_manager is None:
            return None
        record = self._config.session_manager.get_session(self._config.session_id)
        if record is None:
            return None
        return record.title

    def set_session_title(self, title: str) -> str:
        """Set the indexed human-friendly title for this session."""
        if self._config.session_id is None or self._config.session_manager is None:
            raise RuntimeError("Session naming requires an indexed session.")
        normalized = str(title).strip()
        if not normalized:
            raise ValueError("Session name must be non-empty.")
        if any(char in normalized for char in "\r\n\t"):
            raise ValueError("Session name must be a single line.")
        updated = self._config.session_manager.touch_session(
            self._config.session_id,
            model=self.model,
            provider_name=self.provider_name,
            title=normalized,
        )
        if updated is None:
            raise RuntimeError(f"Unknown current session: {self._config.session_id}")
        return updated.title or normalized

    @property
    def session_manager(self) -> SessionManager | None:
        """Return the session manager, if available."""
        return self._config.session_manager

    @property
    def is_running(self) -> bool:
        """Return whether this session currently has an active agent run."""
        return self._harness.is_running

    @property
    def queued_messages(self) -> QueuedMessages:
        """Return queued steering and follow-up messages."""
        return self._harness.queued_messages

    @property
    def queued_steering_messages(self) -> tuple[str, ...]:
        """Return queued steering message text for UI display."""
        return tuple(message.content for message in self._harness.queued_messages.steering)

    @property
    def queued_follow_up_messages(self) -> tuple[str, ...]:
        """Return queued follow-up message text for UI display."""
        return tuple(message.content for message in self._harness.queued_messages.follow_up)

    @property
    def last_diagnostic_log_path(self) -> Path | None:
        """Return the last diagnostic log path written by this session."""
        return self._last_diagnostic_log_path

    def _bash_session_environment(self) -> Mapping[str, str | None]:
        return _bash_session_environment(
            session_id=self.session_id,
            storage=self._config.storage,
            provider_name=self.provider_name,
            model=self.model,
            thinking_level=self.thinking_level,
        )

    def cancel(self) -> None:
        """Cancel the currently running agent turn, if any."""
        self._harness.cancel()

    def cancel_terminal_command(self) -> None:
        """Cancel the currently running input-bar terminal command, if any."""
        if self._terminal_signal is not None:
            self._terminal_signal.cancel()

    def queue_update_event(self) -> QueueUpdateEvent:
        """Return the current queue state as an agent event."""
        return self._harness.queue_update_event()

    def clear_queued_messages(self) -> QueuedMessages:
        """Clear queued steering and follow-up messages."""
        return self._harness.clear_queues()

    def pop_latest_follow_up_message(self) -> str | None:
        """Remove and return the most recently queued follow-up message."""
        message = self._harness.pop_latest_follow_up()
        return None if message is None else message.content

    def set_model(self, model: str) -> object | None:
        """Switch the active model for future turns and make it the default."""
        return self._set_model(model, source="set")

    def _set_model(
        self,
        model: str,
        *,
        source: ModelSelectSource,
        previous_choice: ModelChoice | None = None,
    ) -> object | None:
        previous = previous_choice or ModelChoice(
            provider_name=self.provider_name,
            model=self.model,
        )
        self._harness.config.model = model
        self._sync_thinking_level_to_active_model()
        self._refresh_runtime_provider()
        self._persist_default_model_choice()
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(
                self._config.session_id,
                model=model,
                provider_name=self.provider_name,
            )
        return self._emit_extension_model_select(
            ModelChoice(provider_name=self.provider_name, model=model),
            previous_choice=previous,
            source=source,
        )

    def set_model_choice(
        self,
        choice: ModelChoice,
        *,
        source: ModelSelectSource = "set",
    ) -> object | None:
        """Switch provider/model as one operation."""
        previous = ModelChoice(provider_name=self.provider_name, model=self.model)
        if choice.provider_name != self.provider_name:
            self.set_provider(choice.provider_name, emit_model_select=False)
        return self._set_model(choice.model, previous_choice=previous, source=source)

    def is_scoped_model(self, choice: ModelChoice) -> bool:
        """Return whether a provider/model pair is in the scoped model list."""
        return choice in self.scoped_model_choices

    def toggle_scoped_model(self, choice: ModelChoice) -> tuple[ModelChoice, ...]:
        """Add or remove a model from the persisted scoped model list."""
        if self._provider_settings is None:
            raise ProviderConfigError("Provider settings are not available for this session")
        available = set(self.available_model_choices)
        if choice not in available:
            raise ProviderConfigError(
                f"Model is not available: {choice.provider_name}:{choice.model}"
            )

        self._provider_settings = toggle_saved_scoped_model(
            provider_name=choice.provider_name,
            model=choice.model,
            paths=self._resource_paths.paths,
            fallback_settings=self._provider_settings,
        )
        self._sync_thinking_level_to_active_model()
        return self.configured_scoped_model_choices

    def set_scoped_models(self, choices: Sequence[ModelChoice]) -> tuple[ModelChoice, ...]:
        """Replace the persisted scoped model list."""
        if self._provider_settings is None:
            raise ProviderConfigError("Provider settings are not available for this session")
        self._provider_settings = set_saved_scoped_models(
            tuple(
                ScopedModelConfig(provider=choice.provider_name, model=choice.model)
                for choice in choices
            ),
            paths=self._resource_paths.paths,
            fallback_settings=self._provider_settings,
        )
        self._sync_thinking_level_to_active_model()
        return self.configured_scoped_model_choices

    def cycle_scoped_model(self, *, reverse: bool = False) -> ModelChoice:
        """Switch to the next configured scoped model."""
        scoped = self.scoped_model_choices
        if not scoped:
            raise ProviderConfigError("No scoped models configured.")
        current = ModelChoice(provider_name=self.provider_name, model=self.model)
        try:
            current_index = scoped.index(current)
        except ValueError:
            current_index = -1 if not reverse else 0
        delta = -1 if reverse else 1
        choice = scoped[(current_index + delta) % len(scoped)]
        self.set_model_choice(choice, source="cycle")
        return choice

    def set_provider(
        self,
        provider_name: str,
        *,
        persist_default: bool = True,
        emit_model_select: bool = True,
        source: ModelSelectSource = "set",
    ) -> object | None:
        """Switch the active provider and reset to that provider's default model."""
        if self._provider_settings is None:
            raise ProviderConfigError("Provider settings are not available for this session")

        previous = ModelChoice(provider_name=self.provider_name, model=self.model)
        provider_config = self._provider_settings.get_provider(provider_name)
        model = provider_config.default_model
        thinking_level = _coerced_thinking_level(
            provider_config,
            model=model,
            current=self._thinking_level,
        )
        try:
            provider = create_model_provider(
                provider_config,
                credential_store=self._credential_store,
                model=model,
                thinking_level=thinking_level,
            )
        except RuntimeError as exc:
            raise ProviderConfigError(str(exc)) from exc
        self._owned_providers.append(provider)
        self._install_extension_provider_adapter(provider)
        self._provider_name = provider_config.name
        self._runtime_provider_config = provider_config
        self._harness.config.model = model
        self._thinking_level = thinking_level
        if persist_default:
            self._persist_default_model_choice()
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(
                self._config.session_id,
                model=model,
                provider_name=self.provider_name,
            )
        if emit_model_select:
            return self._emit_extension_model_select(
                ModelChoice(provider_name=self.provider_name, model=model),
                previous_choice=previous,
                source=source,
            )
        return None

    async def set_thinking_level(self, level: str) -> str:
        """Persist and activate a thinking mode for future turns."""
        normalized = normalize_thinking_level(level)
        available = self.available_thinking_levels
        if not available:
            raise ValueError(_unavailable_thinking_message(self))
        if normalized not in available:
            modes = ", ".join(available)
            raise ValueError(
                f"Thinking mode {normalized} is not available for "
                f"{self._provider_name}:{self.model}. Available modes: {modes}"
            )
        if normalized == self._thinking_level:
            return f"Thinking mode: {normalized}"

        previous = self._thinking_level
        self._thinking_level = normalized
        try:
            self._refresh_runtime_provider()
        except ProviderConfigError:
            self._thinking_level = previous
            raise

        entry = ThinkingLevelChangeEntry(
            parent_id=self._last_parent_id,
            thinking_level=normalized,
        )
        await self._append_session_entry(entry)
        leaf = LeafEntry(parent_id=entry.id, entry_id=entry.id)
        await self._append_session_entry(leaf)
        self._last_parent_id = entry.id

        await self._refresh_persisted_state(leaf_id=entry.id)
        await self.emit_extension_event(
            {
                "type": "thinking_level_select",
                "level": normalized,
                "previousLevel": previous,
            }
        )
        return f"Thinking mode: {normalized}"

    async def cycle_thinking_level(self) -> str:
        """Cycle to the next supported thinking mode and persist it."""
        return await self.set_thinking_level(
            next_thinking_level(
                self._thinking_level,
                available=self.available_thinking_levels,
            )
        )

    def _active_provider_config(self) -> ProviderConfig | None:
        if self._provider_settings is None:
            return None
        try:
            return self._provider_settings.get_provider(self._provider_name)
        except ProviderConfigError:
            return None

    def _sync_thinking_level_to_active_model(self) -> None:
        provider = self._active_provider_config()
        if provider is None:
            return
        self._thinking_level = _coerced_thinking_level(
            provider,
            model=self.model,
            current=self._thinking_level,
        )

    def _persist_default_model_choice(self) -> None:
        if self._provider_settings is None:
            return
        self._provider_settings = save_default_provider_model(
            provider_name=self.provider_name,
            model=self.model,
            paths=self._resource_paths.paths,
            fallback_settings=self._provider_settings,
        )
        self._sync_thinking_level_to_active_model()

    def _refresh_runtime_provider(self) -> None:
        if self._runtime_provider_config is None:
            return
        provider_config = self._active_provider_config() or self._runtime_provider_config
        if self._provider_timeout_override_seconds is not None:
            provider_config = _provider_config_with_timeout(
                provider_config,
                timeout_seconds=self._provider_timeout_override_seconds,
            )
        try:
            provider = create_model_provider(
                provider_config,
                credential_store=self._credential_store,
                model=self.model,
                thinking_level=self._thinking_level,
            )
        except RuntimeError as exc:
            raise ProviderConfigError(str(exc)) from exc
        self._owned_providers.append(provider)
        self._install_extension_provider_adapter(provider)
        self._runtime_provider_config = provider_config

    def reload(self) -> CodingReloadSummary:
        """Reload local coding resources and project context for future turns."""
        before_skills = _skill_signatures(self._skills)
        before_prompt_templates = _prompt_template_signatures(self._prompt_templates)
        before_context_files = _context_file_signatures(self._context_files)
        before_diagnostics = _diagnostic_signatures(self._resource_diagnostics)
        before_system_prompt_inputs = _system_prompt_resource_signatures(
            skills=self._skills,
            context_files=self._context_files,
            tools=self._harness.config.tools,
        )

        resources = _load_session_resources(
            self._resource_paths,
            self._config.context_files,
            skill_paths=self._config.skill_paths,
            prompt_template_paths=self._config.prompt_template_paths,
            theme_paths=self._config.theme_paths,
            extension_paths=self._config.extension_paths,
            discover_skills=self._config.discover_skills,
            discover_prompt_templates=self._config.discover_prompt_templates,
            discover_themes=self._config.discover_themes,
            discover_extensions=self._config.discover_extensions,
            discover_context_files=self._config.discover_context_files,
            default_project_trust=self._config.default_project_trust,
        )
        tools = _build_session_tools(
            self._config,
            extension_tools=resources.extension_tools,
            bash_environment=self._bash_session_environment,
        )

        after_skills = _skill_signatures(resources.skills)
        after_prompt_templates = _prompt_template_signatures(resources.prompt_templates)
        after_context_files = _context_file_signatures(resources.context_files)
        command_registry, command_diagnostics = _command_registry_with_extensions(
            self._base_command_registry,
            resources.extensions,
        )
        resource_diagnostics = (*resources.diagnostics, *command_diagnostics)
        after_diagnostics = _diagnostic_signatures(resource_diagnostics)
        after_system_prompt_inputs = _system_prompt_resource_signatures(
            skills=resources.skills,
            context_files=resources.context_files,
            tools=tools,
        )

        rebuilt_system_prompt: str | None = None
        system_prompt_rebuilt = False
        if (
            self._config.system is None
            and before_system_prompt_inputs != after_system_prompt_inputs
        ):
            rebuilt_system_prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    cwd=self._config.cwd,
                    tools=tools,
                    skills=resources.skills,
                    custom_prompt=self._config.custom_system_prompt,
                    append_system_prompt=self._config.append_system_prompt,
                    context_files=resources.context_files,
                )
            )
            system_prompt_rebuilt = True

        self._available_tools = list(tools)
        self._harness.config.tools = tools
        self._skills = resources.skills
        self._prompt_templates = resources.prompt_templates
        self._context_files = resources.context_files
        self._extensions = resources.extensions
        self._resource_diagnostics = resource_diagnostics
        self._command_registry = command_registry
        if rebuilt_system_prompt is not None:
            self._harness.config.system = rebuilt_system_prompt

        return CodingReloadSummary(
            skills=_category_summary(before_skills, after_skills),
            prompt_templates=_category_summary(
                before_prompt_templates,
                after_prompt_templates,
            ),
            context_files=_category_summary(before_context_files, after_context_files),
            diagnostics=_category_summary(before_diagnostics, after_diagnostics),
            system_prompt_rebuilt=system_prompt_rebuilt,
        )

    def reload_provider_settings(self) -> None:
        """Reload provider settings for login and model-selection flows."""
        if self._provider_settings is None:
            return
        previous_settings = self._provider_settings
        previous_thinking_level = self._thinking_level
        self._provider_settings = load_provider_settings(self._resource_paths.paths)
        try:
            self._sync_thinking_level_to_active_model()
            self._refresh_runtime_provider()
        except ProviderConfigError:
            self._provider_settings = previous_settings
            self._thinking_level = previous_thinking_level
            raise

    async def resume(self, session_id: str) -> str:
        """Replace this session's active state with another indexed session."""
        manager = self._config.session_manager
        if manager is None:
            raise ValueError("Session manager is not available")
        record = manager.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")

        provider_name = self._provider_name
        runtime_provider_config = self._runtime_provider_config
        if record.provider_name:
            if self._provider_settings is None:
                raise ValueError(
                    "Cannot resume session provider without provider settings: "
                    f"{record.provider_name}"
                )
            try:
                runtime_provider_config = self._provider_settings.get_provider(record.provider_name)
            except ProviderConfigError as exc:
                raise ValueError(
                    f"Session provider is not configured: {record.provider_name}"
                ) from exc
            provider_name = runtime_provider_config.name

        previous_session_file = _session_storage_path(self._config.storage)
        if await self._emit_extension_before_switch(
            reason="resume",
            target_session_file=str(record.path),
        ):
            return "Session resume cancelled by extension."
        await self.emit_extension_event(
            {
                "type": "session_shutdown",
                "reason": "resume",
                "targetSessionFile": str(record.path),
            }
        )
        replacement = await type(self).load(
            CodingSessionConfig(
                provider=self._harness.config.provider,
                model=record.model or self.model,
                cwd=record.cwd,
                storage=jsonl_session_storage(record.path),
                system=self._config.system,
                custom_system_prompt=self._config.custom_system_prompt,
                append_system_prompt=self._config.append_system_prompt,
                context_files=self._config.context_files,
                skill_paths=self._config.skill_paths,
                prompt_template_paths=self._config.prompt_template_paths,
                theme_paths=self._config.theme_paths,
                resource_paths=self._config.resource_paths,
                session_id=record.id,
                session_manager=manager,
                command_registry=self._command_registry,
                provider_name=provider_name,
                provider_settings=self._provider_settings,
                runtime_provider_config=runtime_provider_config,
                auto_compact_token_threshold=self._auto_compact_token_threshold,
                auto_compact_enabled=self._auto_compact_enabled,
                steering_queue_mode=self.steering_queue_mode,
                follow_up_queue_mode=self.follow_up_queue_mode,
                default_project_trust=self._config.default_project_trust,
                thinking_level=self._thinking_level,
                shell_path=self._shell_path,
                shell_command_prefix=self._shell_command_prefix,
                auto_resize_images=self._config.auto_resize_images,
                extension_start_reason="resume",
                extension_previous_session_file=previous_session_file,
                discover_skills=self._config.discover_skills,
                discover_prompt_templates=self._config.discover_prompt_templates,
                discover_themes=self._config.discover_themes,
                discover_context_files=self._config.discover_context_files,
                tool_allowlist=self._config.tool_allowlist,
                tool_denylist=self._config.tool_denylist,
                no_tools=self._config.no_tools,
                no_builtin_tools=self._config.no_builtin_tools,
            )
        )
        self._config = replacement._config
        self._state = replacement._state
        self._harness = replacement._harness
        self._last_parent_id = replacement._last_parent_id
        self._skills = replacement._skills
        self._prompt_templates = replacement._prompt_templates
        self._context_files = replacement._context_files
        self._extensions = replacement._extensions
        self._available_tools = replacement._available_tools
        self._resource_diagnostics = replacement._resource_diagnostics
        self._command_registry = replacement._command_registry
        self._provider_name = replacement._provider_name
        self._provider_settings = replacement._provider_settings
        self._runtime_provider_config = replacement._runtime_provider_config
        self._resource_paths = replacement._resource_paths
        self._auto_compact_token_threshold = replacement._auto_compact_token_threshold
        self._auto_compact_enabled = replacement._auto_compact_enabled
        self._thinking_level = replacement._thinking_level
        return f"Resumed session: {record.id}"

    async def new_session(self) -> str:
        """Replace this session's active state with a newly indexed session."""
        manager = self._config.session_manager
        if manager is None:
            raise ValueError("Session manager is not available")

        provider_name = self._provider_name
        model = self.model
        runtime_provider_config = self._runtime_provider_config
        thinking_level = self._thinking_level
        if self._provider_settings is not None:
            selection = resolve_provider_selection(self._provider_settings)
            provider_name = selection.provider.name
            model = selection.model
            runtime_provider_config = selection.provider
            thinking_level = _coerced_thinking_level(
                selection.provider,
                model=model,
                current=self._thinking_level,
            )

        if await self._emit_extension_before_switch(reason="new"):
            return "New session cancelled by extension."
        record = manager.create_session(
            cwd=self.cwd,
            model=model,
            provider_name=provider_name,
        )
        previous_session_file = _session_storage_path(self._config.storage)
        await self.emit_extension_event(
            {
                "type": "session_shutdown",
                "reason": "new",
                "targetSessionFile": str(record.path),
            }
        )
        replacement = await type(self).load(
            replace(
                self._config,
                provider=self._harness.config.provider,
                model=record.model or model,
                cwd=record.cwd,
                storage=jsonl_session_storage(record.path),
                session_id=record.id,
                provider_name=provider_name,
                provider_settings=self._provider_settings,
                runtime_provider_config=runtime_provider_config,
                thinking_level=thinking_level,
                extension_start_reason="new",
                extension_previous_session_file=previous_session_file,
            )
        )
        self._config = replacement._config
        self._state = replacement._state
        self._harness = replacement._harness
        self._last_parent_id = replacement._last_parent_id
        self._skills = replacement._skills
        self._prompt_templates = replacement._prompt_templates
        self._context_files = replacement._context_files
        self._extensions = replacement._extensions
        self._available_tools = replacement._available_tools
        self._resource_diagnostics = replacement._resource_diagnostics
        self._command_registry = replacement._command_registry
        self._provider_name = replacement._provider_name
        self._provider_settings = replacement._provider_settings
        self._runtime_provider_config = replacement._runtime_provider_config
        self._resource_paths = replacement._resource_paths
        self._auto_compact_token_threshold = replacement._auto_compact_token_threshold
        self._auto_compact_enabled = replacement._auto_compact_enabled
        self._thinking_level = replacement._thinking_level
        return f"Started new session: {record.id}"

    async def clone_current_session(self) -> str:
        """Clone the current active path into a newly indexed session and resume it."""
        manager = self._config.session_manager
        if manager is None:
            raise ValueError("Session manager is not available")
        active_leaf_id = self._state.active_leaf_id
        if active_leaf_id is None:
            raise ValueError("Nothing to clone yet")

        entries = await self._read_session_entries()
        try:
            active_path = path_to_entry(entries, active_leaf_id)
        except SessionTreeError as exc:
            raise ValueError(f"Cannot clone current session: {exc}") from exc
        if not any(_is_branchable_tree_entry(entry) for entry in active_path):
            raise ValueError("Nothing to clone yet")

        if await self._emit_extension_before_fork(active_leaf_id, position="at"):
            return "Session fork cancelled by extension."
        title = self.session_title
        provider_name = self._provider_name
        model = self.model
        record = manager.create_session(
            cwd=self.cwd,
            model=model,
            provider_name=provider_name,
            title=f"Clone of {title}" if title else None,
            parent_session_id=self._config.session_id,
        )
        storage = jsonl_session_storage(record.path)
        for entry in active_path:
            await storage.append(entry)
        await storage.append(LeafEntry(parent_id=active_leaf_id, entry_id=active_leaf_id))

        previous_session_file = _session_storage_path(self._config.storage)
        await self.emit_extension_event(
            {
                "type": "session_shutdown",
                "reason": "fork",
                "targetSessionFile": str(record.path),
            }
        )
        replacement = await type(self).load(
            replace(
                self._config,
                provider=self._harness.config.provider,
                model=record.model or model,
                cwd=record.cwd,
                storage=storage,
                session_id=record.id,
                provider_name=provider_name,
                provider_settings=self._provider_settings,
                runtime_provider_config=self._runtime_provider_config,
                thinking_level=self._thinking_level,
                extension_start_reason="fork",
                extension_previous_session_file=previous_session_file,
            )
        )
        self._config = replacement._config
        self._state = replacement._state
        self._harness = replacement._harness
        self._last_parent_id = replacement._last_parent_id
        self._skills = replacement._skills
        self._prompt_templates = replacement._prompt_templates
        self._context_files = replacement._context_files
        self._extensions = replacement._extensions
        self._available_tools = replacement._available_tools
        self._resource_diagnostics = replacement._resource_diagnostics
        self._command_registry = replacement._command_registry
        self._provider_name = replacement._provider_name
        self._provider_settings = replacement._provider_settings
        self._runtime_provider_config = replacement._runtime_provider_config
        self._resource_paths = replacement._resource_paths
        self._auto_compact_token_threshold = replacement._auto_compact_token_threshold
        self._auto_compact_enabled = replacement._auto_compact_enabled
        self._thinking_level = replacement._thinking_level
        return f"Cloned to new session: {record.id}"

    async def fork_from_entry(
        self,
        entry_id: str,
        *,
        position: Literal["before", "at"] | None = None,
    ) -> SessionTreeBranchResult:
        """Fork a newly indexed session from a selected tree entry and resume it."""
        manager = self._config.session_manager
        if manager is None:
            raise ValueError("Session manager is not available")
        if position not in {None, "before", "at"}:
            raise ValueError("fork position must be 'before' or 'at'")

        entries = await self._read_session_entries()
        by_id = {entry.id: entry for entry in entries}
        selected_entry = by_id.get(entry_id)
        if selected_entry is None:
            raise ValueError(f"Unknown session entry: {entry_id}")
        if not _is_branchable_tree_entry(selected_entry):
            raise ValueError(f"Session entry cannot be forked from: {entry_id}")

        target_id: str | None = entry_id
        input_prefill: str | None = None
        if position == "before" or (
            position is None
            and selected_entry.type == "message"
            and isinstance(selected_entry.message, UserMessage)
        ):
            target_id = selected_entry.parent_id
            if selected_entry.type == "message" and isinstance(selected_entry.message, UserMessage):
                input_prefill = selected_entry.message.content

        event_position: Literal["before", "at"] = "before" if input_prefill is not None else "at"
        if await self._emit_extension_before_fork(entry_id, position=event_position):
            return SessionTreeBranchResult(message="Session fork cancelled by extension.")

        if target_id is None:
            active_path: list[SessionEntry] = []
        else:
            try:
                active_path = path_to_entry(entries, target_id)
            except SessionTreeError as exc:
                raise ValueError(f"Cannot fork session: {exc}") from exc

        title = self.session_title
        record = manager.create_session(
            cwd=self.cwd,
            model=self.model,
            provider_name=self._provider_name,
            title=f"Fork of {title}" if title else None,
            parent_session_id=self._config.session_id,
        )
        storage = jsonl_session_storage(record.path)
        for entry in active_path:
            await storage.append(entry)
        if target_id is not None:
            await storage.append(LeafEntry(parent_id=target_id, entry_id=target_id))

        previous_session_file = _session_storage_path(self._config.storage)
        await self.emit_extension_event(
            {
                "type": "session_shutdown",
                "reason": "fork",
                "targetSessionFile": str(record.path),
            }
        )
        replacement = await type(self).load(
            replace(
                self._config,
                provider=self._harness.config.provider,
                model=record.model or self.model,
                cwd=record.cwd,
                storage=storage,
                session_id=record.id,
                provider_name=self._provider_name,
                provider_settings=self._provider_settings,
                runtime_provider_config=self._runtime_provider_config,
                thinking_level=self._thinking_level,
                extension_start_reason="fork",
                extension_previous_session_file=previous_session_file,
            )
        )
        self._config = replacement._config
        self._state = replacement._state
        self._harness = replacement._harness
        self._last_parent_id = replacement._last_parent_id
        self._skills = replacement._skills
        self._prompt_templates = replacement._prompt_templates
        self._context_files = replacement._context_files
        self._extensions = replacement._extensions
        self._available_tools = replacement._available_tools
        self._resource_diagnostics = replacement._resource_diagnostics
        self._command_registry = replacement._command_registry
        self._provider_name = replacement._provider_name
        self._provider_settings = replacement._provider_settings
        self._runtime_provider_config = replacement._runtime_provider_config
        self._resource_paths = replacement._resource_paths
        self._auto_compact_token_threshold = replacement._auto_compact_token_threshold
        self._auto_compact_enabled = replacement._auto_compact_enabled
        self._thinking_level = replacement._thinking_level

        if input_prefill is not None:
            return SessionTreeBranchResult(
                message=f"Forked session before {entry_id}: {record.id}.",
                input_prefill=input_prefill,
            )
        return SessionTreeBranchResult(message=f"Forked session at {target_id}: {record.id}.")

    async def import_session(self, path: Path) -> str:
        """Import a JSONL session artifact into a newly indexed session and resume it."""
        manager = self._config.session_manager
        if manager is None:
            raise ValueError("Session manager is not available")

        source_path = path.expanduser()
        if not source_path.is_absolute():
            source_path = self.cwd / source_path
        if not source_path.exists():
            raise ValueError(f"Import file does not exist: {source_path}")
        if not source_path.is_file():
            raise ValueError(f"Import path is not a file: {source_path}")

        source_storage = jsonl_session_storage(source_path)
        entries = await source_storage.read_all()
        if not entries:
            raise ValueError(f"Import file has no session entries: {source_path}")

        state = SessionState.from_entries(_detach_missing_parents(entries))
        model = state.model or self.model
        if await self._emit_extension_before_switch(
            reason="resume",
            target_session_file=str(source_path),
        ):
            return "Session import cancelled by extension."
        record = manager.create_session(
            cwd=self.cwd,
            model=model,
            provider_name=self._provider_name,
            title=f"Imported {source_path.name}",
        )
        storage = jsonl_session_storage(record.path)
        for entry in entries:
            await storage.append(entry)

        previous_session_file = _session_storage_path(self._config.storage)
        await self.emit_extension_event(
            {
                "type": "session_shutdown",
                "reason": "resume",
                "targetSessionFile": str(record.path),
            }
        )
        replacement = await type(self).load(
            replace(
                self._config,
                provider=self._harness.config.provider,
                model=record.model or model,
                cwd=record.cwd,
                storage=storage,
                session_id=record.id,
                provider_name=self._provider_name,
                provider_settings=self._provider_settings,
                runtime_provider_config=self._runtime_provider_config,
                thinking_level=self._thinking_level,
                extension_start_reason="resume",
                extension_previous_session_file=previous_session_file,
            )
        )
        self._config = replacement._config
        self._state = replacement._state
        self._harness = replacement._harness
        self._last_parent_id = replacement._last_parent_id
        self._skills = replacement._skills
        self._prompt_templates = replacement._prompt_templates
        self._context_files = replacement._context_files
        self._extensions = replacement._extensions
        self._available_tools = replacement._available_tools
        self._resource_diagnostics = replacement._resource_diagnostics
        self._command_registry = replacement._command_registry
        self._provider_name = replacement._provider_name
        self._provider_settings = replacement._provider_settings
        self._runtime_provider_config = replacement._runtime_provider_config
        self._resource_paths = replacement._resource_paths
        self._auto_compact_token_threshold = replacement._auto_compact_token_threshold
        self._auto_compact_enabled = replacement._auto_compact_enabled
        self._thinking_level = replacement._thinking_level
        return f"Imported session: {record.id}"

    async def share(self) -> str:
        """Export the current session and share it as a secret GitHub gist."""
        if shutil.which("gh") is None:
            raise ValueError("GitHub CLI (gh) is not installed.")

        auth = await asyncio.to_thread(
            subprocess.run,
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
        if auth.returncode != 0:
            detail = (auth.stderr or auth.stdout).strip() or "Run 'gh auth login' first."
            raise ValueError(f"GitHub CLI is not logged in: {detail}")

        with tempfile.TemporaryDirectory(prefix="tau-share-") as temp_dir:
            html_path = Path(temp_dir) / "session.html"
            await self.export(html_path, format="html")
            gist = await asyncio.to_thread(
                subprocess.run,
                ["gh", "gist", "create", "--public=false", str(html_path)],
                capture_output=True,
                text=True,
                check=False,
            )

        if gist.returncode != 0:
            detail = (gist.stderr or gist.stdout).strip() or "Unknown error"
            raise ValueError(f"Failed to create gist: {detail}")

        gist_url = gist.stdout.strip().splitlines()[-1] if gist.stdout.strip() else ""
        gist_id = gist_url.rstrip("/").split("/")[-1] if gist_url else ""
        if not gist_id:
            raise ValueError("Failed to parse gist ID from GitHub CLI output.")

        viewer_url = _share_viewer_url(gist_id)
        return f"Share URL: {viewer_url}\nGist: {gist_url}"

    def project_trust_state(self) -> ProjectTrustState:
        """Return project trust state for the active cwd."""
        store = ProjectTrustStore.from_resource_paths(self._resource_paths)
        return project_trust_state(self.cwd, store)

    def save_project_trust(self, option: ProjectTrustOption) -> str:
        """Persist one selected project trust option."""
        store = ProjectTrustStore.from_resource_paths(self._resource_paths)
        store.set_many(option.updates)
        decision = "trusted" if option.trusted else "untrusted"
        return f"Saved trust decision: {decision}. Restart Tau for this to take effect."

    def set_default_project_trust(self, default_project_trust: DefaultProjectTrust) -> None:
        """Update the fallback project trust policy used by future resource reloads."""
        self._config = replace(self._config, default_project_trust=default_project_trust)

    async def compact(self, instructions: str | None = None) -> str:
        """Generate a manual compaction summary and rebuild active context."""
        plan = self._manual_compaction_plan()
        if await self._emit_extension_before_compact(
            plan,
            custom_instructions=instructions,
            reason="manual",
            will_retry=False,
        ):
            return "Compaction cancelled by extension."
        summary = await self._generate_compaction_summary(
            plan.messages_to_summarize,
            custom_instructions=instructions,
        )
        compaction = await self._append_compaction(
            summary,
            replace_entry_ids=plan.replace_entry_ids,
        )
        await self._emit_extension_compact(compaction, reason="manual", will_retry=False)
        return f"Compacted {len(compaction.replaces_entry_ids)} context entries."

    async def aclose(self) -> None:
        """Close runtime providers created by this coding session."""
        if not self._quit_shutdown_emitted:
            self._quit_shutdown_emitted = True
            await self.emit_extension_event(
                {
                    "type": "session_shutdown",
                    "reason": "quit",
                    "targetSessionFile": _session_storage_path(self._config.storage),
                }
            )
        for provider in self._owned_providers:
            await provider.aclose()
        self._owned_providers.clear()

    def handle_command(
        self,
        text: str,
        *,
        current_editor_text: str | None = None,
        show_tool_results: bool = False,
        current_theme: str | None = None,
    ) -> CommandResult:
        """Handle coding-session slash commands.

        Prompt-template slash commands are expansion directives, so they remain
        unhandled here and flow through `prompt()` for on-the-fly replacement.
        """
        if expand_prompt_template_command(text, self._prompt_templates) is not None:
            return CommandResult(handled=False)
        return self._command_registry.execute(
            self,
            text,
            current_editor_text=current_editor_text,
            show_tool_results=show_tool_results,
            current_theme=current_theme,
        )

    async def handle_command_async(
        self,
        text: str,
        *,
        current_editor_text: str | None = None,
        show_tool_results: bool = False,
        current_theme: str | None = None,
    ) -> CommandResult:
        """Handle slash commands, awaiting extension handlers that need TUI UI."""
        if expand_prompt_template_command(text, self._prompt_templates) is not None:
            return CommandResult(handled=False)
        return await self._command_registry.execute_async(
            self,
            text,
            current_editor_text=current_editor_text,
            show_tool_results=show_tool_results,
            current_theme=current_theme,
        )

    def set_extension_ui_handler(self, handler: Callable[..., object] | None) -> None:
        """Install the frontend callback used by Pi-style async extension UI calls."""
        self._extension_ui_handler = handler

    def set_extension_terminal_input_handler(
        self,
        handler: Callable[..., object] | None,
    ) -> None:
        """Install the frontend callback used by Pi-style terminal input listeners."""
        self._extension_terminal_input_handler = handler

    def register_extension_terminal_input_listener(
        self,
        handler: Callable[[str], object],
        *,
        extension_name: str,
    ) -> Callable[[], None]:
        """Register a TUI terminal-input listener and return its unsubscribe hook."""
        if self._extension_terminal_input_handler is None:
            return lambda: None
        result = self._extension_terminal_input_handler(
            extension_name=extension_name,
            handler=handler,
        )
        if not callable(result):
            return lambda: None
        return cast(Callable[[], None], result)

    def set_extension_autocomplete_provider_handler(
        self,
        handler: Callable[..., object] | None,
    ) -> None:
        """Install the frontend callback used by Pi-style autocomplete providers."""
        self._extension_autocomplete_provider_handler = handler

    def register_extension_autocomplete_provider(
        self,
        factory: Callable[[object], object],
        *,
        extension_name: str,
    ) -> Callable[[], None]:
        """Register a TUI autocomplete provider factory and return its unsubscribe hook."""
        if self._extension_autocomplete_provider_handler is None:
            return lambda: None
        result = self._extension_autocomplete_provider_handler(
            extension_name=extension_name,
            factory=factory,
        )
        if not callable(result):
            return lambda: None
        return cast(Callable[[], None], result)

    def set_extension_editor_component_handler(
        self,
        handler: Callable[..., object] | None,
    ) -> None:
        """Install the frontend callback used by Pi-style editor component overrides."""
        self._extension_editor_component_handler = handler

    def set_extension_editor_component(
        self,
        factory: Callable[..., object] | None,
        *,
        extension_name: str,
    ) -> object:
        """Set or clear a TUI prompt editor component factory."""
        if self._extension_editor_component_handler is None:
            return None
        return self._extension_editor_component_handler(
            action="set",
            extension_name=extension_name,
            factory=factory,
        )

    def get_extension_editor_component(self) -> object:
        """Return the active TUI prompt editor component factory, when available."""
        if self._extension_editor_component_handler is None:
            return None
        return self._extension_editor_component_handler(action="get")

    def set_extension_widget_component_handler(
        self,
        handler: Callable[..., object] | None,
    ) -> None:
        """Install the frontend callback used by Pi-style widget factories."""
        self._extension_widget_component_handler = handler

    def set_extension_widget_component(
        self,
        key: str,
        factory: Callable[..., object] | None,
        *,
        extension_name: str,
        placement: str,
    ) -> object:
        """Set or clear a prompt-region widget component factory."""
        if self._extension_widget_component_handler is None:
            return None
        return self._extension_widget_component_handler(
            key=key,
            extension_name=extension_name,
            factory=factory,
            placement=placement,
        )

    def set_extension_chrome_component_handler(
        self,
        handler: Callable[..., object] | None,
    ) -> None:
        """Install the frontend callback used by Pi-style header/footer factories."""
        self._extension_chrome_component_handler = handler

    def set_extension_chrome_component(
        self,
        target: str,
        factory: Callable[..., object] | None,
        *,
        extension_name: str,
    ) -> object:
        """Set or clear a TUI chrome component factory."""
        if self._extension_chrome_component_handler is None:
            return None
        return self._extension_chrome_component_handler(
            target=target,
            extension_name=extension_name,
            factory=factory,
        )

    @property
    def extension_ui_available(self) -> bool:
        """Return whether an interactive frontend can answer extension UI requests."""
        return self._extension_ui_handler is not None

    async def request_extension_ui(
        self,
        *,
        method: str,
        extension_name: str,
        **payload: Any,
    ) -> object:
        """Dispatch an extension UI request to the active frontend."""
        if self._extension_ui_handler is None:
            raise RuntimeError("No extension UI handler is available for this session.")
        result = self._extension_ui_handler(
            method=method,
            extension_name=extension_name,
            **payload,
        )
        if inspect.isawaitable(result):
            return await result
        return result

    async def handle_extension_shortcut(
        self,
        key: str,
        *,
        current_editor_text: str | None = None,
        show_tool_results: bool = False,
        current_theme: str | None = None,
    ) -> CommandResult:
        """Handle a keyboard shortcut registered by a loaded extension."""
        normalized = key.strip().lower()
        for extension in reversed(self._extensions):
            for shortcut in reversed(extension.shortcuts):
                if shortcut.key != normalized:
                    continue
                extension_context = ExtensionShortcutContext(
                    session=self,
                    key=normalized,
                    extension_name=extension.name,
                    current_editor_text=current_editor_text or "",
                    current_tools_expanded=show_tool_results,
                    current_theme=current_theme,
                )
                result = shortcut.handler(extension_context)
                if inspect.isawaitable(result):
                    return await _await_extension_shortcut_result(result, extension_context)
                return _extension_shortcut_result(result, extension_context)
        return CommandResult(handled=False)

    def expand_prompt_text(self, text: str) -> str:
        """Expand prompt text using loaded markdown resources."""
        expanded_prompt = expand_prompt_template_command(text, self._prompt_templates)
        if expanded_prompt is not None:
            return expanded_prompt
        expanded_skill = expand_skill_command(text, self._skills)
        return expanded_skill if expanded_skill is not None else text

    async def run_terminal_command(
        self,
        command: str,
        *,
        add_to_context: bool,
        on_output_chunk: Callable[[str], None] | None = None,
    ) -> TerminalCommandResult:
        """Run a shell command in the session cwd, optionally adding output to context."""
        normalized_command = command.strip()
        if not normalized_command:
            raise ValueError("Terminal command cannot be empty")

        extension_result = await self._extension_terminal_command_result(
            normalized_command,
            add_to_context=add_to_context,
        )
        if extension_result is not None:
            if add_to_context:
                await self._append_or_defer_terminal_context_message(
                    UserMessage(
                        content=_terminal_command_context_message(
                            normalized_command,
                            extension_result.output,
                        )
                    )
                )
            return extension_result

        shell_command = _apply_shell_command_prefix(
            normalized_command,
            self._shell_command_prefix,
        )
        bash_tool = create_bash_tool(
            cwd=self.cwd,
            shell_path=self._shell_path,
            on_output_chunk=on_output_chunk,
        )
        signal = SimpleCancellationToken()
        self._terminal_signal = signal
        try:
            result = await bash_tool.execute({"command": shell_command}, signal=signal)
        finally:
            if self._terminal_signal is signal:
                self._terminal_signal = None
        exit_code = None
        if result.data is not None:
            raw_exit_code = result.data.get("exit_code")
            exit_code = raw_exit_code if isinstance(raw_exit_code, int) else None

        if add_to_context:
            await self._append_or_defer_terminal_context_message(
                UserMessage(
                    content=_terminal_command_context_message(normalized_command, result.content)
                )
            )

        return TerminalCommandResult(
            command=normalized_command,
            output=result.content,
            exit_code=exit_code,
            ok=result.ok,
            added_to_context=add_to_context,
        )

    async def _extension_terminal_command_result(
        self,
        command: str,
        *,
        add_to_context: bool,
    ) -> TerminalCommandResult | None:
        results = await self.emit_extension_event(
            {
                "type": "user_bash",
                "command": command,
                "excludeFromContext": not add_to_context,
                "cwd": str(self.cwd),
            }
        )
        for result in results:
            terminal_result = _extension_user_bash_result(
                result,
                command=command,
                add_to_context=add_to_context,
            )
            if terminal_result is not None:
                return terminal_result
        return None

    async def _append_or_defer_terminal_context_message(self, message: UserMessage) -> None:
        if self._harness.is_running:
            self._pending_terminal_context_messages.append(message)
            return
        before_count = len(self._harness.messages)
        self._harness.append_message(message)
        await self._persist_messages_since(before_count)

    async def _flush_pending_terminal_context_messages(self) -> None:
        if not self._pending_terminal_context_messages:
            return
        messages = tuple(self._pending_terminal_context_messages)
        self._pending_terminal_context_messages.clear()
        before_count = len(self._harness.messages)
        for message in messages:
            self._harness.append_message(message)
        await self._persist_messages_since(before_count)

    async def _emit_extension_input_event(
        self,
        text: str,
        *,
        source: InputSource,
        streaming_behavior: StreamingBehavior | None,
    ) -> tuple[Literal["continue", "transform", "handled"], str]:
        """Emit Pi-style input handlers before skill/template expansion."""
        current_text = text
        action: Literal["continue", "transform", "handled"] = "continue"
        diagnostics: list[ResourceDiagnostic] = []
        event_streaming_behavior = (
            "followUp" if streaming_behavior == "follow_up" else streaming_behavior
        )
        for extension in self._extensions:
            handlers = tuple((extension.event_handlers or {}).get("input", ()))
            if not handlers:
                continue
            context = ExtensionShortcutContext(
                session=self,
                key="",
                extension_name=extension.name,
            )
            for handler in handlers:
                event: dict[str, object] = {
                    "type": "input",
                    "text": current_text,
                    "images": None,
                    "source": source,
                }
                if event_streaming_behavior is not None:
                    event["streamingBehavior"] = event_streaming_behavior
                try:
                    result = _call_extension_lifecycle_handler(handler, event, context)
                    if inspect.isawaitable(result):
                        result = await result
                except Exception as exc:  # noqa: BLE001 - extensions are isolated plugins
                    diagnostics.append(
                        ResourceDiagnostic(
                            kind="extension",
                            name=extension.name,
                            path=extension.path,
                            message=f"input handler failed: {type(exc).__name__}: {exc}",
                            severity="error",
                        )
                    )
                    continue
                parsed = _extension_input_result(result)
                if parsed is None:
                    continue
                parsed_action, parsed_text = parsed
                if parsed_action == "handled":
                    if diagnostics:
                        self._resource_diagnostics = (*self._resource_diagnostics, *diagnostics)
                    return "handled", current_text
                if parsed_action == "transform":
                    current_text = parsed_text
                    action = "transform"
        if diagnostics:
            self._resource_diagnostics = (*self._resource_diagnostics, *diagnostics)
        return action, current_text

    async def prompt(
        self,
        content: str,
        *,
        streaming_behavior: StreamingBehavior | None = None,
        source: InputSource = "interactive",
    ) -> AsyncIterator[AgentEvent]:
        """Append a user prompt, run the agent, and persist new messages."""
        context = self._diagnostic_context()
        input_action, prompt_content = await self._emit_extension_input_event(
            content,
            source=source,
            streaming_behavior=streaming_behavior if self._harness.is_running else None,
        )
        if input_action == "handled":
            return
        try:
            expanded_content = self.expand_prompt_text(prompt_content)
        except ResourceError:
            raise
        except Exception as exc:
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="expand_prompt",
                exc=exc,
            )
            raise

        if self._harness.is_running:
            if streaming_behavior == "steer":
                yield self._harness.steer(expanded_content)
                return
            if streaming_behavior == "follow_up":
                yield self._harness.follow_up(expanded_content)
                return
            raise RuntimeError(
                "CodingSession is already running; pass streaming_behavior to queue a message."
            )

        await self._flush_pending_terminal_context_messages()
        await self._try_auto_compact(context=context, phase="auto_compact_before_prompt")
        base_system_prompt = self._harness.config.system
        turn_system_prompt, custom_messages = await self._emit_extension_before_agent_start(
            expanded_content
        )
        for custom_type, data in custom_messages:
            await self.append_custom_entry(custom_type, data)
        self._harness.config.system = turn_system_prompt
        persisted_count = len(self._harness.messages)
        overflow_event: ErrorEvent | None = None
        terminal_error_message = ""
        loop_receipt = self._start_loop_receipt(objective=expanded_content)
        try:
            async for event in self._harness.prompt(expanded_content):
                if loop_receipt is not None:
                    loop_receipt.record(event)
                await self._emit_extension_agent_event(event)
                if isinstance(event, MessageEndEvent):
                    persisted_count = await self._persist_messages_since(persisted_count)
                if isinstance(event, ErrorEvent) and not event.recoverable:
                    self._last_diagnostic_log_path = self._diagnostic_logger.log_error_event(
                        context=context,
                        phase="agent_loop",
                        event=event,
                    )
                    if _is_context_overflow_error(event):
                        overflow_event = event
                    else:
                        terminal_error_message = event.message
                yield event
            persisted_count = await self._persist_messages_since(persisted_count)
            if overflow_event is not None:
                compacted = await self._try_overflow_compact(context=context)
                if compacted:
                    retry_persisted_count = len(self._harness.messages)
                    async for retry_event in self._harness.continue_():
                        if loop_receipt is not None:
                            loop_receipt.record(retry_event)
                        await self._emit_extension_agent_event(retry_event)
                        if isinstance(retry_event, MessageEndEvent):
                            retry_persisted_count = await self._persist_messages_since(
                                retry_persisted_count
                            )
                        if isinstance(retry_event, ErrorEvent) and not retry_event.recoverable:
                            self._last_diagnostic_log_path = (
                                self._diagnostic_logger.log_error_event(
                                    context=context,
                                    phase="agent_loop_retry",
                                    event=retry_event,
                                )
                            )
                            terminal_error_message = retry_event.message
                        yield retry_event
                    await self._persist_messages_since(retry_persisted_count)
                await self._flush_pending_terminal_context_messages()
                if loop_receipt is not None:
                    await self._finish_loop_receipt(
                        loop_receipt,
                        terminal_error_message=terminal_error_message,
                    )
                return
            if loop_receipt is not None:
                await self._finish_loop_receipt(
                    loop_receipt,
                    terminal_error_message=terminal_error_message,
                )
            await self._flush_pending_terminal_context_messages()
            await self._try_auto_compact(context=context, phase="auto_compact_after_prompt")
        except Exception as exc:
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="agent_loop",
                exc=exc,
            )
            raise
        finally:
            self._harness.config.system = base_system_prompt

    async def continue_(self) -> AsyncIterator[AgentEvent]:
        """Continue the agent from restored state and persist new messages."""
        context = self._diagnostic_context()
        persisted_count = len(self._harness.messages)
        try:
            async for event in self._harness.continue_():
                await self._emit_extension_agent_event(event)
                if isinstance(event, MessageEndEvent):
                    persisted_count = await self._persist_messages_since(persisted_count)
                if isinstance(event, ErrorEvent) and not event.recoverable:
                    self._last_diagnostic_log_path = self._diagnostic_logger.log_error_event(
                        context=context,
                        phase="agent_loop",
                        event=event,
                    )
                yield event
            await self._persist_messages_since(persisted_count)
            await self._flush_pending_terminal_context_messages()
            await self._try_auto_compact(context=context, phase="auto_compact_after_continue")
        except Exception as exc:
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="agent_loop",
                exc=exc,
            )
            raise

    async def _emit_extension_agent_event(self, event: AgentEvent) -> None:
        for payload in _extension_agent_event_payloads(event, messages=self._harness.messages):
            await self.emit_extension_event(payload)

    async def _emit_extension_before_agent_start(
        self,
        prompt: str,
    ) -> tuple[str, tuple[tuple[str, Mapping[str, Any]], ...]]:
        current_system_prompt = self._harness.config.system
        system_prompt_options = _extension_system_prompt_options(self)
        custom_messages: list[tuple[str, Mapping[str, Any]]] = []
        diagnostics: list[ResourceDiagnostic] = []
        for extension in self._extensions:
            handlers = tuple((extension.event_handlers or {}).get("before_agent_start", ()))
            if not handlers:
                continue
            context = ExtensionShortcutContext(
                session=self,
                key="",
                extension_name=extension.name,
            )
            for handler in handlers:
                event: dict[str, object] = {
                    "type": "before_agent_start",
                    "prompt": prompt,
                    "images": None,
                    "systemPrompt": current_system_prompt,
                    "systemPromptOptions": system_prompt_options,
                }
                try:
                    result = _call_extension_lifecycle_handler(handler, event, context)
                    if inspect.isawaitable(result):
                        result = await result
                except Exception as exc:  # noqa: BLE001 - extensions are isolated plugins
                    diagnostics.append(
                        ResourceDiagnostic(
                            kind="extension",
                            name=extension.name,
                            path=extension.path,
                            message=(
                                "before_agent_start handler failed: "
                                f"{type(exc).__name__}: {exc}"
                            ),
                            severity="error",
                        )
                    )
                    continue
                parsed_message = _extension_before_agent_custom_message(result)
                if parsed_message is not None:
                    custom_messages.append(parsed_message)
                parsed_system_prompt = _extension_before_agent_system_prompt(result)
                if parsed_system_prompt is not None:
                    current_system_prompt = parsed_system_prompt
        if diagnostics:
            self._resource_diagnostics = (*self._resource_diagnostics, *diagnostics)
        return current_system_prompt, tuple(custom_messages)

    async def _emit_extension_context_event(
        self,
        messages: Sequence[AgentMessage],
    ) -> list[AgentMessage]:
        current_messages = list(messages)
        diagnostics: list[ResourceDiagnostic] = []
        for extension in self._extensions:
            handlers = tuple((extension.event_handlers or {}).get("context", ()))
            if not handlers:
                continue
            context = ExtensionShortcutContext(
                session=self,
                key="",
                extension_name=extension.name,
            )
            for handler in handlers:
                event: dict[str, object] = {
                    "type": "context",
                    "messages": [_agent_message_payload(message) for message in current_messages],
                }
                try:
                    result = _call_extension_lifecycle_handler(handler, event, context)
                    if inspect.isawaitable(result):
                        result = await result
                except Exception as exc:  # noqa: BLE001 - extensions are isolated plugins
                    diagnostics.append(
                        ResourceDiagnostic(
                            kind="extension",
                            name=extension.name,
                            path=extension.path,
                            message=f"context handler failed: {type(exc).__name__}: {exc}",
                            severity="error",
                        )
                    )
                    continue
                parsed_messages = _extension_context_messages(result)
                if parsed_messages is not None:
                    current_messages = parsed_messages
        if diagnostics:
            self._resource_diagnostics = (*self._resource_diagnostics, *diagnostics)
        return current_messages

    async def _emit_extension_before_compact(
        self,
        plan: CompactionPlan,
        *,
        custom_instructions: str | None,
        reason: Literal["manual", "threshold", "overflow"],
        will_retry: bool,
    ) -> bool:
        results = await self.emit_extension_event(
            {
                "type": "session_before_compact",
                "preparation": {
                    "replaceEntryIds": plan.replace_entry_ids,
                    "messageCount": len(plan.messages_to_summarize),
                    "messages": [
                        _agent_message_payload(message) for message in plan.messages_to_summarize
                    ],
                },
                "branchEntries": [
                    entry.model_dump(mode="json") for entry in await self._read_session_entries()
                ],
                "customInstructions": custom_instructions,
                "reason": reason,
                "willRetry": will_retry,
                "signal": None,
            }
        )
        return any(
            isinstance(result, Mapping) and bool(result.get("cancel")) for result in results
        )

    async def _emit_extension_before_switch(
        self,
        *,
        reason: Literal["new", "resume"],
        target_session_file: str | None = None,
    ) -> bool:
        event: dict[str, object] = {
            "type": "session_before_switch",
            "reason": reason,
        }
        if target_session_file is not None:
            event["targetSessionFile"] = target_session_file
        results = await self.emit_extension_event(event)
        return any(
            isinstance(result, Mapping) and bool(result.get("cancel")) for result in results
        )

    async def _emit_extension_before_fork(
        self,
        entry_id: str,
        *,
        position: Literal["before", "at"],
    ) -> bool:
        results = await self.emit_extension_event(
            {
                "type": "session_before_fork",
                "entryId": entry_id,
                "position": position,
            }
        )
        return any(
            isinstance(result, Mapping) and bool(result.get("cancel")) for result in results
        )

    async def _emit_extension_compact(
        self,
        compaction: CompactionEntry,
        *,
        reason: Literal["manual", "threshold", "overflow"],
        will_retry: bool,
    ) -> None:
        await self.emit_extension_event(
            {
                "type": "session_compact",
                "compactionEntry": compaction.model_dump(mode="json"),
                "fromExtension": False,
                "reason": reason,
                "willRetry": will_retry,
            }
        )

    async def _emit_extension_before_tree(
        self,
        entries: Sequence[SessionEntry],
        *,
        target_entry_id: str,
        summarize: bool,
    ) -> bool:
        target_index = next(
            (index for index, entry in enumerate(entries) if entry.id == target_entry_id),
            None,
        )
        results = await self.emit_extension_event(
            {
                "type": "session_before_tree",
                "preparation": {
                    "targetEntryId": target_entry_id,
                    "targetIndex": target_index,
                    "summarize": summarize,
                    "oldLeafId": self._state.active_leaf_id,
                    "branchEntries": [entry.model_dump(mode="json") for entry in entries],
                },
                "signal": None,
            }
        )
        return any(
            isinstance(result, Mapping) and bool(result.get("cancel")) for result in results
        )

    async def _emit_extension_tree(
        self,
        *,
        old_leaf_id: str | None,
        new_leaf_id: str | None,
        summary_entry: BranchSummaryEntry | None,
    ) -> None:
        await self.emit_extension_event(
            {
                "type": "session_tree",
                "newLeafId": new_leaf_id,
                "oldLeafId": old_leaf_id,
                "summaryEntry": (
                    summary_entry.model_dump(mode="json") if summary_entry is not None else None
                ),
                "fromExtension": False if summary_entry is not None else None,
            }
        )

    def _emit_extension_model_select(
        self,
        choice: ModelChoice,
        *,
        previous_choice: ModelChoice | None,
        source: ModelSelectSource,
    ) -> object | None:
        if previous_choice == choice:
            return None
        return self._dispatch_extension_event_from_sync(
            {
                "type": "model_select",
                "model": _extension_model_payload(choice),
                "previousModel": (
                    _extension_model_payload(previous_choice)
                    if previous_choice is not None
                    else None
                ),
                "source": source,
            }
        )

    def _dispatch_extension_event_from_sync(
        self,
        event: Mapping[str, object],
    ) -> object | None:
        event_type = str(event.get("type") or "").strip()
        if not event_type:
            raise ValueError("extension event requires a non-empty type")
        if not any(
            (extension.event_handlers or {}).get(event_type)
            for extension in self._extensions
        ):
            return None
        coroutine = self.emit_extension_event(event)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        return loop.create_task(coroutine)

    def _diagnostic_context(self) -> AgentCallDiagnosticContext:
        return AgentCallDiagnosticContext(
            provider_name=self._provider_name,
            model=self.model,
            cwd=self.cwd,
            session_id=self.session_id,
            run_id=new_agent_call_run_id(),
        )

    def _start_loop_receipt(self, *, objective: str) -> LoopReceiptRecorder | None:
        config = self._config.loop_receipt
        if config is None:
            return None
        recorder = LoopReceiptRecorder.create(root_dir=config.root_dir)
        recorder.write_contract(
            node_id=config.node_id,
            objective=objective,
            repo=self.cwd,
            allowed_globs=config.allowed_globs,
            checks=config.checks,
            max_attempts=config.max_attempts,
            backend=config.backend,
            backend_config=config.backend_config,
            required_changed_globs=config.required_changed_globs,
            run_root=config.root_dir,
        )
        return recorder

    async def _finish_loop_receipt(
        self,
        recorder: LoopReceiptRecorder,
        *,
        terminal_error_message: str = "",
    ) -> None:
        config = self._config.loop_receipt
        if config is None:
            return
        checks = await self._run_loop_receipt_checks(recorder)
        checks_passed = all(check["exit_code"] == 0 for check in checks)
        changed_files = await self._loop_receipt_changed_files()
        missing_required = _missing_required_changed_globs(
            changed_files,
            config.required_changed_globs,
        )
        if terminal_error_message:
            receipt_status = "BLOCKED"
            receipt_error = terminal_error_message
        elif checks_passed and missing_required:
            receipt_status = "BLOCKED"
            receipt_error = "required changed globs missing: " + ", ".join(missing_required)
            recorder.emit_loop2_event(
                "required_changes_missing",
                node_id=config.node_id,
                status="blocked",
                message=receipt_error,
                data={"missing_required_globs": missing_required},
            )
        else:
            receipt_status = "PASS" if checks_passed else "FAILED"
            receipt_error = ""
        recorder.write_final_receipt(
            node_id=config.node_id,
            status=receipt_status,
            mocked=config.mocked,
            live=config.live,
            provider=self.provider_name,
            model=self.model,
            checks=checks,
            changed_files=changed_files,
            proof_scope=config.proof_scope,
            proves=config.proves,
            does_not_prove=config.does_not_prove,
            error=receipt_error,
        )
        recorder.emit_loop2_event(
            "receipt_written",
            node_id=config.node_id,
            status="completed" if receipt_status == "PASS" else "blocked",
        )
        recorder.write_transport_dag_evidence()
        recorder.write_node_result(
            node_id=config.node_id,
            status=receipt_status,
            mocked=config.mocked,
            live=config.live,
            checks=checks,
            changed_files=changed_files,
        )

    async def _run_loop_receipt_checks(
        self,
        recorder: LoopReceiptRecorder,
    ) -> list[dict[str, object]]:
        config = self._config.loop_receipt
        if config is None:
            return []
        check_results: list[dict[str, object]] = []
        checks_dir = recorder.run.run_dir / "checks"
        checks_dir.mkdir(parents=True, exist_ok=True)
        recorder.emit_loop2_event("checks_started", node_id=config.node_id, status="running")
        for index, command in enumerate(config.checks, start=1):
            stdout_path = checks_dir / f"check-{index:04d}.stdout.txt"
            stderr_path = checks_dir / f"check-{index:04d}.stderr.txt"
            started = monotonic()
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=self.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            elapsed_s = monotonic() - started
            stdout_path.write_bytes(stdout)
            stderr_path.write_bytes(stderr)
            check_result = {
                "command": command,
                "exit_code": int(process.returncode or 0),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "elapsed_s": elapsed_s,
            }
            check_results.append(check_result)
            recorder.emit_loop2_event(
                "check_finished",
                node_id=config.node_id,
                status="completed" if check_result["exit_code"] == 0 else "failed",
                message=command,
                data=check_result,
            )
        checks_ok = all(check["exit_code"] == 0 for check in check_results)
        recorder.emit_loop2_event(
            "checks_finished",
            node_id=config.node_id,
            status="completed" if checks_ok else "failed",
        )
        return check_results

    async def _loop_receipt_changed_files(self) -> list[str]:
        config = self._config.loop_receipt
        if config is None:
            return []
        if config.changed_files:
            return list(config.changed_files)
        if not (self.cwd / ".git").exists() or shutil.which("git") is None:
            return []
        process = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain",
            "--untracked-files=all",
            cwd=self.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _stderr = await process.communicate()
        if process.returncode != 0:
            return []
        files: list[str] = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            if len(line) > 3:
                files.append(line[3:].strip())
        return sorted(files)

    async def _persist_messages_since(self, persisted_count: int) -> int:
        """Persist completed harness messages after ``persisted_count``.

        Message lifecycle events are the durable-message boundary. Each persisted
        message advances the append-only tree and records a leaf pointer so tree
        navigation can observe the current branch while a run is still active.
        """
        new_messages = self._harness.messages[persisted_count:]
        if not new_messages:
            return persisted_count

        for message in new_messages:
            entry = MessageEntry(parent_id=self._last_parent_id, message=message)
            await self._append_session_entry(entry)
            self._last_parent_id = entry.id
            leaf = LeafEntry(parent_id=entry.id, entry_id=entry.id)
            await self._append_session_entry(leaf)

        await self._refresh_persisted_state()
        return persisted_count + len(new_messages)

    async def _refresh_persisted_state(
        self,
        *,
        leaf_id: str | None | object = _UNSET_LEAF_ID,
    ) -> None:
        entries = await self._read_session_entries()
        self._state = (
            SessionState.from_entries(entries)
            if leaf_id is _UNSET_LEAF_ID
            else SessionState.from_entries(entries, leaf_id=cast(str | None, leaf_id))
        )
        if self._config.session_id is not None and self._config.session_manager is not None:
            self._config.session_manager.touch_session(
                self._config.session_id,
                model=self.model,
                provider_name=self.provider_name,
            )

    async def _read_session_entries(self) -> list[SessionEntry]:
        """Read stored entries, detaching roots imported from external history."""
        return _detach_missing_parents(await self._config.storage.read_all())

    async def _append_session_entry(self, entry: SessionEntry) -> None:
        """Append one durable entry after flushing deferred session metadata."""
        await self._ensure_session_initialized()
        await self._config.storage.append(entry)

    async def _ensure_session_initialized(self) -> None:
        if not self._pending_initial_entries:
            return
        for entry in self._pending_initial_entries:
            await self._config.storage.append(entry)
        self._pending_initial_entries = ()

    async def _try_auto_compact(
        self,
        *,
        context: AgentCallDiagnosticContext,
        phase: str,
    ) -> bool:
        try:
            return await self._maybe_auto_compact()
        except Exception as exc:  # noqa: BLE001 - automatic compaction must not lose a turn
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase=phase,
                exc=exc,
            )
            return False

    async def _try_overflow_compact(
        self,
        *,
        context: AgentCallDiagnosticContext,
    ) -> bool:
        try:
            plan = self._recent_preserving_compaction_plan()
            if plan is None:
                return False
            if await self._emit_extension_before_compact(
                plan,
                custom_instructions=None,
                reason="overflow",
                will_retry=True,
            ):
                return False
            summary = await self._generate_compaction_summary(plan.messages_to_summarize)
            compaction = await self._append_compaction(
                summary,
                replace_entry_ids=plan.replace_entry_ids,
            )
            await self._emit_extension_compact(compaction, reason="overflow", will_retry=True)
            return True
        except Exception as exc:  # noqa: BLE001 - the original overflow remains visible
            self._last_diagnostic_log_path = self._diagnostic_logger.log_exception(
                context=context,
                phase="overflow_compact",
                exc=exc,
            )
            return False

    def _provider_is_usable(self, provider: ProviderConfig) -> bool:
        return provider_has_usable_credentials(
            provider,
            credential_reader=self._credential_store,
        )

    def _usable_provider_configs(self) -> tuple[ProviderConfig, ...]:
        if self._provider_settings is None:
            return ()
        return tuple(
            provider
            for provider in self._provider_settings.providers
            if self._provider_is_usable(provider)
        )

    async def _maybe_auto_compact(self) -> bool:
        threshold = self.auto_compact_token_threshold
        if threshold is None or threshold <= 0:
            return False
        if len(self._state.context_entry_ids) < 2:
            return False
        if self.context_token_estimate <= threshold:
            return False
        plan = self._recent_preserving_compaction_plan()
        if plan is None:
            return False
        if await self._emit_extension_before_compact(
            plan,
            custom_instructions=None,
            reason="threshold",
            will_retry=False,
        ):
            return False
        summary = await self._generate_compaction_summary(plan.messages_to_summarize)
        compaction = await self._append_compaction(
            summary,
            replace_entry_ids=plan.replace_entry_ids,
        )
        await self._emit_extension_compact(compaction, reason="threshold", will_retry=False)
        return True

    async def _generate_compaction_summary(
        self,
        messages: tuple[AgentMessage, ...],
        *,
        custom_instructions: str | None = None,
    ) -> str:
        prompt = build_compaction_summary_prompt(
            messages,
            custom_instructions=custom_instructions,
        )
        text_parts: list[str] = []
        final_text: str | None = None
        summary_messages: list[AgentMessage] = [UserMessage(content=prompt)]
        async for event in self._harness.config.provider.stream_response(
            model=self.model,
            system=SUMMARIZATION_SYSTEM_PROMPT,
            messages=summary_messages,
            tools=[],
        ):
            if isinstance(event, ProviderTextDeltaEvent):
                text_parts.append(event.delta)
            elif isinstance(event, ProviderResponseEndEvent):
                final_text = event.message.content
            elif isinstance(event, ProviderErrorEvent):
                details = f": {event.data}" if event.data is not None else ""
                raise RuntimeError(f"Compaction summarization failed: {event.message}{details}")

        summary = (final_text if final_text is not None else "".join(text_parts)).strip()
        if not summary:
            raise RuntimeError("Compaction summarization returned an empty summary")
        return summary

    async def _summarize_branch_messages(
        self,
        messages: tuple[AgentMessage, ...],
        *,
        custom_instructions: str | None = None,
        replace_instructions: bool = False,
    ) -> str:
        try:
            summary = await summarize_branch_messages_with_model(
                provider=self._harness.config.provider,
                model=self.model,
                messages=messages,
                custom_instructions=custom_instructions,
                replace_instructions=replace_instructions,
            )
        except Exception:
            summary = None
        return summary or summarize_messages_for_compaction(messages)

    def _manual_compaction_plan(self) -> CompactionPlan:
        rows = self._active_context_rows()
        if not rows:
            raise ValueError("No active context messages to compact")
        return CompactionPlan(
            replace_entry_ids=tuple(entry_id for entry_id, _message in rows),
            messages_to_summarize=tuple(message for _entry_id, message in rows),
        )

    def _recent_preserving_compaction_plan(self) -> CompactionPlan | None:
        rows = self._active_context_rows()
        if len(rows) < 2:
            return None

        first_kept_index = _first_recent_context_index(
            rows,
            keep_recent_tokens=DEFAULT_COMPACTION_KEEP_RECENT_TOKENS,
        )
        if first_kept_index <= 0:
            return None

        replaced = rows[:first_kept_index]
        if not replaced:
            return None
        return CompactionPlan(
            replace_entry_ids=tuple(entry_id for entry_id, _message in replaced),
            messages_to_summarize=tuple(message for _entry_id, message in replaced),
        )

    def _active_context_rows(self) -> tuple[tuple[str, AgentMessage], ...]:
        return tuple(zip(self._state.context_entry_ids, self._state.messages, strict=True))

    async def _append_compaction(
        self,
        summary: str,
        *,
        replace_entry_ids: tuple[str, ...],
    ) -> CompactionEntry:
        if not replace_entry_ids:
            raise ValueError("No active context messages to compact")

        compaction = CompactionEntry(
            parent_id=self._last_parent_id,
            summary=summary,
            replaces_entry_ids=list(replace_entry_ids),
        )
        await self._append_session_entry(compaction)
        leaf = LeafEntry(parent_id=compaction.id, entry_id=compaction.id)
        await self._append_session_entry(leaf)
        self._last_parent_id = compaction.id

        await self._refresh_persisted_state(leaf_id=compaction.id)
        self._harness.replace_messages(self._state.messages)
        return compaction


def _first_recent_context_index(
    rows: tuple[tuple[str, AgentMessage], ...],
    *,
    keep_recent_tokens: int,
) -> int:
    if keep_recent_tokens <= 0:
        return len(rows)

    accumulated_tokens = 0
    candidate_index: int | None = None
    for index in range(len(rows) - 1, -1, -1):
        _entry_id, message = rows[index]
        accumulated_tokens += estimate_message_tokens(message)
        if accumulated_tokens >= keep_recent_tokens:
            candidate_index = index
            break

    if candidate_index is None:
        return 0

    candidate_message = rows[candidate_index][1]
    if candidate_message.role == "user":
        if candidate_index > 0:
            return candidate_index
        next_user_index = _next_user_message_index(rows, start=1)
        return next_user_index if next_user_index is not None else 0

    next_user_index = _next_user_message_index(rows, start=candidate_index + 1)
    if next_user_index is not None:
        return next_user_index

    for index in range(candidate_index, len(rows)):
        if rows[index][1].role != "tool":
            return index
    return len(rows)


def _next_user_message_index(
    rows: tuple[tuple[str, AgentMessage], ...],
    *,
    start: int,
) -> int | None:
    for index in range(start, len(rows)):
        if rows[index][1].role == "user":
            return index
    return None


def _is_context_overflow_error(event: ErrorEvent) -> bool:
    text = event.message
    if event.data is not None:
        text = f"{text} {event.data}"
    normalized = text.lower()
    markers = (
        "context length",
        "context window",
        "context limit",
        "maximum context",
        "max context",
        "input is too long",
        "input length",
        "prompt is too long",
        "too many tokens",
        "token limit",
        "exceeds the limit",
        "exceeded the limit",
    )
    return any(marker in normalized for marker in markers)


def _detach_missing_parents(entries: list[SessionEntry]) -> list[SessionEntry]:
    """Return entries with dangling parent pointers detached from external history."""
    entry_ids = {entry.id for entry in entries}
    return [
        entry.model_copy(update={"parent_id": None})
        if entry.parent_id is not None and entry.parent_id not in entry_ids
        else entry
        for entry in entries
    ]


def _last_parent_id_from_state(state: SessionState) -> str | None:
    if state.active_leaf_id is not None:
        return state.active_leaf_id
    if state.entries:
        return state.entries[-1].id
    return None


def _latest_leaf_entry(entries: list[SessionEntry]) -> LeafEntry | None:
    for entry in reversed(entries):
        if isinstance(entry, LeafEntry):
            return entry
    return None


def _is_branchable_tree_entry(entry: SessionEntry) -> bool:
    if entry.type in {"compaction", "branch_summary"}:
        return True
    if entry.type != "message":
        return False
    return isinstance(entry.message, UserMessage | AssistantMessage)


def _tree_entry_labels(entries: list[SessionEntry]) -> dict[str, tuple[str, float]]:
    labels: dict[str, tuple[str, float]] = {}
    for entry in entries:
        if not isinstance(entry, LabelEntry) or entry.target_entry_id is None:
            continue
        normalized = entry.label.strip()
        if normalized:
            labels[entry.target_entry_id] = (normalized, entry.timestamp)
        else:
            labels.pop(entry.target_entry_id, None)
    return labels


def _tree_choice_label(entry: SessionEntry, *, branch_indent: int = 0) -> str:
    prefix = "  " * branch_indent
    return f"{prefix}{_tree_entry_title(entry)}"


def _tree_branch_indents(entries: list[SessionEntry]) -> dict[str, int]:
    children_by_parent: dict[str | None, list[str]] = {}
    for entry in entries:
        if entry.type != "leaf":
            children_by_parent.setdefault(entry.parent_id, []).append(entry.id)

    sibling_indexes = {
        child_id: index
        for children in children_by_parent.values()
        for index, child_id in enumerate(children)
    }
    indents: dict[str, int] = {}
    for entry in entries:
        if entry.type == "leaf":
            continue
        parent_indent = indents.get(entry.parent_id, 0) if entry.parent_id is not None else 0
        sibling_index = sibling_indexes.get(entry.id, 0)
        indents[entry.id] = parent_indent + (1 if sibling_index > 0 else 0)
    return indents


def _ordered_tree_entries(entries: list[SessionEntry]) -> tuple[SessionEntry, ...]:
    children_by_parent: dict[str | None, list[SessionEntry]] = {}
    for entry in entries:
        if entry.type != "leaf":
            children_by_parent.setdefault(entry.parent_id, []).append(entry)

    ordered: list[SessionEntry] = []
    seen: set[str] = set()

    def append_descendants(parent_id: str | None) -> None:
        children = children_by_parent.get(parent_id, [])
        for child in children:
            if child.id not in seen:
                ordered.append(child)
                seen.add(child.id)
        for child in children:
            append_descendants(child.id)

    append_descendants(None)
    for entry in entries:
        if entry.type != "leaf" and entry.id not in seen:
            ordered.append(entry)
            seen.add(entry.id)
            append_descendants(entry.id)
    return tuple(ordered)


def _is_tool_call_tree_entry(entry: SessionEntry) -> bool:
    return (
        entry.type == "message"
        and isinstance(entry.message, AssistantMessage)
        and bool(entry.message.tool_calls)
    )


def _tree_entry_title(entry: SessionEntry) -> str:
    match entry.type:
        case "message":
            message = entry.message
            if isinstance(message, AssistantMessage) and message.tool_calls and not message.content:
                tool_names = ", ".join(call.name for call in message.tool_calls)
                return f"tool call: {tool_names}"
            return f"{message.role}: {_message_text_preview(message)}"
        case "compaction":
            return f"compaction summary: {_short_preview(entry.summary)}"
        case "branch_summary":
            return f"branch summary: {_short_preview(entry.summary)}"
        case _:
            return entry.type


def _tree_choice_copy_text(entry: SessionEntry) -> str | None:
    match entry.type:
        case "message":
            content = entry.message.content.strip()
            return content or None
        case "compaction" | "branch_summary":
            summary = entry.summary.strip()
            return summary or None
        case _:
            return None


def _message_text_preview(message: AgentMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return _short_preview(content)
    return _short_preview(str(content))


def _short_preview(text: str, *, limit: int = 72) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized or "(empty)"
    return f"{normalized[: limit - 1]}..."


def _messages_after_entry_on_active_path(
    entries: list[SessionEntry],
    entry_id: str,
    active_leaf_id: str | None,
) -> tuple[AgentMessage, ...]:
    if active_leaf_id is None:
        return ()
    try:
        active_path = path_to_entry(entries, active_leaf_id)
    except SessionTreeError:
        return ()
    try:
        target_index = next(
            index for index, entry in enumerate(active_path) if entry.id == entry_id
        )
    except StopIteration:
        return ()
    return tuple(
        entry.message for entry in active_path[target_index + 1 :] if entry.type == "message"
    )


def _storage_path(storage: SessionStorage) -> Path | None:
    path = getattr(storage, "path", None)
    return path if isinstance(path, Path) else None


def _resolve_export_destination(
    destination: Path | None,
    *,
    cwd: Path,
    session_path: Path | None,
    format: str,
) -> Path:
    if destination is None:
        if session_path is not None:
            return default_session_export_artifact_path(
                session_path,
                destination_dir=cwd,
                format=format,
            )
        return cwd / f"tau-session.{format}"

    resolved = destination if destination.is_absolute() else cwd / destination
    if resolved.suffix:
        return resolved
    name = session_path.stem if session_path is not None else "tau-session"
    return default_session_export_artifact_path(
        Path(name),
        destination_dir=resolved,
        format=format,
    )


def _share_viewer_url(gist_id: str) -> str:
    base_url = os.environ.get("TAU_SHARE_VIEWER_URL") or os.environ.get("PI_SHARE_VIEWER_URL")
    if not base_url:
        base_url = "https://pi.dev/session/"
    return f"{base_url.rstrip('/')}/{gist_id}"


def _session_export_title(session: CodingSession) -> str:
    manager = session.session_manager
    session_id = session.session_id
    if manager is not None and session_id is not None:
        record = manager.get_session(session_id)
        if record is not None and record.title:
            return record.title
    return f"Tau session {session_id}" if session_id is not None else "Tau Session Export"


def _extension_model_payload(choice: ModelChoice) -> dict[str, str]:
    return {
        "provider": choice.provider_name,
        "id": choice.model,
        "model": choice.model,
    }


def _state_thinking_level(
    state: SessionState,
    default: ThinkingLevel,
) -> ThinkingLevel:
    thinking_level = getattr(state, "thinking_level", None)
    if thinking_level is None:
        return default
    return normalize_thinking_level(thinking_level)


def _coerced_thinking_level(
    provider: ProviderConfig,
    *,
    model: str,
    current: ThinkingLevel,
) -> ThinkingLevel:
    levels = provider_thinking_levels(provider, model=model)
    if not levels or current in levels:
        return current
    default = provider_default_thinking_level(provider, model=model)
    return default or levels[0]


def _unavailable_thinking_message(session: CodingSession) -> str:
    message = f"Thinking controls are unavailable for {session.provider_name}:{session.model}"
    reason = session.thinking_unavailable_reason
    if reason:
        return f"{message}: {reason}"
    return message


def _display_queue_mode(mode: QueueMode) -> str:
    return "one-at-a-time" if mode == "one_at_a_time" else mode


def _terminal_command_context_message(command: str, output: str) -> str:
    return (
        "Terminal command executed by the user.\n\n"
        f"Command:\n```bash\n{command}\n```\n\n"
        f"Output:\n```text\n{output}\n```"
    )


def _extension_user_bash_result(
    value: object,
    *,
    command: str,
    add_to_context: bool,
) -> TerminalCommandResult | None:
    if not isinstance(value, Mapping):
        return None
    raw_result = value.get("result", value)
    if not isinstance(raw_result, Mapping):
        return None
    raw_output = raw_result.get("output", raw_result.get("content"))
    if not isinstance(raw_output, str):
        return None
    raw_command = raw_result.get("command", command)
    raw_exit_code = raw_result.get("exitCode", raw_result.get("exit_code"))
    exit_code = raw_exit_code if isinstance(raw_exit_code, int) else None
    raw_ok = raw_result.get("ok")
    ok = (
        raw_ok
        if isinstance(raw_ok, bool)
        else (exit_code == 0 if exit_code is not None else True)
    )
    return TerminalCommandResult(
        command=raw_command if isinstance(raw_command, str) else command,
        output=raw_output,
        exit_code=exit_code,
        ok=ok,
        added_to_context=add_to_context,
    )


def _extension_input_result(
    value: object,
) -> tuple[Literal["continue", "transform", "handled"], str] | None:
    if not isinstance(value, Mapping):
        return None
    raw_action = value.get("action")
    if raw_action == "continue":
        return "continue", ""
    if raw_action == "handled":
        return "handled", ""
    if raw_action != "transform":
        return None
    raw_text = value.get("text")
    if not isinstance(raw_text, str):
        return None
    return "transform", raw_text


def _extension_before_agent_system_prompt(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    raw_system_prompt = value.get("systemPrompt", value.get("system_prompt"))
    return raw_system_prompt if isinstance(raw_system_prompt, str) else None


class _ExtensionAwareModelProvider:
    def __init__(self, *, session: CodingSession, provider: ModelProvider) -> None:
        self._session = session
        self._provider = provider

    @property
    def base_provider(self) -> ModelProvider:
        return self._provider

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        async def iterator() -> AsyncIterator[ProviderEvent]:
            provider_messages = await self._session._emit_extension_context_event(messages)
            async for event in self._provider.stream_response(
                model=model,
                system=system,
                messages=provider_messages,
                tools=tools,
                signal=signal,
            ):
                yield event

        return iterator()


def _base_model_provider(provider: ModelProvider) -> ModelProvider:
    current = provider
    while isinstance(current, _ExtensionAwareModelProvider):
        current = current.base_provider
    return current


def _extension_before_agent_custom_message(
    value: object,
) -> tuple[str, Mapping[str, Any]] | None:
    if not isinstance(value, Mapping):
        return None
    raw_message = value.get("message")
    if not isinstance(raw_message, Mapping):
        return None
    custom_type = str(raw_message.get("customType", raw_message.get("custom_type", ""))).strip()
    if not custom_type:
        return None
    data: dict[str, Any] = {}
    for key in ("content", "display", "details"):
        if key in raw_message:
            data[key] = _json_compatible_value(raw_message[key])
    return custom_type, data


def _extension_context_messages(value: object) -> list[AgentMessage] | None:
    if not isinstance(value, Mapping):
        return None
    raw_messages = value.get("messages")
    if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, str | bytes | bytearray):
        return None
    messages: list[AgentMessage] = []
    for raw_message in raw_messages:
        message = _agent_message_from_extension_value(raw_message)
        if message is None:
            return None
        messages.append(message)
    return messages


def _agent_message_from_extension_value(value: object) -> AgentMessage | None:
    if isinstance(value, UserMessage | AssistantMessage | ToolResultMessage):
        return value
    if not isinstance(value, Mapping):
        return None
    role = value.get("role")
    try:
        if role == "user":
            return UserMessage.model_validate(value)
        if role == "assistant":
            return AssistantMessage.model_validate(value)
        if role == "tool":
            return ToolResultMessage.model_validate(value)
    except Exception:
        return None
    return None


def _extension_system_prompt_options(session: CodingSession) -> dict[str, object]:
    tools = tuple(getattr(session._harness.config, "tools", ()))
    prompt_guidelines: list[str] = []
    for tool in tools:
        for guideline in getattr(tool, "prompt_guidelines", ()):
            text = str(guideline).strip()
            if text and text not in prompt_guidelines:
                prompt_guidelines.append(text)
    return {
        "customPrompt": session._config.custom_system_prompt,
        "selectedTools": tuple(str(getattr(tool, "name", "")) for tool in tools),
        "toolSnippets": {
            str(getattr(tool, "name", "")): str(snippet)
            for tool in tools
            if (snippet := getattr(tool, "prompt_snippet", None))
        },
        "promptGuidelines": tuple(prompt_guidelines),
        "appendSystemPrompt": session._config.append_system_prompt,
        "cwd": str(session.cwd),
        "contextFiles": tuple(
            {
                "path": str(context_file.path),
                "content": context_file.content,
            }
            for context_file in session.context_files
        ),
        "skills": tuple(
            {
                "name": skill.name,
                "path": str(skill.path),
                "description": skill.description,
                "content": skill.content,
            }
            for skill in session.skills
        ),
    }


def _extension_agent_event_payloads(
    event: AgentEvent,
    *,
    messages: Sequence[AgentMessage],
) -> tuple[dict[str, object], ...]:
    if isinstance(event, AgentStartEvent):
        return ({"type": "agent_start"},)
    if isinstance(event, AgentEndEvent):
        return (
            {
                "type": "agent_end",
                "messages": [_agent_message_payload(message) for message in messages],
            },
        )
    if isinstance(event, TurnStartEvent):
        return ({"type": "turn_start", "turnIndex": event.turn, "timestamp": time()},)
    if isinstance(event, TurnEndEvent):
        return ({"type": "turn_end", "turnIndex": event.turn},)
    if isinstance(event, MessageStartEvent):
        return (
            {
                "type": "message_start",
                "message": _empty_message_payload(event.message_role),
                "messageRole": event.message_role,
            },
        )
    if isinstance(event, MessageDeltaEvent):
        return (
            {
                "type": "message_update",
                "message": {"role": "assistant", "content": event.delta},
                "assistantMessageEvent": {"type": event.type, "delta": event.delta},
            },
        )
    if isinstance(event, MessageEndEvent):
        return ({"type": "message_end", "message": _agent_message_payload(event.message)},)
    if isinstance(event, ToolExecutionStartEvent):
        return (
            {
                "type": "tool_call",
                "toolCallId": event.tool_call.id,
                "toolName": event.tool_call.name,
                "input": event.tool_call.arguments,
            },
            {
                "type": "tool_execution_start",
                "toolCallId": event.tool_call.id,
                "toolName": event.tool_call.name,
                "args": event.tool_call.arguments,
            },
        )
    if isinstance(event, ToolExecutionUpdateEvent):
        return (
            {
                "type": "tool_execution_update",
                "toolCallId": event.tool_call_id,
                "toolName": "",
                "args": {},
                "partialResult": {
                    "message": event.message,
                    "data": event.data,
                },
            },
        )
    if isinstance(event, ToolExecutionEndEvent):
        content = ({"type": "text", "text": event.result.content},)
        return (
            {
                "type": "tool_result",
                "toolCallId": event.result.tool_call_id,
                "toolName": event.result.name,
                "input": {},
                "content": content,
                "details": event.result.details,
                "isError": not event.result.ok,
            },
            {
                "type": "tool_execution_end",
                "toolCallId": event.result.tool_call_id,
                "toolName": event.result.name,
                "result": event.result.model_dump(mode="json"),
                "isError": not event.result.ok,
            },
        )
    return ()


def _agent_message_payload(message: AgentMessage) -> dict[str, object]:
    return cast(dict[str, object], message.model_dump(mode="json"))


def _empty_message_payload(role: str) -> dict[str, object]:
    if role == "tool":
        return {"role": "tool", "tool_call_id": "", "name": "", "content": ""}
    if role == "user":
        return {"role": "user", "content": ""}
    return {"role": "assistant", "content": "", "tool_calls": []}


def _json_compatible_value(value: object) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_compatible_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_compatible_value(item) for item in value]
    return str(value)


def parse_terminal_command(text: str) -> TerminalCommandRequest | None:
    """Parse input-bar terminal command syntax."""
    stripped = text.strip()
    if stripped.startswith("!!"):
        command = stripped[2:].strip()
        if not command:
            return None
        return TerminalCommandRequest(command=command, add_to_context=False)
    if stripped.startswith("!"):
        command = stripped[1:].strip()
        if not command:
            return None
        return TerminalCommandRequest(command=command, add_to_context=True)
    return None


def _apply_shell_command_prefix(command: str, prefix: str | None) -> str:
    stripped_prefix = prefix.strip() if prefix is not None else ""
    if not stripped_prefix:
        return command
    return f"{stripped_prefix}\n{command}"


def _missing_required_changed_globs(
    changed_files: list[str],
    required_globs: tuple[str, ...],
) -> list[str]:
    return [
        pattern
        for pattern in required_globs
        if not any(fnmatch.fnmatch(path, pattern) for path in changed_files)
    ]


def _category_summary(
    before: tuple[tuple[object, ...], ...],
    after: tuple[tuple[object, ...], ...],
) -> ReloadCategorySummary:
    return ReloadCategorySummary(
        before=len(before),
        after=len(after),
        changed=before != after,
    )


def _skill_signatures(skills: tuple[Skill, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (skill.name, str(skill.path), skill.description, skill.content) for skill in skills
    )


def _prompt_template_signatures(
    prompt_templates: tuple[PromptTemplate, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (template.name, str(template.path), template.description, template.content)
        for template in prompt_templates
    )


def _context_file_signatures(
    context_files: tuple[ProjectContextFile, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple((context_file.path, context_file.content) for context_file in context_files)


def _diagnostic_signatures(
    diagnostics: tuple[ResourceDiagnostic, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            diagnostic.kind,
            diagnostic.message,
            str(diagnostic.path) if diagnostic.path is not None else None,
            diagnostic.name,
            diagnostic.severity,
        )
        for diagnostic in diagnostics
    )


def _system_prompt_resource_signatures(
    *,
    skills: tuple[Skill, ...],
    context_files: tuple[ProjectContextFile, ...],
    tools: Sequence[AgentTool],
) -> tuple[tuple[object, ...], tuple[object, ...], tuple[object, ...]]:
    prompt_skills = tuple(
        (skill.name, str(skill.path), skill.description)
        for skill in sorted(skills, key=lambda item: item.name)
    )
    prompt_tools = tuple(
        (tool.name, tool.description, tool.prompt_snippet)
        for tool in sorted(tools, key=lambda item: item.name)
    )
    return (prompt_skills, _context_file_signatures(context_files), prompt_tools)


def _bash_session_environment(
    *,
    session_id: str | None,
    storage: SessionStorage,
    provider_name: str,
    model: str,
    thinking_level: ThinkingLevel,
) -> dict[str, str | None]:
    environment: dict[str, str | None] = {key: None for key in _BASH_SESSION_ENV_KEYS}
    if session_id:
        environment["TAU_SESSION_ID"] = session_id
    session_file = _session_storage_path(storage)
    if session_file is not None:
        environment["TAU_SESSION_FILE"] = str(session_file)
    environment["TAU_PROVIDER"] = provider_name
    environment["TAU_MODEL"] = model
    environment["TAU_REASONING_LEVEL"] = thinking_level
    return environment


def _session_storage_path(storage: SessionStorage) -> Path | None:
    path = getattr(storage, "path", None)
    if path is None:
        return None
    return Path(path).expanduser().resolve()


def _build_session_tools(
    config: CodingSessionConfig,
    *,
    extension_tools: tuple[AgentTool, ...] = (),
    bash_environment: BashEnvironment | None = None,
) -> list[AgentTool]:
    tools = (
        list(config.tools)
        if config.tools is not None
        else create_coding_tools(
            cwd=config.cwd,
            shell_path=config.shell_path,
            bash_environment=bash_environment,
            auto_resize_images=config.auto_resize_images,
        )
    )
    if config.tools is None:
        tools.extend(extension_tools)
    return _select_session_tools(
        tools,
        allowlist=config.tool_allowlist,
        denylist=config.tool_denylist,
        no_tools=config.no_tools,
        no_builtin_tools=config.no_builtin_tools,
    )


def _select_session_tools(
    tools: list[AgentTool],
    *,
    allowlist: tuple[str, ...] | None = None,
    denylist: tuple[str, ...] = (),
    no_tools: bool = False,
    no_builtin_tools: bool = False,
) -> list[AgentTool]:
    available = {tool.name for tool in tools}
    requested = set(allowlist or ()) | set(denylist)
    missing = sorted(requested - available)
    if missing:
        names = ", ".join(missing)
        available_names = ", ".join(sorted(available)) or "none"
        raise RuntimeError(f"Unknown tool name(s): {names}. Available tools: {available_names}")

    if allowlist is not None:
        selected_names = set(allowlist)
    elif no_tools:
        selected_names = set()
    elif no_builtin_tools:
        selected_names = available - set(BUILTIN_CODING_TOOL_NAMES)
    else:
        selected_names = available
    selected_names -= set(denylist)
    return [tool for tool in tools if tool.name in selected_names]


def _merge_skills_by_name(
    discovered: list[Skill],
    explicit: list[Skill],
) -> tuple[list[Skill], list[ResourceDiagnostic]]:
    skills_by_name = {skill.name: skill for skill in discovered}
    diagnostics: list[ResourceDiagnostic] = []
    for skill in explicit:
        previous = skills_by_name.get(skill.name)
        if previous is not None:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="skill",
                    name=skill.name,
                    path=skill.path,
                    message=f"overrides lower-precedence resource at {previous.path}",
                )
            )
        skills_by_name[skill.name] = skill
    return sorted(skills_by_name.values(), key=lambda item: item.name), diagnostics


def _merge_prompt_templates_by_name(
    discovered: list[PromptTemplate],
    explicit: list[PromptTemplate],
) -> tuple[list[PromptTemplate], list[ResourceDiagnostic]]:
    templates_by_name = {template.name: template for template in discovered}
    diagnostics: list[ResourceDiagnostic] = []
    for template in explicit:
        previous = templates_by_name.get(template.name)
        if previous is not None:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="prompt",
                    name=template.name,
                    path=template.path,
                    message=f"overrides lower-precedence resource at {previous.path}",
                )
            )
        templates_by_name[template.name] = template
    return sorted(templates_by_name.values(), key=lambda item: item.name), diagnostics


def _provider_settings_with_extension_providers(
    settings: ProviderSettings | None,
    providers: tuple[ProviderConfig, ...],
) -> ProviderSettings:
    updated = settings or ProviderSettings()
    for provider in providers:
        updated = upsert_provider(updated, provider)
    return updated


def _load_session_resources(
    resource_paths: TauResourcePaths,
    explicit_context_files: tuple[ProjectContextFile, ...],
    *,
    skill_paths: tuple[Path, ...] = (),
    prompt_template_paths: tuple[Path, ...] = (),
    theme_paths: tuple[Path, ...] = (),
    extension_paths: tuple[Path, ...] = (),
    extension_flag_values: Mapping[str, bool | str] | None = None,
    discover_skills: bool = True,
    discover_prompt_templates: bool = True,
    discover_themes: bool = True,
    discover_extensions: bool = True,
    discover_context_files: bool = True,
    default_project_trust: DefaultProjectTrust = "ask",
) -> SessionResources:
    effective_paths, trust_diagnostics = _project_trusted_resource_paths(
        resource_paths,
        default_project_trust=default_project_trust,
    )
    if discover_skills:
        loaded_skills, skill_diagnostics = load_skills_with_diagnostics(effective_paths)
    else:
        loaded_skills, skill_diagnostics = [], []
    explicit_skills, explicit_skill_diagnostics = load_skills_from_paths_with_diagnostics(
        skill_paths
    )
    loaded_skills, explicit_skill_override_diagnostics = _merge_skills_by_name(
        loaded_skills,
        explicit_skills,
    )
    if discover_prompt_templates:
        loaded_prompt_templates, prompt_diagnostics = load_prompt_templates_with_diagnostics(
            effective_paths
        )
    else:
        loaded_prompt_templates, prompt_diagnostics = [], []
    explicit_prompt_templates, explicit_prompt_diagnostics = (
        load_prompt_templates_from_paths_with_diagnostics(prompt_template_paths)
    )
    loaded_prompt_templates, explicit_prompt_override_diagnostics = _merge_prompt_templates_by_name(
        loaded_prompt_templates,
        explicit_prompt_templates,
    )
    if discover_context_files:
        discovered_context, context_diagnostics = discover_project_context_with_diagnostics(
            effective_paths
        )
    else:
        discovered_context, context_diagnostics = (), ()
    if discover_themes:
        custom_themes, theme_diagnostics = load_custom_tui_themes(effective_paths.themes_dirs)
    else:
        custom_themes, theme_diagnostics = {}, []
    explicit_themes, explicit_theme_diagnostics = load_custom_tui_themes_from_paths(theme_paths)
    custom_themes = {**custom_themes, **explicit_themes}
    set_custom_tui_themes(custom_themes)
    extensions = load_extension_tools(
        effective_paths,
        explicit_paths=extension_paths,
        discover_user_extensions=discover_extensions,
        flag_values=extension_flag_values,
    )
    return SessionResources(
        skills=tuple(loaded_skills),
        prompt_templates=tuple(loaded_prompt_templates),
        context_files=_merge_context_files(explicit_context_files, discovered_context),
        extensions=extensions.extensions,
        extension_tools=extensions.tools,
        extension_provider_configs=extensions.provider_configs,
        diagnostics=tuple(
            [
                *trust_diagnostics,
                *skill_diagnostics,
                *explicit_skill_diagnostics,
                *explicit_skill_override_diagnostics,
                *prompt_diagnostics,
                *explicit_prompt_diagnostics,
                *explicit_prompt_override_diagnostics,
                *context_diagnostics,
                *theme_diagnostics,
                *explicit_theme_diagnostics,
                *extensions.diagnostics,
            ]
        ),
    )


def _command_registry_with_extensions(
    base_registry: CommandRegistry,
    extensions: tuple[LoadedExtension, ...],
) -> tuple[CommandRegistry, tuple[ResourceDiagnostic, ...]]:
    registry = base_registry.copy()
    diagnostics: list[ResourceDiagnostic] = []
    command_counts: dict[str, int] = {}
    for extension in extensions:
        for command in extension.commands:
            command_counts[command.name] = command_counts.get(command.name, 0) + 1
    seen_counts: dict[str, int] = {}
    for extension in extensions:
        for command in extension.commands:
            seen_counts[command.name] = seen_counts.get(command.name, 0) + 1
            invocation_name = _extension_command_invocation_name(
                registry,
                command.name,
                occurrence=seen_counts[command.name],
                duplicate_count=command_counts[command.name],
            )
            slash_command = _extension_slash_command(
                extension,
                command,
                invocation_name=invocation_name,
            )
            try:
                registry.register(slash_command)
            except ValueError as exc:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="extension",
                        name=extension.name,
                        path=extension.path,
                        message=f"slash command /{command.name} ignored: {exc}",
                        severity="error",
                    )
                )
                continue
            if invocation_name != command.name:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="extension",
                        name=extension.name,
                        path=extension.path,
                        message=(
                            f"slash command /{command.name} registered as /{invocation_name} "
                            "because the original name was already taken"
                        ),
                        severity="warning",
                    )
                )
    return registry, tuple(diagnostics)


def _extension_command_invocation_name(
    registry: CommandRegistry,
    name: str,
    *,
    occurrence: int,
    duplicate_count: int,
) -> str:
    candidate = f"{name}:{occurrence}" if duplicate_count > 1 else name
    if registry.get(candidate) is None:
        return candidate
    suffix = occurrence if duplicate_count > 1 else 1
    while True:
        candidate = f"{name}:{suffix}"
        if registry.get(candidate) is None:
            return candidate
        suffix += 1


def _extension_slash_command(
    extension: LoadedExtension,
    command: ExtensionCommand,
    *,
    invocation_name: str | None = None,
) -> SlashCommand:
    name = invocation_name or command.name

    def handler(context: CommandContext) -> CommandResult:
        extension_context = ExtensionCommandContext(
            session=context.session,
            registry=context.registry,
            text=context.text,
            name=context.name,
            args=context.args,
            extension_name=extension.name,
            current_editor_text=context.current_editor_text,
            current_tools_expanded=context.show_tool_results,
            current_theme=context.current_theme,
        )
        result = _call_extension_command_handler(
            command.handler,
            context.args,
            extension_context,
        )
        if inspect.isawaitable(result):
            if not context.async_ui_supported:
                close = getattr(result, "close", None)
                if callable(close):
                    close()
                return CommandResult(
                    handled=True,
                    message=(
                        f"Extension command /{command.name} requires async UI support; "
                        "run it from the interactive TUI."
                    ),
                )
            return _await_extension_command_result(result, extension_context)
        return _extension_command_result(result, extension_context)

    return SlashCommand(
        name=name,
        usage=_extension_command_usage(command.usage, command.name, name),
        description=f"{command.description} (extension: {extension.name})",
        handler=handler,
        aliases=command.aliases if name == command.name else (),
        search_terms=command.search_terms,
        argument_hint=command.argument_hint,
        argument_completions=tuple(
            CommandArgumentCompletion(
                value=completion.value,
                description=completion.description,
            )
            for completion in command.argument_completions
        ),
        argument_completion_provider=command.argument_completion_provider,
        hidden=command.hidden,
        source=f"extension:{extension.name}",
    )


def _call_extension_command_handler(
    handler: Callable[..., object],
    args: str,
    extension_context: ExtensionCommandContext,
) -> object:
    """Call Tau `(ctx)` or Pi-style `(args, ctx)` extension command handlers."""
    try:
        parameters = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return handler(extension_context)
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    accepts_varargs = any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL
        for parameter in parameters.values()
    )
    if accepts_varargs or len(positional) >= 2:
        return handler(args, extension_context)
    return handler(extension_context)


def _call_extension_lifecycle_handler(
    handler: Callable[..., object],
    event: Mapping[str, object],
    extension_context: ExtensionShortcutContext,
) -> object:
    """Call Pi-style ``(event, ctx)`` or compact ``(event)`` lifecycle handlers."""
    try:
        parameters = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return handler(event, extension_context)
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    accepts_varargs = any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL
        for parameter in parameters.values()
    )
    if accepts_varargs or len(positional) >= 2:
        return handler(event, extension_context)
    return handler(event)


async def _await_extension_command_result(
    result: object,
    extension_context: ExtensionCommandContext,
) -> CommandResult:
    awaited = await result  # type: ignore[misc]
    return _extension_command_result(awaited, extension_context)


async def _await_extension_shortcut_result(
    result: object,
    extension_context: ExtensionShortcutContext,
) -> CommandResult:
    awaited = await result  # type: ignore[misc]
    return _extension_shortcut_result(awaited, extension_context)


def _extension_command_result(
    result: object,
    extension_context: ExtensionCommandContext,
) -> CommandResult:
    notifications = _extension_command_notifications(extension_context)
    status_updates = _extension_command_status_updates(extension_context)
    widget_updates = _extension_command_widget_updates(extension_context)
    working_indicator_update = _extension_command_working_indicator_update(extension_context)
    footer_update = _extension_command_footer_update(extension_context)
    header_update = _extension_command_header_update(extension_context)
    if isinstance(result, CommandResult):
        if extension_context.shutdown_requested and not result.exit_requested:
            result = replace(result, exit_requested=True)
        if extension_context.editor_text is not None and result.editor_text is None:
            result = replace(result, editor_text=extension_context.editor_text)
        if (
            extension_context.editor_insert_text is not None
            and result.editor_insert_text is None
        ):
            result = replace(
                result,
                editor_insert_text=extension_context.editor_insert_text,
            )
        if (
            extension_context.editor_paste_text is not None
            and result.editor_paste_text is None
        ):
            result = replace(
                result,
                editor_paste_text=extension_context.editor_paste_text,
            )
        if (
            extension_context.terminal_title_requested
            and not result.terminal_title_requested
        ):
            result = replace(
                result,
                terminal_title_requested=True,
                terminal_title=extension_context.terminal_title,
            )
        if notifications:
            result = replace(result, notifications=(*result.notifications, *notifications))
        if status_updates:
            result = replace(result, status_updates=(*result.status_updates, *status_updates))
        if widget_updates:
            result = replace(result, widget_updates=(*result.widget_updates, *widget_updates))
        if working_indicator_update is not None and result.working_indicator_update is None:
            result = replace(result, working_indicator_update=working_indicator_update)
        if footer_update is not None and result.footer_update is None:
            result = replace(result, footer_update=footer_update)
        if header_update is not None and result.header_update is None:
            result = replace(result, header_update=header_update)
        if extension_context.theme is not None and result.theme is None:
            result = replace(result, theme=extension_context.theme)
        if (
            extension_context.show_tool_results is not None
            and result.show_tool_results is None
        ):
            result = replace(result, show_tool_results=extension_context.show_tool_results)
        if (
            extension_context.hidden_thinking_label_requested
            and not result.hidden_thinking_label_requested
        ):
            result = replace(
                result,
                hidden_thinking_label_requested=True,
                hidden_thinking_label=extension_context.hidden_thinking_label,
            )
        if extension_context.user_message is not None and result.user_message is None:
            result = replace(
                result,
                user_message=extension_context.user_message,
                user_message_delivery=cast(
                    Literal["steer", "follow_up"],
                    extension_context.user_message_delivery,
                ),
            )
        return result
    if extension_context.editor_text is not None:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            editor_text=extension_context.editor_text,
            terminal_title_requested=extension_context.terminal_title_requested,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
        )
    if extension_context.editor_insert_text is not None:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            editor_insert_text=extension_context.editor_insert_text,
            terminal_title_requested=extension_context.terminal_title_requested,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
        )
    if extension_context.editor_paste_text is not None:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            editor_paste_text=extension_context.editor_paste_text,
            terminal_title_requested=extension_context.terminal_title_requested,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
        )
    if extension_context.terminal_title_requested:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            terminal_title_requested=True,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
        )
    if extension_context.user_message is not None:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            terminal_title_requested=extension_context.terminal_title_requested,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
            user_message=extension_context.user_message,
            user_message_delivery=cast(
                Literal["steer", "follow_up"],
                extension_context.user_message_delivery,
            ),
        )
    if result is None:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            terminal_title_requested=extension_context.terminal_title_requested,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
        )
    return CommandResult(
        handled=True,
        exit_requested=extension_context.shutdown_requested,
        terminal_title_requested=extension_context.terminal_title_requested,
        terminal_title=extension_context.terminal_title,
        message=str(result),
        notifications=notifications,
        status_updates=status_updates,
        widget_updates=widget_updates,
        working_indicator_update=working_indicator_update,
        footer_update=footer_update,
        header_update=header_update,
        theme=extension_context.theme,
        show_tool_results=extension_context.show_tool_results,
        hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
        hidden_thinking_label=extension_context.hidden_thinking_label,
    )


def _extension_shortcut_result(
    result: object,
    extension_context: ExtensionShortcutContext,
) -> CommandResult:
    notifications = _extension_command_notifications(extension_context)
    status_updates = _extension_command_status_updates(extension_context)
    widget_updates = _extension_command_widget_updates(extension_context)
    working_indicator_update = _extension_command_working_indicator_update(extension_context)
    footer_update = _extension_command_footer_update(extension_context)
    header_update = _extension_command_header_update(extension_context)
    if isinstance(result, CommandResult):
        if extension_context.shutdown_requested and not result.exit_requested:
            result = replace(result, exit_requested=True)
        if extension_context.editor_text is not None and result.editor_text is None:
            result = replace(result, editor_text=extension_context.editor_text)
        if (
            extension_context.editor_insert_text is not None
            and result.editor_insert_text is None
        ):
            result = replace(
                result,
                editor_insert_text=extension_context.editor_insert_text,
            )
        if (
            extension_context.editor_paste_text is not None
            and result.editor_paste_text is None
        ):
            result = replace(
                result,
                editor_paste_text=extension_context.editor_paste_text,
            )
        if (
            extension_context.terminal_title_requested
            and not result.terminal_title_requested
        ):
            result = replace(
                result,
                terminal_title_requested=True,
                terminal_title=extension_context.terminal_title,
            )
        if notifications:
            result = replace(result, notifications=(*result.notifications, *notifications))
        if status_updates:
            result = replace(result, status_updates=(*result.status_updates, *status_updates))
        if widget_updates:
            result = replace(result, widget_updates=(*result.widget_updates, *widget_updates))
        if working_indicator_update is not None and result.working_indicator_update is None:
            result = replace(result, working_indicator_update=working_indicator_update)
        if footer_update is not None and result.footer_update is None:
            result = replace(result, footer_update=footer_update)
        if header_update is not None and result.header_update is None:
            result = replace(result, header_update=header_update)
        if extension_context.theme is not None and result.theme is None:
            result = replace(result, theme=extension_context.theme)
        if (
            extension_context.show_tool_results is not None
            and result.show_tool_results is None
        ):
            result = replace(result, show_tool_results=extension_context.show_tool_results)
        if (
            extension_context.hidden_thinking_label_requested
            and not result.hidden_thinking_label_requested
        ):
            result = replace(
                result,
                hidden_thinking_label_requested=True,
                hidden_thinking_label=extension_context.hidden_thinking_label,
            )
        return result
    if extension_context.editor_text is not None:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            editor_text=extension_context.editor_text,
            terminal_title_requested=extension_context.terminal_title_requested,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
        )
    if extension_context.editor_insert_text is not None:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            editor_insert_text=extension_context.editor_insert_text,
            terminal_title_requested=extension_context.terminal_title_requested,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
        )
    if extension_context.editor_paste_text is not None:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            editor_paste_text=extension_context.editor_paste_text,
            terminal_title_requested=extension_context.terminal_title_requested,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
        )
    if extension_context.terminal_title_requested:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            terminal_title_requested=True,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
        )
    if result is None:
        return CommandResult(
            handled=True,
            exit_requested=extension_context.shutdown_requested,
            terminal_title_requested=extension_context.terminal_title_requested,
            terminal_title=extension_context.terminal_title,
            notifications=notifications,
            status_updates=status_updates,
            widget_updates=widget_updates,
            working_indicator_update=working_indicator_update,
            footer_update=footer_update,
            header_update=header_update,
            theme=extension_context.theme,
            show_tool_results=extension_context.show_tool_results,
            hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
            hidden_thinking_label=extension_context.hidden_thinking_label,
        )
    return CommandResult(
        handled=True,
        exit_requested=extension_context.shutdown_requested,
        terminal_title_requested=extension_context.terminal_title_requested,
        terminal_title=extension_context.terminal_title,
        message=str(result),
        notifications=notifications,
        status_updates=status_updates,
        widget_updates=widget_updates,
        working_indicator_update=working_indicator_update,
        footer_update=footer_update,
        header_update=header_update,
        theme=extension_context.theme,
        show_tool_results=extension_context.show_tool_results,
        hidden_thinking_label_requested=extension_context.hidden_thinking_label_requested,
        hidden_thinking_label=extension_context.hidden_thinking_label,
    )


def _extension_command_notifications(
    context: ExtensionCommandContext | ExtensionShortcutContext,
) -> tuple[CommandNotification, ...]:
    return tuple(
        CommandNotification(
            message=notification.message,
            severity=cast(
                Literal["information", "warning", "error"],
                notification.severity,
            ),
        )
        for notification in context.notifications
    )


def _extension_command_status_updates(
    context: ExtensionCommandContext | ExtensionShortcutContext,
) -> tuple[CommandStatusUpdate, ...]:
    return tuple(
        CommandStatusUpdate(key=update.key, text=update.text)
        for update in context.status_updates
    )


def _extension_command_widget_updates(
    context: ExtensionCommandContext | ExtensionShortcutContext,
) -> tuple[CommandWidgetUpdate, ...]:
    return tuple(
        CommandWidgetUpdate(
            key=update.key,
            lines=update.lines,
            placement=cast(CommandWidgetPlacement, update.placement),
        )
        for update in context.widget_updates
    )


def _extension_command_working_indicator_update(
    context: ExtensionCommandContext | ExtensionShortcutContext,
) -> CommandWorkingIndicatorUpdate | None:
    update = context.working_indicator_update
    if update is None:
        return None
    return CommandWorkingIndicatorUpdate(
        visible=update.visible,
        message_requested=update.message_requested,
        message=update.message,
        indicator_requested=update.indicator_requested,
        frames=update.frames,
        interval_ms=update.interval_ms,
    )


def _extension_command_footer_update(
    context: ExtensionCommandContext | ExtensionShortcutContext,
) -> CommandFooterUpdate | None:
    update = context.footer_update
    if update is None:
        return None
    return CommandFooterUpdate(lines=update.lines)


def _extension_command_header_update(
    context: ExtensionCommandContext | ExtensionShortcutContext,
) -> CommandHeaderUpdate | None:
    update = context.header_update
    if update is None:
        return None
    return CommandHeaderUpdate(lines=update.lines)


def _extension_command_usage(usage: str, original_name: str, invocation_name: str) -> str:
    if invocation_name == original_name:
        return usage
    original = f"/{original_name}"
    replacement = f"/{invocation_name}"
    if usage.startswith(original):
        return f"{replacement}{usage[len(original) :]}"
    return replacement


def _project_trusted_resource_paths(
    resource_paths: TauResourcePaths,
    *,
    default_project_trust: DefaultProjectTrust = "ask",
) -> tuple[TauResourcePaths, tuple[ResourceDiagnostic, ...]]:
    cwd = resource_paths.cwd
    if cwd is None:
        return resource_paths, ()
    tau_paths = resource_paths.paths or TauPaths(
        home=resource_paths.root,
        agents_home=resource_paths.agents_root or Path.home() / ".agents",
    )
    if not has_trust_requiring_project_resources(cwd, tau_paths):
        return resource_paths, ()
    store = ProjectTrustStore.from_resource_paths(resource_paths)
    decision = store.get(cwd)
    if decision is True:
        return resource_paths, ()
    if decision is None and default_project_trust == "always":
        return resource_paths, ()
    return (
        TauResourcePaths(
            root=resource_paths.root,
            cwd=None,
            agents_root=resource_paths.agents_root,
            paths=resource_paths.paths,
        ),
        (
            ResourceDiagnostic(
                kind="trust",
                path=cwd,
                message="project-local resources ignored until this project is trusted with /trust",
            ),
        ),
    )


def _provider_config_with_timeout(
    provider_config: ProviderConfig,
    *,
    timeout_seconds: float,
) -> ProviderConfig:
    return replace(provider_config, timeout_seconds=timeout_seconds)


def _merge_context_files(
    explicit: tuple[ProjectContextFile, ...],
    discovered: tuple[ProjectContextFile, ...],
) -> tuple[ProjectContextFile, ...]:
    merged: list[ProjectContextFile] = []
    seen: set[str] = set()
    for context_file in (*explicit, *discovered):
        if context_file.path in seen:
            continue
        seen.add(context_file.path)
        merged.append(context_file)
    return tuple(merged)


def default_session_path(cwd: Path) -> Path:
    """Return Tau's default user-home session path for a project cwd."""
    return TauPaths().default_session_path(cwd)


def jsonl_session_storage(path: str | Path) -> JsonlSessionStorage:
    """Convenience factory for local JSONL coding-session storage."""
    return JsonlSessionStorage(path)


def _session_storage_path(storage: SessionStorage) -> str | None:
    path = getattr(storage, "path", None)
    return None if path is None else str(path)
