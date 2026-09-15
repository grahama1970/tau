# project-memory-dream (packaged workflow)

Nightly project-memory consolidation as a canonical Tau DAG. Resolves
grahama1970/tau#320 on top of #318 (`tau.project_dream_receipt.v1`) and
agent-skills#1239 (bounded OpenCode synthesis + fail-closed candidate
validation).

The workflow compiles to `tau.generic_dag_spec.v1` and executes on the
canonical scheduler (`tau_coding.generic_dag.run_generic_dag`, which drives the
`dag_runtime` plan compiler, durable run store, and replay machinery). It is
not a bespoke shell pipeline: every node is a bounded, hash-bound work order
with declared inputs, outputs, timeout, retries, and receipt.

## Run

```bash
# one stable command; schedulers call this (no cron registration in Tau)
uv run python examples/project-memory-dream/workflow.py run \
    --config <run-config.json> --run-dir <new-or-existing-run-dir>
# re-invoking the same command resumes the recorded run from durable receipts
```

A runnable example config is built by
`examples/project-memory-dream/fixture_workspace.py` (`default_workspace` +
`write_config`); `run-config.example.json` documents the full surface.

## Topology (per project work item, after shared preflight)

```text
preflight
  ├── discover-and-normalize-transcripts.<pid>
  │      └── archive-and-compact-sessions.<pid> ─────────────┐
  └── discover-project-deltas.<pid>                          │
          ├── project-state.<pid> ───────────────────────────┤
          └── ingest-code-incremental.<pid> ─────────────────┤
                                                             ▼
                                       join.<pid>   (explicit join before packet)
                                                             ▼
                                       build-evidence-packet.<pid>
                                                             ▼
                                       opencode-synthesize.<pid>
                                                             ▼
                                       validate-candidate.<pid>
                                                             ▼
                                       stage-candidate.<pid>
                                                             ▼
                                       promotion-policy.<pid>
                                          ├── SHADOW_STAGED terminal (default)
                                          ├── human approval -> CAS promote -> head readback
                                          └── BLOCKED_APPROVAL / BLOCKED_CAS terminals
                                                             ▼
                                       aggregate (run summary + final
                                       tau.project_dream_receipt.v1)
```

Dispatch eligibility is decided per project by `discover-project-deltas`:
changed head/worktree, new mapped sessions, or a forced reconciliation
request. Unchanged projects get a typed `NO_CHANGE` row with
`skip_reason: unchanged_project`; their model nodes never dispatch.

## Configuration surface

`promotion_mode` (shadow default), `model_id` (resolved at preflight from
config; missing model id fails closed — no legacy DeepSeek alias anywhere),
`synthesis_backend` (`fixture` or `opencode`), `max_projects_per_run`,
`max_parallel_projects`, `max_input_chars_per_project`, `max_topic_mutations`,
`max_project_mutation_ratio`, `timeouts`, `retries`, `cost_budget_usd`,
`force_reconciliation`, and the data-boundary/policy-profile paths.

## Data boundary and model containment

- The model node receives only the evidence-packet directory and one output
  path (declared in `tau.opencode_command_spec.v1` as capabilities).
- Preflight rejects any command spec using undeclared or permanently forbidden
  capabilities (`memory.direct`, `arangodb.write`, `qdrant.write`,
  `generic_upsert`, ...) before any model dispatch; the run fails closed.
- All worker sessions/output are marked `excluded_from_learning`.
- Memory effects in production go through the agent-skills `memory` wrapper
  (configured stage/promote backend), never direct HTTP/AQL. The fixture uses
  a local deterministic head store with CAS semantics.

## Retry and recovery

- Read-only discovery/state/ingest nodes retry under bounded typed policy
  (`retries.read_only_max_attempts`); synthesis retries get fresh attempt ids
  from the durable run store.
- Staging is idempotent by candidate digest; resume after stage reuses durable
  node receipts byte-identically (no duplicate candidates, turns, or head
  transitions).
- Promotion is CAS-gated: the approval binds candidate digest and base head
  generation; a changed head yields a `BLOCKED_CAS` terminal row, never an
  overwrite.
- A failure in one project produces that project's typed terminal row and
  never suppresses sibling projects' terminals.

## Proof matrix (deterministic, fixture-backed)

`scripts/agentic-eval-tau-project-memory-dream.py` (driven by
`evals/tau_project_memory_dream_agentic_eval.json`) proves, over local
deterministic fake source/model adapters:

1. two eligible projects execute with independent terminal rows;
2. unchanged project skipped with a typed reason;
3. transcript and delta branches join before packet construction;
4. malformed transcript bundle blocks only the affected project;
5. project-state failure and ingest failure remain distinct;
6. undeclared model capability rejected before dispatch;
7. shadow staging leaves the active head unchanged;
8. restart after stage duplicates nothing (byte-identical replay);
9. one project failure does not suppress other terminals;
10. every terminal receipt validates under `tau.project_dream_receipt.v1`,
    read back from retained artifacts.

Model semantic quality is not a pass criterion.

## OpenCode shadow canary (non-mocked)

```bash
# config: synthesis_backend=opencode, model_id=<provider/model from `opencode models`>
uv run python examples/project-memory-dream/workflow.py run \
    --config <canary-config.json> --run-dir <run-dir>
```

The canary dispatches one bounded `opencode run --model <id> --dir <packet-dir>`
per eligible project, retains the raw model output with `provider_live: true`
in `raw-model-output.json`, and still stages under shadow only. The scheduler
node receipts stay plain subprocess receipts; provider-call truth lives in the
hash-bound raw-output artifact.

## Proof boundary

Proves: packaged-wheel execution on the canonical scheduler, topology, typed
isolation, shadow default, replay/restart idempotency, fail-closed gates, and
#318-valid terminal receipts read back from disk.

Does not prove: semantic truth of synthesized knowledge, provider/model
quality, production Graph Memory persistence beyond the fixture head store, or
human identity verification.
