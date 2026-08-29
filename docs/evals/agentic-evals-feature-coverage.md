# Tau feature agentic-evals coverage

Generated from `local/agentic-evals/tau-feature-coverage-proof.json`.

## Contract

- Each committed Tau feature eval manifest under `evals/*agentic_eval.json` declares at least one `capability_claim`.
- Each critical claim has supporting cases for every required evidence class.
- Each manifest has a retained `$agentic-evals` report that is `READY`, `live=true`, and `mocked=false`.
- `evals/tau_feature_coverage_agentic_eval.json` fails if a feature manifest loses its claim coverage.

## Current manifest-to-claim map

| Manifest | Skill | Capability claims | Retained report |
| --- | --- | --- | --- |
| `evals/codebase_ingest_agentic_eval.json` | `tau-codebase-ingest` | tau.codebase_ingest.scan_projection_receipts | `local/agentic-evals/codebase-ingest-agentic-evals-report.json` |
| `evals/python_host_bridge_agentic_eval.json` | `tau-python-host-bridge` | tau.python_host_bridge.capability_boundaries | `local/agentic-evals/python-host-bridge-agentic-evals-report.json` |
| `evals/python_workspace_kernel_agentic_eval.json` | `tau-python-workspace-kernel` | tau.python_workspace_kernel.lifecycle_receipts | `local/agentic-evals/python-workspace-kernel-agentic-evals-report.json` |
| `evals/tau_core_agentic_eval.json` | `tau-core` | tau.core.local_dag_with_tamper_evident_ledger | `local/agentic-evals/tau-core-agentic-evals-report.json` |
| `evals/tau_discord_failure_path_agentic_eval.json` | `tau-discord-failure-path` | tau.discord.failure_path_visible | `local/agentic-evals/tau-discord-failure-path-agentic-evals-report.json` |
| `evals/tau_discord_idempotency_agentic_eval.json` | `tau-discord-idempotency` | tau.discord.notification_idempotency | `local/agentic-evals/tau-discord-idempotency-agentic-evals-report.json` |
| `evals/tau_discord_unblock_agentic_eval.json` | `tau-discord-unblock` | tau.discord.typed_human_unblock_receipts | `local/agentic-evals/tau-discord-unblock-agentic-evals-report.json` |
| `evals/tau_ledger_trace_agentic_eval.json` | `tau-ledger-trace` | tau.ledger_trace.default | `local/agentic-evals/tau-ledger-trace-agentic-evals-report.json` |
| `evals/tau_pipeline_self_repair_agentic_eval.json` | `tau-pipeline-self-repair` | tau.repair.required_node_failure_overlay | `local/agentic-evals/tau-pipeline-self-repair-agentic-evals-report.json` |
| `evals/tau_repair_overlay_browser_agentic_eval.json` | `tau-repair-overlay-browser` | tau.viewer.repair_overlay_discord_visible | `local/agentic-evals/tau-repair-overlay-browser-agentic-evals-report.json` |
| `evals/tau_same_node_rerun_agentic_eval.json` | `tau-same-node-rerun` | tau.repair.same_semantic_node_rerun | `local/agentic-evals/tau-same-node-rerun-agentic-evals-report.json` |
| `evals/tau_terminal_dag_watch_agentic_eval.json` | `tau-terminal-dag-watch` | tau.terminal_dag_progress_watch | `local/agentic-evals/tau-terminal-dag-watch-agentic-evals-report.json` |
| `evals/tau_timeline_viewer_agentic_eval.json` | `tau-timeline-viewer` | tau.viewer.timeline_proof_clips | `local/agentic-evals/tau-timeline-viewer-agentic-evals-report.json` |

## Proof boundary

This coverage guard proves the committed Tau eval posture. It does not prove the feature inventory is exhaustive against `GOAL.md`; new Tau features still need a new or amended `capability_claim`, evidence-class cases, and a retained report.
