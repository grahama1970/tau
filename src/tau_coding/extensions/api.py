"""Minimal extension-facing API for Tau coding sessions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tau_agent.tools import AgentTool

ExtensionCommandHandler = Callable[[Any], Any]


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
    hidden: bool = False


class ExtensionAPI:
    """API object passed to an extension module's `setup(tau)` function."""

    def __init__(self) -> None:
        self._tools: list[AgentTool] = []
        self._commands: list[ExtensionCommand] = []

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        """Return tools registered by this extension."""
        return tuple(self._tools)

    @property
    def commands(self) -> tuple[ExtensionCommand, ...]:
        """Return slash commands registered by this extension."""
        return tuple(self._commands)

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
                hidden=hidden,
            )
        )
