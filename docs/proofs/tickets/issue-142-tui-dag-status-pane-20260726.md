# Issue #142 Compact TUI DAG Status Pane Proof

Date: 2026-07-26

## Scope

Implemented a compact Textual TUI DAG status pane that:

- mounts below the extension widget area as `#dag-run-status-pane`;
- polls the current remembered DAG run directory every 750 ms;
- builds status from the shared web-viewer projection path:
  `_last_dag_viewer_run_dir` -> `load_dag_replay()` -> `build_dag_live_snapshot()`;
- renders active node ids, scheduler state counts, elapsed time, highest-priority blocker, pending transaction action cue, journal sequence, and `/dag-viewer` handoff text;
- renders readable text when the run directory is missing or invalid;
- does not render React Flow, terminal graphics, topology, receipt trees, or a second graph/state projection.

## Proof Commands

```text
uv run ruff check src/tau_coding/tui/widgets.py src/tau_coding/tui/app.py src/tau_coding/tui/__init__.py tests/test_tui_app.py
```

Result:

```text
All checks passed!
```

```text
uv run python -m py_compile src/tau_coding/tui/widgets.py src/tau_coding/tui/app.py src/tau_coding/tui/__init__.py tests/test_tui_app.py
```

Result: exit code 0.

```text
uv run pytest -q tests/test_tui_app.py -k 'dag_status or dag_viewer_command_opens_existing_link_target or dag_viewer_keybinding_targets_current_node or compact_session_info'
```

Result:

```text
15 passed, 462 deselected in 3.99s
```

```text
git diff --check
```

Result: exit code 0.

## Evidence Classification

- mocked: no for production path; tests include a controlled snapshot renderer fixture and an actual local DAG viewer fixture
- live: yes, local Textual app exercise and local DAG run fixture through the existing DAG viewer link/projection path
- provider_live: no
- exercised: TUI pane rendering, app refresh without operator input, shared projection adapter, existing `/dag-viewer` handoff tests, invalid-run text fallback
- remains unverified: real operator UX in an attached terminal over a long-running production DAG; no browser/mobile check is needed for this TUI-only pane

## Acceptance Mapping

- unattended state transition in pane: `test_tui_app_dag_status_pane_updates_without_operator_input`
- field-level snapshot parity and single projection: `test_tui_dag_status_snapshot_uses_shared_projection`
- no second graph renderer: implementation renders only text from the snapshot and leaves React Flow/web viewer code untouched
- no-graphics fallback: `test_tui_app_dag_status_pane_uses_readable_text_fallback`
