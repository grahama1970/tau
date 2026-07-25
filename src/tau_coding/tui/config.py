"""Durable Textual TUI configuration for Tau."""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from json import JSONDecodeError, dumps, loads
from pathlib import Path
from typing import Any, Literal, cast

from rich.color import Color, ColorParseError
from rich.errors import StyleSyntaxError
from rich.style import Style
from textual.color import Color as TextualColor
from textual.color import ColorParseError as TextualColorParseError

from tau_coding.paths import TauPaths
from tau_coding.resources import ResourceDiagnostic
from tau_coding.thinking import DEFAULT_THINKING_LEVEL, THINKING_LEVELS, ThinkingLevel
from tau_coding.trust import DefaultProjectTrust


class TuiConfigError(ValueError):
    """Raised when Tau TUI configuration is invalid."""


class TuiThemeError(ValueError):
    """Raised when a TUI theme definition is invalid."""


type TurnNotificationMode = Literal["off", "bell", "desktop"]
type SidebarPosition = Literal["left", "right", "off"]


@dataclass(frozen=True, slots=True)
class TuiKeybindings:
    """Configurable keys for Tau's built-in Textual frontend."""

    cancel: str = "escape"
    command_palette: str = "ctrl+k"
    session_picker: str = "ctrl+r"
    session_new: str = ""
    session_tree: str = ""
    session_fork: str = ""
    session_resume: str = ""
    session_toggle_named_filter: str = "ctrl+n"
    session_toggle_path: str = "ctrl+p"
    session_toggle_sort: str = "ctrl+s"
    session_rename: str = "ctrl+r,f2"
    session_delete: str = "ctrl+d"
    session_delete_noninvasive: str = "ctrl+backspace"
    queue_follow_up: str = "alt+enter"
    dequeue_messages: str = "alt+up"
    submit_prompt: str = "enter"
    insert_newline: str = "shift+enter"
    accept_completion: str = "tab"
    editor_cursor_up: str = "up"
    editor_cursor_down: str = "down"
    editor_cursor_left: str = "left,ctrl+b"
    editor_cursor_right: str = "right,ctrl+f"
    editor_cursor_word_left: str = "alt+left,ctrl+left,alt+b"
    editor_cursor_word_right: str = "alt+right,ctrl+right,alt+f"
    editor_cursor_line_start: str = "home,ctrl+a"
    editor_cursor_line_end: str = "end,ctrl+e"
    editor_jump_forward: str = "ctrl+]"
    editor_jump_backward: str = "ctrl+alt+]"
    editor_page_up: str = "pageup"
    editor_page_down: str = "pagedown"
    editor_delete_char_backward: str = "backspace"
    editor_delete_char_forward: str = "delete,ctrl+d"
    editor_delete_word_backward: str = "ctrl+w,alt+backspace"
    editor_delete_word_forward: str = "alt+d,alt+delete"
    editor_delete_to_line_start: str = "ctrl+u"
    editor_delete_to_line_end: str = "ctrl+k"
    editor_yank: str = "ctrl+y"
    editor_yank_pop: str = "alt+y"
    editor_undo: str = "ctrl+-,ctrl+minus"
    completion_next: str = "down"
    completion_previous: str = "up"
    thinking_cycle: str = "shift+tab"
    model_cycle: str = "ctrl+p"
    model_cycle_previous: str = "shift+ctrl+p"
    model_picker: str = "ctrl+l"
    models_save: str = "ctrl+s"
    models_enable_all: str = "ctrl+a"
    models_clear_all: str = "ctrl+x"
    models_toggle_provider: str = "ctrl+p"
    models_reorder_up: str = "alt+up"
    models_reorder_down: str = "alt+down"
    select_up: str = "up"
    select_down: str = "down"
    select_page_up: str = "pageup"
    select_page_down: str = "pagedown"
    select_confirm: str = "enter"
    select_cancel: str = "escape,ctrl+c"
    toggle_thinking: str = "ctrl+t"
    toggle_tool_results: str = "ctrl+o"
    copy_message: str = "ctrl+c"
    copy_last_message: str = "ctrl+x"
    external_editor: str = "ctrl+g"
    paste_clipboard: str = "ctrl+v"
    suspend: str = "ctrl+z"
    quit: str = "ctrl+d"
    tree_fold_or_up: str = "ctrl+left,alt+left"
    tree_unfold_or_down: str = "ctrl+right,alt+right"
    tree_edit_label: str = "shift+l"
    tree_toggle_label_timestamp: str = "shift+t"
    tree_filter_default: str = "ctrl+d"
    tree_filter_no_tools: str = "ctrl+t"
    tree_filter_user_only: str = "ctrl+u"
    tree_filter_labeled_only: str = "ctrl+l"
    tree_filter_all: str = "ctrl+a"
    tree_filter_cycle: str = "ctrl+o"
    tree_filter_cycle_previous: str = "shift+ctrl+o"

    def to_json(self) -> dict[str, str]:
        """Serialize these keybindings to JSON-compatible data."""
        return {
            "cancel": self.cancel,
            "command_palette": self.command_palette,
            "session_picker": self.session_picker,
            "session_new": self.session_new,
            "session_tree": self.session_tree,
            "session_fork": self.session_fork,
            "session_resume": self.session_resume,
            "session_toggle_named_filter": self.session_toggle_named_filter,
            "session_toggle_path": self.session_toggle_path,
            "session_toggle_sort": self.session_toggle_sort,
            "session_rename": self.session_rename,
            "session_delete": self.session_delete,
            "session_delete_noninvasive": self.session_delete_noninvasive,
            "queue_follow_up": self.queue_follow_up,
            "dequeue_messages": self.dequeue_messages,
            "submit_prompt": self.submit_prompt,
            "insert_newline": self.insert_newline,
            "accept_completion": self.accept_completion,
            "editor_cursor_up": self.editor_cursor_up,
            "editor_cursor_down": self.editor_cursor_down,
            "editor_cursor_left": self.editor_cursor_left,
            "editor_cursor_right": self.editor_cursor_right,
            "editor_cursor_word_left": self.editor_cursor_word_left,
            "editor_cursor_word_right": self.editor_cursor_word_right,
            "editor_cursor_line_start": self.editor_cursor_line_start,
            "editor_cursor_line_end": self.editor_cursor_line_end,
            "editor_jump_forward": self.editor_jump_forward,
            "editor_jump_backward": self.editor_jump_backward,
            "editor_page_up": self.editor_page_up,
            "editor_page_down": self.editor_page_down,
            "editor_delete_char_backward": self.editor_delete_char_backward,
            "editor_delete_char_forward": self.editor_delete_char_forward,
            "editor_delete_word_backward": self.editor_delete_word_backward,
            "editor_delete_word_forward": self.editor_delete_word_forward,
            "editor_delete_to_line_start": self.editor_delete_to_line_start,
            "editor_delete_to_line_end": self.editor_delete_to_line_end,
            "editor_yank": self.editor_yank,
            "editor_yank_pop": self.editor_yank_pop,
            "editor_undo": self.editor_undo,
            "completion_next": self.completion_next,
            "completion_previous": self.completion_previous,
            "thinking_cycle": self.thinking_cycle,
            "model_cycle": self.model_cycle,
            "model_cycle_previous": self.model_cycle_previous,
            "model_picker": self.model_picker,
            "models_save": self.models_save,
            "models_enable_all": self.models_enable_all,
            "models_clear_all": self.models_clear_all,
            "models_toggle_provider": self.models_toggle_provider,
            "models_reorder_up": self.models_reorder_up,
            "models_reorder_down": self.models_reorder_down,
            "select_up": self.select_up,
            "select_down": self.select_down,
            "select_page_up": self.select_page_up,
            "select_page_down": self.select_page_down,
            "select_confirm": self.select_confirm,
            "select_cancel": self.select_cancel,
            "toggle_thinking": self.toggle_thinking,
            "toggle_tool_results": self.toggle_tool_results,
            "copy_message": self.copy_message,
            "copy_last_message": self.copy_last_message,
            "external_editor": self.external_editor,
            "paste_clipboard": self.paste_clipboard,
            "suspend": self.suspend,
            "quit": self.quit,
            "tree_fold_or_up": self.tree_fold_or_up,
            "tree_unfold_or_down": self.tree_unfold_or_down,
            "tree_edit_label": self.tree_edit_label,
            "tree_toggle_label_timestamp": self.tree_toggle_label_timestamp,
            "tree_filter_default": self.tree_filter_default,
            "tree_filter_no_tools": self.tree_filter_no_tools,
            "tree_filter_user_only": self.tree_filter_user_only,
            "tree_filter_labeled_only": self.tree_filter_labeled_only,
            "tree_filter_all": self.tree_filter_all,
            "tree_filter_cycle": self.tree_filter_cycle,
            "tree_filter_cycle_previous": self.tree_filter_cycle_previous,
        }


type TuiThemeName = str
type TerminalTheme = Literal["dark", "light"]
type DoubleEscapeAction = Literal["tree", "fork", "none"]
type TuiTreeFilterMode = Literal["default", "no-tools", "user-only", "labeled-only", "all"]
type TuiQueueDrainMode = Literal["one-at-a-time", "all"]
DEFAULT_AUTOCOMPLETE_MAX_VISIBLE = 5
DEFAULT_HTTP_IDLE_TIMEOUT_MS = 300_000
MIN_AUTOCOMPLETE_MAX_VISIBLE = 3
MAX_AUTOCOMPLETE_MAX_VISIBLE = 20
DEFAULT_EDITOR_PADDING_X = 1
MIN_EDITOR_PADDING_X = 0
MAX_EDITOR_PADDING_X = 3
DEFAULT_OUTPUT_PADDING_X = 1
MIN_OUTPUT_PADDING_X = 0
MAX_OUTPUT_PADDING_X = 1
DEFAULT_IMAGE_WIDTH_CELLS = 60
MIN_IMAGE_WIDTH_CELLS = 1
MAX_IMAGE_WIDTH_CELLS = 240


def _default_clear_on_shrink() -> bool:
    """Return Pi-compatible terminal shrink clearing default from the environment."""
    return os.environ.get("TAU_CLEAR_ON_SHRINK") == "1" or os.environ.get(
        "PI_CLEAR_ON_SHRINK"
    ) == "1"


@dataclass(frozen=True, slots=True)
class TuiRoleStyle:
    """Colors for one transcript role block."""

    border: str
    body: str


@dataclass(frozen=True, slots=True)
class TuiTheme:
    """Resolved visual theme for Tau's built-in Textual frontend."""

    name: TuiThemeName
    screen_background: str
    screen_text: str
    chrome_background: str
    chrome_text: str
    muted_text: str
    sidebar_background: str
    border: str
    transcript_background: str
    prompt_background: str
    prompt_text: str
    prompt_border: str
    autocomplete_background: str
    accent: str
    success: str
    error: str
    tool_success_text: str
    tool_error_text: str
    highlight_background: str
    highlight_text: str
    markdown_heading: str
    markdown_table_header: str
    markdown_table_border: str
    markdown_inline_code: str
    markdown_code_block_background: str
    markdown_link: str
    markdown_bullet: str
    completion_selected: str
    completion_selected_description: str
    completion_description: str
    syntax_theme: str
    role_styles: dict[str, TuiRoleStyle]
    dark: bool = True


THEME_COLOR_FIELDS: tuple[str, ...] = tuple(
    theme_field.name
    for theme_field in fields(TuiTheme)
    if theme_field.name not in {"name", "dark", "syntax_theme", "role_styles"}
)

TranscriptRole = Literal[
    "user",
    "assistant",
    "tool",
    "error",
    "status",
    "custom",
    "thinking",
    "skill",
    "branch_summary",
    "compaction_summary",
]
TRANSCRIPT_ROLES: tuple[str, ...] = (
    "user",
    "assistant",
    "tool",
    "error",
    "status",
    "custom",
    "thinking",
    "skill",
    "branch_summary",
    "compaction_summary",
)

_TOP_LEVEL_THEME_FIELDS = {
    "$schema",
    "name",
    "dark",
    "vars",
    "syntax_theme",
    "colors",
    "roles",
}
_RICH_STYLE_THEME_FIELDS = {
    "completion_selected",
    "completion_selected_description",
    "completion_description",
}
_RICH_ONLY_THEME_COLOR_FIELDS = {
    "tool_success_text",
    "tool_error_text",
}
_RICH_STYLE_KEYWORDS = frozenset(
    {
        "on",
        "not",
        "none",
        "default",
        "b",
        "bold",
        "d",
        "dim",
        "i",
        "italic",
        "u",
        "underline",
        "uu",
        "underline2",
        "s",
        "strike",
        "r",
        "reverse",
        "blink",
        "blink2",
        "conceal",
        "o",
        "overline",
        "frame",
        "encircle",
        "link",
    }
)


TAU_DARK_THEME = TuiTheme(
    name="tau-dark",
    screen_background="#000000",
    screen_text="#d8dee9",
    chrome_background="#000000",
    chrome_text="#d8dee9",
    muted_text="#667085",
    sidebar_background="#000000",
    border="#141922",
    transcript_background="#000000",
    prompt_background="#101419",
    prompt_text="#e5e7eb",
    prompt_border="#2d3748",
    autocomplete_background="#000000",
    accent="#db945a",
    success="#9cffb1",
    error="#ff4f4f",
    tool_success_text="#9cffb1",
    tool_error_text="#ff4f4f",
    highlight_background="#a7f3f0",
    highlight_text="#061a1a",
    markdown_heading="#db945a",
    markdown_table_header="#7b7b7b",
    markdown_table_border="#7b7b7b",
    markdown_inline_code="#759e95",
    markdown_code_block_background="#161b21",
    markdown_link="#93c5fd",
    markdown_bullet="#db945a",
    completion_selected="bold #061a1a on #a7f3f0",
    completion_selected_description="#123333 on #a7f3f0",
    completion_description="#667085",
    syntax_theme="ansi_dark",
    dark=True,
    role_styles={
        "user": TuiRoleStyle(border="#7c8ea6", body="#d8dee9 on #000000"),
        "assistant": TuiRoleStyle(border="#6ea6a0", body="#d8dee9 on #000000"),
        "tool": TuiRoleStyle(border="#8a7a52", body="#cbd5e1 on #000000"),
        "error": TuiRoleStyle(border="#ff4f4f", body="#ffb4b4 on #000000"),
        "status": TuiRoleStyle(border="#526070", body="#aab4c2 on #000000"),
        "custom": TuiRoleStyle(border="#5e81ac", body="#d8dee9 on #000000"),
        "thinking": TuiRoleStyle(border="#4b5563", body="#9ca3af on #000000"),
        "skill": TuiRoleStyle(border="#b48ead", body="#e5d4ef on #000000"),
        "branch_summary": TuiRoleStyle(border="#c084fc", body="#e9d5ff on #000000"),
        "compaction_summary": TuiRoleStyle(border="#c084fc", body="#e9d5ff on #000000"),
    },
)


HIGH_CONTRAST_THEME = TuiTheme(
    name="high-contrast",
    screen_background="#000000",
    screen_text="#ffffff",
    chrome_background="#111111",
    chrome_text="#ffffff",
    muted_text="#d0d0d0",
    sidebar_background="#111111",
    border="#888888",
    transcript_background="#000000",
    prompt_background="#1a1a1a",
    prompt_text="#ffffff",
    prompt_border="#00ff66",
    autocomplete_background="#111111",
    accent="#ffb454",
    success="#9cffb1",
    error="#ff4f4f",
    tool_success_text="#9cffb1",
    tool_error_text="#ff4f4f",
    highlight_background="#7fffd4",
    highlight_text="#000000",
    markdown_heading="#ffb454",
    markdown_table_header="#d0d0d0",
    markdown_table_border="#d0d0d0",
    markdown_inline_code="#7fffd4",
    markdown_code_block_background="#161b21",
    markdown_link="#80d8ff",
    markdown_bullet="#ffb454",
    completion_selected="bold black on #7fffd4",
    completion_selected_description="black on #7fffd4",
    completion_description="white",
    syntax_theme="ansi_dark",
    dark=True,
    role_styles={
        "user": TuiRoleStyle(border="#00b7ff", body="white on #001626"),
        "assistant": TuiRoleStyle(border="#00ff66", body="white on #001a0b"),
        "tool": TuiRoleStyle(border="#ffd000", body="white on #211900"),
        "error": TuiRoleStyle(border="#ff4f4f", body="white on #260000"),
        "status": TuiRoleStyle(border="#ffffff", body="white on #111111"),
        "custom": TuiRoleStyle(border="#00b7ff", body="white on #001626"),
        "thinking": TuiRoleStyle(border="#00b7ff", body="white on #001626"),
        "skill": TuiRoleStyle(border="#ff8cff", body="white on #260026"),
        "branch_summary": TuiRoleStyle(border="#d8b4fe", body="white on #260026"),
        "compaction_summary": TuiRoleStyle(border="#d8b4fe", body="white on #260026"),
    },
)


TAU_LIGHT_THEME = TuiTheme(
    name="tau-light",
    screen_background="#ffffff",
    screen_text="#111827",
    chrome_background="#f3f4f6",
    chrome_text="#111827",
    muted_text="#475569",
    sidebar_background="#f8fafc",
    border="#cbd5e1",
    transcript_background="#ffffff",
    prompt_background="#f8fafc",
    prompt_text="#111827",
    prompt_border="#2563eb",
    autocomplete_background="#ffffff",
    accent="#0f766e",
    success="#166534",
    error="#b91c1c",
    tool_success_text="#166534",
    tool_error_text="#b91c1c",
    highlight_background="#dbeafe",
    highlight_text="#1d4ed8",
    markdown_heading="#b45309",
    markdown_table_header="#64748b",
    markdown_table_border="#cbd5e1",
    markdown_inline_code="#0f766e",
    markdown_code_block_background="#f1f5f9",
    markdown_link="#2563eb",
    markdown_bullet="#b45309",
    completion_selected="bold #0f172a on #dbeafe",
    completion_selected_description="#334155 on #dbeafe",
    completion_description="#667085",
    syntax_theme="ansi_light",
    dark=False,
    role_styles={
        "user": TuiRoleStyle(border="#2563eb", body="#111827"),
        "assistant": TuiRoleStyle(border="#0f766e", body="#111827"),
        "tool": TuiRoleStyle(border="#a16207", body="#1f2937"),
        "error": TuiRoleStyle(border="#b91c1c", body="#7f1d1d"),
        "status": TuiRoleStyle(border="#64748b", body="#334155"),
        "custom": TuiRoleStyle(border="#0e7490", body="#164e63"),
        "thinking": TuiRoleStyle(border="#6b7280", body="#4b5563"),
        "skill": TuiRoleStyle(border="#7c3aed", body="#4c1d95"),
        "branch_summary": TuiRoleStyle(border="#9333ea", body="#581c87"),
        "compaction_summary": TuiRoleStyle(border="#9333ea", body="#581c87"),
    },
)


_BUILTIN_THEMES: dict[TuiThemeName, TuiTheme] = {
    TAU_DARK_THEME.name: TAU_DARK_THEME,
    TAU_LIGHT_THEME.name: TAU_LIGHT_THEME,
    HIGH_CONTRAST_THEME.name: HIGH_CONTRAST_THEME,
}
BUILTIN_TUI_THEME_NAMES: tuple[TuiThemeName, ...] = tuple(_BUILTIN_THEMES)
DEFAULT_AUTOMATIC_TUI_THEME_SETTING = "tau-light/tau-dark"
_custom_themes: dict[TuiThemeName, TuiTheme] = {}


def set_custom_tui_themes(themes: Mapping[str, TuiTheme]) -> None:
    """Replace the registered custom themes."""
    _custom_themes.clear()
    _custom_themes.update(themes)


def available_tui_theme_names() -> tuple[TuiThemeName, ...]:
    """Return built-in theme names followed by sorted custom theme names."""
    return (*BUILTIN_TUI_THEME_NAMES, *sorted(_custom_themes))


def get_tui_theme(name: TuiThemeName = "tau-dark") -> TuiTheme:
    """Return a built-in or registered custom TUI theme by name."""
    if name in _BUILTIN_THEMES:
        return _BUILTIN_THEMES[name]
    if name in _custom_themes:
        return _custom_themes[name]
    return TAU_DARK_THEME


def parse_tui_theme_json(data: object) -> TuiTheme:
    """Parse a theme from JSON-compatible data, reporting all problems at once."""
    if not isinstance(data, dict):
        raise TuiThemeError("Theme must be a JSON object")

    problems: list[str] = []
    for key in sorted(set(data) - _TOP_LEVEL_THEME_FIELDS):
        problems.append(f"unknown field: {key}")

    name = _parse_theme_definition_name(data.get("name"), problems)
    variables = _parse_theme_vars(data.get("vars", {}), problems)
    colors = _parse_theme_colors(data.get("colors"), variables, problems)
    role_styles = _parse_theme_roles(data.get("roles"), variables, problems)
    dark = _parse_theme_dark(data.get("dark"), colors, problems)
    syntax_theme = _parse_theme_syntax_theme(data.get("syntax_theme"), dark=dark, problems=problems)

    if problems:
        label = f"theme {name!r}" if name else "theme"
        raise TuiThemeError(f"Invalid {label}: " + "; ".join(problems))
    return TuiTheme(
        name=name,
        syntax_theme=syntax_theme,
        role_styles=role_styles,
        dark=dark,
        **colors,
    )


def load_custom_tui_themes(
    theme_dirs: Sequence[Path],
) -> tuple[dict[str, TuiTheme], list[ResourceDiagnostic]]:
    """Load custom themes from directories given in increasing precedence order."""
    themes: dict[str, TuiTheme] = {}
    diagnostics: list[ResourceDiagnostic] = []
    for directory in reversed(list(theme_dirs)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                data = loads(path.read_text(encoding="utf-8"))
            except (OSError, JSONDecodeError, UnicodeDecodeError) as error:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="theme",
                        message=f"could not parse theme JSON: {error}",
                        path=path,
                    )
                )
                continue
            try:
                theme = parse_tui_theme_json(data)
            except TuiThemeError as error:
                diagnostics.append(ResourceDiagnostic(kind="theme", message=str(error), path=path))
                continue
            if theme.name in _BUILTIN_THEMES:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="theme",
                        message=f"theme {theme.name!r} shadows a built-in theme and was ignored",
                        path=path,
                        name=theme.name,
                    )
                )
                continue
            if theme.name in themes:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="theme",
                        message=f"theme {theme.name!r} is already defined with higher precedence",
                        path=path,
                        name=theme.name,
                    )
                )
                continue
            themes[theme.name] = theme
    return themes, diagnostics


def load_custom_tui_themes_from_paths(
    theme_paths: Sequence[Path],
) -> tuple[dict[str, TuiTheme], list[ResourceDiagnostic]]:
    """Load explicit theme JSON files or directories in increasing precedence order."""
    themes: dict[str, TuiTheme] = {}
    diagnostics: list[ResourceDiagnostic] = []
    for raw_path in theme_paths:
        path = raw_path.expanduser()
        if not path.exists():
            diagnostics.append(
                ResourceDiagnostic(
                    kind="theme",
                    path=path,
                    message="Theme path does not exist",
                    severity="error",
                )
            )
            continue
        if path.is_dir():
            loaded, path_diagnostics = load_custom_tui_themes((path,))
            diagnostics.extend(path_diagnostics)
            for name, theme in loaded.items():
                if name in themes:
                    diagnostics.append(
                        ResourceDiagnostic(
                            kind="theme",
                            name=name,
                            path=path,
                            message=f"theme {name!r} overrides an earlier explicit theme",
                        )
                    )
                themes[name] = theme
            continue
        if path.is_file() and path.suffix.lower() == ".json":
            theme = _load_explicit_theme_file(path, diagnostics)
            if theme is not None:
                if theme.name in _BUILTIN_THEMES:
                    diagnostics.append(
                        ResourceDiagnostic(
                            kind="theme",
                            message=(
                                f"theme {theme.name!r} shadows a built-in theme and was ignored"
                            ),
                            path=path,
                            name=theme.name,
                        )
                    )
                    continue
                if theme.name in themes:
                    diagnostics.append(
                        ResourceDiagnostic(
                            kind="theme",
                            name=theme.name,
                            path=path,
                            message=f"theme {theme.name!r} overrides an earlier explicit theme",
                        )
                    )
                themes[theme.name] = theme
            continue
        diagnostics.append(
            ResourceDiagnostic(
                kind="theme",
                path=path,
                message="Theme path must be a JSON file or directory",
                severity="error",
            )
        )
    return themes, diagnostics


def _load_explicit_theme_file(
    path: Path,
    diagnostics: list[ResourceDiagnostic],
) -> TuiTheme | None:
    try:
        data = loads(path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError, UnicodeDecodeError) as error:
        diagnostics.append(
            ResourceDiagnostic(
                kind="theme",
                message=f"could not parse theme JSON: {error}",
                path=path,
                severity="error",
            )
        )
        return None
    try:
        return parse_tui_theme_json(data)
    except TuiThemeError as error:
        diagnostics.append(
            ResourceDiagnostic(kind="theme", message=str(error), path=path, severity="error")
        )
        return None


def _parse_theme_definition_name(value: object, problems: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        problems.append("name must be a non-empty string")
        return ""
    name = value.strip()
    if "/" in name:
        problems.append("name must not contain '/'")
    return name


def _parse_theme_vars(value: object, problems: list[str]) -> dict[str, str]:
    if not isinstance(value, dict):
        problems.append("vars must be an object")
        return {}
    variables: dict[str, str] = {}
    for var_name, var_value in value.items():
        name_allowed = isinstance(var_name, str) and var_name
        if not name_allowed or var_name.lower() in _RICH_STYLE_KEYWORDS:
            problems.append(f"vars name is not allowed: {var_name!r}")
            continue
        if (
            not isinstance(var_value, str)
            or len(var_value.split()) != 1
            or _theme_color_problem(var_value) is not None
        ):
            problems.append(f"vars value must be a single color: {var_name}")
            continue
        variables[var_name] = var_value
    return variables


def _substitute_theme_vars(value: str, variables: dict[str, str]) -> str:
    if not variables:
        return value
    return " ".join(variables.get(token, token) for token in value.split())


def _parse_theme_colors(
    value: object,
    variables: dict[str, str],
    problems: list[str],
) -> dict[str, str]:
    if not isinstance(value, dict):
        problems.append("colors must be an object")
        return {}
    missing = [field_name for field_name in THEME_COLOR_FIELDS if field_name not in value]
    if missing:
        problems.append("colors missing: " + ", ".join(missing))
    unknown = sorted(set(value) - set(THEME_COLOR_FIELDS))
    if unknown:
        problems.append("colors unknown: " + ", ".join(unknown))
    colors: dict[str, str] = {}
    for field_name in THEME_COLOR_FIELDS:
        if field_name not in value:
            continue
        raw = value[field_name]
        if not isinstance(raw, str) or not raw.strip():
            problems.append(f"colors.{field_name} must be a non-empty string")
            continue
        resolved = _substitute_theme_vars(raw.strip(), variables)
        if field_name in _RICH_STYLE_THEME_FIELDS:
            error = _theme_style_problem(resolved)
        elif field_name in _RICH_ONLY_THEME_COLOR_FIELDS:
            error = _theme_color_problem(resolved)
        else:
            error = _theme_color_problem(resolved) or _textual_theme_color_problem(resolved)
        if error is not None:
            problems.append(f"colors.{field_name} {error}")
            continue
        colors[field_name] = resolved
    return colors


def _parse_theme_roles(
    value: object,
    variables: dict[str, str],
    problems: list[str],
) -> dict[str, TuiRoleStyle]:
    if not isinstance(value, dict):
        problems.append("roles must be an object")
        return {}
    missing = [role for role in TRANSCRIPT_ROLES if role not in value]
    if missing:
        problems.append("roles missing: " + ", ".join(missing))
    unknown = sorted(set(value) - set(TRANSCRIPT_ROLES))
    if unknown:
        problems.append("roles unknown: " + ", ".join(unknown))
    role_styles: dict[str, TuiRoleStyle] = {}
    for role in TRANSCRIPT_ROLES:
        if role not in value:
            continue
        raw = value[role]
        if not isinstance(raw, dict) or set(raw) != {"border", "body"}:
            problems.append(f"roles.{role} must be an object with 'border' and 'body'")
            continue
        border, body = raw["border"], raw["body"]
        if not isinstance(border, str) or not isinstance(body, str):
            problems.append(f"roles.{role} border and body must be strings")
            continue
        resolved_border = _substitute_theme_vars(border.strip(), variables)
        resolved_body = _substitute_theme_vars(body.strip(), variables)
        border_error = _theme_color_problem(resolved_border) or _textual_theme_color_problem(
            resolved_border
        )
        if border_error is not None:
            problems.append(f"roles.{role}.border {border_error}")
        body_error = _theme_style_problem(resolved_body) or _textual_theme_style_colors_problem(
            resolved_body
        )
        if body_error is not None:
            problems.append(f"roles.{role}.body {body_error}")
        if border_error is None and body_error is None:
            role_styles[role] = TuiRoleStyle(border=resolved_border, body=resolved_body)
    return role_styles


def _theme_color_problem(value: str) -> str | None:
    try:
        Color.parse(value)
    except ColorParseError:
        return f"is not a valid color: {value!r}"
    return None


def _textual_theme_color_problem(value: str) -> str | None:
    try:
        TextualColor.parse(value)
    except TextualColorParseError:
        return f"is not a color Textual accepts: {value!r}"
    return None


def _textual_theme_style_colors_problem(value: str) -> str | None:
    style = Style.parse(value)
    for color in (style.color, style.bgcolor):
        if color is not None and color.name is not None:
            error = _textual_theme_color_problem(color.name)
            if error is not None:
                return error
    return None


def _theme_style_problem(value: str) -> str | None:
    try:
        Style.parse(value)
    except (StyleSyntaxError, ColorParseError):
        return f"is not a valid style: {value!r}"
    return None


def _parse_theme_dark(value: object, colors: dict[str, str], problems: list[str]) -> bool:
    if isinstance(value, bool):
        return value
    if value is not None:
        problems.append("dark must be a boolean")
    return _is_dark_background(colors.get("screen_background", ""))


def _is_dark_background(value: str) -> bool:
    tokens = value.split()
    if not tokens:
        return True
    try:
        color = Color.parse(tokens[0])
    except ColorParseError:
        return True
    red, green, blue = color.get_truecolor()
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
    return luminance < 0.5


def _parse_theme_syntax_theme(value: object, *, dark: bool, problems: list[str]) -> str:
    if value is None:
        return "ansi_dark" if dark else "ansi_light"
    if not isinstance(value, str) or value not in _known_syntax_themes():
        problems.append(f"unknown syntax_theme: {value!r}")
        return "ansi_dark"
    return value


def _known_syntax_themes() -> frozenset[str]:
    from pygments.styles import get_all_styles

    return frozenset(get_all_styles()) | {"ansi_dark", "ansi_light"}


def parse_tui_auto_theme_setting(theme_setting: str) -> tuple[TuiThemeName, TuiThemeName] | None:
    """Parse Pi-style ``light-theme/dark-theme`` automatic theme settings."""
    slash_index = theme_setting.find("/")
    if slash_index < 0:
        return None
    light_theme = theme_setting[:slash_index].strip()
    dark_theme = theme_setting[slash_index + 1 :].strip()
    if not light_theme or not dark_theme:
        raise TuiConfigError("TUI automatic theme must be '<light-theme>/<dark-theme>'")
    return _fixed_theme_name(light_theme), _fixed_theme_name(dark_theme)


def resolve_tui_theme_name(
    theme_setting: str,
    *,
    terminal_theme: TerminalTheme | None = None,
) -> TuiThemeName:
    """Resolve a fixed or automatic theme setting to one built-in theme name."""
    auto_theme = parse_tui_auto_theme_setting(theme_setting)
    if auto_theme is None:
        return _fixed_theme_name(theme_setting)
    light_theme, dark_theme = auto_theme
    detected_theme = terminal_theme or detect_terminal_theme_from_env()
    return light_theme if detected_theme == "light" else dark_theme


def resolve_tui_theme_setting(
    theme_setting: str,
    *,
    terminal_theme: TerminalTheme | None = None,
) -> TuiTheme:
    """Resolve a fixed or Pi-style automatic theme setting to a built-in theme."""
    return get_tui_theme(
        resolve_tui_theme_name(theme_setting, terminal_theme=terminal_theme)
    )


def detect_terminal_theme_from_env(environ: dict[str, str] | None = None) -> TerminalTheme:
    """Infer terminal background from ``COLORFGBG`` when the terminal exposes it."""
    values = os.environ if environ is None else environ
    colorfgbg = values.get("COLORFGBG", "")
    if colorfgbg:
        raw_background = colorfgbg.split(";")[-1].strip()
        if raw_background.isdigit():
            luminance = _ansi_color_luminance(int(raw_background))
            if luminance is not None:
                return "light" if luminance >= 0.5 else "dark"
    return "dark"


@dataclass(frozen=True, slots=True)
class TuiSettings:
    """Tau TUI settings loaded from Tau home."""

    keybindings: TuiKeybindings = field(default_factory=TuiKeybindings)
    theme: str = "tau-dark"
    auto_compact: bool = True
    auto_copy_selection: bool = False
    sidebar_position: SidebarPosition = "right"
    auto_resize_images: bool = True
    show_images: bool = True
    image_width_cells: int = DEFAULT_IMAGE_WIDTH_CELLS
    block_images: bool = False
    double_escape_action: DoubleEscapeAction = "tree"
    tree_filter_mode: TuiTreeFilterMode = "default"
    hide_thinking: bool = True
    thinking_level: ThinkingLevel = DEFAULT_THINKING_LEVEL
    steering_mode: TuiQueueDrainMode = "one-at-a-time"
    follow_up_mode: TuiQueueDrainMode = "one-at-a-time"
    http_idle_timeout_ms: int = DEFAULT_HTTP_IDLE_TIMEOUT_MS
    default_project_trust: DefaultProjectTrust = "ask"
    autocomplete_max_visible: int = DEFAULT_AUTOCOMPLETE_MAX_VISIBLE
    enable_skill_commands: bool = True
    editor_padding_x: int = DEFAULT_EDITOR_PADDING_X
    output_padding_x: int = DEFAULT_OUTPUT_PADDING_X
    clear_on_shrink: bool = field(default_factory=_default_clear_on_shrink)
    show_hardware_cursor: bool = True
    show_terminal_progress: bool = False
    anthropic_extra_usage_warning: bool = True
    quiet_startup: bool = False
    collapse_changelog: bool = False
    turn_notification: TurnNotificationMode = "desktop"
    external_editor: str | None = None
    shell_path: str | None = None
    shell_command_prefix: str | None = None
    disabled_resource_paths: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """Serialize these settings to JSON-compatible data."""
        return {
            "autocomplete_max_visible": self.autocomplete_max_visible,
            "auto_compact": self.auto_compact,
            "auto_copy_selection": self.auto_copy_selection,
            "sidebar_position": self.sidebar_position,
            "auto_resize_images": self.auto_resize_images,
            "block_images": self.block_images,
            "default_project_trust": self.default_project_trust,
            "double_escape_action": self.double_escape_action,
            "editor_padding_x": self.editor_padding_x,
            "enable_skill_commands": self.enable_skill_commands,
            "hide_thinking": self.hide_thinking,
            "keybindings": self.keybindings.to_json(),
            "output_padding_x": self.output_padding_x,
            "clear_on_shrink": self.clear_on_shrink,
            "show_images": self.show_images,
            "image_width_cells": self.image_width_cells,
            "show_hardware_cursor": self.show_hardware_cursor,
            "show_terminal_progress": self.show_terminal_progress,
            "anthropic_extra_usage_warning": self.anthropic_extra_usage_warning,
            "quiet_startup": self.quiet_startup,
            "collapse_changelog": self.collapse_changelog,
            "turn_notification": self.turn_notification,
            "external_editor": self.external_editor,
            "shell_path": self.shell_path,
            "shell_command_prefix": self.shell_command_prefix,
            "disabled_resource_paths": list(self.disabled_resource_paths),
            "follow_up_mode": self.follow_up_mode,
            "http_idle_timeout_ms": self.http_idle_timeout_ms,
            "steering_mode": self.steering_mode,
            "theme": self.theme,
            "thinking_level": self.thinking_level,
            "tree_filter_mode": self.tree_filter_mode,
        }

    @property
    def resolved_theme(self) -> TuiTheme:
        """Return the selected built-in theme."""
        return resolve_tui_theme_setting(self.theme)


@dataclass(frozen=True, slots=True)
class ProjectTuiSettings:
    """Project-local TUI settings backed by ``<cwd>/.tau/tui.json``."""

    disabled_resource_paths: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """Serialize these settings to JSON-compatible data."""
        return {"disabled_resource_paths": list(self.disabled_resource_paths)}


def tui_settings_path(paths: TauPaths | None = None) -> Path:
    """Return the durable TUI settings path."""
    return (paths or TauPaths()).home / "tui.json"


def project_tui_settings_path(cwd: Path, paths: TauPaths | None = None) -> Path:
    """Return the project-local TUI settings path."""
    return (paths or TauPaths()).project_tau_dir(cwd) / "tui.json"


def load_tui_settings(paths: TauPaths | None = None) -> TuiSettings:
    """Load durable TUI settings, falling back to built-in defaults."""
    path = tui_settings_path(paths)
    if not path.exists():
        return TuiSettings()
    raw = loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TuiConfigError("TUI settings must be a JSON object")
    return tui_settings_from_json(raw)


def save_tui_settings(settings: TuiSettings, paths: TauPaths | None = None) -> Path:
    """Persist durable TUI settings and return the written path."""
    path = tui_settings_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(settings.to_json(), indent=2) + "\n", encoding="utf-8")
    return path


def load_project_tui_settings(
    cwd: Path,
    paths: TauPaths | None = None,
) -> ProjectTuiSettings:
    """Load project-local TUI settings, falling back to built-in defaults."""
    path = project_tui_settings_path(cwd, paths)
    if not path.exists():
        return ProjectTuiSettings()
    raw = loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TuiConfigError("Project TUI settings must be a JSON object")
    return project_tui_settings_from_json(raw)


def save_project_tui_settings(
    settings: ProjectTuiSettings,
    cwd: Path,
    paths: TauPaths | None = None,
) -> Path:
    """Persist project-local TUI settings and return the written path."""
    path = project_tui_settings_path(cwd, paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(settings.to_json(), indent=2) + "\n", encoding="utf-8")
    return path


def project_tui_settings_from_json(data: dict[str, Any]) -> ProjectTuiSettings:
    """Parse project-local TUI settings from JSON-compatible data."""
    return ProjectTuiSettings(
        disabled_resource_paths=_string_tuple_setting(
            data.get("disabled_resource_paths", data.get("disabledResourcePaths", ())),
            "disabled_resource_paths",
        )
    )


def tui_settings_from_json(data: dict[str, Any]) -> TuiSettings:
    """Parse TUI settings from JSON-compatible data."""
    # Ignore settings from newer Tau versions so one user-level file can survive
    # upgrades, downgrades, and multiple installations. Known fields below still
    # keep strict type and value validation.
    keybindings_data = data.get("keybindings", {})
    if not isinstance(keybindings_data, dict):
        raise TuiConfigError("TUI keybindings must be a JSON object")
    terminal_data = data.get("terminal", {})
    if not isinstance(terminal_data, dict):
        raise TuiConfigError("TUI terminal settings must be a JSON object")
    images_data = data.get("images", {})
    if not isinstance(images_data, dict):
        raise TuiConfigError("TUI images settings must be a JSON object")
    return TuiSettings(
        keybindings=_keybindings_from_json(keybindings_data),
        theme=_theme_name(data.get("theme", "tau-dark")),
        auto_compact=_bool_setting(data.get("auto_compact", True), "auto_compact"),
        auto_copy_selection=_bool_setting(
            data.get("auto_copy_selection", False),
            "auto_copy_selection",
        ),
        sidebar_position=_sidebar_position(
            data.get("sidebar_position", data.get("sidebarPosition", "right"))
        ),
        auto_resize_images=_bool_setting(
            data.get(
                "auto_resize_images",
                data.get("autoResizeImages", images_data.get("autoResize", True)),
            ),
            "auto_resize_images",
        ),
        block_images=_bool_setting(
            data.get(
                "block_images",
                data.get("blockImages", images_data.get("blockImages", False)),
            ),
            "block_images",
        ),
        show_images=_bool_setting(
            data.get(
                "show_images",
                data.get(
                    "showImages",
                    terminal_data.get("show_images", terminal_data.get("showImages", True)),
                ),
            ),
            "show_images",
        ),
        image_width_cells=_image_width_cells(
            data.get(
                "image_width_cells",
                data.get(
                    "imageWidthCells",
                    terminal_data.get(
                        "image_width_cells",
                        terminal_data.get("imageWidthCells", DEFAULT_IMAGE_WIDTH_CELLS),
                    ),
                ),
            )
        ),
        double_escape_action=_double_escape_action(
            data.get("double_escape_action", "tree"),
        ),
        hide_thinking=_bool_setting(data.get("hide_thinking", True), "hide_thinking"),
        steering_mode=_queue_drain_mode(
            data.get("steering_mode", data.get("steeringMode", "one-at-a-time")),
            "steering_mode",
        ),
        follow_up_mode=_queue_drain_mode(
            data.get("follow_up_mode", data.get("followUpMode", "one-at-a-time")),
            "follow_up_mode",
        ),
        http_idle_timeout_ms=_http_idle_timeout_ms(
            data.get(
                "http_idle_timeout_ms",
                data.get("httpIdleTimeoutMs", DEFAULT_HTTP_IDLE_TIMEOUT_MS),
            )
        ),
        default_project_trust=_default_project_trust(
            data.get("default_project_trust", data.get("defaultProjectTrust", "ask"))
        ),
        autocomplete_max_visible=_autocomplete_max_visible(
            data.get(
                "autocomplete_max_visible",
                data.get("autocompleteMaxVisible", DEFAULT_AUTOCOMPLETE_MAX_VISIBLE),
            )
        ),
        enable_skill_commands=_bool_setting(
            data.get("enable_skill_commands", data.get("enableSkillCommands", True)),
            "enable_skill_commands",
        ),
        editor_padding_x=_editor_padding_x(
            data.get("editor_padding_x", data.get("editorPaddingX", DEFAULT_EDITOR_PADDING_X))
        ),
        output_padding_x=_output_padding_x(
            data.get("output_padding_x", data.get("outputPad", DEFAULT_OUTPUT_PADDING_X))
        ),
        clear_on_shrink=_bool_setting(
            data.get(
                "clear_on_shrink",
                data.get(
                    "clearOnShrink",
                    terminal_data.get("clearOnShrink", _default_clear_on_shrink()),
                ),
            ),
            "clear_on_shrink",
        ),
        show_hardware_cursor=_bool_setting(
            data.get(
                "show_hardware_cursor",
                data.get(
                    "showHardwareCursor",
                    terminal_data.get("showHardwareCursor", True),
                ),
            ),
            "show_hardware_cursor",
        ),
        show_terminal_progress=_bool_setting(
            data.get(
                "show_terminal_progress",
                data.get(
                    "showTerminalProgress",
                    terminal_data.get("showTerminalProgress", False),
                ),
            ),
            "show_terminal_progress",
        ),
        anthropic_extra_usage_warning=_bool_setting(
            data.get(
                "anthropic_extra_usage_warning",
                data.get(
                    "anthropicExtraUsageWarning",
                    data.get("warnings", {}).get("anthropicExtraUsage", True)
                    if isinstance(data.get("warnings", {}), dict)
                    else True,
                ),
            ),
            "anthropic_extra_usage_warning",
        ),
        quiet_startup=_bool_setting(
            data.get("quiet_startup", data.get("quietStartup", False)),
            "quiet_startup",
        ),
        collapse_changelog=_bool_setting(
            data.get("collapse_changelog", data.get("collapseChangelog", False)),
            "collapse_changelog",
        ),
        turn_notification=_turn_notification_mode(
            data.get("turn_notification", data.get("turnNotification", "desktop")),
        ),
        external_editor=_optional_string_setting(
            data.get("external_editor", data.get("externalEditor")),
            "external_editor",
        ),
        shell_path=_expand_optional_user_path(
            _optional_string_setting(
                data.get("shell_path", data.get("shellPath")),
                "shell_path",
            )
        ),
        shell_command_prefix=_optional_string_setting(
            data.get("shell_command_prefix", data.get("shellCommandPrefix")),
            "shell_command_prefix",
        ),
        disabled_resource_paths=_string_tuple_setting(
            data.get("disabled_resource_paths", data.get("disabledResourcePaths", ())),
            "disabled_resource_paths",
        ),
        thinking_level=_thinking_level(
            data.get("thinking_level", data.get("thinkingLevel", DEFAULT_THINKING_LEVEL))
        ),
        tree_filter_mode=_tree_filter_mode(data.get("tree_filter_mode", "default")),
    )


def _bool_setting(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise TuiConfigError(f"TUI setting must be a boolean: {field_name}")


def _sidebar_position(value: object) -> SidebarPosition:
    if isinstance(value, str) and value in {"left", "right", "off"}:
        return cast(SidebarPosition, value)
    raise TuiConfigError("sidebar_position must be 'left', 'right', or 'off'")


def _turn_notification_mode(value: object) -> TurnNotificationMode:
    if isinstance(value, str) and value in {"off", "bell", "desktop"}:
        return cast(TurnNotificationMode, value)
    raise TuiConfigError("turn_notification must be 'off', 'bell', or 'desktop'")


def _optional_string_setting(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TuiConfigError(f"TUI setting must be a string or null: {field_name}")
    stripped = value.strip()
    return stripped or None


def _string_tuple_setting(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TuiConfigError(f"TUI setting must be a list of strings: {field_name}")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TuiConfigError(f"TUI setting must be a list of strings: {field_name}")
        stripped = item.strip()
        if stripped:
            values.append(stripped)
    return tuple(dict.fromkeys(values))


def _expand_optional_user_path(value: str | None) -> str | None:
    if value is None:
        return None
    return str(Path(value).expanduser())


def _double_escape_action(value: object) -> DoubleEscapeAction:
    if value in {"tree", "fork", "none"}:
        return cast(DoubleEscapeAction, value)
    raise TuiConfigError("TUI double_escape_action must be one of: tree, fork, none")


def _tree_filter_mode(value: object) -> TuiTreeFilterMode:
    if value in {"default", "no-tools", "user-only", "labeled-only", "all"}:
        return cast(TuiTreeFilterMode, value)
    raise TuiConfigError(
        "TUI tree_filter_mode must be one of: default, no-tools, user-only, labeled-only, all"
    )


def _queue_drain_mode(value: object, field_name: str) -> TuiQueueDrainMode:
    if value in {"one-at-a-time", "all"}:
        return cast(TuiQueueDrainMode, value)
    raise TuiConfigError(f"TUI {field_name} must be one of: one-at-a-time, all")


def _http_idle_timeout_ms(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TuiConfigError("TUI http_idle_timeout_ms must be an integer")
    if value < 0:
        raise TuiConfigError("TUI http_idle_timeout_ms must be 0 or greater")
    return value


def _default_project_trust(value: object) -> DefaultProjectTrust:
    if value in {"ask", "always", "never"}:
        return cast(DefaultProjectTrust, value)
    raise TuiConfigError("TUI default_project_trust must be one of: ask, always, never")


def _autocomplete_max_visible(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TuiConfigError("TUI autocomplete_max_visible must be an integer")
    if MIN_AUTOCOMPLETE_MAX_VISIBLE <= value <= MAX_AUTOCOMPLETE_MAX_VISIBLE:
        return value
    raise TuiConfigError(
        "TUI autocomplete_max_visible must be between "
        f"{MIN_AUTOCOMPLETE_MAX_VISIBLE} and {MAX_AUTOCOMPLETE_MAX_VISIBLE}"
    )


def _editor_padding_x(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TuiConfigError("TUI editor_padding_x must be an integer")
    if MIN_EDITOR_PADDING_X <= value <= MAX_EDITOR_PADDING_X:
        return value
    raise TuiConfigError(
        f"TUI editor_padding_x must be between {MIN_EDITOR_PADDING_X} and {MAX_EDITOR_PADDING_X}"
    )


def _output_padding_x(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TuiConfigError("TUI output_padding_x must be an integer")
    if MIN_OUTPUT_PADDING_X <= value <= MAX_OUTPUT_PADDING_X:
        return value
    raise TuiConfigError(
        f"TUI output_padding_x must be between {MIN_OUTPUT_PADDING_X} and {MAX_OUTPUT_PADDING_X}"
    )


def _image_width_cells(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TuiConfigError("TUI image_width_cells must be an integer")
    if MIN_IMAGE_WIDTH_CELLS <= value <= MAX_IMAGE_WIDTH_CELLS:
        return value
    raise TuiConfigError(
        "TUI image_width_cells must be between "
        f"{MIN_IMAGE_WIDTH_CELLS} and {MAX_IMAGE_WIDTH_CELLS}"
    )


def _thinking_level(value: object) -> ThinkingLevel:
    if value in THINKING_LEVELS:
        return cast(ThinkingLevel, value)
    allowed = ", ".join(THINKING_LEVELS)
    raise TuiConfigError(f"TUI thinking_level must be one of: {allowed}")


def _ansi_color_luminance(color_index: int) -> float | None:
    if color_index < 0 or color_index > 255:
        return None
    if color_index < 16:
        colors = (
            (0, 0, 0),
            (128, 0, 0),
            (0, 128, 0),
            (128, 128, 0),
            (0, 0, 128),
            (128, 0, 128),
            (0, 128, 128),
            (192, 192, 192),
            (128, 128, 128),
            (255, 0, 0),
            (0, 255, 0),
            (255, 255, 0),
            (0, 0, 255),
            (255, 0, 255),
            (0, 255, 255),
            (255, 255, 255),
        )
        red, green, blue = colors[color_index]
    elif color_index < 232:
        levels = (0, 95, 135, 175, 215, 255)
        offset = color_index - 16
        red = levels[offset // 36]
        green = levels[(offset % 36) // 6]
        blue = levels[offset % 6]
    else:
        red = green = blue = 8 + ((color_index - 232) * 10)
    return ((0.2126 * red) + (0.7152 * green) + (0.0722 * blue)) / 255


def _keybindings_from_json(data: dict[str, Any]) -> TuiKeybindings:
    defaults = TuiKeybindings()
    normalized_data = _normalize_keybinding_fields(data)
    # Future versions may add actions. Read only actions this version supports;
    # recognized actions still reject invalid or duplicate key values.
    values = {
        field_name: _optional_key_string(normalized_data.get(field_name, default_value), field_name)
        if field_name in _OPTIONAL_KEYBINDING_FIELDS
        else _key_string(normalized_data.get(field_name, default_value), field_name)
        for field_name, default_value in defaults.to_json().items()
    }
    _reject_duplicate_keys(values)
    return TuiKeybindings(**values)


_PI_KEYBINDING_ALIASES = {
    "app.interrupt": "cancel",
    "app.clear": "copy_message",
    "app.exit": "quit",
    "app.suspend": "suspend",
    "app.editor.external": "external_editor",
    "app.clipboard.pasteImage": "paste_clipboard",
    "app.message.copy": "copy_last_message",
    "app.message.followUp": "queue_follow_up",
    "app.message.dequeue": "dequeue_messages",
    "app.tools.expand": "toggle_tool_results",
    "app.thinking.toggle": "toggle_thinking",
    "app.thinking.cycle": "thinking_cycle",
    "app.model.cycleForward": "model_cycle",
    "app.model.cycleBackward": "model_cycle_previous",
    "app.model.select": "model_picker",
    "app.models.save": "models_save",
    "app.models.enableAll": "models_enable_all",
    "app.models.clearAll": "models_clear_all",
    "app.models.toggleProvider": "models_toggle_provider",
    "app.models.reorderUp": "models_reorder_up",
    "app.models.reorderDown": "models_reorder_down",
    "app.session.new": "session_new",
    "app.session.tree": "session_tree",
    "app.session.fork": "session_fork",
    "app.session.resume": "session_resume",
    "app.session.toggleNamedFilter": "session_toggle_named_filter",
    "app.session.togglePath": "session_toggle_path",
    "app.session.toggleSort": "session_toggle_sort",
    "app.session.rename": "session_rename",
    "app.session.delete": "session_delete",
    "app.session.deleteNoninvasive": "session_delete_noninvasive",
    "app.tree.foldOrUp": "tree_fold_or_up",
    "app.tree.unfoldOrDown": "tree_unfold_or_down",
    "app.tree.editLabel": "tree_edit_label",
    "app.tree.toggleLabelTimestamp": "tree_toggle_label_timestamp",
    "app.tree.filter.default": "tree_filter_default",
    "app.tree.filter.noTools": "tree_filter_no_tools",
    "app.tree.filter.userOnly": "tree_filter_user_only",
    "app.tree.filter.labeledOnly": "tree_filter_labeled_only",
    "app.tree.filter.all": "tree_filter_all",
    "app.tree.filter.cycleForward": "tree_filter_cycle",
    "app.tree.filter.cycleBackward": "tree_filter_cycle_previous",
    "tui.input.newLine": "insert_newline",
    "tui.input.submit": "submit_prompt",
    "tui.input.tab": "accept_completion",
    "tui.input.copy": "copy_message",
    "tui.editor.cursorUp": "editor_cursor_up",
    "tui.editor.cursorDown": "editor_cursor_down",
    "tui.editor.cursorLeft": "editor_cursor_left",
    "tui.editor.cursorRight": "editor_cursor_right",
    "tui.editor.cursorWordLeft": "editor_cursor_word_left",
    "tui.editor.cursorWordRight": "editor_cursor_word_right",
    "tui.editor.cursorLineStart": "editor_cursor_line_start",
    "tui.editor.cursorLineEnd": "editor_cursor_line_end",
    "tui.editor.jumpForward": "editor_jump_forward",
    "tui.editor.jumpBackward": "editor_jump_backward",
    "tui.editor.pageUp": "editor_page_up",
    "tui.editor.pageDown": "editor_page_down",
    "tui.editor.deleteCharBackward": "editor_delete_char_backward",
    "tui.editor.deleteCharForward": "editor_delete_char_forward",
    "tui.editor.deleteWordBackward": "editor_delete_word_backward",
    "tui.editor.deleteWordForward": "editor_delete_word_forward",
    "tui.editor.deleteToLineStart": "editor_delete_to_line_start",
    "tui.editor.deleteToLineEnd": "editor_delete_to_line_end",
    "tui.editor.yank": "editor_yank",
    "tui.editor.yankPop": "editor_yank_pop",
    "tui.editor.undo": "editor_undo",
    "tui.select.up": "select_up",
    "tui.select.down": "select_down",
    "tui.select.pageUp": "select_page_up",
    "tui.select.pageDown": "select_page_down",
    "tui.select.confirm": "select_confirm",
    "tui.select.cancel": "select_cancel",
    "interrupt": "cancel",
    "clear": "copy_message",
    "exit": "quit",
    "suspend": "suspend",
    "externalEditor": "external_editor",
    "pasteImage": "paste_clipboard",
    "followUp": "queue_follow_up",
    "dequeue": "dequeue_messages",
    "expandTools": "toggle_tool_results",
    "toggleThinking": "toggle_thinking",
    "cycleThinkingLevel": "thinking_cycle",
    "cycleModelForward": "model_cycle",
    "cycleModelBackward": "model_cycle_previous",
    "selectModel": "model_picker",
    "modelsSave": "models_save",
    "modelsEnableAll": "models_enable_all",
    "modelsClearAll": "models_clear_all",
    "modelsToggleProvider": "models_toggle_provider",
    "modelsReorderUp": "models_reorder_up",
    "modelsReorderDown": "models_reorder_down",
    "newSession": "session_new",
    "tree": "session_tree",
    "fork": "session_fork",
    "resume": "session_resume",
    "toggleSessionNamedFilter": "session_toggle_named_filter",
    "toggleSessionPath": "session_toggle_path",
    "toggleSessionSort": "session_toggle_sort",
    "renameSession": "session_rename",
    "deleteSession": "session_delete",
    "deleteSessionNoninvasive": "session_delete_noninvasive",
    "sessionToggleNamedFilter": "session_toggle_named_filter",
    "sessionTogglePath": "session_toggle_path",
    "sessionToggleSort": "session_toggle_sort",
    "sessionRename": "session_rename",
    "sessionDelete": "session_delete",
    "sessionDeleteNoninvasive": "session_delete_noninvasive",
    "treeFoldOrUp": "tree_fold_or_up",
    "treeUnfoldOrDown": "tree_unfold_or_down",
    "treeEditLabel": "tree_edit_label",
    "treeToggleLabelTimestamp": "tree_toggle_label_timestamp",
    "treeFilterDefault": "tree_filter_default",
    "treeFilterNoTools": "tree_filter_no_tools",
    "treeFilterUserOnly": "tree_filter_user_only",
    "treeFilterLabeledOnly": "tree_filter_labeled_only",
    "treeFilterAll": "tree_filter_all",
    "treeFilterCycleForward": "tree_filter_cycle",
    "treeFilterCycleBackward": "tree_filter_cycle_previous",
    "newLine": "insert_newline",
    "submit": "submit_prompt",
    "tab": "accept_completion",
    "copy": "copy_message",
    "cursorUp": "editor_cursor_up",
    "cursorDown": "editor_cursor_down",
    "cursorLeft": "editor_cursor_left",
    "cursorRight": "editor_cursor_right",
    "cursorWordLeft": "editor_cursor_word_left",
    "cursorWordRight": "editor_cursor_word_right",
    "cursorLineStart": "editor_cursor_line_start",
    "cursorLineEnd": "editor_cursor_line_end",
    "jumpForward": "editor_jump_forward",
    "jumpBackward": "editor_jump_backward",
    "pageUp": "editor_page_up",
    "pageDown": "editor_page_down",
    "deleteCharBackward": "editor_delete_char_backward",
    "deleteCharForward": "editor_delete_char_forward",
    "deleteWordBackward": "editor_delete_word_backward",
    "deleteWordForward": "editor_delete_word_forward",
    "deleteToLineStart": "editor_delete_to_line_start",
    "deleteToLineEnd": "editor_delete_to_line_end",
    "yank": "editor_yank",
    "yankPop": "editor_yank_pop",
    "undo": "editor_undo",
    "selectUp": "select_up",
    "selectDown": "select_down",
    "selectPageUp": "select_page_up",
    "selectPageDown": "select_page_down",
    "selectConfirm": "select_confirm",
    "selectCancel": "select_cancel",
}


def _normalize_keybinding_fields(data: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for raw_field, value in data.items():
        field_name = _PI_KEYBINDING_ALIASES.get(raw_field, raw_field)
        if field_name != raw_field and field_name in data:
            continue
        if field_name in normalized and normalized[field_name] != value:
            raise TuiConfigError(f"Duplicate TUI keybinding: {field_name}")
        normalized[field_name] = value
    return normalized


def _key_string(value: object, field_name: str) -> str:
    keys = _key_list(value, field_name)
    if not keys:
        raise TuiConfigError(f"TUI keybinding must be a non-empty string: {field_name}")
    return ",".join(keys)


_OPTIONAL_KEYBINDING_FIELDS = {
    "session_new",
    "session_tree",
    "session_fork",
    "session_resume",
    "session_toggle_named_filter",
    "session_toggle_path",
    "session_toggle_sort",
    "session_rename",
    "session_delete",
    "session_delete_noninvasive",
    "models_save",
    "models_enable_all",
    "models_clear_all",
    "models_toggle_provider",
    "models_reorder_up",
    "models_reorder_down",
    "select_up",
    "select_down",
    "select_page_up",
    "select_page_down",
    "select_confirm",
    "select_cancel",
    "editor_cursor_up",
    "editor_cursor_down",
    "editor_cursor_left",
    "editor_cursor_right",
    "editor_cursor_word_left",
    "editor_cursor_word_right",
    "editor_cursor_line_start",
    "editor_cursor_line_end",
    "editor_jump_forward",
    "editor_jump_backward",
    "editor_page_up",
    "editor_page_down",
    "editor_delete_char_backward",
    "editor_delete_char_forward",
    "editor_delete_word_backward",
    "editor_delete_word_forward",
    "editor_delete_to_line_start",
    "editor_delete_to_line_end",
    "editor_yank",
    "editor_yank_pop",
    "editor_undo",
    "tree_fold_or_up",
    "tree_unfold_or_down",
    "tree_edit_label",
    "tree_toggle_label_timestamp",
    "tree_filter_default",
    "tree_filter_no_tools",
    "tree_filter_user_only",
    "tree_filter_labeled_only",
    "tree_filter_all",
    "tree_filter_cycle",
    "tree_filter_cycle_previous",
}


_SCOPED_KEYBINDING_FIELDS = {
    "session_toggle_named_filter",
    "session_toggle_path",
    "session_toggle_sort",
    "session_rename",
    "session_delete",
    "session_delete_noninvasive",
    "models_save",
    "models_enable_all",
    "models_clear_all",
    "models_toggle_provider",
    "models_reorder_up",
    "models_reorder_down",
    "select_up",
    "select_down",
    "select_page_up",
    "select_page_down",
    "select_confirm",
    "select_cancel",
    "editor_cursor_up",
    "editor_cursor_down",
    "editor_cursor_left",
    "editor_cursor_right",
    "editor_cursor_word_left",
    "editor_cursor_word_right",
    "editor_cursor_line_start",
    "editor_cursor_line_end",
    "editor_jump_forward",
    "editor_jump_backward",
    "editor_page_up",
    "editor_page_down",
    "editor_delete_char_backward",
    "editor_delete_char_forward",
    "editor_delete_word_backward",
    "editor_delete_word_forward",
    "editor_delete_to_line_start",
    "editor_delete_to_line_end",
    "editor_yank",
    "editor_yank_pop",
    "editor_undo",
    "tree_fold_or_up",
    "tree_unfold_or_down",
    "tree_edit_label",
    "tree_toggle_label_timestamp",
    "tree_filter_default",
    "tree_filter_no_tools",
    "tree_filter_user_only",
    "tree_filter_labeled_only",
    "tree_filter_all",
    "tree_filter_cycle",
    "tree_filter_cycle_previous",
}


def _optional_key_string(value: object, field_name: str) -> str:
    return ",".join(_key_list(value, field_name))


def _key_list(value: object, field_name: str) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        keys: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise TuiConfigError(
                    f"TUI keybinding list entries must be non-empty strings: {field_name}"
                )
            keys.append(item.strip())
        return keys
    raise TuiConfigError(f"TUI keybinding must be a string or string list: {field_name}")


def _theme_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TuiConfigError("TUI theme must be a non-empty string")
    name = value.strip()
    if parse_tui_auto_theme_setting(name) is not None:
        return name
    return _fixed_theme_name(name)


def _fixed_theme_name(name: str) -> TuiThemeName:
    if not name.strip() or "/" in name:
        raise TuiConfigError(f"Unknown TUI theme: {name}")
    return name.strip()


def _reject_duplicate_keys(values: dict[str, str]) -> None:
    key_to_action: dict[str, str] = {}
    for action, keys in values.items():
        if action in _SCOPED_KEYBINDING_FIELDS:
            continue
        for key in _configured_key_parts(keys):
            previous_action = key_to_action.get(key)
            if previous_action is not None:
                raise TuiConfigError(
                    f"TUI keybinding {key!r} is assigned to both {previous_action!r} and {action!r}"
                )
            key_to_action[key] = action


def _configured_key_parts(keys: str) -> tuple[str, ...]:
    return tuple(key.strip() for key in keys.split(",") if key.strip())
