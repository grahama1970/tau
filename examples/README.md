# Tau Examples

Copyable examples for Tau's zero-trust and visible-provider lanes.

| Example | What it exercises | Boundary |
| --- | --- | --- |
| [`zero-trust-basic`](zero-trust-basic/) | Local policy/data-boundary preflight through `tau zero-trust-doctor`. | No subagent dispatch, sandbox proof, compliance certification, or provider call. |
| [`herdr-visible-provider`](herdr-visible-provider/) | Herdr-visible provider readiness through the real-world sanity lane. | Requires local Herdr/provider tooling; visible panes are evidence, not truth. |
