"""Durable Textual TUI configuration for Tau."""

from dataclasses import dataclass, field
from json import loads
from pathlib import Path
from typing import Any

from tau_coding.paths import TauPaths


class TuiConfigError(ValueError):
    """Raised when Tau TUI configuration is invalid."""


@dataclass(frozen=True, slots=True)
class TuiKeybindings:
    """Configurable keys for Tau's built-in Textual frontend."""

    cancel: str = "escape"
    command_palette: str = "ctrl+k"
    accept_completion: str = "tab"
    completion_next: str = "down"
    completion_previous: str = "up"
    quit: str = "ctrl+q"

    def to_json(self) -> dict[str, str]:
        """Serialize these keybindings to JSON-compatible data."""
        return {
            "cancel": self.cancel,
            "command_palette": self.command_palette,
            "accept_completion": self.accept_completion,
            "completion_next": self.completion_next,
            "completion_previous": self.completion_previous,
            "quit": self.quit,
        }


@dataclass(frozen=True, slots=True)
class TuiSettings:
    """Tau TUI settings loaded from Tau home."""

    keybindings: TuiKeybindings = field(default_factory=TuiKeybindings)

    def to_json(self) -> dict[str, Any]:
        """Serialize these settings to JSON-compatible data."""
        return {"keybindings": self.keybindings.to_json()}


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


def tui_settings_from_json(data: dict[str, Any]) -> TuiSettings:
    """Parse TUI settings from JSON-compatible data."""
    allowed_fields = {"keybindings"}
    unknown_fields = set(data) - allowed_fields
    if unknown_fields:
        raise TuiConfigError(f"Unknown TUI settings field: {sorted(unknown_fields)[0]}")

    keybindings_data = data.get("keybindings", {})
    if not isinstance(keybindings_data, dict):
        raise TuiConfigError("TUI keybindings must be a JSON object")
    return TuiSettings(keybindings=_keybindings_from_json(keybindings_data))


def _keybindings_from_json(data: dict[str, Any]) -> TuiKeybindings:
    defaults = TuiKeybindings()
    allowed_fields = set(defaults.to_json())
    unknown_fields = set(data) - allowed_fields
    if unknown_fields:
        raise TuiConfigError(f"Unknown TUI keybinding: {sorted(unknown_fields)[0]}")

    values = {
        field_name: _key_string(data.get(field_name, default_value), field_name)
        for field_name, default_value in defaults.to_json().items()
    }
    _reject_duplicate_keys(values)
    return TuiKeybindings(**values)


def _key_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TuiConfigError(f"TUI keybinding must be a non-empty string: {field_name}")
    return value.strip()


def _reject_duplicate_keys(values: dict[str, str]) -> None:
    key_to_action: dict[str, str] = {}
    for action, key in values.items():
        previous_action = key_to_action.get(key)
        if previous_action is not None:
            raise TuiConfigError(
                f"TUI keybinding {key!r} is assigned to both "
                f"{previous_action!r} and {action!r}"
            )
        key_to_action[key] = action
