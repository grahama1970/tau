"""Minimal extension-facing API for Tau coding sessions."""

from __future__ import annotations

from tau_agent.tools import AgentTool


class ExtensionAPI:
    """API object passed to an extension module's `setup(tau)` function."""

    def __init__(self) -> None:
        self._tools: list[AgentTool] = []

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        """Return tools registered by this extension."""
        return tuple(self._tools)

    def register_tool(self, tool: AgentTool) -> None:
        """Register an `AgentTool` for the current coding session."""
        if not isinstance(tool, AgentTool):
            raise TypeError("register_tool expects an AgentTool instance")
        if any(existing.name == tool.name for existing in self._tools):
            raise ValueError(f"Extension already registered tool: {tool.name}")
        self._tools.append(tool)
