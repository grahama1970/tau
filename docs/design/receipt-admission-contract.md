# Receipt Admission Contract (v2 — ratified with amendments)

Status: RATIFIED FOR IMPLEMENTATION per external review 2026-07-28: D2 and D4
ratified as proposed; D1 and D3 ratified with the amendments recorded in
Section 8, which override the corresponding v1 text below wherever they
conflict. The v1 crash-matrix row for "durable file, no row" is superseded by
Amendment A1. Produced 2026-07-28 against `main` @ `fbbcd821` in response to
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

## 8. Ratified amendments (v2, 2026-07-28) — these override v1 where they conflict

### A1. D1 amended: composite admission authority and the orphan rule

ADMITTED is a composite invariant, not a row property:

    ADMITTED = committed admission row
             AND matching durable evidence object
             AND successful schema/hash validation

SQLite is the authority for STATE and IDENTITY; the content-addressed file is
the authority for EVIDENCE CONTENT. This supersedes the v1 phrase "the SQLite
row is authoritative."

Orphan recovery rule (resolves the v1 D1/crash-matrix contradiction — the
crash-matrix row "durable file, no row => re-validated and re-admitted" is
replaced by this): a file without an admission row is re-admitted ONLY when
ALL of the following hold, otherwise it is quarantined and the run BLOCKED:

1. a matching, valid write-intent record exists in the sidecar;
2. run_id, node_id, attempt_id, receipt_kind, goal_hash, schema, path and
   digest all verify against that intent;
3. the attempt has not been superseded, cancelled, or explicitly rejected.

A bare file is never sufficient for re-admission. This prevents an old,
injected, or abandoned file from becoming authoritative.

### A2. D2 clarification: candidate staging vs authoritative durability

Child staging is intentionally NON-authoritative. A killed child may leave an
incomplete staging candidate; that is acceptable because only the parent can
promote it. Protocol:

- the child writes into a private per-attempt staging directory
  (`staging/<node_id>/attempt-<n>/`);
- the parent never validates or admits from a path still owned by a live
  child: promotion requires the child process to be reaped first;
- after child exit, the parent PROMOTES the candidate by `os.replace` into a
  parent-owned immutable path, then performs the authoritative
  temp/fsync/rename/dir-fsync itself if the staged object was not already
  written with the atomic primitive, and only then hashes, validates, and
  admits (S6-S7). A child can therefore never mutate a file the parent is
  hashing.

### A3. D3 amended: effect identity, ownership, reconciliation, evidence

1. Namespace. Effect uniqueness is `(effect_type, effect_scope, effect_key)`,
   not a bare key. run_id is provenance, never part of the uniqueness key —
   duplicate suppression must survive a new run.
2. Ownership. The `accepted_effects` row carries `owner_attempt_id`,
   `lease_token`, `lease_expires_at`, `state_version`. Acquisition is a
   single transactional conditional update (proceed only when exactly one row
   was updated); two workers can never both own an intent.
3. Reconciliation is effect-type-specific and DECLARED. A node whose external
   effect has no declared reconciliation strategy fails validation before
   execution or is explicitly labelled manual-reconciliation-only.
   Current inventory (verified on `main` this date): scheduler/workflow core
   performs filesystem publication only — staged `_publish` in
   `approved_release_bundle.py:293` and
   `durable_repository_qualification.py:433`, the latter already carrying a
   deterministic digest read-back (`_verify_published`, `:478`) — plus git
   worktree mutation in `runtime_backends/worktrees.py`. No GitHub/HTTP
   writes exist in scheduler or workflow core (grep verified). Provider/
   browser calls and Memory writes live in skill-node adapters outside the
   core and default to manual-reconciliation-only until each declares a
   strategy. Reconciliation classes: filesystem => digest compare
   (deterministic); git => worktree list/status compare (deterministic);
   external services => read-back by idempotency key where one exists, else
   human resolution.
4. `succeeded` requires external evidence recorded on the row: remote
   operation id or target identity, response digest, completion timestamp,
   read-back result. Without these, `succeeded` is a local assertion and is
   not accepted.

D3 is thereby an at-least-once-with-reconciliation contract. Exactly-once is
claimed only for admission (database uniqueness), never for external calls.

### A4. D4 addition: durable, never-reused attempt IDs

An attempt number is allocated DURABLY BEFORE node execution and is never
reused, even when no receipt is produced: an `attempts` table row
(run_id, node_id, attempt_no, allocated_at, state) with
UNIQUE(run_id, node_id, attempt_no) commits before dispatch; attempt_no is
max+1 computed inside the same transaction; allocator rows are exempt from
retention deletion. Staging directories are named by the allocated attempt,
so crash recovery can never confuse abandoned staging with a new attempt.

### A5. Scheduler-authored system settlement receipts (recursion fix)

Node-authored receipts and scheduler-authored receipts are distinct kinds.
When a worker cannot produce or admit its expected receipt, the scheduler
settles the node through its own trusted admission path with a minimal
authoritative receipt:

    { "receipt_kind": "system_settlement",
      "verdict": "BLOCKED",
      "reason_code": "expected_receipt_not_admitted",
      "expected_receipt_kind": "...",
      "attempt_id": "...",
      "classification": "attempted_and_swallowed" }

This breaks the recursion "BLOCKED requires a receipt whose admission just
failed." If even the trusted system path cannot admit, the run store enters
RUN_STORE_FAILURE (below) — a storage-level state, not a node state.

### A6. State model

Normal progression (per node attempt):

    ALLOCATED -> RUNNING -> CANDIDATE_STAGED -> EVIDENCE_DURABLE
      -> VALIDATED -> ADMITTED_AND_SETTLED -> RELEASED

Exceptional states: ABANDONED_STAGING, QUARANTINED_EVIDENCE,
RECONCILIATION_REQUIRED, EVIDENCE_LOST, RUN_STORE_FAILURE.

Core invariant: no accepted terminal state exists before
ADMITTED_AND_SETTLED. RELEASED is derived from the committed projection and
is recoverable; it is not part of settlement authority.

RUN_STORE_FAILURE: entered when the trusted admission path itself fails.
The scheduler stops dispatching; a last-resort marker (sidecar append plus a
fsynced `run-store-failure.marker` file, since SQLite may be unusable) records
the condition; the viewer must render storage failure; no node status beyond
the last committed state is trusted.

### A7. Rollout v2 (shadow-mode invariant before enforcement)

1. Ratify amended D1-D4 (done, this section). 2. Define schemas + state
machine. 3. Implement atomic primitive, attempt allocator, sidecar.
4. Startup reconciliation + system_settlement receipts. 5. Scheduler
invariant in OBSERVATION mode: record every terminal transition lacking
admission; block nothing yet — this measures existing bypasses before
enforcement breaks legacy paths. 6. Classification controls
(a/b/c/WAL-pinned). 7. Route in-process receipts. 8. Route subprocess
staging/promotion/admission. 9. Route TUI manifests. 10. Flip invariant to
ENFORCEMENT. 11. Delete legacy writers + CI architectural guard.
12. Load, clean-wheel, and acceptance campaigns.

### A8. Answers to the ten clarifying questions

1. Orphan re-admission is conditional on a matching durable intent AND
   current-attempt validation (A1); bare valid orphans always quarantine.
2. Yes: reaped-child, then promotion to a parent-owned immutable path, then
   hash/validate/admit (A2). Never hash a child-owned path.
3. Attempt IDs: durable pre-dispatch allocator rows, UNIQUE, monotonic,
   retention-exempt (A4).
4. Namespace `(effect_type, effect_scope, effect_key)`; transactional
   conditional-update lease with owner/lease/state_version (A3.1-2).
5. Current effect inventory and per-type reconciliation: A3.3. Only
   filesystem publication and git worktree mutation exist in core;
   both have deterministic reconciliation; everything else is adapter-level
   and manual-reconciliation-only until declared.
6. Missing/invalid worker evidence settles via `system_settlement` (A5).
7. Trusted-path failure => RUN_STORE_FAILURE, run-wide, marker-file backed
   (A6).
8. Startup order: reconciliation runs before scheduler dispatch/resume; the
   run row is set to a `reconciling` state first, which the (read-only,
   separate-connection) viewer renders as reconciling — the viewer is never
   blocked, it is informed.
9. Legacy receipts: migration admits `receipts/<node_id>.json` as
   attempt 1 with `legacy: true` on the admission row; the allocator for a
   migrated node starts at max(1, existing)+1, so a legacy file can never be
   read as both attempt 1 and a new attempt.
10. The sidecar is attempt-scoped (every record carries run/node/attempt/
    kind), length-prefixed and CRC-framed per record, O_APPEND single-writer;
    a torn final append fails its CRC and is ignored as trailing garbage —
    detected, logged, never fatal, and never able to corrupt earlier records.
- 2026-07-28 branch protection enabled (ticket #214): required checks 'uv run pytest -q' + 'canonical-browser-proofs', force pushes and deletions disabled. Verified by API read-back.
