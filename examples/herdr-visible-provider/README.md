# Herdr-Visible Provider Example

This example runs Tau's provider-readiness sanity lane, which allocates visible
Herdr-backed provider panes when the local Herdr and provider CLIs are
available.

```bash
./examples/herdr-visible-provider/run.sh
```

To write a stable example receipt for automated checks:

```bash
./examples/herdr-visible-provider/run.sh /tmp/tau-herdr-visible-provider-example
```

The script writes a compact `tau.herdr_visible_provider_example_receipt.v1` at
`demo-receipt.json`, plus the underlying
`tau.real_world_sanity_suite_receipt.v1` under the output proof tree.

The coding capability sanity runner excludes this live example by default. To
include it explicitly:

```bash
TAU_CODING_SANITY_LIVE_HERDR=1 \
  uv run python scripts/run-coding-capability-sanity.py --run-dir /tmp/tau-coding-sanity-herdr
```

Visible panes are evidence, not truth. This example proves Herdr/provider
readiness telemetry for the local machine; it does not prove provider/model
semantic quality, GitHub mutation, production UI behavior, or future route
correctness.
