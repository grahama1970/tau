from pathlib import Path

import pytest

from tau_coding.paths import TauPaths
from tau_coding.tui.config import (
    HIGH_CONTRAST_THEME,
    TuiConfigError,
    TuiKeybindings,
    TuiSettings,
    get_tui_theme,
    load_tui_settings,
    save_tui_settings,
    tui_settings_from_json,
    tui_settings_path,
)


def test_tui_settings_path_uses_tau_home(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    assert tui_settings_path(paths) == tmp_path / ".tau" / "tui.json"


def test_load_tui_settings_returns_defaults_when_file_is_missing(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    assert load_tui_settings(paths) == TuiSettings()
    assert load_tui_settings(paths).keybindings.quit == "ctrl+d"


def test_load_tui_settings_reads_keybindings(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    path = tui_settings_path(paths)
    path.parent.mkdir(parents=True)
    path.write_text(
        """
        {
          "keybindings": {
            "command_palette": "ctrl+j",
            "session_picker": "ctrl+y",
            "session_new": "f13",
            "session_tree": "f14",
            "session_fork": "f15",
            "session_resume": "f16",
            "queue_follow_up": "f5",
            "dequeue_messages": "f9",
            "accept_completion": "f2",
            "thinking_cycle": "f3",
            "model_cycle": "f6",
            "model_cycle_previous": "f11",
            "model_picker": "f10",
            "toggle_thinking": "f4",
            "external_editor": "f7",
            "paste_clipboard": "f8",
            "suspend": "f12",
            "copy_message": "ctrl+b",
            "copy_last_message": "ctrl+x"
          },
          "theme": "high-contrast",
          "autocompleteMaxVisible": 12,
          "enableSkillCommands": false,
          "tree_filter_mode": "user-only",
          "steering_mode": "all",
          "followUpMode": "all",
          "thinkingLevel": "high"
        }
        """,
        encoding="utf-8",
    )

    settings = load_tui_settings(paths)

    assert settings.keybindings.command_palette == "ctrl+j"
    assert settings.keybindings.session_picker == "ctrl+y"
    assert settings.keybindings.session_new == "f13"
    assert settings.keybindings.session_tree == "f14"
    assert settings.keybindings.session_fork == "f15"
    assert settings.keybindings.session_resume == "f16"
    assert settings.keybindings.queue_follow_up == "f5"
    assert settings.keybindings.dequeue_messages == "f9"
    assert settings.keybindings.toggle_tool_results == "ctrl+o"
    assert settings.keybindings.toggle_thinking == "f4"
    assert settings.keybindings.accept_completion == "f2"
    assert settings.keybindings.thinking_cycle == "f3"
    assert settings.keybindings.model_cycle == "f6"
    assert settings.keybindings.model_cycle_previous == "f11"
    assert settings.keybindings.model_picker == "f10"
    assert settings.keybindings.external_editor == "f7"
    assert settings.keybindings.paste_clipboard == "f8"
    assert settings.keybindings.suspend == "f12"
    assert settings.keybindings.copy_message == "ctrl+b"
    assert settings.keybindings.copy_last_message == "ctrl+x"
    assert settings.keybindings.cancel == "escape"
    assert settings.theme == "high-contrast"
    assert settings.autocomplete_max_visible == 12
    assert settings.enable_skill_commands is False
    assert settings.auto_compact is True
    assert settings.double_escape_action == "tree"
    assert settings.hide_thinking is True
    assert settings.steering_mode == "all"
    assert settings.follow_up_mode == "all"
    assert settings.thinking_level == "high"
    assert settings.tree_filter_mode == "user-only"
    assert settings.resolved_theme == HIGH_CONTRAST_THEME


def test_save_tui_settings_writes_json(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    path = save_tui_settings(TuiSettings(theme="tau-light"), paths)

    assert path == tmp_path / ".tau" / "tui.json"
    assert load_tui_settings(paths).theme == "tau-light"


def test_tui_settings_ignores_removed_message_selection_keybindings() -> None:
    settings = tui_settings_from_json(
        {
            "keybindings": {
                "message_previous": "alt+up",
                "message_next": "alt+down",
            }
        }
    )

    assert settings == TuiSettings()


def test_tui_settings_reject_unknown_fields() -> None:
    with pytest.raises(TuiConfigError, match="Unknown TUI settings field"):
        tui_settings_from_json({"palette": {}})


def test_tui_settings_reject_invalid_queue_modes() -> None:
    with pytest.raises(TuiConfigError, match="steering_mode"):
        tui_settings_from_json({"steering_mode": "latest"})
    with pytest.raises(TuiConfigError, match="follow_up_mode"):
        tui_settings_from_json({"follow_up_mode": "latest"})


def test_tui_settings_reject_invalid_thinking_level() -> None:
    with pytest.raises(TuiConfigError, match="thinking_level"):
        tui_settings_from_json({"thinking_level": "ultra"})


def test_tui_keybindings_reject_duplicate_keys() -> None:
    with pytest.raises(TuiConfigError, match="assigned to both"):
        tui_settings_from_json(
            {
                "keybindings": {
                    "cancel": "escape",
                    "command_palette": "escape",
                }
            }
        )


def test_tui_settings_reject_unknown_theme() -> None:
    with pytest.raises(TuiConfigError, match="Unknown TUI theme"):
        tui_settings_from_json({"theme": "solarized"})


def test_tui_settings_accept_light_theme() -> None:
    settings = tui_settings_from_json({"theme": "tau-light"})

    assert settings.theme == "tau-light"
    assert settings.resolved_theme.screen_background == "#ffffff"
    assert settings.resolved_theme.syntax_theme == "ansi_light"


def test_tui_settings_load_auto_copy_selection() -> None:
    settings = tui_settings_from_json({"auto_copy_selection": True})

    assert settings.auto_copy_selection is True
    assert settings.to_json()["auto_copy_selection"] is True


def test_tui_settings_load_auto_compact() -> None:
    settings = tui_settings_from_json({"auto_compact": False})

    assert settings.auto_compact is False
    assert settings.to_json()["auto_compact"] is False


def test_tui_settings_load_double_escape_action() -> None:
    settings = tui_settings_from_json({"double_escape_action": "fork"})

    assert settings.double_escape_action == "fork"
    assert settings.to_json()["double_escape_action"] == "fork"


def test_tui_settings_load_tree_filter_mode() -> None:
    settings = tui_settings_from_json({"tree_filter_mode": "labeled-only"})

    assert settings.tree_filter_mode == "labeled-only"
    assert settings.to_json()["tree_filter_mode"] == "labeled-only"


def test_tui_settings_load_hide_thinking() -> None:
    settings = tui_settings_from_json({"hide_thinking": False})

    assert settings.hide_thinking is False
    assert settings.to_json()["hide_thinking"] is False


def test_tui_settings_reject_invalid_double_escape_action() -> None:
    with pytest.raises(TuiConfigError, match="double_escape_action"):
        tui_settings_from_json({"double_escape_action": "open"})


def test_tui_settings_reject_invalid_tree_filter_mode() -> None:
    with pytest.raises(TuiConfigError, match="tree_filter_mode"):
        tui_settings_from_json({"tree_filter_mode": "named"})


def test_tui_settings_reject_invalid_hide_thinking() -> None:
    with pytest.raises(TuiConfigError, match="hide_thinking"):
        tui_settings_from_json({"hide_thinking": "yes"})


def test_tui_settings_reject_invalid_auto_copy_selection() -> None:
    with pytest.raises(TuiConfigError, match="auto_copy_selection"):
        tui_settings_from_json({"auto_copy_selection": "yes"})


def test_tui_settings_reject_invalid_auto_compact() -> None:
    with pytest.raises(TuiConfigError, match="auto_compact"):
        tui_settings_from_json({"auto_compact": "no"})


def test_tui_settings_load_autocomplete_max_visible_aliases() -> None:
    camel = tui_settings_from_json({"autocompleteMaxVisible": 8})
    snake = tui_settings_from_json({"autocomplete_max_visible": 12})

    assert camel.autocomplete_max_visible == 8
    assert snake.autocomplete_max_visible == 12


def test_tui_settings_reject_invalid_autocomplete_max_visible() -> None:
    with pytest.raises(TuiConfigError, match="autocomplete_max_visible"):
        tui_settings_from_json({"autocomplete_max_visible": 2})
    with pytest.raises(TuiConfigError, match="autocomplete_max_visible"):
        tui_settings_from_json({"autocomplete_max_visible": 21})
    with pytest.raises(TuiConfigError, match="autocomplete_max_visible"):
        tui_settings_from_json({"autocomplete_max_visible": "5"})


def test_tui_settings_load_enable_skill_commands_aliases() -> None:
    camel = tui_settings_from_json({"enableSkillCommands": False})
    snake = tui_settings_from_json({"enable_skill_commands": False})

    assert camel.enable_skill_commands is False
    assert snake.enable_skill_commands is False


def test_tui_settings_reject_invalid_enable_skill_commands() -> None:
    with pytest.raises(TuiConfigError, match="enable_skill_commands"):
        tui_settings_from_json({"enable_skill_commands": "false"})


def test_tui_keybindings_serialize_to_json() -> None:
    settings = TuiSettings(
        keybindings=TuiKeybindings(
            command_palette="ctrl+j",
            session_picker="ctrl+y",
            session_new="f13",
            session_tree="f14",
            session_fork="f15",
            session_resume="f16",
            queue_follow_up="f5",
            dequeue_messages="f9",
            accept_completion="f2",
            thinking_cycle="f3",
            model_cycle="f6",
            model_cycle_previous="f11",
            model_picker="f10",
            toggle_thinking="f4",
            external_editor="f7",
            paste_clipboard="f8",
            suspend="f12",
            copy_message="ctrl+b",
            copy_last_message="ctrl+x",
        ),
        theme="high-contrast",
    )

    assert settings.to_json()["keybindings"]["command_palette"] == "ctrl+j"
    assert settings.to_json()["keybindings"]["session_picker"] == "ctrl+y"
    assert settings.to_json()["keybindings"]["session_new"] == "f13"
    assert settings.to_json()["keybindings"]["session_tree"] == "f14"
    assert settings.to_json()["keybindings"]["session_fork"] == "f15"
    assert settings.to_json()["keybindings"]["session_resume"] == "f16"
    assert settings.to_json()["keybindings"]["queue_follow_up"] == "f5"
    assert settings.to_json()["keybindings"]["dequeue_messages"] == "f9"
    assert settings.to_json()["keybindings"]["toggle_tool_results"] == "ctrl+o"
    assert settings.to_json()["keybindings"]["toggle_thinking"] == "f4"
    assert settings.to_json()["keybindings"]["accept_completion"] == "f2"
    assert settings.to_json()["keybindings"]["thinking_cycle"] == "f3"
    assert settings.to_json()["keybindings"]["model_cycle"] == "f6"
    assert settings.to_json()["keybindings"]["model_cycle_previous"] == "f11"
    assert settings.to_json()["keybindings"]["model_picker"] == "f10"
    assert settings.to_json()["keybindings"]["external_editor"] == "f7"
    assert settings.to_json()["keybindings"]["paste_clipboard"] == "f8"
    assert settings.to_json()["keybindings"]["suspend"] == "f12"
    assert settings.to_json()["keybindings"]["copy_message"] == "ctrl+b"
    assert settings.to_json()["keybindings"]["copy_last_message"] == "ctrl+x"
    assert settings.to_json()["autocomplete_max_visible"] == 5
    assert settings.to_json()["enable_skill_commands"] is True
    assert settings.to_json()["theme"] == "high-contrast"
    assert settings.to_json()["auto_compact"] is True
    assert settings.to_json()["auto_copy_selection"] is False
    assert settings.to_json()["double_escape_action"] == "tree"
    assert settings.to_json()["hide_thinking"] is True
    assert settings.to_json()["tree_filter_mode"] == "default"


def test_get_tui_theme_returns_builtin_theme() -> None:
    assert get_tui_theme("high-contrast").prompt_border == "#00ff66"
    assert get_tui_theme("tau-light").prompt_border == "#2563eb"
    assert get_tui_theme("tau-dark").screen_background == "#000000"
