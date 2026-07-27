# Issue 182 Installed Wheel Viewer Proof

This proof demonstrates that an installed Tau wheel can serve the packaged DAG
viewer without requiring Node at runtime.

## Commands

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/installed_wheel_viewer_proof.py src/tau_coding/cli.py
PYTHONPATH=src uv run tau installed-wheel-viewer-proof --allow-live-browser --output docs/proofs/tickets/issue-182-installed-wheel-viewer-proof-20260727/installed-wheel-viewer-proof.json
jq '{status,mocked,live,provider_live,wheel_static_index:.wheel.static_index_present,wheel_static_asset_count:.wheel.static_asset_count,no_runtime_node_required:.runtime.no_runtime_node_required,node_commands_after_wheel_install:.runtime.node_commands_after_wheel_install,desktop_screenshot:.screenshots.desktop.path,mobile_screenshot:.screenshots.mobile.path,mutating_method:.http.mutating_method,capabilities_read_only:.http.capabilities_read_only,state_schema:.http.state_schema}' docs/proofs/tickets/issue-182-installed-wheel-viewer-proof-20260727/installed-wheel-viewer-proof.json
```

## Artifacts

- `installed-wheel-viewer-proof.json`: live installed-wheel proof receipt.
- `installed-wheel-viewer-desktop.png`: Chrome-rendered desktop screenshot.
- `installed-wheel-viewer-mobile.png`: Chrome-rendered mobile screenshot.
- `dist/tau-0.1.0-py3-none-any.whl`: wheel used by the isolated installed proof.

## Readback

```json
{
  "status": "PASS",
  "mocked": false,
  "live": true,
  "provider_live": false,
  "wheel_static_index": true,
  "wheel_static_asset_count": 2,
  "no_runtime_node_required": true,
  "node_commands_after_wheel_install": [],
  "mutating_method": {
    "method": "POST",
    "path": "/api/v1/state",
    "status": 405
  },
  "capabilities_read_only": true,
  "state_schema": "tau.dag_view_snapshot.v2"
}
```

## Visual Inspection

The desktop and mobile screenshots were inspected after generation. Both render
the packaged viewer with the live status, read-only label, journal state, and
worker node visible. The earlier loading-only mobile capture was rejected and
the proof command was repaired to wait for Chrome virtual time before capture.

## Boundary

This proves a freshly built Tau wheel includes and serves the DAG viewer static
assets from an isolated installed environment, and that no Node/npm command is
used after wheel installation. It does not prove provider semantic correctness
or every possible browser environment.
