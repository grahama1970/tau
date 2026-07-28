# Receipt Admission Contract (proposed, v1)

Status: DESIGN — do not implement until the four decision points below are
human-ratified. Produced 2026-07-28 against `main` @ `fbbcd821` in response to
external review questions. Every code fact cites file:line read on that commit;
proposals are labeled PROPOSAL.

## 1. Verified current state (the defect being fixed)

- No single admission boundary. Three writer families produce authoritative
  receipts independently: in-process skill nodes
  (`generic_dag.py:970 write_json(node.receipt_path, receipt)`), packaged
  workflow node subprocesses (each child writes `receipts/<node_id>.json`
  itself; the parent observes exit codes and `workflows/runner.py:98,159`
  reads the files afterward), and the TUI proof writer.
- All of them use bare non-atomic writes. Three duplicated copies of
  `write_json` = `mkdir` + `Path.write_text` with no temp file, no rename, no
  fsync (`generic_artifact_transaction.py:572`,
  `canonical_scheduler_conformance.py:633`, `traycer/receipts.py:37`). The
  repository already owns a correct atomic primitive
  (`runtime_backends/worktrees.py::_atomic_json_write`, `_durable_unlink`)
  that no receipt path uses.
- Settlement never requires receipts. `_node_is_ready`
  (`dag_runtime/scheduler.py:1720`) releases on edge-state == "success" only;
  transitions (`transition.py:185,217`, `project_transition.py:200`) settle on
  status. There is no invariant anywhere of the form "accepted terminal state
  requires an admitted receipt".
- Journal is WAL, FULL sync. Writer connection: `run_store.py:684-692` —
  `isolation_level=None`, `PRAGMA journal_mode = WAL` (fails closed as
  `dag_run_store_wal_unavailable` if not granted), `synchronous = FULL`,
  `busy_timeout = 5000`. Read back from a live run database:
  `journal_mode=wal, synchronous=2 (FULL), locking_mode=normal`. Note the
  reader/viewer connection (`run_store.py:505-510`) is `query_only=ON` with
  `foreign_keys=OFF` at the probe — separate connection from the writer.
  Consequence: a long-lived read snapshot CAN legitimately miss a
  just-committed sibling receipt row (WAL snapshot pinning); the join itself
  runs in the single scheduler process off in-memory state, so the pinning
  risk applies to cross-process readers (viewer, tests, resume), not the
  scheduler loop.
- Cancellation semantics (verified). In-process worker threads poll
  `cancel_event`; Python cannot interrupt a running `write_text`, so
  cancellation does NOT tear in-process receipt writes (corrects an earlier
  inference). Subprocess children are killed by process group:
  `subprocess_control.py:116-128` sends SIGTERM, sleeps 0.5 s, then SIGKILL;
  the invoking wrapper (`:75-95`) returns exit 130 (cancelled) / 124
  (timeout). A child killed inside its non-atomic receipt write leaves a
  truncated or absent file with no record — this is the concrete tear path.
- Receipt paths are not attempt-scoped. Workflow receipts live at
  `receipts/<node_id>.json`; a retry or repair re-execution overwrites the
  prior attempt's evidence in place.
- Known receipt/run-dir mutators (initial inventory, to be completed in
  implementation): `dag_runtime/retention.py:67 shutil.rmtree`;
  staging swap-in via `shutil.rmtree` + `os.replace` in
  `workflows/nodes/repository_evidence_map.py:415-423` and
  `workflows/nodes/operator_reference.py:640-648`;
  `dag_viewer/source_artifact.py:63-66` (atomic pattern, correct);
  `graph_artifacts.py:165 unlink`. The evidence-map staging swap
  (`rmtree(staging)` on failure after partial `os.replace`) is a suspect for
  the fan-in sibling loss and must be audited first.
- Failure family this contract addresses (five members, all
  "artifact absent after settlement, nothing failed closed"): sibling receipt
  at blocked join; approval-gate receipt after repair/resume;
  `qualify-documentation.json` after targeted repair (new, CI 2026-07-28);
  TUI proof receipt+screenshot; graphviz None-on-missing-dot (fixed).

## 2. Decision points requiring ratification

D1. Authority. PROPOSAL: the SQLite admission row is authoritative; the
    filesystem receipt is required content-addressed evidence. An accepted
    terminal state requires BOTH: a committed admission row AND a durable file
    whose sha256 matches the row. Disagreement is never resolved silently:
    row-without-valid-file => run BLOCKED (evidence loss; no reconstruction —
    receipt content does not live in the DB); file-without-row => quarantined
    at startup reconciliation and recorded as attempted-not-admitted.

D2. Subprocess contract. PROPOSAL: children never write final receipts.
    Children write to `staging/<node_id>/attempt-<n>/` using the atomic
    primitive; the parent validates (schema + goal_hash + sha256), performs
    admission, and settles. Exit codes become hints only; no node settles on
    exit code alone. Parent classification of child outcomes:
    nonzero-no-staged => failed(a); zero-no-staged => failed-with-alert
    (attempted-and-swallowed suspect, b); staged-invalid => failed(b) with
    quarantine; staged-valid => admit; killed-mid-write => staging is
    partial/absent, classified by the write-intent sidecar.

D3. Effect identity. PROPOSAL: new `accepted_effects` table keyed by a
    node-spec-declared `effect_key` (UNIQUE), rows transition
    intent -> succeeded -> accepted; the external call is made only after the
    intent row commits; `accepted` requires validation + receipt admission.
    Crash after external success but before admission => startup
    reconciliation marks the effect `uncertain` and the run BLOCKED with a
    reconciliation task. Semantics are at-least-once with reconciliation,
    stated openly; exactly-once is claimed only for admission (DB uniqueness),
    never for the external call. Receipt identity (per attempt) is separate
    from effect identity (per logical effect).

D4. Attempt history. PROPOSAL: receipts move to
    `receipts/<node_id>/attempt-<n>.json`; the admission row records which
    attempt is accepted. Prior attempts are immutable; repair always creates
    a new attempt. Compatibility: readers resolve legacy
    `receipts/<node_id>.json` as attempt-1 during migration.

## 3. Admission protocol (state transitions and crash matrix)

Steps, in order, for every authoritative receipt:

    S1 write-intent appended to sidecar (append-only file, fsync)
    S2 receipt written to temp file in same directory
    S3 temp file fsynced
    S4 atomic rename to final attempt path
    S5 parent directory fsynced          -- receipt now DURABLE
    S6 receipt validated (schema, goal_hash, sha256 computed)
    S7 one SQLite transaction commits: admission row + terminal event +
       edge-state projection                -- receipt now ADMITTED,
                                               node now SETTLED
    S8 downstream release (ready-queue update from the committed projection)

Visibility rule: scheduler and viewer treat a receipt as existing at S7, not
before. The admission row carries (run_id, node_id, attempt_id, receipt_kind,
sha256, path, bytes, admitted_at) with UNIQUE(run_id, node_id, attempt_id,
receipt_kind); a duplicate admitting writer loses the race at the constraint
and records `duplicate_admission_suppressed` — it must not error the node.

Crash/kill at each boundary and the deterministic recovery:

| Died at | On-disk state | Recovery classification |
| --- | --- | --- |
| before S1 | nothing | (a) never attempted; node not settled; re-run |
| S1..S3 | intent, maybe temp | (b) attempted; temp discarded; re-run |
| S4..S5 partial | intent + renamed-but-unsynced file | (b); file revalidated, re-admitted if valid else quarantined |
| after S5, before S7 | durable file, no row | attempted-not-admitted; startup reconciliation re-validates and re-admits idempotently |
| S7 committed, file later lost | row without file | evidence loss => run BLOCKED (fail closed) |
| after S7, before S8 | admitted + settled, release pending | release is derived from committed projection at next loop/resume; no loss |

Cancellation: S2-S7 run inside a cancellation-masked critical section
(in-process: the section does not poll `cancel_event`; subprocess: only the
parent performs S6-S7, so killpg cannot interrupt admission — it can only
interrupt S2-S5 in the child's staging dir, which recovery classifies as (b)).

## 4. Scheduler invariant (independent of the writer helper)

Add to the transition layer, not the writer: a node whose spec declares
required receipt kinds cannot enter an accepted terminal state unless the
admission rows exist and validate — enforced inside the S7 transaction.
Receipt-requiring terminal states: PASS, FAIL, BLOCKED, CANCELLED, and
approval decisions; SKIPPED-by-routing requires a routing receipt. This is
the invariant whose absence (verified: none exists today) let five silent
absences ship.

## 5. TUI proof path

Same primitive, one logical artifact set: receipt + screenshot admitted
atomically via a manifest (the manifest is the receipt; the screenshot is a
listed artifact with its own sha256). The UI may not report a proof complete
before manifest admission; screenshot-ok/receipt-fail or the reverse are both
"manifest not admitted" => not complete.

## 6. Open items folded into implementation

- Complete the mutator inventory (Section 1 list is initial, not exhaustive);
  audit the evidence-map staging swap first.
- Scheduler read for join reopen-after-repair and conditional-edge
  re-evaluation (external review Q21) before routing the workflow family.
- Ubuntu-container incremental replay of the traced 15-command git sequence
  to name which built-in prunes the unrelated worktree registration on git
  2.54 (trace shows no explicit prune command; candidates: worktree add, its
  internal `reset --hard` child, worktree remove --force). Then decide
  between command-scoped `-c gc.worktreePruneExpire=never` for Tau-issued git
  and preflight refusal when unrelated stale registrations exist.

## 7. Rollout sequence (adopted from external review)

1. Ratify D1-D4. 2. Implement the atomic admission primitive + sidecar.
3. Startup reconciliation. 4. Route the in-process skill family. 5. Run the
four deterministic classification controls (a/b/c/WAL-pinned). 6. Route
subprocess workflow receipts (D2 staging). 7. Route TUI proofs. 8. Enforce
the scheduler invariant. 9. Prohibit legacy writers: CI test that fails on
any `write_text`/duplicated `write_json` targeting receipt paths (grep-based
architectural check) and delete the three `write_json` copies. 10. Load and
clean-wheel campaigns; only then the acceptance walkthrough.
