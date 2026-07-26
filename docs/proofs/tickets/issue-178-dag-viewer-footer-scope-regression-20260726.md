# Issue 178 Regression Proof: Scoped DAG Viewer Footer Hint

Ticket: https://github.com/grahama1970/tau/issues/178

## Scope

Issue #178 was reopened because the new `dag_viewer_handoff` keybinding leaked
into every Textual footer hint set. The footer advertised `ctrl+alt+v` in normal
startup, completion popup, and running states even when no current DAG run was
known.

This patch keeps the action registered but hides the app-level binding from the
always-visible footer. The prompt footer now shows `DAG viewer` only in normal
prompt mode when the TUI has a current DAG run directory. The shortcut remains
registered as a hidden prompt binding so focused prompt input still routes the
key to the handoff action.

## Changed Paths

- `src/tau_coding/tui/app.py`
- `tests/test_tui_app.py`
- `docs/proofs/tickets/issue-178-dag-viewer-footer-scope-regression-20260726.md`

## Deterministic Commands

Reopened footer failures after repair:

```bash
uv run pytest -q \
  tests/test_tui_app.py::test_tui_app_uses_textual_footer_for_shortcut_hints \
  tests/test_tui_app.py::test_tui_app_footer_hints_update_for_completions \
  tests/test_tui_app.py::test_tui_app_footer_hints_update_while_running \
  -vv
```

Result:

```text
3 passed in 0.93s
```

Focused #178 handoff/config/footer proof:

```bash
uv run pytest -q \
  tests/test_tui_app.py::test_tui_app_uses_textual_footer_for_shortcut_hints \
  tests/test_tui_app.py::test_tui_app_footer_hints_update_for_completions \
  tests/test_tui_app.py::test_tui_app_footer_hints_update_while_running \
  tests/test_tui_app.py::test_tui_app_footer_shows_dag_viewer_only_when_run_is_known \
  tests/test_tui_app.py::test_tui_app_hotkeys_uses_configured_keybindings \
  tests/test_tui_app.py::test_tui_app_dag_viewer_command_opens_existing_link_target \
  tests/test_tui_app.py::test_tui_app_dag_viewer_keybinding_targets_current_node \
  tests/test_tui_app.py::test_tui_app_dag_viewer_unavailable_path_surfaces_message \
  tests/test_tui_config.py::test_load_tui_settings_reads_keybindings \
  tests/test_tui_config.py::test_tui_settings_reads_pi_keybinding_aliases \
  tests/test_tui_config.py::test_tui_keybindings_serialize_to_json
```

Result:

```text
11 passed in 5.01s
```

Lint:

```bash
uv run ruff check \
  src/tau_coding/tui/app.py \
  src/tau_coding/tui/config.py \
  tests/test_tui_app.py \
  tests/test_tui_config.py
```

Result:

```text
All checks passed!
```

Compile:

```bash
uv run python -m py_compile \
  src/tau_coding/tui/app.py \
  src/tau_coding/tui/config.py \
  tests/test_tui_app.py \
  tests/test_tui_config.py
```

Result: exit code `0`.

Diff hygiene:

```bash
git diff --check
```

Result: exit code `0`.

## Evidence Classification

- mocked: no for DAG journal creation, `build_dag_viewer_link`, loopback viewer
  server creation, and `/healthz` response in the existing handoff tests.
- mocked: yes for Textual footer state fixtures and intercepted browser opening.
- live: no provider calls and no external network.
- exercised: scoped footer hints, configured keybinding dispatch, `/dag-viewer`
  command path, node-level targeting, unavailable-viewer message path, key list
  discoverability, lint, compile, and diff hygiene.
- remains unverified: real desktop browser rendering after `webbrowser.open`.
