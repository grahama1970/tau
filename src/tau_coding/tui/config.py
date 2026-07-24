"""Durable Textual TUI configuration for Tau."""

import os
from dataclasses import dataclass, field
from json import dumps, loads
from pathlib import Path
from typing import Any, Literal, cast

from tau_coding.paths import TauPaths
from tau_coding.thinking import DEFAULT_THINKING_LEVEL, THINKING_LEVELS, ThinkingLevel


class TuiConfigError(ValueError):
    """Raised when Tau TUI configuration is invalid."""


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
    queue_follow_up: str = "alt+enter"
    dequeue_messages: str = "alt+up"
    accept_completion: str = "tab"
    completion_next: str = "down"
    completion_previous: str = "up"
    thinking_cycle: str = "shift+tab"
    model_cycle: str = "ctrl+p"
    model_cycle_previous: str = "shift+ctrl+p"
    model_picker: str = "ctrl+l"
    toggle_thinking: str = "ctrl+t"
    toggle_tool_results: str = "ctrl+o"
    copy_message: str = "ctrl+c"
    copy_last_message: str = "ctrl+x"
    external_editor: str = "ctrl+g"
    paste_clipboard: str = "ctrl+v"
    suspend: str = "ctrl+z"
    quit: str = "ctrl+d"

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
            "queue_follow_up": self.queue_follow_up,
            "dequeue_messages": self.dequeue_messages,
            "accept_completion": self.accept_completion,
            "completion_next": self.completion_next,
            "completion_previous": self.completion_previous,
            "thinking_cycle": self.thinking_cycle,
            "model_cycle": self.model_cycle,
            "model_cycle_previous": self.model_cycle_previous,
            "model_picker": self.model_picker,
            "toggle_thinking": self.toggle_thinking,
            "toggle_tool_results": self.toggle_tool_results,
            "copy_message": self.copy_message,
            "copy_last_message": self.copy_last_message,
            "external_editor": self.external_editor,
            "paste_clipboard": self.paste_clipboard,
            "suspend": self.suspend,
            "quit": self.quit,
        }


type TuiThemeName = Literal["tau-dark", "tau-light", "high-contrast"]
type DoubleEscapeAction = Literal["tree", "fork", "none"]
type TuiTreeFilterMode = Literal["default", "no-tools", "user-only", "labeled-only", "all"]
type TuiQueueDrainMode = Literal["one-at-a-time", "all"]
DEFAULT_AUTOCOMPLETE_MAX_VISIBLE = 5
MIN_AUTOCOMPLETE_MAX_VISIBLE = 3
MAX_AUTOCOMPLETE_MAX_VISIBLE = 20
DEFAULT_EDITOR_PADDING_X = 1
MIN_EDITOR_PADDING_X = 0
MAX_EDITOR_PADDING_X = 3
DEFAULT_OUTPUT_PADDING_X = 1
MIN_OUTPUT_PADDING_X = 0
MAX_OUTPUT_PADDING_X = 1


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
    role_styles={
        "user": TuiRoleStyle(border="#7c8ea6", body="#d8dee9 on #000000"),
        "assistant": TuiRoleStyle(border="#6ea6a0", body="#d8dee9 on #000000"),
        "tool": TuiRoleStyle(border="#8a7a52", body="#cbd5e1 on #000000"),
        "error": TuiRoleStyle(border="#ff4f4f", body="#ffb4b4 on #000000"),
        "status": TuiRoleStyle(border="#526070", body="#aab4c2 on #000000"),
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
    role_styles={
        "user": TuiRoleStyle(border="#00b7ff", body="white on #001626"),
        "assistant": TuiRoleStyle(border="#00ff66", body="white on #001a0b"),
        "tool": TuiRoleStyle(border="#ffd000", body="white on #211900"),
        "error": TuiRoleStyle(border="#ff4f4f", body="white on #260000"),
        "status": TuiRoleStyle(border="#ffffff", body="white on #111111"),
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
    role_styles={
        "user": TuiRoleStyle(border="#2563eb", body="#111827"),
        "assistant": TuiRoleStyle(border="#0f766e", body="#111827"),
        "tool": TuiRoleStyle(border="#a16207", body="#1f2937"),
        "error": TuiRoleStyle(border="#b91c1c", body="#7f1d1d"),
        "status": TuiRoleStyle(border="#64748b", body="#334155"),
        "thinking": TuiRoleStyle(border="#6b7280", body="#4b5563"),
        "skill": TuiRoleStyle(border="#7c3aed", body="#4c1d95"),
        "branch_summary": TuiRoleStyle(border="#9333ea", body="#581c87"),
        "compaction_summary": TuiRoleStyle(border="#9333ea", body="#581c87"),
    },
)


_THEMES: dict[TuiThemeName, TuiTheme] = {
    TAU_DARK_THEME.name: TAU_DARK_THEME,
    TAU_LIGHT_THEME.name: TAU_LIGHT_THEME,
    HIGH_CONTRAST_THEME.name: HIGH_CONTRAST_THEME,
}
BUILTIN_TUI_THEME_NAMES: tuple[TuiThemeName, ...] = tuple(_THEMES)


def get_tui_theme(name: TuiThemeName = "tau-dark") -> TuiTheme:
    """Return a built-in TUI theme by name."""
    return _THEMES[name]


@dataclass(frozen=True, slots=True)
class TuiSettings:
    """Tau TUI settings loaded from Tau home."""

    keybindings: TuiKeybindings = field(default_factory=TuiKeybindings)
    theme: TuiThemeName = "tau-dark"
    auto_compact: bool = True
    auto_copy_selection: bool = False
    block_images: bool = False
    double_escape_action: DoubleEscapeAction = "tree"
    tree_filter_mode: TuiTreeFilterMode = "default"
    hide_thinking: bool = True
    thinking_level: ThinkingLevel = DEFAULT_THINKING_LEVEL
    steering_mode: TuiQueueDrainMode = "one-at-a-time"
    follow_up_mode: TuiQueueDrainMode = "one-at-a-time"
    autocomplete_max_visible: int = DEFAULT_AUTOCOMPLETE_MAX_VISIBLE
    enable_skill_commands: bool = True
    editor_padding_x: int = DEFAULT_EDITOR_PADDING_X
    output_padding_x: int = DEFAULT_OUTPUT_PADDING_X
    clear_on_shrink: bool = field(default_factory=_default_clear_on_shrink)
    show_terminal_progress: bool = False

    def to_json(self) -> dict[str, Any]:
        """Serialize these settings to JSON-compatible data."""
        return {
            "autocomplete_max_visible": self.autocomplete_max_visible,
            "auto_compact": self.auto_compact,
            "auto_copy_selection": self.auto_copy_selection,
            "block_images": self.block_images,
            "double_escape_action": self.double_escape_action,
            "editor_padding_x": self.editor_padding_x,
            "enable_skill_commands": self.enable_skill_commands,
            "hide_thinking": self.hide_thinking,
            "keybindings": self.keybindings.to_json(),
            "output_padding_x": self.output_padding_x,
            "clear_on_shrink": self.clear_on_shrink,
            "show_terminal_progress": self.show_terminal_progress,
            "follow_up_mode": self.follow_up_mode,
            "steering_mode": self.steering_mode,
            "theme": self.theme,
            "thinking_level": self.thinking_level,
            "tree_filter_mode": self.tree_filter_mode,
        }

    @property
    def resolved_theme(self) -> TuiTheme:
        """Return the selected built-in theme."""
        return get_tui_theme(self.theme)


def tui_settings_path(paths: TauPaths | None = None) -> Path:
    """Return the durable TUI settings path."""
    return (paths or TauPaths()).home / "tui.json"


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


def tui_settings_from_json(data: dict[str, Any]) -> TuiSettings:
    """Parse TUI settings from JSON-compatible data."""
    allowed_fields = {
        "auto_compact",
        "auto_copy_selection",
        "autocompleteMaxVisible",
        "autocomplete_max_visible",
        "blockImages",
        "block_images",
        "clearOnShrink",
        "clear_on_shrink",
        "double_escape_action",
        "editorPaddingX",
        "editor_padding_x",
        "enableSkillCommands",
        "enable_skill_commands",
        "hide_thinking",
        "keybindings",
        "outputPad",
        "output_padding_x",
        "showTerminalProgress",
        "show_terminal_progress",
        "followUpMode",
        "follow_up_mode",
        "steeringMode",
        "steering_mode",
        "theme",
        "thinkingLevel",
        "thinking_level",
        "terminal",
        "tree_filter_mode",
    }
    unknown_fields = set(data) - allowed_fields
    if unknown_fields:
        raise TuiConfigError(f"Unknown TUI settings field: {sorted(unknown_fields)[0]}")

    keybindings_data = data.get("keybindings", {})
    if not isinstance(keybindings_data, dict):
        raise TuiConfigError("TUI keybindings must be a JSON object")
    terminal_data = data.get("terminal", {})
    if not isinstance(terminal_data, dict):
        raise TuiConfigError("TUI terminal settings must be a JSON object")
    return TuiSettings(
        keybindings=_keybindings_from_json(keybindings_data),
        theme=_theme_name(data.get("theme", "tau-dark")),
        auto_compact=_bool_setting(data.get("auto_compact", True), "auto_compact"),
        auto_copy_selection=_bool_setting(
            data.get("auto_copy_selection", False),
            "auto_copy_selection",
        ),
        block_images=_bool_setting(
            data.get("block_images", data.get("blockImages", False)),
            "block_images",
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
        thinking_level=_thinking_level(
            data.get("thinking_level", data.get("thinkingLevel", DEFAULT_THINKING_LEVEL))
        ),
        tree_filter_mode=_tree_filter_mode(data.get("tree_filter_mode", "default")),
    )


def _bool_setting(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise TuiConfigError(f"TUI setting must be a boolean: {field_name}")


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


def _thinking_level(value: object) -> ThinkingLevel:
    if value in THINKING_LEVELS:
        return cast(ThinkingLevel, value)
    allowed = ", ".join(THINKING_LEVELS)
    raise TuiConfigError(f"TUI thinking_level must be one of: {allowed}")


def _keybindings_from_json(data: dict[str, Any]) -> TuiKeybindings:
    defaults = TuiKeybindings()
    allowed_fields = set(defaults.to_json())
    legacy_fields = {"message_previous", "message_next"}
    unknown_fields = set(data) - allowed_fields - legacy_fields
    if unknown_fields:
        raise TuiConfigError(f"Unknown TUI keybinding: {sorted(unknown_fields)[0]}")

    values = {
        field_name: _optional_key_string(data.get(field_name, default_value), field_name)
        if field_name in _OPTIONAL_KEYBINDING_FIELDS
        else _key_string(data.get(field_name, default_value), field_name)
        for field_name, default_value in defaults.to_json().items()
    }
    _reject_duplicate_keys(values)
    return TuiKeybindings(**values)


def _key_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TuiConfigError(f"TUI keybinding must be a non-empty string: {field_name}")
    return value.strip()


_OPTIONAL_KEYBINDING_FIELDS = {
    "session_new",
    "session_tree",
    "session_fork",
    "session_resume",
}


def _optional_key_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TuiConfigError(f"TUI keybinding must be a string: {field_name}")
    return value.strip()


def _theme_name(value: object) -> TuiThemeName:
    if not isinstance(value, str) or not value.strip():
        raise TuiConfigError("TUI theme must be a non-empty string")
    name = value.strip()
    if name == "tau-dark" or name == "tau-light" or name == "high-contrast":
        return cast(TuiThemeName, name)
    raise TuiConfigError(f"Unknown TUI theme: {name}")


def _reject_duplicate_keys(values: dict[str, str]) -> None:
    key_to_action: dict[str, str] = {}
    for action, key in values.items():
        if not key:
            continue
        previous_action = key_to_action.get(key)
        if previous_action is not None:
            raise TuiConfigError(
                f"TUI keybinding {key!r} is assigned to both {previous_action!r} and {action!r}"
            )
        key_to_action[key] = action
