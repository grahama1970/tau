# Tau Feature Agentic-Evals Coverage

Generated from `local/agentic-evals/tau-feature-coverage-agentic-evals-report.json`.

## Contract

- `scripts/agentic-eval-tau-feature-coverage.py --mode positive` derives `local/agentic-evals/source-inventory.json` from Tau source.
- The inventory includes CLI commands, packaged workflow definitions, selected capability modules, skill capability registrations, DAG viewer surfaces, ledger-producing paths, and evaluator scripts.
- `local/agentic-evals/reconciliation-report.json` reconciles every inventory item against `evals/tau_source_feature_coverage_records.json`.
- A feature passes reconciliation only when it has a `CLAIMED` record pointing at an existing `capability_claim`, or an explicit `BLOCKED`/`OUT_OF_SCOPE` record with `owner`, `reason`, and non-expired `expires`.
- Every non-self feature eval manifest under `evals/*agentic_eval.json` must have at least one mapped source feature and a retained `$agentic-evals` report that is `READY`, `live=true`, and `mocked=false`.
- Duplicate claim ownership for one feature fails unless an explicit merge record exists.

## Current Source Inventory

| Kind | Count |
| --- | ---: |
| `capability-module` | 3 |
| `cli-command` | 193 |
| `evaluator-script` | 11 |
| `ledger-path` | 7 |
| `skill-capability` | 10 |
| `viewer-surface` | 20 |
| `workflow-definition` | 5 |

Total source-visible features: 249.

Coverage records: 249 total, with 47 `CLAIMED` records and 202 expiring `OUT_OF_SCOPE` records. The out-of-scope records are not proof that those features work; they only make the current non-claim explicit and auditable.

## Current Manifest-To-Claim Map

| Manifest | Skill | Capability claims | Retained report |
| --- | --- | --- | --- |
| `evals/codebase_ingest_agentic_eval.json` | `tau-codebase-ingest` | `tau.codebase_ingest.scan_projection_receipts` | `local/agentic-evals/codebase-ingest-agentic-evals-report.json` |
| `evals/python_host_bridge_agentic_eval.json` | `tau-python-host-bridge` | `tau.python_host_bridge.capability_boundaries` | `local/agentic-evals/python-host-bridge-agentic-evals-report.json` |
| `evals/python_workspace_kernel_agentic_eval.json` | `tau-python-workspace-kernel` | `tau.python_workspace_kernel.lifecycle_receipts` | `local/agentic-evals/python-workspace-kernel-agentic-evals-report.json` |
| `evals/tau_core_agentic_eval.json` | `tau-core` | `tau.core.local_dag_with_tamper_evident_ledger` | `local/agentic-evals/tau-core-agentic-evals-report.json` |
| `evals/tau_dag_ladder_rung1_agentic_eval.json` | `tau-dag-ladder-rung1` | `tau.dag_ladder.rung1_clean_checkout` | `local/agentic-evals/tau-dag-ladder-rung1-agentic-evals-report.json` |
| `evals/tau_discord_failure_path_agentic_eval.json` | `tau-discord-failure-path` | `tau.discord.failure_path_visible` | `local/agentic-evals/tau-discord-failure-path-agentic-evals-report.json` |
| `evals/tau_discord_idempotency_agentic_eval.json` | `tau-discord-idempotency` | `tau.discord.notification_idempotency` | `local/agentic-evals/tau-discord-idempotency-agentic-evals-report.json` |
| `evals/tau_discord_unblock_agentic_eval.json` | `tau-discord-unblock` | `tau.discord.typed_human_unblock_receipts` | `local/agentic-evals/tau-discord-unblock-agentic-evals-report.json` |
| `evals/tau_ledger_trace_agentic_eval.json` | `tau-ledger-trace` | `tau.ledger_trace.default` | `local/agentic-evals/tau-ledger-trace-agentic-evals-report.json` |
| `evals/tau_pipeline_self_repair_agentic_eval.json` | `tau-pipeline-self-repair` | `tau.repair.required_node_failure_overlay` | `local/agentic-evals/tau-pipeline-self-repair-agentic-evals-report.json` |
| `evals/tau_repair_overlay_browser_agentic_eval.json` | `tau-repair-overlay-browser` | `tau.viewer.repair_overlay_discord_visible` | `local/agentic-evals/tau-repair-overlay-browser-agentic-evals-report.json` |
| `evals/tau_same_node_rerun_agentic_eval.json` | `tau-same-node-rerun` | `tau.repair.same_semantic_node_rerun` | `local/agentic-evals/tau-same-node-rerun-agentic-evals-report.json` |
| `evals/tau_terminal_dag_watch_agentic_eval.json` | `tau-terminal-dag-watch` | `tau.terminal_dag_progress_watch` | `local/agentic-evals/tau-terminal-dag-watch-agentic-evals-report.json` |
| `evals/tau_timeline_viewer_agentic_eval.json` | `tau-timeline-viewer` | `tau.viewer.timeline_proof_clips` | `local/agentic-evals/tau-timeline-viewer-agentic-evals-report.json` |

## Negative Controls

The retained guard fails closed, and the agentic-evals fixture proves these injected cases:

| Case | Expected finding code | Receipt |
| --- | --- | --- |
| Intentionally added unmanifested CLI command | `uncovered_source_feature` | `local/agentic-evals/tau-feature-coverage-unmanifested-cli-proof.json` |
| Orphan eval manifest | `orphan_eval_manifest` | `local/agentic-evals/tau-feature-coverage-orphan-manifest-proof.json` |
| Duplicate feature owner | `duplicate_feature_owner` | `local/agentic-evals/tau-feature-coverage-duplicate-owner-proof.json` |
| Missing retained report | `missing_retained_report` | `local/agentic-evals/tau-feature-coverage-missing-report-proof.json` |
| Stale waiver | `stale_waiver` | `local/agentic-evals/tau-feature-coverage-stale-waiver-proof.json` |
| Missing capability claim | `missing_capability_claims` | `local/agentic-evals/tau-feature-coverage-missing-claim-proof.json` |

## Proof Boundary

The guard proves that the current source-derived inventory is reconciled to claim or non-claim records and that retained feature eval reports remain READY/live/non-mocked. It does not prove the 202 `OUT_OF_SCOPE` features work, provider/model quality, or human acceptance of the full `GOAL.md` outcome.
