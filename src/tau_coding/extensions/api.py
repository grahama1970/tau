"""Minimal extension-facing API for Tau coding sessions."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from tau_agent.tools import AgentTool
from tau_coding.provider_config import OpenAICompatibleProviderConfig, ProviderConfig

ExtensionCommandHandler = Callable[[Any], Any]
ExtensionShortcutHandler = Callable[[Any], Any]
ExtensionEntryRenderer = Callable[..., Any]
ExtensionMessageRenderer = Callable[..., Any]
ExtensionArgumentCompletionProvider = Callable[[str], Sequence[Any] | None]
ExtensionEventHandler = Callable[[Any], Any]
ExtensionLifecycleHandler = Callable[..., Any]
ThemeInfo = dict[str, str | None]
ContextUsageInfo = dict[str, int | float | None]
SystemPromptOptionsInfo = dict[str, Any]
CommandInfo = dict[str, Any]
ToolInfo = dict[str, Any]
ExecResultInfo = dict[str, str | int | bool]


@dataclass(frozen=True, slots=True)
class ExtensionCommand:
    """A slash command registered by a Tau extension."""

    name: str
    description: str
    handler: ExtensionCommandHandler
    usage: str
    aliases: tuple[str, ...] = ()
    search_terms: tuple[str, ...] = ()
    argument_hint: str | None = None
    argument_completions: tuple[ExtensionArgumentCompletion, ...] = ()
    argument_completion_provider: ExtensionArgumentCompletionProvider | None = None
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class ExtensionArgumentCompletion:
    """A static argument completion registered by an extension command."""

    value: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ExtensionShortcut:
    """A keyboard shortcut registered by a Tau extension."""

    key: str
    description: str
    handler: ExtensionShortcutHandler


@dataclass(frozen=True, slots=True)
class ExtensionFlag:
    """A CLI-style flag registered by a Tau extension."""

    name: str
    description: str | None
    type: str
    default: bool | str | None = None


@dataclass(slots=True)
class ExtensionShortcutContext:
    """Runtime context passed to extension shortcut handlers."""

    session: Any
    key: str
    extension_name: str
    current_editor_text: str = ""
    current_tools_expanded: bool = False
    current_theme: str | None = None
    shutdown_requested: bool = False
    editor_text: str | None = None
    editor_insert_text: str | None = None
    editor_paste_text: str | None = None
    terminal_title_requested: bool = False
    terminal_title: str | None = None
    notifications: list[ExtensionNotification] = field(default_factory=list)
    status_updates: list[ExtensionStatusUpdate] = field(default_factory=list)
    widget_updates: list[ExtensionWidgetUpdate] = field(default_factory=list)
    working_indicator_update: ExtensionWorkingIndicatorUpdate | None = None
    footer_update: ExtensionFooterUpdate | None = None
    header_update: ExtensionHeaderUpdate | None = None
    theme: str | None = None
    show_tool_results: bool | None = None
    hidden_thinking_label_requested: bool = False
    hidden_thinking_label: str | None = None
    ui: ExtensionCommandUi = field(init=False)

    def __post_init__(self) -> None:
        self.ui = ExtensionCommandUi(self)

    @property
    def has_ui(self) -> bool:
        """Return whether this shortcut has an interactive UI backend."""
        return _session_has_extension_ui(self.session)

    @property
    def hasUI(self) -> bool:  # noqa: N802 - Pi compatibility spelling.
        """Pi-compatible camelCase alias for has_ui."""
        return self.has_ui

    @property
    def mode(self) -> str:
        """Return the active extension mode."""
        return "tui" if self.has_ui else "print"

    @property
    def cwd(self) -> Any:
        """Return the active session working directory when available."""
        return getattr(self.session, "cwd", None)

    @property
    def session_manager(self) -> Any:
        """Return Tau's session manager when this session has one."""
        return _session_manager(self.session)

    @property
    def sessionManager(self) -> Any:  # noqa: N802
        """Pi-compatible camelCase alias for session_manager."""
        return self.session_manager

    @property
    def model(self) -> str | None:
        """Return the active session model when available."""
        return _session_model(self.session)

    @property
    def thinking_level(self) -> str | None:
        """Return the active Tau thinking level when available."""
        return _session_thinking_level(self.session)

    @property
    def thinkingLevel(self) -> str | None:  # noqa: N802
        """Pi-compatible camelCase alias for thinking_level."""
        return self.thinking_level

    async def set_model(self, model: str | Mapping[str, Any]) -> bool:
        """Set the active model for future turns when the session supports it."""
        return await _session_set_model(self.session, model)

    async def setModel(self, model: str | Mapping[str, Any]) -> bool:  # noqa: N802
        """Pi-compatible camelCase alias for set_model."""
        return await self.set_model(model)

    def get_thinking_level(self) -> str | None:
        """Return the active Tau thinking level when available."""
        return self.thinking_level

    def getThinkingLevel(self) -> str | None:  # noqa: N802
        """Pi-compatible camelCase alias for get_thinking_level."""
        return self.get_thinking_level()

    async def set_thinking_level(self, level: str) -> str:
        """Set the active Tau thinking level for future turns."""
        return await _session_set_thinking_level(self.session, level)

    async def setThinkingLevel(self, level: str) -> str:  # noqa: N802
        """Pi-compatible camelCase alias for set_thinking_level."""
        return await self.set_thinking_level(level)

    def is_idle(self) -> bool:
        """Return whether the session is not currently streaming."""
        return _session_is_idle(self.session)

    def isIdle(self) -> bool:  # noqa: N802
        """Pi-compatible camelCase alias for is_idle."""
        return self.is_idle()

    def has_pending_messages(self) -> bool:
        """Return whether queued steering or follow-up messages are pending."""
        return _session_has_pending_messages(self.session)

    def hasPendingMessages(self) -> bool:  # noqa: N802
        """Pi-compatible camelCase alias for has_pending_messages."""
        return self.has_pending_messages()

    async def wait_for_idle(self) -> None:
        """Wait until the active session is idle."""
        await _session_wait_for_idle(self.session)

    async def waitForIdle(self) -> None:  # noqa: N802
        """Pi-compatible camelCase alias for wait_for_idle."""
        await self.wait_for_idle()

    async def reload(self) -> object:
        """Reload session resources for future extension commands and turns."""
        return await _session_reload(self.session)

    async def compact(self, options: Mapping[str, Any] | None = None) -> str:
        """Run Tau's manual compaction path when the session supports it."""
        return await _session_compact(self.session, options)

    async def new_session(self, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Replace the active Tau session with a newly indexed session."""
        return await _session_new_session(self.session, options)

    async def newSession(  # noqa: N802
        self,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pi-compatible camelCase alias for new_session."""
        return await self.new_session(options)

    async def fork(
        self,
        entry_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fork the active Tau session from a selected tree entry."""
        return await _session_fork(self.session, entry_id, options)

    async def navigate_tree(
        self,
        target_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Navigate the active Tau session tree to a prior entry."""
        return await _session_navigate_tree(self.session, target_id, options)

    async def navigateTree(  # noqa: N802
        self,
        target_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pi-compatible camelCase alias for navigate_tree."""
        return await self.navigate_tree(target_id, options)

    async def switch_session(
        self,
        session_path: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Switch the active Tau session by id or indexed session path."""
        return await _session_switch_session(self.session, session_path, options)

    async def switchSession(  # noqa: N802
        self,
        session_path: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pi-compatible camelCase alias for switch_session."""
        return await self.switch_session(session_path, options)

    def is_project_trusted(self) -> bool:
        """Return whether project-local trust is active for this session."""
        return _session_project_trusted(self.session)

    def isProjectTrusted(self) -> bool:  # noqa: N802
        """Pi-compatible camelCase alias for is_project_trusted."""
        return self.is_project_trusted()

    def abort(self) -> None:
        """Request cancellation of the active agent operation, if supported."""
        _session_abort(self.session)

    @property
    def signal(self) -> Any:
        """Return the active Tau cancellation signal, or None when idle."""
        return _session_signal(self.session)

    def get_context_usage(self) -> ContextUsageInfo | None:
        """Return Pi-style context usage for the active session."""
        return _session_context_usage(self.session)

    def getContextUsage(self) -> ContextUsageInfo | None:  # noqa: N802
        """Pi-compatible camelCase alias for get_context_usage."""
        return self.get_context_usage()

    def get_system_prompt(self) -> str:
        """Return the current effective system prompt."""
        return _session_system_prompt(self.session)

    def getSystemPrompt(self) -> str:  # noqa: N802
        """Pi-compatible camelCase alias for get_system_prompt."""
        return self.get_system_prompt()

    def get_system_prompt_options(self) -> SystemPromptOptionsInfo:
        """Return the current Tau system-prompt construction inputs."""
        return _session_system_prompt_options(self.session)

    def getSystemPromptOptions(self) -> SystemPromptOptionsInfo:  # noqa: N802
        """Pi-compatible camelCase alias for get_system_prompt_options."""
        return self.get_system_prompt_options()

    def get_commands(self) -> tuple[CommandInfo, ...]:
        """Return visible slash-command metadata for the active session."""
        return _command_infos(getattr(self.session, "command_registry", None))

    def getCommands(self) -> tuple[CommandInfo, ...]:  # noqa: N802
        """Pi-compatible camelCase alias for get_commands."""
        return self.get_commands()

    def get_active_tools(self) -> tuple[str, ...]:
        """Return active tool names for future agent turns."""
        return _session_active_tools(self.session)

    def getActiveTools(self) -> tuple[str, ...]:  # noqa: N802
        """Pi-compatible camelCase alias for get_active_tools."""
        return self.get_active_tools()

    def get_all_tools(self) -> tuple[ToolInfo, ...]:
        """Return metadata for all session tools that can be activated."""
        return _session_all_tools(self.session)

    def getAllTools(self) -> tuple[ToolInfo, ...]:  # noqa: N802
        """Pi-compatible camelCase alias for get_all_tools."""
        return self.get_all_tools()

    def set_active_tools(self, tool_names: Sequence[str]) -> tuple[str, ...]:
        """Replace the active tool set for future agent turns."""
        return _session_set_active_tools(self.session, tool_names)

    def setActiveTools(self, tool_names: Sequence[str]) -> tuple[str, ...]:  # noqa: N802
        """Pi-compatible camelCase alias for set_active_tools."""
        return self.set_active_tools(tool_names)

    def get_session_name(self) -> str | None:
        """Return the indexed display name for the active session."""
        return _session_name(self.session)

    def getSessionName(self) -> str | None:  # noqa: N802
        """Pi-compatible camelCase alias for get_session_name."""
        return self.get_session_name()

    def set_session_name(self, name: str) -> str:
        """Set the indexed display name for the active session."""
        return _session_set_name(self.session, name)

    def setSessionName(self, name: str) -> str:  # noqa: N802
        """Pi-compatible camelCase alias for set_session_name."""
        return self.set_session_name(name)

    async def exec(
        self,
        command: str,
        args: Sequence[str] = (),
        options: Mapping[str, Any] | None = None,
    ) -> ExecResultInfo:
        """Execute a command without a shell and return Pi-style output metadata."""
        return await _session_exec(self.session, command, args, options)

    async def append_entry(self, custom_type: str, data: Mapping[str, Any] | None = None) -> str:
        """Append an extension-owned durable session entry."""
        return await _session_append_entry(self.session, custom_type, data)

    async def appendEntry(  # noqa: N802
        self,
        custom_type: str,
        data: Mapping[str, Any] | None = None,
    ) -> str:
        """Pi-compatible camelCase alias for append_entry."""
        return await self.append_entry(custom_type, data)

    async def send_message(
        self,
        message: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> str:
        """Append a Pi-style custom message as a durable Tau custom entry."""
        custom_type, data = _custom_message_entry_payload(message, options)
        return await _session_append_entry(self.session, custom_type, data)

    async def sendMessage(  # noqa: N802
        self,
        message: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> str:
        """Pi-compatible camelCase alias for send_message."""
        return await self.send_message(message, options)

    async def set_label(self, entry_id: str, label: str | None) -> str:
        """Set or clear a durable label on a branchable session entry."""
        return await _session_set_label(self.session, entry_id, label)

    async def setLabel(self, entry_id: str, label: str | None) -> str:  # noqa: N802
        """Pi-compatible camelCase alias for set_label."""
        return await self.set_label(entry_id, label)

    def notify(self, message: str, severity: str = "info") -> None:
        """Request that Tau show a TUI notification after the shortcut returns."""
        notification_message = str(message).strip()
        if not notification_message:
            raise ValueError("notify requires a non-empty message")
        self.notifications.append(
            ExtensionNotification(
                message=notification_message,
                severity=_normalize_notification_severity(severity),
            )
        )

    def get_editor_text(self) -> str:
        """Return the prompt editor text captured before this shortcut ran."""
        return self.current_editor_text

    def shutdown(self) -> None:
        """Request that Tau exit after the shortcut returns."""
        self.shutdown_requested = True

    def register_tool(self, tool: AgentTool) -> str:
        """Register a tool for future agent turns in this session."""
        register = getattr(self.session, "register_extension_tool", None)
        if not callable(register):
            raise RuntimeError("active session does not support runtime extension tools")
        return str(register(tool, extension_name=self.extension_name))

    def register_terminal_input_listener(
        self,
        handler: Callable[[str], Any],
    ) -> Callable[[], None]:
        """Register a terminal-input listener for the active TUI frontend."""
        if not callable(handler):
            raise TypeError("terminal input listener must be callable")
        register = getattr(self.session, "register_extension_terminal_input_listener", None)
        if not callable(register):
            return lambda: None
        return register(handler, extension_name=self.extension_name)

    def register_autocomplete_provider(
        self,
        factory: Callable[[Any], Any],
    ) -> Callable[[], None]:
        """Register an autocomplete provider factory for the active TUI frontend."""
        if not callable(factory):
            raise TypeError("autocomplete provider factory must be callable")
        register = getattr(self.session, "register_extension_autocomplete_provider", None)
        if not callable(register):
            return lambda: None
        return register(factory, extension_name=self.extension_name)

    def set_editor_text(self, text: str) -> None:
        """Request that Tau replace the prompt editor contents after the shortcut returns."""
        if not isinstance(text, str):
            raise TypeError("set_editor_text requires text")
        self.editor_text = text

    def insert_editor_text(self, text: str) -> None:
        """Request that Tau insert text into the prompt editor after the shortcut returns."""
        if not isinstance(text, str):
            raise TypeError("insert_editor_text requires text")
        self.editor_insert_text = text

    def paste_to_editor(self, text: str) -> None:
        """Request that Tau paste text into the prompt editor after the shortcut returns."""
        if not isinstance(text, str):
            raise TypeError("paste_to_editor requires text")
        self.editor_paste_text = text

    def set_title(self, title: str | None) -> None:
        """Request that Tau override or clear the terminal title."""
        if title is not None and not isinstance(title, str):
            raise TypeError("set_title requires text or None")
        self.terminal_title_requested = True
        self.terminal_title = title

    def set_status(self, key: str, text: str | None) -> None:
        """Request that Tau set or clear a persistent extension status line."""
        status_key = key.strip()
        if not status_key:
            raise ValueError("set_status requires a non-empty key")
        if text is not None and not isinstance(text, str):
            raise TypeError("set_status text must be text or None")
        self.status_updates.append(ExtensionStatusUpdate(key=status_key, text=text))

    def set_widget(
        self,
        key: str,
        lines: str | Sequence[str] | Callable[..., Any] | None,
        *,
        placement: str = "above_editor",
    ) -> None:
        """Request that Tau set or clear a prompt-region extension widget."""
        widget_key = key.strip()
        if not widget_key:
            raise ValueError("set_widget requires a non-empty key")
        normalized_placement = _normalize_widget_placement(placement)
        if callable(lines):
            self._set_widget_component(widget_key, lines, placement=normalized_placement)
            return
        self._set_widget_component(widget_key, None, placement=normalized_placement)
        self.widget_updates.append(
            ExtensionWidgetUpdate(
                key=widget_key,
                lines=_normalize_widget_lines(lines),
                placement=normalized_placement,
            )
        )

    def _set_widget_component(
        self,
        key: str,
        factory: Callable[..., Any] | None,
        *,
        placement: str,
    ) -> object:
        setter = getattr(self.session, "set_extension_widget_component", None)
        if not callable(setter):
            return None
        return setter(
            key,
            factory,
            extension_name=self.extension_name,
            placement=placement,
        )

    def set_working_message(self, message: str | None = None) -> None:
        """Request a custom running message, or restore Tau's default."""
        update = self.working_indicator_update or ExtensionWorkingIndicatorUpdate()
        self.working_indicator_update = replace(
            update,
            message_requested=True,
            message=None if message is None else str(message),
        )

    def set_working_visible(self, visible: bool) -> None:
        """Request whether Tau's built-in working indicator is visible."""
        update = self.working_indicator_update or ExtensionWorkingIndicatorUpdate()
        self.working_indicator_update = replace(update, visible=bool(visible))

    def set_working_indicator(self, options: Any = None) -> None:
        """Request custom working indicator frames, or restore Tau's default."""
        update = self.working_indicator_update or ExtensionWorkingIndicatorUpdate()
        frames, interval_ms = _normalize_working_indicator_options(options)
        self.working_indicator_update = replace(
            update,
            indicator_requested=True,
            frames=frames,
            interval_ms=interval_ms,
        )

    def set_footer(self, lines: str | Sequence[str] | Callable[..., Any] | None = None) -> None:
        """Request a custom footer, or restore Tau's built-in footer."""
        if callable(lines):
            self._set_chrome_component("footer", lines)
            return
        self._set_chrome_component("footer", None)
        self.footer_update = ExtensionFooterUpdate(lines=_normalize_footer_lines(lines))

    def set_header(self, lines: str | Sequence[str] | Callable[..., Any] | None = None) -> None:
        """Request a custom header, or restore Tau's built-in header."""
        if callable(lines):
            self._set_chrome_component("header", lines)
            return
        self._set_chrome_component("header", None)
        self.header_update = ExtensionHeaderUpdate(lines=_normalize_header_lines(lines))

    def _set_chrome_component(
        self,
        target: str,
        factory: Callable[..., Any] | None,
    ) -> object:
        setter = getattr(self.session, "set_extension_chrome_component", None)
        if not callable(setter):
            return None
        return setter(target, factory, extension_name=self.extension_name)

    def set_theme(self, theme: Any) -> dict[str, str | bool]:
        """Request a TUI theme switch after this shortcut returns."""
        theme_name = _theme_name_from_value(theme)
        if not theme_name:
            return {"success": False, "error": "theme name is required"}
        self.theme = theme_name
        return {"success": True, "error": ""}

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        """Request a custom hidden-thinking label, or restore Tau's default."""
        self.hidden_thinking_label_requested = True
        self.hidden_thinking_label = _normalize_optional_text(label)

    def get_all_themes(self) -> tuple[ThemeInfo, ...]:
        """Return available Tau TUI themes as Pi-style theme info records."""
        return _available_theme_infos()

    def get_theme(self, name: str) -> ThemeInfo | None:
        """Return a Pi-style theme info record by name without switching to it."""
        return _theme_info_by_name(name)

    def get_tools_expanded(self) -> bool:
        """Return whether tool results are currently expanded in the TUI."""
        return self.current_tools_expanded

    def set_tools_expanded(self, expanded: bool) -> None:
        """Request that Tau expand or collapse tool output after this shortcut returns."""
        self.show_tool_results = bool(expanded)


@dataclass(slots=True)
class ExtensionCommandContext:
    """Runtime context passed to extension slash-command handlers."""

    session: Any
    registry: Any
    text: str
    name: str
    args: str
    extension_name: str
    current_editor_text: str = ""
    current_tools_expanded: bool = False
    current_theme: str | None = None
    shutdown_requested: bool = False
    editor_text: str | None = None
    editor_insert_text: str | None = None
    editor_paste_text: str | None = None
    terminal_title_requested: bool = False
    terminal_title: str | None = None
    notifications: list[ExtensionNotification] = field(default_factory=list)
    status_updates: list[ExtensionStatusUpdate] = field(default_factory=list)
    widget_updates: list[ExtensionWidgetUpdate] = field(default_factory=list)
    working_indicator_update: ExtensionWorkingIndicatorUpdate | None = None
    footer_update: ExtensionFooterUpdate | None = None
    header_update: ExtensionHeaderUpdate | None = None
    theme: str | None = None
    show_tool_results: bool | None = None
    hidden_thinking_label_requested: bool = False
    hidden_thinking_label: str | None = None
    user_message: str | None = None
    user_message_delivery: str = "steer"
    ui: ExtensionCommandUi = field(init=False)

    def __post_init__(self) -> None:
        self.ui = ExtensionCommandUi(self)

    @property
    def has_ui(self) -> bool:
        """Return whether this command has an interactive UI backend."""
        return _session_has_extension_ui(self.session)

    @property
    def hasUI(self) -> bool:  # noqa: N802 - Pi compatibility spelling.
        """Pi-compatible camelCase alias for has_ui."""
        return self.has_ui

    @property
    def mode(self) -> str:
        """Return the active extension mode."""
        return "tui" if self.has_ui else "print"

    @property
    def cwd(self) -> Any:
        """Return the active session working directory when available."""
        return getattr(self.session, "cwd", None)

    @property
    def session_manager(self) -> Any:
        """Return Tau's session manager when this session has one."""
        return _session_manager(self.session)

    @property
    def sessionManager(self) -> Any:  # noqa: N802
        """Pi-compatible camelCase alias for session_manager."""
        return self.session_manager

    @property
    def model(self) -> str | None:
        """Return the active session model when available."""
        return _session_model(self.session)

    @property
    def thinking_level(self) -> str | None:
        """Return the active Tau thinking level when available."""
        return _session_thinking_level(self.session)

    @property
    def thinkingLevel(self) -> str | None:  # noqa: N802
        """Pi-compatible camelCase alias for thinking_level."""
        return self.thinking_level

    async def set_model(self, model: str | Mapping[str, Any]) -> bool:
        """Set the active model for future turns when the session supports it."""
        return await _session_set_model(self.session, model)

    async def setModel(self, model: str | Mapping[str, Any]) -> bool:  # noqa: N802
        """Pi-compatible camelCase alias for set_model."""
        return await self.set_model(model)

    def get_thinking_level(self) -> str | None:
        """Return the active Tau thinking level when available."""
        return self.thinking_level

    def getThinkingLevel(self) -> str | None:  # noqa: N802
        """Pi-compatible camelCase alias for get_thinking_level."""
        return self.get_thinking_level()

    async def set_thinking_level(self, level: str) -> str:
        """Set the active Tau thinking level for future turns."""
        return await _session_set_thinking_level(self.session, level)

    async def setThinkingLevel(self, level: str) -> str:  # noqa: N802
        """Pi-compatible camelCase alias for set_thinking_level."""
        return await self.set_thinking_level(level)

    def is_idle(self) -> bool:
        """Return whether the session is not currently streaming."""
        return _session_is_idle(self.session)

    def isIdle(self) -> bool:  # noqa: N802
        """Pi-compatible camelCase alias for is_idle."""
        return self.is_idle()

    def has_pending_messages(self) -> bool:
        """Return whether queued steering or follow-up messages are pending."""
        return _session_has_pending_messages(self.session)

    def hasPendingMessages(self) -> bool:  # noqa: N802
        """Pi-compatible camelCase alias for has_pending_messages."""
        return self.has_pending_messages()

    async def wait_for_idle(self) -> None:
        """Wait until the active session is idle."""
        await _session_wait_for_idle(self.session)

    async def waitForIdle(self) -> None:  # noqa: N802
        """Pi-compatible camelCase alias for wait_for_idle."""
        await self.wait_for_idle()

    async def reload(self) -> object:
        """Reload session resources for future extension commands and turns."""
        return await _session_reload(self.session)

    async def compact(self, options: Mapping[str, Any] | None = None) -> str:
        """Run Tau's manual compaction path when the session supports it."""
        return await _session_compact(self.session, options)

    async def new_session(self, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Replace the active Tau session with a newly indexed session."""
        return await _session_new_session(self.session, options)

    async def newSession(  # noqa: N802
        self,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pi-compatible camelCase alias for new_session."""
        return await self.new_session(options)

    async def fork(
        self,
        entry_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fork the active Tau session from a selected tree entry."""
        return await _session_fork(self.session, entry_id, options)

    async def navigate_tree(
        self,
        target_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Navigate the active Tau session tree to a prior entry."""
        return await _session_navigate_tree(self.session, target_id, options)

    async def navigateTree(  # noqa: N802
        self,
        target_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pi-compatible camelCase alias for navigate_tree."""
        return await self.navigate_tree(target_id, options)

    async def switch_session(
        self,
        session_path: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Switch the active Tau session by id or indexed session path."""
        return await _session_switch_session(self.session, session_path, options)

    async def switchSession(  # noqa: N802
        self,
        session_path: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pi-compatible camelCase alias for switch_session."""
        return await self.switch_session(session_path, options)

    def is_project_trusted(self) -> bool:
        """Return whether project-local trust is active for this session."""
        return _session_project_trusted(self.session)

    def isProjectTrusted(self) -> bool:  # noqa: N802
        """Pi-compatible camelCase alias for is_project_trusted."""
        return self.is_project_trusted()

    def abort(self) -> None:
        """Request cancellation of the active agent operation, if supported."""
        _session_abort(self.session)

    @property
    def signal(self) -> Any:
        """Return the active Tau cancellation signal, or None when idle."""
        return _session_signal(self.session)

    def get_context_usage(self) -> ContextUsageInfo | None:
        """Return Pi-style context usage for the active session."""
        return _session_context_usage(self.session)

    def getContextUsage(self) -> ContextUsageInfo | None:  # noqa: N802
        """Pi-compatible camelCase alias for get_context_usage."""
        return self.get_context_usage()

    def get_system_prompt(self) -> str:
        """Return the current effective system prompt."""
        return _session_system_prompt(self.session)

    def getSystemPrompt(self) -> str:  # noqa: N802
        """Pi-compatible camelCase alias for get_system_prompt."""
        return self.get_system_prompt()

    def get_system_prompt_options(self) -> SystemPromptOptionsInfo:
        """Return the current Tau system-prompt construction inputs."""
        return _session_system_prompt_options(self.session)

    def getSystemPromptOptions(self) -> SystemPromptOptionsInfo:  # noqa: N802
        """Pi-compatible camelCase alias for get_system_prompt_options."""
        return self.get_system_prompt_options()

    def get_commands(self) -> tuple[CommandInfo, ...]:
        """Return visible slash-command metadata for the active session."""
        return _command_infos(self.registry)

    def getCommands(self) -> tuple[CommandInfo, ...]:  # noqa: N802
        """Pi-compatible camelCase alias for get_commands."""
        return self.get_commands()

    def get_active_tools(self) -> tuple[str, ...]:
        """Return active tool names for future agent turns."""
        return _session_active_tools(self.session)

    def getActiveTools(self) -> tuple[str, ...]:  # noqa: N802
        """Pi-compatible camelCase alias for get_active_tools."""
        return self.get_active_tools()

    def get_all_tools(self) -> tuple[ToolInfo, ...]:
        """Return metadata for all session tools that can be activated."""
        return _session_all_tools(self.session)

    def getAllTools(self) -> tuple[ToolInfo, ...]:  # noqa: N802
        """Pi-compatible camelCase alias for get_all_tools."""
        return self.get_all_tools()

    def set_active_tools(self, tool_names: Sequence[str]) -> tuple[str, ...]:
        """Replace the active tool set for future agent turns."""
        return _session_set_active_tools(self.session, tool_names)

    def setActiveTools(self, tool_names: Sequence[str]) -> tuple[str, ...]:  # noqa: N802
        """Pi-compatible camelCase alias for set_active_tools."""
        return self.set_active_tools(tool_names)

    def get_session_name(self) -> str | None:
        """Return the indexed display name for the active session."""
        return _session_name(self.session)

    def getSessionName(self) -> str | None:  # noqa: N802
        """Pi-compatible camelCase alias for get_session_name."""
        return self.get_session_name()

    def set_session_name(self, name: str) -> str:
        """Set the indexed display name for the active session."""
        return _session_set_name(self.session, name)

    def setSessionName(self, name: str) -> str:  # noqa: N802
        """Pi-compatible camelCase alias for set_session_name."""
        return self.set_session_name(name)

    async def exec(
        self,
        command: str,
        args: Sequence[str] = (),
        options: Mapping[str, Any] | None = None,
    ) -> ExecResultInfo:
        """Execute a command without a shell and return Pi-style output metadata."""
        return await _session_exec(self.session, command, args, options)

    async def append_entry(self, custom_type: str, data: Mapping[str, Any] | None = None) -> str:
        """Append an extension-owned durable session entry."""
        return await _session_append_entry(self.session, custom_type, data)

    async def appendEntry(  # noqa: N802
        self,
        custom_type: str,
        data: Mapping[str, Any] | None = None,
    ) -> str:
        """Pi-compatible camelCase alias for append_entry."""
        return await self.append_entry(custom_type, data)

    async def send_message(
        self,
        message: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> str:
        """Append a Pi-style custom message as a durable Tau custom entry."""
        custom_type, data = _custom_message_entry_payload(message, options)
        return await _session_append_entry(self.session, custom_type, data)

    async def sendMessage(  # noqa: N802
        self,
        message: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> str:
        """Pi-compatible camelCase alias for send_message."""
        return await self.send_message(message, options)

    async def set_label(self, entry_id: str, label: str | None) -> str:
        """Set or clear a durable label on a branchable session entry."""
        return await _session_set_label(self.session, entry_id, label)

    async def setLabel(self, entry_id: str, label: str | None) -> str:  # noqa: N802
        """Pi-compatible camelCase alias for set_label."""
        return await self.set_label(entry_id, label)

    def notify(self, message: str, severity: str = "info") -> None:
        """Request that Tau show a TUI notification after the command returns."""
        notification_message = str(message).strip()
        if not notification_message:
            raise ValueError("notify requires a non-empty message")
        self.notifications.append(
            ExtensionNotification(
                message=notification_message,
                severity=_normalize_notification_severity(severity),
            )
        )

    def get_editor_text(self) -> str:
        """Return the prompt editor text captured before this handler ran."""
        return self.current_editor_text

    def shutdown(self) -> None:
        """Request that Tau exit after the command returns."""
        self.shutdown_requested = True

    def register_tool(self, tool: AgentTool) -> str:
        """Register a tool for future agent turns in this session."""
        register = getattr(self.session, "register_extension_tool", None)
        if not callable(register):
            raise RuntimeError("active session does not support runtime extension tools")
        return str(register(tool, extension_name=self.extension_name))

    def register_terminal_input_listener(
        self,
        handler: Callable[[str], Any],
    ) -> Callable[[], None]:
        """Register a terminal-input listener for the active TUI frontend."""
        if not callable(handler):
            raise TypeError("terminal input listener must be callable")
        register = getattr(self.session, "register_extension_terminal_input_listener", None)
        if not callable(register):
            return lambda: None
        return register(handler, extension_name=self.extension_name)

    def register_autocomplete_provider(
        self,
        factory: Callable[[Any], Any],
    ) -> Callable[[], None]:
        """Register an autocomplete provider factory for the active TUI frontend."""
        if not callable(factory):
            raise TypeError("autocomplete provider factory must be callable")
        register = getattr(self.session, "register_extension_autocomplete_provider", None)
        if not callable(register):
            return lambda: None
        return register(factory, extension_name=self.extension_name)

    def set_editor_text(self, text: str) -> None:
        """Request that Tau replace the prompt editor contents after the command returns."""
        if not isinstance(text, str):
            raise TypeError("set_editor_text requires text")
        self.editor_text = text

    def insert_editor_text(self, text: str) -> None:
        """Request that Tau insert text into the prompt editor after the command returns."""
        if not isinstance(text, str):
            raise TypeError("insert_editor_text requires text")
        self.editor_insert_text = text

    def paste_to_editor(self, text: str) -> None:
        """Request that Tau paste text into the prompt editor after the command returns."""
        if not isinstance(text, str):
            raise TypeError("paste_to_editor requires text")
        self.editor_paste_text = text

    def set_title(self, title: str | None) -> None:
        """Request that Tau override or clear the terminal title."""
        if title is not None and not isinstance(title, str):
            raise TypeError("set_title requires text or None")
        self.terminal_title_requested = True
        self.terminal_title = title

    def set_status(self, key: str, text: str | None) -> None:
        """Request that Tau set or clear a persistent extension status line."""
        status_key = key.strip()
        if not status_key:
            raise ValueError("set_status requires a non-empty key")
        if text is not None and not isinstance(text, str):
            raise TypeError("set_status text must be text or None")
        self.status_updates.append(ExtensionStatusUpdate(key=status_key, text=text))

    def set_widget(
        self,
        key: str,
        lines: str | Sequence[str] | Callable[..., Any] | None,
        *,
        placement: str = "above_editor",
    ) -> None:
        """Request that Tau set or clear a prompt-region extension widget."""
        widget_key = key.strip()
        if not widget_key:
            raise ValueError("set_widget requires a non-empty key")
        normalized_placement = _normalize_widget_placement(placement)
        if callable(lines):
            self._set_widget_component(widget_key, lines, placement=normalized_placement)
            return
        self._set_widget_component(widget_key, None, placement=normalized_placement)
        self.widget_updates.append(
            ExtensionWidgetUpdate(
                key=widget_key,
                lines=_normalize_widget_lines(lines),
                placement=normalized_placement,
            )
        )

    def _set_widget_component(
        self,
        key: str,
        factory: Callable[..., Any] | None,
        *,
        placement: str,
    ) -> object:
        setter = getattr(self.session, "set_extension_widget_component", None)
        if not callable(setter):
            return None
        return setter(
            key,
            factory,
            extension_name=self.extension_name,
            placement=placement,
        )

    def set_working_message(self, message: str | None = None) -> None:
        """Request a custom running message, or restore Tau's default."""
        update = self.working_indicator_update or ExtensionWorkingIndicatorUpdate()
        self.working_indicator_update = replace(
            update,
            message_requested=True,
            message=None if message is None else str(message),
        )

    def set_working_visible(self, visible: bool) -> None:
        """Request whether Tau's built-in working indicator is visible."""
        update = self.working_indicator_update or ExtensionWorkingIndicatorUpdate()
        self.working_indicator_update = replace(update, visible=bool(visible))

    def set_working_indicator(self, options: Any = None) -> None:
        """Request custom working indicator frames, or restore Tau's default."""
        update = self.working_indicator_update or ExtensionWorkingIndicatorUpdate()
        frames, interval_ms = _normalize_working_indicator_options(options)
        self.working_indicator_update = replace(
            update,
            indicator_requested=True,
            frames=frames,
            interval_ms=interval_ms,
        )

    def set_footer(self, lines: str | Sequence[str] | Callable[..., Any] | None = None) -> None:
        """Request a custom footer, or restore Tau's built-in footer."""
        if callable(lines):
            self._set_chrome_component("footer", lines)
            return
        self._set_chrome_component("footer", None)
        self.footer_update = ExtensionFooterUpdate(lines=_normalize_footer_lines(lines))

    def set_header(self, lines: str | Sequence[str] | Callable[..., Any] | None = None) -> None:
        """Request a custom header, or restore Tau's built-in header."""
        if callable(lines):
            self._set_chrome_component("header", lines)
            return
        self._set_chrome_component("header", None)
        self.header_update = ExtensionHeaderUpdate(lines=_normalize_header_lines(lines))

    def _set_chrome_component(
        self,
        target: str,
        factory: Callable[..., Any] | None,
    ) -> object:
        setter = getattr(self.session, "set_extension_chrome_component", None)
        if not callable(setter):
            return None
        return setter(target, factory, extension_name=self.extension_name)

    def set_theme(self, theme: Any) -> dict[str, str | bool]:
        """Request a TUI theme switch after this command returns."""
        theme_name = _theme_name_from_value(theme)
        if not theme_name:
            return {"success": False, "error": "theme name is required"}
        self.theme = theme_name
        return {"success": True, "error": ""}

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        """Request a custom hidden-thinking label, or restore Tau's default."""
        self.hidden_thinking_label_requested = True
        self.hidden_thinking_label = _normalize_optional_text(label)

    def get_all_themes(self) -> tuple[ThemeInfo, ...]:
        """Return available Tau TUI themes as Pi-style theme info records."""
        return _available_theme_infos()

    def get_theme(self, name: str) -> ThemeInfo | None:
        """Return a Pi-style theme info record by name without switching to it."""
        return _theme_info_by_name(name)

    def get_tools_expanded(self) -> bool:
        """Return whether tool results are currently expanded in the TUI."""
        return self.current_tools_expanded

    def set_tools_expanded(self, expanded: bool) -> None:
        """Request that Tau expand or collapse tool output after this command returns."""
        self.show_tool_results = bool(expanded)

    def set_editor_component(self, factory: Callable[..., Any] | None) -> object:
        """Request a PromptInput-compatible editor component override."""
        if factory is not None and not callable(factory):
            raise TypeError("editor component factory must be callable or None")
        setter = getattr(self.session, "set_extension_editor_component", None)
        if not callable(setter):
            return None
        return setter(factory, extension_name=self.extension_name)

    def get_editor_component(self) -> object:
        """Return the active editor component factory, when the TUI supports it."""
        getter = getattr(self.session, "get_extension_editor_component", None)
        if not callable(getter):
            return None
        return getter()

    def send_user_message(self, content: Any, *, deliver_as: str = "steer") -> None:
        """Request that Tau send or queue a user message after the command returns."""
        message = _normalize_user_message_content(content)
        if not message:
            raise ValueError("send_user_message requires non-empty text")
        delivery = _normalize_user_message_delivery(deliver_as)
        self.user_message = message
        self.user_message_delivery = delivery

    async def sendUserMessage(  # noqa: N802 - Pi compatibility spelling.
        self,
        content: Any,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        """Pi-compatible alias for sending a user message from an extension command."""
        deliver_as = _user_message_deliver_as_from_options(options)
        self.send_user_message(content, deliver_as=deliver_as)


class ExtensionCommandUi:
    """Pi-like UI helper facade for extension command handlers."""

    def __init__(self, context: ExtensionCommandContext | ExtensionShortcutContext) -> None:
        self._context = context

    async def select(
        self,
        title: str,
        options: Sequence[Any],
        opts: Mapping[str, Any] | None = None,
    ) -> str | None:
        """Ask the interactive UI to select one option, or return None on cancel."""
        normalized_options = tuple(str(option) for option in options)
        if not normalized_options:
            raise ValueError("select requires at least one option")
        result = await self._request_ui(
            "select",
            title=str(title),
            options=normalized_options,
            **_dialog_options_payload(opts),
        )
        return None if result is None else str(result)

    async def confirm(
        self,
        title: str,
        message: str = "",
        opts: Mapping[str, Any] | None = None,
    ) -> bool:
        """Ask the interactive UI for a yes/no confirmation."""
        result = await self._request_ui(
            "confirm",
            title=str(title),
            message=str(message),
            **_dialog_options_payload(opts),
        )
        return bool(result)

    async def input(
        self,
        title: str,
        placeholder: str = "",
        opts: Mapping[str, Any] | None = None,
        *,
        prefill: str = "",
    ) -> str | None:
        """Ask the interactive UI for one line of text, or return None on cancel."""
        result = await self._request_ui(
            "input",
            title=str(title),
            placeholder=str(placeholder),
            prefill=str(prefill),
            **_dialog_options_payload(opts),
        )
        return None if result is None else str(result)

    async def editor(self, title: str, prefill: str = "") -> str | None:
        """Ask the interactive UI for multi-line text, or return None on cancel."""
        result = await self._request_ui(
            "editor",
            title=str(title),
            prefill=str(prefill),
        )
        return None if result is None else str(result)

    async def custom(
        self,
        factory: Callable[..., Any],
        options: Mapping[str, Any] | None = None,
    ) -> Any:
        """Show a custom Textual-backed extension UI and return its result."""
        if not callable(factory):
            raise TypeError("custom UI factory must be callable")
        return await self._request_ui(
            "custom",
            factory=factory,
            options={} if options is None else dict(options),
        )

    def onTerminalInput(self, handler: Callable[[str], Any]) -> Callable[[], None]:  # noqa: N802
        """Pi-compatible alias for listening to prompt terminal input."""
        return self._context.register_terminal_input_listener(handler)

    def on_terminal_input(self, handler: Callable[[str], Any]) -> Callable[[], None]:
        """Listen to prompt terminal input."""
        return self._context.register_terminal_input_listener(handler)

    def addAutocompleteProvider(self, factory: Callable[[Any], Any]) -> Callable[[], None]:  # noqa: N802
        """Pi-compatible alias for stacking prompt autocomplete behavior."""
        return self._context.register_autocomplete_provider(factory)

    def add_autocomplete_provider(self, factory: Callable[[Any], Any]) -> Callable[[], None]:
        """Stack prompt autocomplete behavior."""
        return self._context.register_autocomplete_provider(factory)

    def setWorkingMessage(self, message: str | None = None) -> None:  # noqa: N802
        """Pi-compatible alias for setting the running message."""
        self._context.set_working_message(message)

    def setWorkingVisible(self, visible: bool) -> None:  # noqa: N802
        """Pi-compatible alias for showing or hiding the running indicator."""
        self._context.set_working_visible(visible)

    def setWorkingIndicator(self, options: Any = None) -> None:  # noqa: N802
        """Pi-compatible alias for setting custom running indicator frames."""
        self._context.set_working_indicator(options)

    def setHiddenThinkingLabel(self, label: str | None = None) -> None:  # noqa: N802
        """Pi-compatible alias for setting the hidden thinking label."""
        self._context.set_hidden_thinking_label(label)

    def setFooter(  # noqa: N802
        self,
        lines: str | Sequence[str] | Callable[..., Any] | None = None,
    ) -> None:
        """Pi-compatible alias for replacing or restoring Tau's footer."""
        self._context.set_footer(lines)

    def setHeader(  # noqa: N802
        self,
        lines: str | Sequence[str] | Callable[..., Any] | None = None,
    ) -> None:
        """Pi-compatible alias for replacing or restoring Tau's header."""
        self._context.set_header(lines)

    def setTitle(self, title: str | None) -> None:  # noqa: N802
        """Pi-compatible alias for setting the terminal title."""
        self._context.set_title(title)

    def setStatus(self, key: str, text: str | None) -> None:  # noqa: N802
        """Pi-compatible alias for setting extension status text."""
        self._context.set_status(key, text)

    def setWidget(
        self,
        key: str,
        lines: str | Sequence[str] | Callable[..., Any] | None,
        options: Mapping[str, Any] | None = None,
        *,
        placement: str = "above_editor",
    ) -> None:  # noqa: N802
        """Pi-compatible alias for setting prompt-region extension widget text."""
        self._context.set_widget(
            key,
            lines,
            placement=_widget_placement_from_options(options, placement),
        )

    def getAllThemes(self) -> tuple[ThemeInfo, ...]:  # noqa: N802
        """Pi-compatible alias for listing available Tau themes."""
        return self._context.get_all_themes()

    def getTheme(self, name: str) -> ThemeInfo | None:  # noqa: N802
        """Pi-compatible alias for looking up a Tau theme record by name."""
        return self._context.get_theme(name)

    def setTheme(self, theme: Any) -> dict[str, str | bool]:  # noqa: N802
        """Pi-compatible alias for switching Tau's theme by name or record."""
        return self._context.set_theme(theme)

    def getToolsExpanded(self) -> bool:  # noqa: N802
        """Pi-compatible alias for reading tool-result expansion state."""
        return self._context.get_tools_expanded()

    def setToolsExpanded(self, expanded: bool) -> None:  # noqa: N802
        """Pi-compatible alias for setting tool-result expansion state."""
        self._context.set_tools_expanded(expanded)

    def setEditorComponent(self, factory: Callable[..., Any] | None) -> object:  # noqa: N802
        """Pi-compatible alias for installing a PromptInput-compatible editor."""
        return self._context.set_editor_component(factory)

    def getEditorComponent(self) -> object:  # noqa: N802
        """Pi-compatible alias for returning the active editor factory."""
        return self._context.get_editor_component()

    @property
    def theme(self) -> ThemeInfo | None:
        """Return the active Tau theme info when the frontend supplied one."""
        current_theme = self._context.current_theme
        return None if current_theme is None else self._context.get_theme(current_theme)

    def set_working_message(self, message: str | None = None) -> None:
        """Set the running message."""
        self._context.set_working_message(message)

    def set_working_visible(self, visible: bool) -> None:
        """Show or hide the running indicator."""
        self._context.set_working_visible(visible)

    def set_working_indicator(self, options: Any = None) -> None:
        """Set custom running indicator frames."""
        self._context.set_working_indicator(options)

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        """Set the hidden thinking label, or restore Tau's default."""
        self._context.set_hidden_thinking_label(label)

    def set_footer(
        self,
        lines: str | Sequence[str] | Callable[..., Any] | None = None,
    ) -> None:
        """Replace or restore Tau's footer."""
        self._context.set_footer(lines)

    def set_header(
        self,
        lines: str | Sequence[str] | Callable[..., Any] | None = None,
    ) -> None:
        """Replace or restore Tau's header."""
        self._context.set_header(lines)

    def get_all_themes(self) -> tuple[ThemeInfo, ...]:
        """Return available Tau themes."""
        return self._context.get_all_themes()

    def get_theme(self, name: str) -> ThemeInfo | None:
        """Return a Tau theme record by name."""
        return self._context.get_theme(name)

    def set_theme(self, theme: Any) -> dict[str, str | bool]:
        """Switch Tau's theme by name or record."""
        return self._context.set_theme(theme)

    def get_tools_expanded(self) -> bool:
        """Return whether tool results are currently expanded in the TUI."""
        return self._context.get_tools_expanded()

    def set_tools_expanded(self, expanded: bool) -> None:
        """Expand or collapse tool output."""
        self._context.set_tools_expanded(expanded)

    def set_editor_component(self, factory: Callable[..., Any] | None) -> object:
        """Install a PromptInput-compatible editor component."""
        return self._context.set_editor_component(factory)

    def get_editor_component(self) -> object:
        """Return the active editor component factory."""
        return self._context.get_editor_component()

    async def _request_ui(self, method: str, **payload: Any) -> Any:
        request = getattr(self._context.session, "request_extension_ui", None)
        if not callable(request):
            raise RuntimeError("active session does not support extension UI requests")
        timeout_seconds = payload.pop("timeout_seconds", None)
        request_result = request(
            method=method,
            extension_name=self._context.extension_name,
            **payload,
        )
        if timeout_seconds is None:
            return await request_result
        try:
            return await asyncio.wait_for(request_result, timeout=float(timeout_seconds))
        except TimeoutError:
            return None

    def notify(self, message: str, severity: str = "info") -> None:
        """Request that Tau show a TUI notification after the command returns."""
        self._context.notify(message, severity)

    def get_editor_text(self) -> str:
        """Return the prompt editor text captured before this handler ran."""
        return self._context.get_editor_text()

    def getEditorText(self) -> str:  # noqa: N802
        """Pi-compatible alias for returning captured editor text."""
        return self._context.get_editor_text()

    def setEditorText(self, text: str) -> None:  # noqa: N802
        """Pi-compatible alias for replacing editor text."""
        self._context.set_editor_text(text)

    def insertEditorText(self, text: str) -> None:  # noqa: N802
        """Pi-compatible alias for inserting editor text."""
        self._context.insert_editor_text(text)

    def pasteToEditor(self, text: str) -> None:  # noqa: N802
        """Pi-compatible alias for paste-style editor insertion."""
        self._context.paste_to_editor(text)

    def set_editor_text(self, text: str) -> None:
        """Request that Tau replace the prompt editor contents after the command returns."""
        self._context.set_editor_text(text)

    def insert_editor_text(self, text: str) -> None:
        """Request that Tau insert text into the prompt editor after the command returns."""
        self._context.insert_editor_text(text)

    def paste_to_editor(self, text: str) -> None:
        """Paste text into the editor using Tau's large-paste handling."""
        self._context.paste_to_editor(text)

    def set_title(self, title: str | None) -> None:
        """Request that Tau override or clear the terminal title."""
        self._context.set_title(title)

    def set_status(self, key: str, text: str | None) -> None:
        """Request that Tau set or clear a persistent extension status line."""
        self._context.set_status(key, text)

    def set_widget(
        self,
        key: str,
        lines: str | Sequence[str] | Callable[..., Any] | None,
        options: Mapping[str, Any] | None = None,
        *,
        placement: str = "above_editor",
    ) -> None:
        """Request that Tau set or clear a prompt-region extension widget."""
        self._context.set_widget(
            key,
            lines,
            placement=_widget_placement_from_options(options, placement),
        )


class ExtensionAPI:
    """API object passed to an extension module's `setup(tau)` function."""

    def __init__(
        self,
        flag_values: Mapping[str, bool | str] | None = None,
        *,
        event_bus: ExtensionEventBus | None = None,
    ) -> None:
        self._tools: list[AgentTool] = []
        self._commands: list[ExtensionCommand] = []
        self._shortcuts: list[ExtensionShortcut] = []
        self._flags: dict[str, ExtensionFlag] = {}
        self._entry_renderers: dict[str, ExtensionEntryRenderer] = {}
        self._message_renderers: dict[str, ExtensionMessageRenderer] = {}
        self._provider_configs: dict[str, ProviderConfig] = {}
        self._event_handlers: dict[str, list[ExtensionLifecycleHandler]] = {}
        self.events = event_bus or ExtensionEventBus()
        self._flag_values: dict[str, bool | str] = {
            _normalize_flag_name(name): value for name, value in (flag_values or {}).items()
        }

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        """Return tools registered by this extension."""
        return tuple(self._tools)

    @property
    def commands(self) -> tuple[ExtensionCommand, ...]:
        """Return slash commands registered by this extension."""
        return tuple(self._commands)

    @property
    def shortcuts(self) -> tuple[ExtensionShortcut, ...]:
        """Return keyboard shortcuts registered by this extension."""
        return tuple(self._shortcuts)

    @property
    def flags(self) -> tuple[ExtensionFlag, ...]:
        """Return CLI-style flags registered by this extension."""
        return tuple(self._flags.values())

    @property
    def entry_renderers(self) -> Mapping[str, ExtensionEntryRenderer]:
        """Return Pi-style custom-entry renderers registered by this extension."""
        return dict(self._entry_renderers)

    @property
    def message_renderers(self) -> Mapping[str, ExtensionMessageRenderer]:
        """Return Pi-style custom-message renderers registered by this extension."""
        return dict(self._message_renderers)

    @property
    def provider_configs(self) -> tuple[ProviderConfig, ...]:
        """Return provider configs registered by this extension."""
        return tuple(self._provider_configs.values())

    @property
    def event_handlers(self) -> Mapping[str, tuple[ExtensionLifecycleHandler, ...]]:
        """Return Pi-style lifecycle handlers registered by this extension."""
        return {event: tuple(handlers) for event, handlers in self._event_handlers.items()}

    def on(self, event: str, handler: ExtensionLifecycleHandler) -> None:
        """Register a Pi-style lifecycle handler for Tau-emitted extension events."""
        normalized = str(event).strip()
        if not normalized:
            raise ValueError("extension event name must be non-empty")
        if not callable(handler):
            raise TypeError("extension event handler must be callable")
        self._event_handlers.setdefault(normalized, []).append(handler)

    def register_tool(self, tool: AgentTool) -> None:
        """Register an `AgentTool` for the current coding session."""
        if not isinstance(tool, AgentTool):
            raise TypeError("register_tool expects an AgentTool instance")
        if any(existing.name == tool.name for existing in self._tools):
            raise ValueError(f"Extension already registered tool: {tool.name}")
        self._tools.append(tool)

    def register_provider(
        self,
        provider_or_name: str | Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
    ) -> None:
        """Register a bounded OpenAI-compatible provider for this session."""
        if not isinstance(provider_or_name, str):
            raise NotImplementedError("native provider objects are not supported by Tau yet")
        name = provider_or_name.strip()
        if not name:
            raise ValueError("register_provider requires a provider name")
        if config is None:
            raise ValueError("register_provider requires a provider config")
        self._provider_configs[name] = _openai_compatible_provider_from_extension(
            name,
            config,
        )

    def registerProvider(  # noqa: N802
        self,
        provider_or_name: str | Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
    ) -> None:
        """Pi-compatible camelCase alias for register_provider."""
        self.register_provider(provider_or_name, config)

    def unregister_provider(self, name: str) -> None:
        """Remove a provider registered earlier in this extension load."""
        self._provider_configs.pop(str(name).strip(), None)

    def unregisterProvider(self, name: str) -> None:  # noqa: N802
        """Pi-compatible camelCase alias for unregister_provider."""
        self.unregister_provider(name)

    def register_command(
        self,
        name: str,
        *,
        description: str,
        handler: ExtensionCommandHandler,
        usage: str | None = None,
        aliases: tuple[str, ...] = (),
        search_terms: tuple[str, ...] = (),
        argument_hint: str | None = None,
        argument_completions: Sequence[Any] = (),
        argument_completion_provider: ExtensionArgumentCompletionProvider | None = None,
        hidden: bool = False,
    ) -> None:
        """Register a synchronous slash command for the current coding session."""
        normalized = name.strip().removeprefix("/").lower()
        if not normalized:
            raise ValueError("register_command requires a command name")
        if ":" in normalized or any(char.isspace() for char in normalized):
            raise ValueError("register_command names must not contain ':' or whitespace")
        if not callable(handler):
            raise TypeError("register_command handler must be callable")
        if argument_completion_provider is not None and not callable(
            argument_completion_provider
        ):
            raise TypeError("argument_completion_provider must be callable")
        if any(existing.name == normalized for existing in self._commands):
            raise ValueError(f"Extension already registered command: /{normalized}")
        self._commands.append(
            ExtensionCommand(
                name=normalized,
                description=description,
                handler=handler,
                usage=usage or f"/{normalized} [args]",
                aliases=tuple(alias.strip().removeprefix("/").lower() for alias in aliases),
                search_terms=tuple(term.strip().lower() for term in search_terms),
                argument_hint=argument_hint,
                argument_completions=_normalize_argument_completions(argument_completions),
                argument_completion_provider=argument_completion_provider,
                hidden=hidden,
            )
        )

    def registerCommand(  # noqa: N802
        self,
        name: str,
        options: Mapping[str, Any],
    ) -> None:
        """Pi-compatible camelCase alias for register_command."""
        self.register_command(
            name,
            description=str(options.get("description", "")),
            handler=options["handler"],
            usage=options.get("usage"),
            aliases=tuple(options.get("aliases", ())),
            search_terms=tuple(options.get("searchTerms", options.get("search_terms", ()))),
            argument_hint=options.get("argumentHint", options.get("argument_hint")),
            argument_completions=options.get(
                "argumentCompletions",
                options.get("argument_completions", ()),
            ),
            argument_completion_provider=options.get(
                "argumentCompletionProvider",
                options.get("argument_completion_provider"),
            ),
            hidden=bool(options.get("hidden", False)),
        )

    def register_shortcut(
        self,
        key: str,
        *,
        description: str,
        handler: ExtensionShortcutHandler,
    ) -> None:
        """Register a synchronous TUI keyboard shortcut for the current session."""
        normalized = key.strip().lower()
        if not normalized:
            raise ValueError("register_shortcut requires a key")
        if not callable(handler):
            raise TypeError("register_shortcut handler must be callable")
        if any(existing.key == normalized for existing in self._shortcuts):
            raise ValueError(f"Extension already registered shortcut: {normalized}")
        self._shortcuts.append(
            ExtensionShortcut(
                key=normalized,
                description=description,
                handler=handler,
            )
        )

    def registerShortcut(  # noqa: N802
        self,
        shortcut: str,
        options: Mapping[str, Any],
    ) -> None:
        """Pi-compatible camelCase alias for register_shortcut."""
        self.register_shortcut(
            shortcut,
            description=str(options.get("description", "")),
            handler=options["handler"],
        )

    def register_flag(
        self,
        name: str,
        *,
        description: str | None = None,
        type: str,
        default: bool | str | None = None,
    ) -> None:
        """Register a Pi-style extension flag and its optional default value."""
        normalized = _normalize_flag_name(name)
        if not normalized:
            raise ValueError("register_flag requires a flag name")
        if ":" in normalized or any(char.isspace() for char in normalized):
            raise ValueError("register_flag names must not contain ':' or whitespace")
        if type not in {"boolean", "string"}:
            raise ValueError("register_flag type must be 'boolean' or 'string'")
        if default is not None:
            if type == "boolean" and not isinstance(default, bool):
                raise TypeError("boolean extension flag default must be bool")
            if type == "string" and not isinstance(default, str):
                raise TypeError("string extension flag default must be str")
        if normalized in self._flags:
            raise ValueError(f"Extension already registered flag: --{normalized}")
        self._flags[normalized] = ExtensionFlag(
            name=normalized,
            description=description,
            type=type,
            default=default,
        )
        if normalized in self._flag_values:
            self._flag_values[normalized] = _coerce_flag_value(
                normalized,
                self._flag_values[normalized],
                type=type,
            )
        elif default is not None:
            self._flag_values[normalized] = default

    def registerFlag(  # noqa: N802
        self,
        name: str,
        options: Mapping[str, Any],
    ) -> None:
        """Pi-compatible camelCase alias for register_flag."""
        self.register_flag(
            name,
            description=options.get("description"),
            type=str(options["type"]),
            default=options.get("default"),
        )

    def register_entry_renderer(
        self,
        custom_type: str,
        renderer: ExtensionEntryRenderer,
    ) -> None:
        """Register a renderer for durable extension custom entries."""
        normalized = str(custom_type).strip()
        if not normalized:
            raise ValueError("register_entry_renderer requires a custom type")
        if not callable(renderer):
            raise TypeError("entry renderer must be callable")
        self._entry_renderers[normalized] = renderer

    def registerEntryRenderer(  # noqa: N802
        self,
        custom_type: str,
        renderer: ExtensionEntryRenderer,
    ) -> None:
        """Pi-compatible camelCase alias for register_entry_renderer."""
        self.register_entry_renderer(custom_type, renderer)

    def register_message_renderer(
        self,
        custom_type: str,
        renderer: ExtensionMessageRenderer,
    ) -> None:
        """Register a renderer for Pi-style custom messages."""
        normalized = str(custom_type).strip()
        if not normalized:
            raise ValueError("register_message_renderer requires a custom type")
        if not callable(renderer):
            raise TypeError("message renderer must be callable")
        self._message_renderers[normalized] = renderer

    def registerMessageRenderer(  # noqa: N802
        self,
        custom_type: str,
        renderer: ExtensionMessageRenderer,
    ) -> None:
        """Pi-compatible camelCase alias for register_message_renderer."""
        self.register_message_renderer(custom_type, renderer)

    def get_flag(self, name: str) -> bool | str | None:
        """Return the current value for a registered extension flag."""
        normalized = _normalize_flag_name(name)
        if normalized not in self._flags:
            return None
        return self._flag_values.get(normalized)

    def getFlag(self, name: str) -> bool | str | None:  # noqa: N802
        """Pi-compatible camelCase alias for get_flag."""
        return self.get_flag(name)


@dataclass(frozen=True, slots=True)
class ExtensionNotification:
    """Notification requested by an extension command."""

    message: str
    severity: str = "information"


@dataclass(frozen=True, slots=True)
class ExtensionStatusUpdate:
    """Persistent status update requested by an extension command."""

    key: str
    text: str | None


@dataclass(frozen=True, slots=True)
class ExtensionWidgetUpdate:
    """Prompt-region widget update requested by an extension command."""

    key: str
    lines: tuple[str, ...] | None
    placement: str = "above_editor"


@dataclass(frozen=True, slots=True)
class ExtensionWorkingIndicatorUpdate:
    """Working indicator update requested by a Tau extension."""

    visible: bool | None = None
    message_requested: bool = False
    message: str | None = None
    indicator_requested: bool = False
    frames: tuple[str, ...] | None = None
    interval_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ExtensionFooterUpdate:
    """Footer replacement requested by a Tau extension."""

    lines: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class ExtensionHeaderUpdate:
    """Header replacement requested by a Tau extension."""

    lines: tuple[str, ...] | None


class ExtensionEventBus:
    """Small Pi-compatible event bus shared by loaded extensions."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[ExtensionEventHandler]] = {}

    def emit(self, channel: str, data: Any = None) -> None:
        """Emit an event payload to current subscribers."""
        normalized = str(channel).strip()
        if not normalized:
            raise ValueError("event channel must be non-empty")
        for handler in tuple(self._handlers.get(normalized, ())):
            try:
                result = handler(data)
                if hasattr(result, "__await__"):
                    _schedule_event_handler_result(result)
            except Exception:
                continue

    def on(self, channel: str, handler: ExtensionEventHandler) -> Callable[[], None]:
        """Subscribe to a channel and return an unsubscribe callback."""
        normalized = str(channel).strip()
        if not normalized:
            raise ValueError("event channel must be non-empty")
        if not callable(handler):
            raise TypeError("event handler must be callable")
        handlers = self._handlers.setdefault(normalized, [])
        handlers.append(handler)

        def unsubscribe() -> None:
            with suppress(ValueError):
                handlers.remove(handler)

        return unsubscribe

    def clear(self) -> None:
        """Remove every registered event handler."""
        self._handlers.clear()


def _schedule_event_handler_result(result: Any) -> None:
    with suppress(RuntimeError):
        asyncio.get_running_loop().create_task(result)
        return
    close = getattr(result, "close", None)
    if callable(close):
        close()


def _normalize_argument_completions(
    values: Sequence[Any],
) -> tuple[ExtensionArgumentCompletion, ...]:
    completions: list[ExtensionArgumentCompletion] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, ExtensionArgumentCompletion):
            completion = value
        elif isinstance(value, str):
            completion = ExtensionArgumentCompletion(value=value)
        elif (
            isinstance(value, tuple)
            and len(value) == 2
            and isinstance(value[0], str)
            and (value[1] is None or isinstance(value[1], str))
        ):
            completion = ExtensionArgumentCompletion(value=value[0], description=value[1])
        else:
            raise TypeError(
                "argument_completions entries must be strings, "
                "(value, description) tuples, or ExtensionArgumentCompletion values"
            )
        normalized_value = completion.value.strip()
        if not normalized_value:
            raise ValueError("argument completion values must be non-empty")
        if normalized_value in seen:
            continue
        seen.add(normalized_value)
        completions.append(
            ExtensionArgumentCompletion(
                value=normalized_value,
                description=completion.description,
            )
        )
    return tuple(completions)


def _session_has_extension_ui(session: Any) -> bool:
    available = getattr(session, "extension_ui_available", None)
    if available is not None:
        return bool(available)
    return callable(getattr(session, "request_extension_ui", None))


def _command_infos(registry: Any) -> tuple[CommandInfo, ...]:
    list_commands = getattr(registry, "list_commands", None)
    if not callable(list_commands):
        return ()
    return tuple(_command_info(command) for command in list_commands())


def _command_info(command: Any) -> CommandInfo:
    source_value = getattr(command, "source", None)
    source = _command_info_source(source_value)
    source_info = {
        "source": source_value or source,
        "path": source_value,
    }
    completions = tuple(
        {
            "value": str(completion.value),
            "description": None
            if getattr(completion, "description", None) is None
            else str(completion.description),
        }
        for completion in getattr(command, "argument_completions", ())
    )
    return {
        "name": str(getattr(command, "name", "")),
        "description": str(getattr(command, "description", "")),
        "source": source,
        "sourceInfo": source_info,
        "usage": str(getattr(command, "usage", "")),
        "aliases": tuple(str(alias) for alias in getattr(command, "aliases", ())),
        "searchTerms": tuple(str(term) for term in getattr(command, "search_terms", ())),
        "argumentHint": getattr(command, "argument_hint", None),
        "argumentCompletions": completions,
    }


def _command_info_source(source: object) -> str:
    if isinstance(source, str):
        if source.startswith("extension:"):
            return "extension"
        if source.startswith("skill:"):
            return "skill"
        if source.startswith("prompt:"):
            return "prompt"
    return "prompt"


def _session_active_tools(session: Any) -> tuple[str, ...]:
    active_names = getattr(session, "active_tool_names", None)
    if active_names is not None:
        return tuple(str(name) for name in active_names)
    return tuple(str(getattr(tool, "name", "")) for tool in getattr(session, "tools", ()))


def _session_all_tools(session: Any) -> tuple[ToolInfo, ...]:
    all_tools = getattr(session, "all_tools", None)
    tools = all_tools if all_tools is not None else getattr(session, "tools", ())
    extension_sources = getattr(session, "extension_tool_sources", {})
    if not isinstance(extension_sources, Mapping):
        extension_sources = {}
    return tuple(_tool_info(tool, extension_sources=extension_sources) for tool in tools)


def _session_set_active_tools(session: Any, tool_names: Sequence[str]) -> tuple[str, ...]:
    setter = getattr(session, "set_active_tools", None)
    if callable(setter):
        return tuple(str(name) for name in setter(tuple(tool_names)))
    raise RuntimeError("active session does not support extension active tool controls")


def _session_name(session: Any) -> str | None:
    name = getattr(session, "session_title", None)
    return None if name is None else str(name)


def _session_set_name(session: Any, name: str) -> str:
    setter = getattr(session, "set_session_title", None)
    if callable(setter):
        return str(setter(name))
    raise RuntimeError("active session does not support extension session naming")


async def _session_exec(
    session: Any,
    command: str,
    args: Sequence[str],
    options: Mapping[str, Any] | None,
) -> ExecResultInfo:
    command_name = str(command).strip()
    if not command_name:
        raise ValueError("exec command must be non-empty")
    argv = [command_name, *(str(arg) for arg in args)]
    options = {} if options is None else options
    cwd = _exec_cwd(session, options.get("cwd"))
    timeout_s = _exec_timeout_seconds(options.get("timeout"))
    signal = options.get("signal")
    if bool(getattr(signal, "aborted", False)):
        return {"stdout": "", "stderr": "", "code": 1, "killed": True}

    process: asyncio.subprocess.Process | None = None
    killed = False
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_s,
            )
        except TimeoutError:
            killed = True
            process.kill()
            with suppress(ProcessLookupError):
                await process.wait()
            stdout_bytes, stderr_bytes = await process.communicate()
    except OSError as exc:
        return {
            "stdout": "",
            "stderr": str(exc),
            "code": 127,
            "killed": killed,
        }

    return {
        "stdout": stdout_bytes.decode("utf-8", errors="replace"),
        "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        "code": int(process.returncode or 0),
        "killed": killed,
    }


def _exec_cwd(session: Any, value: Any) -> Path:
    cwd = getattr(session, "cwd", Path.cwd()) if value is None else value
    return Path(str(cwd)).expanduser().resolve()


def _exec_timeout_seconds(value: Any) -> float | None:
    if value is None:
        return None
    timeout_ms = float(value)
    return timeout_ms / 1000 if timeout_ms > 0 else None


async def _session_append_entry(
    session: Any,
    custom_type: str,
    data: Mapping[str, Any] | None,
) -> str:
    append = getattr(session, "append_custom_entry", None)
    if callable(append):
        return str(await append(custom_type, data))
    raise RuntimeError("active session does not support extension custom entries")


async def _session_set_label(session: Any, entry_id: str, label: str | None) -> str:
    setter = getattr(session, "set_tree_entry_label", None)
    if callable(setter):
        return str(await setter(entry_id, label))
    raise RuntimeError("active session does not support extension labels")


def _tool_info(tool: Any, *, extension_sources: Mapping[str, object]) -> ToolInfo:
    name = str(getattr(tool, "name", ""))
    extension_source = extension_sources.get(name)
    if extension_source is None:
        source = "builtin"
        source_path = None
    else:
        source = "extension"
        source_path = f"extension:{extension_source}"
    return {
        "name": name,
        "description": str(getattr(tool, "description", "")),
        "parameters": getattr(tool, "input_schema", {}),
        "promptGuidelines": tuple(str(item) for item in getattr(tool, "prompt_guidelines", ())),
        "promptSnippet": getattr(tool, "prompt_snippet", None),
        "source": source,
        "sourceInfo": {
            "source": source,
            "path": source_path,
        },
    }


def _openai_compatible_provider_from_extension(
    name: str,
    config: Mapping[str, Any],
) -> OpenAICompatibleProviderConfig:
    unsupported_options = (
        "streamSimple",
        "stream_simple",
        "oauth",
        "refreshModels",
        "refresh_models",
    )
    for unsupported in unsupported_options:
        if config.get(unsupported) is not None:
            raise NotImplementedError(
                f"registerProvider {unsupported} is not supported by Tau yet"
            )
    api = str(config.get("api", config.get("type", "openai-compatible"))).strip()
    if api and api not in {
        "openai-compatible",
        "openai-chat-completions",
        "openai-responses",
    }:
        raise NotImplementedError(f"registerProvider api is not supported by Tau: {api}")
    base_url = str(config.get("baseUrl", config.get("base_url", ""))).strip().rstrip("/")
    if not base_url:
        raise ValueError("registerProvider requires baseUrl for Tau provider configs")
    api_key_env = _provider_api_key_env(config.get("apiKey", config.get("api_key")))
    models, context_windows = _provider_models_from_extension(config.get("models"))
    headers = config.get("headers", {})
    if not isinstance(headers, Mapping):
        raise TypeError("registerProvider headers must be a mapping")
    return OpenAICompatibleProviderConfig(
        name=name,
        base_url=base_url,
        api_key_env=api_key_env,
        credential_name=None,
        models=models,
        default_model=models[0],
        context_windows=context_windows,
        headers={str(key): str(value) for key, value in headers.items()},
    )


def _provider_api_key_env(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("registerProvider requires apiKey as an environment reference")
    text = value.strip()
    if text.startswith("${") and text.endswith("}"):
        env_name = text[2:-1].strip()
    elif text.startswith("$"):
        env_name = text[1:].strip()
    else:
        raise ValueError("registerProvider apiKey must reference an environment variable")
    if not env_name:
        raise ValueError("registerProvider apiKey environment variable is empty")
    return env_name


def _provider_models_from_extension(value: Any) -> tuple[tuple[str, ...], dict[str, int]]:
    if not isinstance(value, Sequence) or isinstance(value, str) or not value:
        raise ValueError("registerProvider requires at least one model")
    model_ids: list[str] = []
    context_windows: dict[str, int] = {}
    for item in value:
        if isinstance(item, str):
            model_id = item.strip()
            context_window = None
        elif isinstance(item, Mapping):
            model_id = str(item.get("id", "")).strip()
            context_window = item.get("contextWindow", item.get("context_window"))
        else:
            raise TypeError("registerProvider models must be strings or mappings")
        if not model_id:
            raise ValueError("registerProvider model ids must be non-empty")
        model_ids.append(model_id)
        if context_window is not None:
            context_windows[model_id] = int(context_window)
    return tuple(model_ids), context_windows


def _session_model(session: Any) -> str | None:
    model = getattr(session, "model", None)
    return None if model is None else str(model)


def _model_name(model: str | Mapping[str, Any]) -> str:
    if isinstance(model, Mapping):
        candidate = model.get("id") or model.get("model")
        model_name = "" if candidate is None else str(candidate).strip()
    else:
        model_name = str(model).strip()
    if not model_name:
        raise ValueError("model must be a non-empty string or object with id/model")
    return model_name


def _normalize_flag_name(name: str) -> str:
    return str(name).strip().removeprefix("--").lower()


def _coerce_flag_value(
    name: str,
    value: bool | str,
    *,
    type: str,
) -> bool | str:
    if type == "boolean":
        return True if isinstance(value, str) else value
    if type == "string":
        if isinstance(value, str):
            return value
        raise ValueError(f"Extension flag --{name} requires a value")
    raise ValueError("extension flag type must be 'boolean' or 'string'")


async def _session_set_model(session: Any, model: str | Mapping[str, Any]) -> bool:
    set_model = getattr(session, "set_model", None)
    if not callable(set_model):
        return False
    provider_name = _provider_name_from_model_mapping(model)
    if provider_name is not None:
        set_provider = getattr(session, "set_provider", None)
        if not callable(set_provider):
            return False
        result = set_provider(provider_name)
        if hasattr(result, "__await__"):
            await result
    result = set_model(_model_name(model))
    if hasattr(result, "__await__"):
        await result
    return True


def _provider_name_from_model_mapping(model: str | Mapping[str, Any]) -> str | None:
    if not isinstance(model, Mapping):
        return None
    raw_provider = model.get("provider") or model.get("providerName") or model.get("provider_name")
    if raw_provider is None:
        return None
    provider_name = str(raw_provider).strip()
    if not provider_name:
        raise ValueError("provider name must be non-empty")
    return provider_name


def _session_manager(session: Any) -> Any:
    return getattr(session, "session_manager", None)


def _session_thinking_level(session: Any) -> str | None:
    thinking_level = getattr(session, "thinking_level", None)
    return None if thinking_level is None else str(thinking_level)


async def _session_set_thinking_level(session: Any, level: str) -> str:
    set_thinking_level = getattr(session, "set_thinking_level", None)
    if not callable(set_thinking_level):
        raise RuntimeError("active session does not support thinking-level changes")
    result = set_thinking_level(level)
    if hasattr(result, "__await__"):
        result = await result
    return str(result)


def _session_is_idle(session: Any) -> bool:
    return not bool(getattr(session, "is_running", False))


def _session_has_pending_messages(session: Any) -> bool:
    queued = getattr(session, "queued_messages", None)
    count = getattr(queued, "count", None)
    if isinstance(count, int):
        return count > 0
    queued_steering = getattr(session, "queued_steering_messages", ())
    queued_follow_up = getattr(session, "queued_follow_up_messages", ())
    return bool(queued_steering or queued_follow_up)


async def _session_wait_for_idle(session: Any) -> None:
    waiter = getattr(session, "wait_for_idle", None)
    if callable(waiter):
        result = waiter()
        if hasattr(result, "__await__"):
            await result
        return
    for _ in range(200):
        if _session_is_idle(session):
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("session did not become idle within 10 seconds")


async def _session_reload(session: Any) -> object:
    reload = getattr(session, "reload", None)
    if not callable(reload):
        raise RuntimeError("active session does not support resource reload")
    result = reload()
    if hasattr(result, "__await__"):
        return await result
    return result


async def _session_compact(session: Any, options: Mapping[str, Any] | None) -> str:
    compact = getattr(session, "compact", None)
    if not callable(compact):
        raise RuntimeError("active session does not support compaction")
    instructions = None
    if options is not None:
        raw_instructions = options.get("instructions", options.get("customInstructions"))
        if raw_instructions is not None:
            instructions = str(raw_instructions)
    result = compact(instructions)
    if hasattr(result, "__await__"):
        result = await result
    return str(result)


async def _session_new_session(
    session: Any,
    options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    _reject_session_replacement_callbacks(options, action="newSession")
    if options and options.get("parentSession") is not None:
        raise NotImplementedError("newSession parentSession is not supported by Tau yet")
    new_session = getattr(session, "new_session", None)
    if not callable(new_session):
        raise RuntimeError("active session does not support new sessions")
    result = new_session()
    if hasattr(result, "__await__"):
        result = await result
    return _session_replacement_result(session, result)


async def _session_fork(
    session: Any,
    entry_id: str,
    options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    _reject_session_replacement_callbacks(options, action="fork")
    normalized_entry = str(entry_id).strip()
    if not normalized_entry:
        raise ValueError("fork requires a target entry id")
    options = options or {}
    position = options.get("position")
    if position is not None:
        position = str(position).strip().lower()
    fork_from_entry = getattr(session, "fork_from_entry", None)
    if callable(fork_from_entry):
        result = fork_from_entry(normalized_entry, position=position)
        if hasattr(result, "__await__"):
            result = await result
        return _session_tree_replacement_result(session, result)
    if position is not None:
        raise NotImplementedError("fork position requires Tau fork_from_entry support")
    clone_current_session = getattr(session, "clone_current_session", None)
    state = getattr(session, "state", None)
    if (
        callable(clone_current_session)
        and getattr(state, "active_leaf_id", None) == normalized_entry
    ):
        result = clone_current_session()
        if hasattr(result, "__await__"):
            result = await result
        return _session_replacement_result(session, result)
    raise RuntimeError("active session does not support forking from the requested entry")


async def _session_navigate_tree(
    session: Any,
    target_id: str,
    options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    branch_to_entry = getattr(session, "branch_to_entry", None)
    if not callable(branch_to_entry):
        raise RuntimeError("active session does not support tree navigation")
    normalized_target = str(target_id).strip()
    if not normalized_target:
        raise ValueError("navigateTree requires a target entry id")
    options = options or {}
    result = branch_to_entry(
        normalized_target,
        summarize=bool(options.get("summarize", False)),
        custom_instructions=_optional_option_text(options, "customInstructions"),
        replace_instructions=bool(
            options.get("replaceInstructions", options.get("replace_instructions", False))
        ),
    )
    if hasattr(result, "__await__"):
        result = await result
    return {
        "cancelled": False,
        "message": str(getattr(result, "message", result)),
        "inputPrefill": getattr(result, "input_prefill", None),
    }


async def _session_switch_session(
    session: Any,
    session_path: str,
    options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    _reject_session_replacement_callbacks(options, action="switchSession")
    target = str(session_path).strip()
    if not target:
        raise ValueError("switchSession requires a session id or indexed session path")
    resume = getattr(session, "resume", None)
    if not callable(resume):
        raise RuntimeError("active session does not support session switching")
    session_id = _resolve_session_switch_id(session, target)
    result = resume(session_id)
    if hasattr(result, "__await__"):
        result = await result
    return _session_replacement_result(session, result)


def _session_replacement_result(session: Any, result: object) -> dict[str, Any]:
    path = getattr(session, "session_path", None)
    return {
        "cancelled": False,
        "message": str(result),
        "sessionId": getattr(session, "session_id", None),
        "sessionPath": str(path) if path else None,
    }


def _session_tree_replacement_result(session: Any, result: object) -> dict[str, Any]:
    payload = _session_replacement_result(session, getattr(result, "message", result))
    payload["inputPrefill"] = getattr(result, "input_prefill", None)
    return payload


def _resolve_session_switch_id(session: Any, target: str) -> str:
    manager = _session_manager(session)
    if manager is None:
        return target
    get_session = getattr(manager, "get_session", None)
    if callable(get_session) and get_session(target) is not None:
        return target
    list_sessions = getattr(manager, "list_sessions", None)
    if not callable(list_sessions):
        return target

    target_path = Path(target).expanduser()
    target_resolved: Path | None = None
    with suppress(OSError):
        target_resolved = target_path.resolve()
    for record in tuple(list_sessions()):
        record_path = getattr(record, "path", None)
        if record_path is None:
            continue
        record_path = Path(record_path)
        if str(record_path) == target:
            return str(record.id)
        if target_resolved is not None:
            with suppress(OSError):
                if record_path.resolve() == target_resolved:
                    return str(record.id)
    return target


def _reject_session_replacement_callbacks(
    options: Mapping[str, Any] | None,
    *,
    action: str,
) -> None:
    if not options:
        return
    if options.get("setup") is not None:
        raise NotImplementedError(f"{action} setup callback is not supported by Tau yet")
    if options.get("withSession") is not None or options.get("with_session") is not None:
        raise NotImplementedError(f"{action} withSession callback is not supported by Tau yet")


def _optional_option_text(options: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = options.get(name)
        if value is not None:
            text = str(value).strip()
            return text or None
    return None


def _session_project_trusted(session: Any) -> bool:
    project_trust_state = getattr(session, "project_trust_state", None)
    if callable(project_trust_state):
        trust_state = project_trust_state()
        saved_decision = getattr(trust_state, "saved_decision", None)
        if saved_decision is not None:
            return bool(getattr(saved_decision, "decision", False))
    config = getattr(session, "_config", None)
    return getattr(config, "default_project_trust", None) == "always"


def _session_abort(session: Any) -> None:
    cancel = getattr(session, "cancel", None)
    if callable(cancel):
        cancel()


def _session_signal(session: Any) -> Any:
    if not bool(getattr(session, "is_running", False)):
        return None
    harness = getattr(session, "_harness", None)
    return getattr(harness, "_current_signal", None)


def _session_context_usage(session: Any) -> ContextUsageInfo | None:
    usage = getattr(session, "context_usage", None)
    if usage is None:
        return None
    tokens = getattr(usage, "total_tokens", None)
    context_window = getattr(session, "context_window_tokens", None)
    if not isinstance(tokens, int):
        return None
    if not isinstance(context_window, int) or context_window <= 0:
        context_window = 0
    percent = (tokens / context_window) * 100 if context_window else None
    return {
        "tokens": tokens,
        "contextWindow": context_window,
        "percent": percent,
    }


def _session_system_prompt(session: Any) -> str:
    system_prompt = getattr(session, "system_prompt", "")
    return str(system_prompt)


def _session_system_prompt_options(session: Any) -> SystemPromptOptionsInfo:
    config = getattr(session, "_config", None)
    harness = getattr(session, "_harness", None)
    harness_config = getattr(harness, "config", None)
    tools = tuple(getattr(harness_config, "tools", ()))
    context_files = tuple(getattr(session, "context_files", ()))
    skills = tuple(getattr(session, "skills", ()))
    prompt_guidelines: list[str] = []
    for tool in tools:
        for guideline in getattr(tool, "prompt_guidelines", ()):
            text = str(guideline).strip()
            if text and text not in prompt_guidelines:
                prompt_guidelines.append(text)

    return {
        "customPrompt": getattr(config, "custom_system_prompt", None),
        "selectedTools": tuple(str(getattr(tool, "name", "")) for tool in tools),
        "toolSnippets": {
            str(getattr(tool, "name", "")): str(snippet)
            for tool in tools
            if (snippet := getattr(tool, "prompt_snippet", None))
        },
        "promptGuidelines": tuple(prompt_guidelines),
        "appendSystemPrompt": getattr(config, "append_system_prompt", None),
        "cwd": str(getattr(session, "cwd", "")),
        "contextFiles": tuple(
            {
                "path": str(getattr(context_file, "path", "")),
                "content": str(getattr(context_file, "content", "")),
            }
            for context_file in context_files
        ),
        "skills": tuple(
            {
                "name": str(getattr(skill, "name", "")),
                "path": str(getattr(skill, "path", "")),
                "description": getattr(skill, "description", None),
                "content": str(getattr(skill, "content", "")),
            }
            for skill in skills
        ),
    }


def _theme_name_from_value(theme: Any) -> str | None:
    if isinstance(theme, str):
        name = theme.strip()
        return name or None
    raw_name = theme.get("name") if isinstance(theme, Mapping) else getattr(theme, "name", None)
    if raw_name is None:
        return None
    name = str(raw_name).strip()
    return name or None


def _available_theme_infos() -> tuple[ThemeInfo, ...]:
    from tau_coding.tui.config import available_tui_theme_names

    return tuple({"name": name, "path": None} for name in available_tui_theme_names())


def _theme_info_by_name(name: str) -> ThemeInfo | None:
    normalized_name = str(name).strip()
    if not normalized_name:
        return None
    for theme_info in _available_theme_infos():
        if theme_info["name"] == normalized_name:
            return theme_info
    return None


def _normalize_user_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Mapping):
        return _normalize_user_message_content_part(content)
    if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
        text_parts = [
            part_text
            for part in content
            if (part_text := _normalize_user_message_content_part(part))
        ]
        return "\n".join(text_parts).strip()
    return str(content).strip()


def _normalize_user_message_content_part(part: Any) -> str:
    part_type = ""
    text_value: Any = None
    if isinstance(part, Mapping):
        part_type = str(part.get("type", "")).strip().lower()
        text_value = part.get("text")
    else:
        part_type = str(getattr(part, "type", "")).strip().lower()
        text_value = getattr(part, "text", None)
    if part_type and part_type != "text":
        return ""
    if text_value is None:
        return ""
    return str(text_value).strip()


def _custom_message_entry_payload(
    message: Mapping[str, Any],
    options: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    if options is not None:
        if bool(options.get("triggerTurn", options.get("trigger_turn", False))):
            raise NotImplementedError(
                "sendMessage triggerTurn requires Tau custom-message agent delivery"
            )
        deliver_as = options.get("deliverAs", options.get("deliver_as"))
        if deliver_as is not None:
            raise NotImplementedError(
                "sendMessage deliverAs requires Tau custom-message agent delivery"
            )
    custom_type = str(message.get("customType", message.get("custom_type", ""))).strip()
    if not custom_type:
        raise ValueError("sendMessage requires a non-empty customType")
    return custom_type, {
        "content": message.get("content", []),
        "display": bool(message.get("display", True)),
        "details": message.get("details"),
    }


def _user_message_deliver_as_from_options(options: Mapping[str, Any] | None) -> str:
    if options is None:
        return "steer"
    value = options.get("deliverAs", options.get("deliver_as", "steer"))
    return str(value)


def _normalize_user_message_delivery(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"steer", "follow_up"}:
        return normalized
    if normalized == "followup":
        return "follow_up"
    raise ValueError("deliver_as must be 'steer' or 'follow_up'")


def _normalize_notification_severity(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"", "info", "information"}:
        return "information"
    if normalized == "warning":
        return "warning"
    if normalized == "error":
        return "error"
    raise ValueError("notification severity must be 'info', 'warning', or 'error'")


def _normalize_widget_lines(lines: str | Sequence[str] | None) -> tuple[str, ...] | None:
    if lines is None:
        return None
    if isinstance(lines, str):
        return tuple(lines.splitlines() or (lines,))
    return tuple(str(line) for line in lines)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_widget_placement(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"", "above", "above_editor", "aboveeditor"}:
        return "above_editor"
    if normalized in {"below", "below_editor", "beloweditor"}:
        return "below_editor"
    raise ValueError("widget placement must be 'above_editor' or 'below_editor'")


def _dialog_options_payload(options: Mapping[str, Any] | None) -> dict[str, float]:
    if options is None:
        return {}
    if not isinstance(options, Mapping):
        raise TypeError("dialog options must be a mapping")
    signal = options.get("signal")
    if signal is not None:
        raise NotImplementedError("AbortSignal-style dialog dismissal is not supported")
    if "timeout" not in options:
        return {}
    timeout_ms = options.get("timeout")
    if timeout_ms is None:
        return {}
    try:
        timeout_seconds = float(timeout_ms) / 1000.0
    except (TypeError, ValueError) as exc:
        raise TypeError("dialog timeout must be a number of milliseconds") from exc
    if timeout_seconds <= 0:
        raise ValueError("dialog timeout must be greater than zero")
    return {"timeout_seconds": timeout_seconds}


def _widget_placement_from_options(
    options: Mapping[str, Any] | None,
    fallback: str,
) -> str:
    if options is None:
        return fallback
    if not isinstance(options, Mapping):
        raise TypeError("widget options must be a mapping")
    placement = options.get("placement", fallback)
    return str(placement)


def _normalize_working_indicator_options(
    options: Any,
) -> tuple[tuple[str, ...] | None, int | None]:
    if options is None:
        return None, None
    if isinstance(options, str):
        return (options,), None
    if isinstance(options, dict):
        raw_frames = options.get("frames")
        raw_interval = options.get("intervalMs", options.get("interval_ms"))
        interval_ms = None
        if raw_interval is not None:
            interval_ms = int(raw_interval)
            if interval_ms <= 0:
                raise ValueError("working indicator intervalMs must be positive")
        if raw_frames is None:
            return None, interval_ms
        if isinstance(raw_frames, str):
            return (raw_frames,), interval_ms
        return tuple(str(frame) for frame in raw_frames), interval_ms
    frames = tuple(str(frame) for frame in options)
    return frames, None


def _normalize_footer_lines(lines: str | Sequence[str] | None) -> tuple[str, ...] | None:
    if lines is None:
        return None
    if isinstance(lines, str):
        return tuple(lines.splitlines() or (lines,))
    return tuple(str(line) for line in lines)


def _normalize_header_lines(lines: str | Sequence[str] | None) -> tuple[str, ...] | None:
    if lines is None:
        return None
    if isinstance(lines, str):
        return tuple(lines.splitlines() or (lines,))
    return tuple(str(line) for line in lines)
