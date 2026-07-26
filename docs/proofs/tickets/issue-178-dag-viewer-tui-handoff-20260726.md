# Issue #178 Proof: TUI DAG Viewer Handoff

Ticket: https://github.com/grahama1970/tau/issues/178

## Change

- Added configurable TUI keybinding `dag_viewer_handoff` with default `ctrl+alt+v`.
- Added `/dag-viewer [run-dir]` local TUI command.
- The TUI now remembers the most recent workflow run directory from a workflow terminal receipt.
- The handoff calls `build_dag_viewer_link(run_dir)`, consumes the exact `tau dag-view --run-dir ... --run-id ...` launch command it returns, and starts the same read-only loopback DAG viewer server used by the CLI.
- When `current-state.json` names an `active_node_id` or blocked node, the opened viewer URL includes node targeting query parameters.
- Unavailable/missing viewer state is surfaced as an explicit TUI message.

## Deterministic Proof

Commands run from `/tmp/tau-main-issue-137.7nchbf`:

```bash
uv run python -m py_compile src/tau_coding/tui/app.py src/tau_coding/tui/config.py tests/test_tui_app.py tests/test_tui_config.py
```

Result: exit 0.

```bash
uv run ruff check src/tau_coding/tui/app.py src/tau_coding/tui/config.py tests/test_tui_app.py tests/test_tui_config.py
```

Result: `All checks passed!`

```bash
uv run pytest tests/test_tui_app.py::test_tui_app_hotkeys_uses_configured_keybindings tests/test_tui_app.py::test_tui_app_dag_viewer_command_opens_existing_link_target tests/test_tui_app.py::test_tui_app_dag_viewer_keybinding_targets_current_node tests/test_tui_app.py::test_tui_app_dag_viewer_unavailable_path_surfaces_message tests/test_tui_config.py::test_load_tui_settings_reads_keybindings tests/test_tui_config.py::test_tui_settings_reads_pi_keybinding_aliases tests/test_tui_config.py::test_tui_keybindings_serialize_to_json -q
```

Result: `7 passed in 4.49s`.

```bash
git diff --check
```

Result: exit 0.

## Proof Scope

mocked: no for DAG journal creation, `build_dag_viewer_link`, loopback viewer server creation, and `/healthz` response.

live: no provider calls, no external network.

Browser GUI launch was intentionally intercepted in pytest so the test captures the operator-initiated URL without opening a desktop browser. The server behind that captured URL was real and answered `/healthz`.

What this proves:

- `/dag-viewer <run-dir>` opens a web viewer URL backed by the same link target returned by `build_dag_viewer_link`.
- A configured TUI keybinding opens the remembered current DAG run.
- Node-level targeting is added when `current-state.json` names an active node.
- Missing/invalid run directories produce explicit TUI output and do not call `webbrowser.open`.
- The binding appears in `/hotkeys` and serializes through TUI settings.

What remains outside this proof:

- Real desktop browser rendering after `webbrowser.open`.
- Provider/model semantic behavior.
- Long-lived manual TUI ergonomics beyond the focused handoff path.
