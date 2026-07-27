# Issue 150 Memory-First Epic Status Proof

Ticket: <https://github.com/grahama1970/tau/issues/150>

## Result

This run does **not** close #150. The child tickets are closed, and two of the
three epic proof legs are present, but the live Graph Memory chain-recall leg is
still degraded.

## What Passed

- Bounded resident skill context:
  `prompt-assembly-measurement.json` measured 370 loaded skills and a doubled
  740-skill corpus. The selected Memory chain prompt stayed at 630 estimated
  system-prompt tokens with `delta_tokens=0`.
- Memory-down CLI degradation:
  `memory-down-doctor.json` used `TAU_MEMORY_URL=http://127.0.0.1:9` and returned
  `status=DEGRADED`, `mocked=false`, `live=true`, with Memory service
  `state=unreachable`.
- Memory-down TUI surface:
  `tui-memory-down-sidebar-proof.json` rendered the existing Tau TUI
  sidebar/loop-monitor with visible `MEMORY DEGRADED`, receipt `DEGRADED`, and
  `mocked:false live:true`.

## What Blocks Closure

Fresh live Graph Memory calls to `http://127.0.0.1:8601/recall` returned HTTP
200 but did not return the explicit graph-chain contracts required by #150:

- `live-skill-chain-recall.json`: `status=DEGRADED`,
  `alert_codes=["skill_chain_missing"]`, `selection_source=registry_fallback`.
- `live-tool-chain-recall.json`: `status=DEGRADED`,
  `alert_codes=["tool_chain_missing"]`, `selected_tools=[]`.

The raw Memory responses are stored beside those receipts. They contain ordinary
recall `items`/`meta`, not `skill_chain` or `tool_chain` objects with traversal
path, hop count, and provenance.

## Required Next Repair

Repair or seed Graph Memory so `/recall` returns explicit `skill_chain` and
`tool_chain` graph contracts for Tau ticket-repair queries, including traversal
path, hop count, and provenance. Then rerun this proof bundle and close #150 only
if both chain receipts return `PASS`.

## Evidence Boundary

`mocked=false` for the live CLI and Memory recall receipts. The Textual TUI
stage fixture under `tui-memory-stage/` is retained as a renderer smoke only and
is not used as closure proof for live Memory behavior.
