# Herdr-Visible Provider Example

This example runs Tau's provider-readiness sanity lane, which allocates visible
Herdr-backed provider panes when the local Herdr and provider CLIs are
available.

```bash
./examples/herdr-visible-provider/run.sh
```

The script writes a `tau.real_world_sanity_suite_receipt.v1` receipt under
Tau's proof tree and prints compact status fields.

Visible panes are evidence, not truth. This example proves Herdr/provider
readiness telemetry for the local machine; it does not prove provider/model
semantic quality, GitHub mutation, production UI behavior, or future route
correctness.
