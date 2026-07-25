"""Minimal extension-facing API for Tau coding sessions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
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


@dataclass(frozen=True, slots=True)
class ExtensionShortcutContext:
    """Runtime context passed to extension shortcut handlers."""

    session: Any
    key: str
    extension_name: str


@dataclass(slots=True)
class ExtensionCommandContext:
    """Runtime context passed to extension slash-command handlers."""

    session: Any
    registry: Any
    text: str
    name: str
    args: str
    extension_name: str
    shutdown_requested: bool = False
    editor_text: str | None = None
    editor_insert_text: str | None = None
    terminal_title_requested: bool = False
    terminal_title: str | None = None
    notifications: list[ExtensionNotification] = field(default_factory=list)
    status_updates: list[ExtensionStatusUpdate] = field(default_factory=list)
    widget_updates: list[ExtensionWidgetUpdate] = field(default_factory=list)
    user_message: str | None = None
    user_message_delivery: str = "steer"
    ui: ExtensionCommandUi = field(init=False)

    def __post_init__(self) -> None:
        self.ui = ExtensionCommandUi(self)

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

    def shutdown(self) -> None:
        """Request that Tau exit after the command returns."""
        self.shutdown_requested = True

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

    def __init__(self, context: ExtensionCommandContext) -> None:
        self._context = context

    def notify(self, message: str, severity: str = "info") -> None:
        """Request that Tau show a TUI notification after the command returns."""
        self._context.notify(message, severity)

    def set_editor_text(self, text: str) -> None:
        """Request that Tau replace the prompt editor contents after the command returns."""
        self._context.set_editor_text(text)

    def insert_editor_text(self, text: str) -> None:
        """Request that Tau insert text into the prompt editor after the command returns."""
        self._context.insert_editor_text(text)

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
