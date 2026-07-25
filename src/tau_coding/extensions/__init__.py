"""Tau coding-session extension API."""

from tau_coding.extensions.api import (
    ExtensionAPI,
    ExtensionArgumentCompletion,
    ExtensionArgumentCompletionProvider,
    ExtensionCommand,
    ExtensionCommandContext,
    ExtensionCommandUi,
    ExtensionFlag,
    ExtensionFooterUpdate,
    ExtensionHeaderUpdate,
    ExtensionNotification,
    ExtensionShortcut,
    ExtensionShortcutContext,
    ExtensionStatusUpdate,
    ExtensionWidgetUpdate,
    ExtensionWorkingIndicatorUpdate,
)
from tau_coding.extensions.loader import (
    ExtensionLoadResult,
    LoadedExtension,
    load_extension_tools,
)

__all__ = [
    "ExtensionAPI",
    "ExtensionArgumentCompletion",
    "ExtensionArgumentCompletionProvider",
    "ExtensionCommand",
    "ExtensionCommandContext",
    "ExtensionCommandUi",
    "ExtensionFlag",
    "ExtensionFooterUpdate",
    "ExtensionHeaderUpdate",
    "ExtensionNotification",
    "ExtensionShortcut",
    "ExtensionShortcutContext",
    "ExtensionStatusUpdate",
    "ExtensionWidgetUpdate",
    "ExtensionWorkingIndicatorUpdate",
    "ExtensionLoadResult",
    "LoadedExtension",
    "load_extension_tools",
]
