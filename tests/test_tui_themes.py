from collections.abc import Iterator
from json import dumps
from pathlib import Path
from typing import Any

import pytest

from tau_coding.resources import TauResourcePaths
from tau_coding.tui.config import TuiSettings
from tau_coding.tui.themes import (
    BUILTIN_TUI_THEME_NAMES,
    TAU_DARK_THEME,
    TAU_LIGHT_THEME,
    THEME_COLOR_FIELDS,
    TRANSCRIPT_ROLES,
    TuiThemeError,
    available_tui_theme_names,
    get_tui_theme,
    load_custom_tui_themes,
    parse_tui_theme_json,
    set_custom_tui_themes,
)


@pytest.fixture(autouse=True)
def _reset_custom_themes() -> Iterator[None]:
    yield
    set_custom_tui_themes({})


def _theme_data(name: str = "midnight", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": name,
        "colors": {field_name: "#101010" for field_name in THEME_COLOR_FIELDS},
        "roles": {role: {"border": "#101010", "body": "#e0e0e0"} for role in TRANSCRIPT_ROLES},
    }
    data.update(overrides)
    return data


def _write_theme(directory: Path, filename: str, data: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(dumps(data), encoding="utf-8")
    return path


def test_parse_theme_resolves_vars_inside_style_strings() -> None:
    data = _theme_data(vars={"base": "#1e1e2e", "teal": "#94e2d5"})
    data["colors"]["screen_background"] = "base"
    data["colors"]["completion_selected"] = "bold base on teal"
    data["roles"]["user"] = {"border": "teal", "body": "#cdd6f4 on base"}

    theme = parse_tui_theme_json(data)

    assert theme.name == "midnight"
    assert theme.screen_background == "#1e1e2e"
    assert theme.completion_selected == "bold #1e1e2e on #94e2d5"
    assert theme.role_styles["user"].border == "#94e2d5"
    assert theme.role_styles["user"].body == "#cdd6f4 on #1e1e2e"


def test_parse_theme_defaults_dark_and_syntax_theme_from_background() -> None:
    dark_data = _theme_data()
    dark_data["colors"]["screen_background"] = "#1e1e2e"
    light_data = _theme_data(name="daylight")
    light_data["colors"]["screen_background"] = "#eff1f5"

    dark_theme = parse_tui_theme_json(dark_data)
    light_theme = parse_tui_theme_json(light_data)

    assert dark_theme.dark is True
    assert dark_theme.syntax_theme == "ansi_dark"
    assert light_theme.dark is False
    assert light_theme.syntax_theme == "ansi_light"


def test_parse_theme_rejects_invalid_colors_and_styles() -> None:
    data = _theme_data()
    data["colors"]["screen_background"] = "not-a-color"
    data["colors"]["accent"] = "bold #ffffff on #000000"
    data["colors"]["completion_selected"] = "bold nope on #000000"
    data["roles"]["user"] = {"border": "#12345", "body": "shiny on #000000"}

    with pytest.raises(TuiThemeError) as exc_info:
        parse_tui_theme_json(data)

    message = str(exc_info.value)
    assert "colors.screen_background" in message
    assert "colors.accent" in message
    assert "colors.completion_selected" in message
    assert "roles.user.border" in message
    assert "roles.user.body" in message


def test_builtin_and_custom_theme_registry_resolve() -> None:
    midnight = parse_tui_theme_json(_theme_data(name="midnight"))

    set_custom_tui_themes({"midnight": midnight})

    assert BUILTIN_TUI_THEME_NAMES == ("tau-dark", "tau-light", "high-contrast")
    assert TAU_DARK_THEME.dark is True
    assert TAU_LIGHT_THEME.dark is False
    assert available_tui_theme_names() == (*BUILTIN_TUI_THEME_NAMES, "midnight")
    assert get_tui_theme("midnight") is midnight
    assert TuiSettings(theme="midnight").resolved_theme is midnight


def test_resolved_theme_falls_back_to_tau_dark_for_unknown_saved_name() -> None:
    assert TuiSettings(theme="missing-theme").resolved_theme is TAU_DARK_THEME


def test_load_custom_themes_prefers_higher_precedence_dirs(tmp_path: Path) -> None:
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    user_theme = _theme_data(name="midnight")
    user_theme["colors"]["accent"] = "#111111"
    project_theme = _theme_data(name="midnight")
    project_theme["colors"]["accent"] = "#222222"
    _write_theme(user_dir, "midnight.json", user_theme)
    _write_theme(project_dir, "midnight.json", project_theme)

    themes, diagnostics = load_custom_tui_themes([user_dir, project_dir])

    assert themes["midnight"].accent == "#222222"
    assert any(
        "already defined with higher precedence" in diagnostic.message
        for diagnostic in diagnostics
    )


def test_load_custom_themes_skips_invalid_files_with_diagnostics(tmp_path: Path) -> None:
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    (themes_dir / "broken.json").write_text("{not json", encoding="utf-8")
    incomplete = _theme_data(name="incomplete")
    del incomplete["colors"]["accent"]
    _write_theme(themes_dir, "incomplete.json", incomplete)
    _write_theme(themes_dir, "midnight.json", _theme_data(name="midnight"))

    themes, diagnostics = load_custom_tui_themes([themes_dir])

    assert list(themes) == ["midnight"]
    assert len(diagnostics) == 2
    assert {diagnostic.kind for diagnostic in diagnostics} == {"theme"}


def test_load_custom_themes_skips_builtin_shadowing_with_diagnostic(tmp_path: Path) -> None:
    _write_theme(tmp_path / "themes", "tau-dark.json", _theme_data(name="tau-dark"))

    themes, diagnostics = load_custom_tui_themes([tmp_path / "themes"])

    assert themes == {}
    assert any("shadows a built-in theme" in diagnostic.message for diagnostic in diagnostics)


def test_tool_result_accents_derive_from_theme() -> None:
    from rich.console import Console

    from tau_coding.tui.state import ChatItem
    from tau_coding.tui.widgets import render_chat_item

    data = _theme_data(vars={"base": "#1e1e2e"})
    data["colors"]["tool_success_text"] = "#00fa9a"
    data["colors"]["tool_error_text"] = "#fa0064"
    data["roles"]["tool"] = {"border": "#101010", "body": "#e0e0e0 on base"}
    theme = parse_tui_theme_json(data)
    console = Console(record=True, width=80)

    console.print(
        render_chat_item(
            ChatItem(role="tool", text="-> read README.md", tool_result_text="✓ read\nok"),
            theme=theme,
            show_tool_results=True,
        )
    )
    console.print(
        render_chat_item(
            ChatItem(role="tool", text="$ false", tool_result_text="✗ bash\nfailed"),
            theme=theme,
            show_tool_results=True,
        )
    )
    output = console.export_text(styles=True)

    assert "38;2;0;250;154" in output
    assert "38;2;250;0;100" in output
    assert "48;2;30;30;46" in output


def test_resource_paths_expose_theme_dirs(tmp_path: Path) -> None:
    paths = TauResourcePaths(root=tmp_path / ".tau", cwd=tmp_path / "project")

    assert paths.themes_dirs == (
        tmp_path / ".tau" / "themes",
        tmp_path / "project" / ".tau" / "themes",
    )
