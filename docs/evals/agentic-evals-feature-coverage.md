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
| `evaluator-script` | 12 |
| `ledger-path` | 10 |
| `skill-capability` | 10 |
| `viewer-surface` | 20 |
| `workflow-definition` | 5 |

Total source-visible features: 253.

Coverage records: 253 total, with 52 `CLAIMED` records and 201 expiring `OUT_OF_SCOPE` records. The out-of-scope records are not proof that those features work; they only make the current non-claim explicit and auditable.

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
| `evals/tau_agentic_eval_evidence_index_agentic_eval.json` | `tau-agentic-eval-evidence-index` | `tau.agentic_eval_evidence_index.revision_bound_retained_reports` | `local/agentic-evals/tau-agentic-eval-evidence-index-agentic-evals-report.json` |
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
| Mutated retained agentic-eval report | `report_digest_mismatch` | `local/agentic-evals/tau-agentic-eval-evidence-index-mutated-report-proof.json` |
| Substituted retained agentic-eval report from another SHA | `report_repo_sha_mismatch` | `local/agentic-evals/tau-agentic-eval-evidence-index-substituted-report-proof.json` |
| Deleted referenced retained artifact | `artifact_missing` | `local/agentic-evals/tau-agentic-eval-evidence-index-deleted-artifact-proof.json` |
| Dirty verifier checkout | `dirty_tree_mismatch` | `local/agentic-evals/tau-agentic-eval-evidence-index-dirty-tree-proof.json` |

## Evidence Index

`local/agentic-evals/tau-agentic-eval-evidence-index.json` is the verifier-owned evidence index for the retained Tau agentic-eval milestone. It records `tau.agentic_eval_evidence_verifier.v1`, the `agentic_evals.report.v2` runner schema, the command used to build the index, checkout SHA/ref/dirty-tree declaration, 15 retained report digests, and 21 retained artifact digests referenced by report trial `artifact_hashes`.

The verifier PASS receipt is `local/agentic-evals/tau-agentic-eval-evidence-index-pass-receipt.json`. It re-reads the index, reports, and referenced artifacts without regenerating reports, and records `retained_reports_live_readback.mocked=false`, `retained_reports_live_readback.all_unmocked=true`, `retained_reports_live_readback.live=true`, and `retained_reports_live_readback.ready=true`.

## Proof Boundary

The guard proves that the current source-derived inventory is reconciled to claim or non-claim records and that retained feature eval reports remain READY/live/non-mocked. It also proves that the retained evidence-index verifier fails closed on the listed negative controls. It does not prove the 201 `OUT_OF_SCOPE` features work, provider/model quality, or human acceptance of the full `GOAL.md` outcome.
