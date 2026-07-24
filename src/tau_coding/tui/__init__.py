"""Textual TUI frontend for Tau coding sessions."""

from tau_coding.tui.adapter import TuiEventAdapter
from tau_coding.tui.autocomplete import CompletionOption
from tau_coding.tui.config import (
    BUILTIN_TUI_THEME_NAMES,
    HIGH_CONTRAST_THEME,
    TAU_DARK_THEME,
    TAU_LIGHT_THEME,
    TuiConfigError,
    TuiKeybindings,
    TuiRoleStyle,
    TuiSettings,
    TuiTheme,
    TuiThemeError,
    TuiThemeName,
    TurnNotificationMode,
    available_tui_theme_names,
    get_tui_theme,
    load_custom_tui_themes,
    load_tui_settings,
    parse_tui_theme_json,
    save_tui_settings,
    set_custom_tui_themes,
    tui_settings_path,
)
from tau_coding.tui.state import ChatItem, TuiState
from tau_coding.tui.widgets import (
    CompactSessionInfo,
    SessionSidebar,
    StreamingTranscriptMessageWidget,
    TranscriptMessageWidget,
    TranscriptView,
    render_chat_item,
    render_compact_session_info,
    render_session_sidebar,
    transcript_item_selection_text,
)


def __getattr__(name: str) -> object:
    if name in {"TauTuiApp", "run_tui_app"}:
        from tau_coding.tui.app import TauTuiApp, run_tui_app

        return TauTuiApp if name == "TauTuiApp" else run_tui_app
    raise AttributeError(name)

__all__ = [
    "BUILTIN_TUI_THEME_NAMES",
    "ChatItem",
    "CompletionOption",
    "CompactSessionInfo",
    "TauTuiApp",
    "SessionSidebar",
    "TAU_DARK_THEME",
    "TAU_LIGHT_THEME",
    "StreamingTranscriptMessageWidget",
    "TranscriptMessageWidget",
    "TranscriptView",
    "TuiEventAdapter",
    "TuiConfigError",
    "HIGH_CONTRAST_THEME",
    "TuiKeybindings",
    "TuiRoleStyle",
    "TuiSettings",
    "TuiTheme",
    "TuiThemeError",
    "TuiThemeName",
    "TurnNotificationMode",
    "TuiState",
    "available_tui_theme_names",
    "get_tui_theme",
    "load_custom_tui_themes",
    "load_tui_settings",
    "parse_tui_theme_json",
    "render_chat_item",
    "render_compact_session_info",
    "render_session_sidebar",
    "run_tui_app",
    "save_tui_settings",
    "set_custom_tui_themes",
    "transcript_item_selection_text",
    "tui_settings_path",
]
