"""Tau coding-session extension API."""

from tau_coding.extensions.api import (
    ExtensionAPI,
    ExtensionArgumentCompletion,
    ExtensionCommand,
    ExtensionCommandContext,
    ExtensionCommandUi,
    ExtensionNotification,
    ExtensionShortcut,
    ExtensionShortcutContext,
    ExtensionStatusUpdate,
)
from tau_coding.extensions.loader import (
    ExtensionLoadResult,
    LoadedExtension,
    load_extension_tools,
)

__all__ = [
    "ExtensionAPI",
    "ExtensionArgumentCompletion",
    "ExtensionCommand",
    "ExtensionCommandContext",
    "ExtensionCommandUi",
    "ExtensionNotification",
    "ExtensionShortcut",
    "ExtensionShortcutContext",
    "ExtensionStatusUpdate",
    "ExtensionLoadResult",
    "LoadedExtension",
    "load_extension_tools",
]
