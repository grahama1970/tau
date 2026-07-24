"""Validated TUI theme registry and custom theme loading helpers."""

from tau_coding.tui.config import (
    BUILTIN_TUI_THEME_NAMES,
    HIGH_CONTRAST_THEME,
    TAU_DARK_THEME,
    TAU_LIGHT_THEME,
    THEME_COLOR_FIELDS,
    TRANSCRIPT_ROLES,
    TranscriptRole,
    TuiRoleStyle,
    TuiTheme,
    TuiThemeError,
    TuiThemeName,
    available_tui_theme_names,
    get_tui_theme,
    load_custom_tui_themes,
    parse_tui_theme_json,
    set_custom_tui_themes,
)

__all__ = [
    "BUILTIN_TUI_THEME_NAMES",
    "HIGH_CONTRAST_THEME",
    "TAU_DARK_THEME",
    "TAU_LIGHT_THEME",
    "THEME_COLOR_FIELDS",
    "TRANSCRIPT_ROLES",
    "TranscriptRole",
    "TuiRoleStyle",
    "TuiTheme",
    "TuiThemeError",
    "TuiThemeName",
    "available_tui_theme_names",
    "get_tui_theme",
    "load_custom_tui_themes",
    "parse_tui_theme_json",
    "set_custom_tui_themes",
]
