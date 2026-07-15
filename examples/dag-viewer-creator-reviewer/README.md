# Creator-reviewer live viewer example

`scripts/run-dag-viewer-live-smoke.py` materializes this example with absolute
paths, runs its real local subprocesses, and observes the Tau-authored live
snapshots. The first review returns `REVISE`; the second returns a `PASS` claim.
Only the scheduler's committed receipt admission settles the transaction and
releases `continuation`.

The example is deterministic and non-provider-backed: `mocked:false`,
`live:true`, `provider_live:false`.
