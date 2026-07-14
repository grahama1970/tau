# DAG Runtime Durability

Tau's canonical DAG scheduler can persist a run in a local SQLite database. The
generic and project DAG entry points create `dag-run.sqlite3` in the run or
receipt directory and pass it to the same `run_dag_plan` scheduler.

## Transaction boundary

The store uses SQLite WAL mode, full synchronous writes, foreign keys, and an
append-only event table. A node attempt has a stable identity and idempotency
key derived from the run, plan hash, node, and attempt number. Its durable
lifecycle is:

```text
RESERVED -> DISPATCHED -> STAGED -> VALIDATED
         -> OUTPUT_COMMITTED -> SETTLED
```

Retry scheduling is persisted before the next attempt is eligible. Scheduler
transitions, route and join receipt references, and their hashes are committed
before the transition is applied to in-memory scheduler state.

## Restart behavior

On restart, Tau acquires or explicitly takes over an expired fenced lease and
replays committed transitions. It does not rerun settled nodes. Results left in
`STAGED`, `VALIDATED`, or `OUTPUT_COMMITTED` are completed from the journal
without rerunning the adapter. A `RESERVED` attempt retains its identity and can
be dispatched after recovery.

An unfinished scheduler generation is recovered in place. After a generation
has reached a terminal run status, an explicit generic-DAG invocation creates a
new generation. That new generation revalidates existing domain receipts and
approval state instead of treating the preceding scheduler projection as fresh
authority.

An attempt found in `DISPATCHED` without a staged result has an uncertain
external effect. Tau marks it `UNCERTAIN` and blocks with
`DAG_ATTEMPT_EFFECT_UNCERTAIN`; it does not guess that the adapter did or did
not perform a side effect. Identical duplicate results are idempotent, while a
different result for the same attempt is rejected.

The scheduler renews its lease while long-running node adapters are active and
while cancelled parallel workers finish. Lease epochs fence stale scheduler
owners. Direct store takeover must be explicit; the generic and project DAG
wrappers request takeover only after the previous lease has expired.

A committed transition containing a run block remains authoritative if the
process stops before writing the terminal run outcome. Replay restores that
block and cannot convert it into a passing run. Committed transition events are
also replayed so route/join verdicts and progress views retain their terminal
meaning.

Generic DAG entry points acquire the scheduler lease before appending run events
or writing shared checkpoint files. A concurrently rejected invocation therefore
cannot overwrite the active run's observable state before lease fencing occurs.

## Integrity and non-claims

Replay verifies event payload hashes and hashes of committed route/join receipt
files. Staged, validated, and committed output projections are checked against
their stored hashes before recovery, and project replay retains prior
course-correction evidence paths. SQLite integrity and foreign-key checks are
available through `SqliteDagRunStore.integrity_check()`.

This implementation proves local scheduler persistence and deterministic
replay for the tested crash boundaries. It does not prove distributed
consensus, exactly-once behavior inside an external provider, automatic
reconciliation of uncertain effects, provider/model correctness, or survival
of disk or host loss. SQLite remains a local run store, not a remote audit
ledger.
