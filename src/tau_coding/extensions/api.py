"""Minimal extension-facing API for Tau coding sessions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from tau_agent.tools import AgentTool

ExtensionCommandHandler = Callable[[Any], Any]
ExtensionShortcutHandler = Callable[[Any], Any]
ExtensionArgumentCompletionProvider = Callable[[str], Sequence[Any] | None]


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


@dataclass(slots=True)
class ExtensionShortcutContext:
    """Runtime context passed to extension shortcut handlers."""

    session: Any
    key: str
    extension_name: str
    current_editor_text: str = ""
    current_tools_expanded: bool = False
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
        lines: str | Sequence[str] | None,
        *,
        placement: str = "above_editor",
    ) -> None:
        """Request that Tau set or clear a prompt-region extension widget."""
        widget_key = key.strip()
        if not widget_key:
            raise ValueError("set_widget requires a non-empty key")
        self.widget_updates.append(
            ExtensionWidgetUpdate(
                key=widget_key,
                lines=_normalize_widget_lines(lines),
                placement=_normalize_widget_placement(placement),
            )
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

    def set_footer(self, lines: str | Sequence[str] | None = None) -> None:
        """Request a static custom footer, or restore Tau's built-in footer."""
        self.footer_update = ExtensionFooterUpdate(lines=_normalize_footer_lines(lines))

    def set_header(self, lines: str | Sequence[str] | None = None) -> None:
        """Request a static custom header, or restore Tau's built-in header."""
        self.header_update = ExtensionHeaderUpdate(lines=_normalize_header_lines(lines))

    def set_theme(self, theme: str) -> dict[str, str | bool]:
        """Request a TUI theme switch after this shortcut returns."""
        theme_name = str(theme).strip()
        if not theme_name:
            return {"success": False, "error": "theme name is required"}
        self.theme = theme_name
        return {"success": True, "error": ""}

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
        lines: str | Sequence[str] | None,
        *,
        placement: str = "above_editor",
    ) -> None:
        """Request that Tau set or clear a prompt-region extension widget."""
        widget_key = key.strip()
        if not widget_key:
            raise ValueError("set_widget requires a non-empty key")
        self.widget_updates.append(
            ExtensionWidgetUpdate(
                key=widget_key,
                lines=_normalize_widget_lines(lines),
                placement=_normalize_widget_placement(placement),
            )
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

    def set_footer(self, lines: str | Sequence[str] | None = None) -> None:
        """Request a static custom footer, or restore Tau's built-in footer."""
        self.footer_update = ExtensionFooterUpdate(lines=_normalize_footer_lines(lines))

    def set_header(self, lines: str | Sequence[str] | None = None) -> None:
        """Request a static custom header, or restore Tau's built-in header."""
        self.header_update = ExtensionHeaderUpdate(lines=_normalize_header_lines(lines))

    def set_theme(self, theme: str) -> dict[str, str | bool]:
        """Request a TUI theme switch after this command returns."""
        theme_name = str(theme).strip()
        if not theme_name:
            return {"success": False, "error": "theme name is required"}
        self.theme = theme_name
        return {"success": True, "error": ""}

    def get_tools_expanded(self) -> bool:
        """Return whether tool results are currently expanded in the TUI."""
        return self.current_tools_expanded

    def set_tools_expanded(self, expanded: bool) -> None:
        """Request that Tau expand or collapse tool output after this command returns."""
        self.show_tool_results = bool(expanded)

    def send_user_message(self, text: str, *, deliver_as: str = "steer") -> None:
        """Request that Tau send or queue a user message after the command returns."""
        message = text.strip()
        if not message:
            raise ValueError("send_user_message requires non-empty text")
        delivery = _normalize_user_message_delivery(deliver_as)
        self.user_message = message
        self.user_message_delivery = delivery


class ExtensionCommandUi:
    """Pi-like UI helper facade for extension command handlers."""

    def __init__(self, context: ExtensionCommandContext | ExtensionShortcutContext) -> None:
        self._context = context

    async def select(self, title: str, options: Sequence[Any]) -> str | None:
        """Ask the interactive UI to select one option, or return None on cancel."""
        normalized_options = tuple(str(option) for option in options)
        if not normalized_options:
            raise ValueError("select requires at least one option")
        result = await self._request_ui(
            "select",
            title=str(title),
            options=normalized_options,
        )
        return None if result is None else str(result)

    async def confirm(self, title: str, message: str = "") -> bool:
        """Ask the interactive UI for a yes/no confirmation."""
        result = await self._request_ui(
            "confirm",
            title=str(title),
            message=str(message),
        )
        return bool(result)

    async def input(
        self,
        title: str,
        placeholder: str = "",
        *,
        prefill: str = "",
    ) -> str | None:
        """Ask the interactive UI for one line of text, or return None on cancel."""
        result = await self._request_ui(
            "input",
            title=str(title),
            placeholder=str(placeholder),
            prefill=str(prefill),
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

    def setWorkingMessage(self, message: str | None = None) -> None:  # noqa: N802
        """Pi-compatible alias for setting the running message."""
        self._context.set_working_message(message)

    def setWorkingVisible(self, visible: bool) -> None:  # noqa: N802
        """Pi-compatible alias for showing or hiding the running indicator."""
        self._context.set_working_visible(visible)

    def setWorkingIndicator(self, options: Any = None) -> None:  # noqa: N802
        """Pi-compatible alias for setting custom running indicator frames."""
        self._context.set_working_indicator(options)

    def setFooter(self, lines: str | Sequence[str] | None = None) -> None:  # noqa: N802
        """Pi-compatible alias for replacing or restoring Tau's footer."""
        self._context.set_footer(lines)

    def setHeader(self, lines: str | Sequence[str] | None = None) -> None:  # noqa: N802
        """Pi-compatible alias for replacing or restoring Tau's header."""
        self._context.set_header(lines)

    def setTheme(self, theme: str) -> dict[str, str | bool]:  # noqa: N802
        """Pi-compatible alias for switching Tau's theme by name."""
        return self._context.set_theme(theme)

    def getToolsExpanded(self) -> bool:  # noqa: N802
        """Pi-compatible alias for reading tool-result expansion state."""
        return self._context.get_tools_expanded()

    def setToolsExpanded(self, expanded: bool) -> None:  # noqa: N802
        """Pi-compatible alias for setting tool-result expansion state."""
        self._context.set_tools_expanded(expanded)

    def set_working_message(self, message: str | None = None) -> None:
        """Set the running message."""
        self._context.set_working_message(message)

    def set_working_visible(self, visible: bool) -> None:
        """Show or hide the running indicator."""
        self._context.set_working_visible(visible)

    def set_working_indicator(self, options: Any = None) -> None:
        """Set custom running indicator frames."""
        self._context.set_working_indicator(options)

    def set_footer(self, lines: str | Sequence[str] | None = None) -> None:
        """Replace or restore Tau's footer."""
        self._context.set_footer(lines)

    def set_header(self, lines: str | Sequence[str] | None = None) -> None:
        """Replace or restore Tau's header."""
        self._context.set_header(lines)

    def set_theme(self, theme: str) -> dict[str, str | bool]:
        """Switch Tau's theme by name."""
        return self._context.set_theme(theme)

    def get_tools_expanded(self) -> bool:
        """Return whether tool results are currently expanded in the TUI."""
        return self._context.get_tools_expanded()

    def set_tools_expanded(self, expanded: bool) -> None:
        """Expand or collapse tool output."""
        self._context.set_tools_expanded(expanded)

    async def _request_ui(self, method: str, **payload: Any) -> Any:
        request = getattr(self._context.session, "request_extension_ui", None)
        if not callable(request):
            raise RuntimeError("active session does not support extension UI requests")
        return await request(method=method, extension_name=self._context.extension_name, **payload)

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
        lines: str | Sequence[str] | None,
        *,
        placement: str = "above_editor",
    ) -> None:
        """Request that Tau set or clear a prompt-region extension widget."""
        self._context.set_widget(key, lines, placement=placement)


class ExtensionAPI:
    """API object passed to an extension module's `setup(tau)` function."""

    def __init__(self) -> None:
        self._tools: list[AgentTool] = []
        self._commands: list[ExtensionCommand] = []
        self._shortcuts: list[ExtensionShortcut] = []

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

    def register_tool(self, tool: AgentTool) -> None:
        """Register an `AgentTool` for the current coding session."""
        if not isinstance(tool, AgentTool):
            raise TypeError("register_tool expects an AgentTool instance")
        if any(existing.name == tool.name for existing in self._tools):
            raise ValueError(f"Extension already registered tool: {tool.name}")
        self._tools.append(tool)

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


def _normalize_widget_placement(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"", "above", "above_editor", "aboveeditor"}:
        return "above_editor"
    if normalized in {"below", "below_editor", "beloweditor"}:
        return "below_editor"
    raise ValueError("widget placement must be 'above_editor' or 'below_editor'")


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
