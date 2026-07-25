"""Tau coding-session extension API."""

from tau_coding.extensions.api import (
    ExtensionAPI,
    ExtensionArgumentCompletion,
    ExtensionCommand,
    ExtensionCommandContext,
    ExtensionNotification,
    ExtensionShortcut,
    ExtensionShortcutContext,
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
    "ExtensionNotification",
    "ExtensionShortcut",
    "ExtensionShortcutContext",
    "ExtensionLoadResult",
    "LoadedExtension",
    "load_extension_tools",
]
