# Issue #137 Proof: TUI Settings Forward Compatibility

Issue: https://github.com/grahama1970/tau/issues/137

## Change

Tau now keeps user-level `~/.tau/tui.json` forward-compatible by warning and
ignoring unknown top-level TUI settings and unknown keybinding actions. Known
settings and known keybindings still use strict type, value, and duplicate-key
validation.

## Deterministic Proof

Commands were run from clean main worktree:

```bash
uv run pytest -q tests/test_tui_config.py
```

Result:

```text
79 passed in 0.39s
```

```bash
uv run python -m py_compile src/tau_coding/tui/config.py tests/test_tui_config.py
```

Result: exit 0.

```bash
tmpdir=$(mktemp -d /tmp/tau-issue-137-main-load.XXXXXX)
mkdir -p "$tmpdir/.tau"
printf '{"future_top_level_setting": true, "theme": "tau-light", "keybindings": {"future_action": "ctrl+n", "command_palette": "ctrl+j"}}\n' > "$tmpdir/.tau/tui.json"
HOME="$tmpdir" uv run python - <<'PY'
from tau_coding.tui.config import load_tui_settings, tui_settings_path
settings = load_tui_settings()
print(f"settings_path={tui_settings_path()}")
print(f"theme={settings.theme}")
print(f"command_palette={settings.keybindings.command_palette}")
print(f"cancel={settings.keybindings.cancel}")
PY
```

Result excerpt:

```text
RuntimeWarning: Ignoring unknown TUI settings fields: future_top_level_setting
RuntimeWarning: Ignoring unknown TUI keybindings: future_action
settings_path=/tmp/tau-issue-137-main-load.swcOoa/.tau/tui.json
theme=tau-light
command_palette=ctrl+j
cancel=escape
```

## Evidence Boundary

mocked: no
live: no external service calls; yes real local file load through Tau's config
loader

This proves the reported config-loader failure mode no longer raises on future
unknown fields. It does not prove full interactive Textual rendering or provider
calls.
