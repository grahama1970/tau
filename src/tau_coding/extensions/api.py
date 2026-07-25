"""Minimal extension-facing API for Tau coding sessions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from tau_agent.tools import AgentTool

ExtensionCommandHandler = Callable[[Any], Any]
ExtensionShortcutHandler = Callable[[Any], Any]


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
