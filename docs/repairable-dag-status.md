# Repairable DAG status board

This board exists so the repairable-DAG work is visible, reviewable, and bounded by evidence instead of hidden behind commit messages or local JSON files.

## Why this board exists

The repairable-DAG implementation must be collaborative and visual. The default reviewer should be able to answer four questions without reading source first:

1. Which runtime path was exercised?
2. Which proof artifact backs each claim?
3. Which parts are still simulated/local only?
4. What remains before this is a full product-ready loop?

## Current runtime shape

```mermaid
flowchart LR
    A[Human prompt] --> B[$ask tau-dag]
    B --> C[Compiled tau.dag_contract.v1 + command specs]
    C --> D[$tau dag-run]
    D --> E{Required node result}
    E -->|PASS| F[Downstream nodes become eligible]
    E -->|FAIL / BLOCKED| G[$triage-error classification input]
    G --> H[$pipeline-self-repair record-failure]
    H --> I[Blocking repair category + replay ledger]
    I --> J[Viewer repair overlay]
    I --> K[Optional Discord question/status/answer receipts]
    K --> L{Typed answer validates?}
    L -->|No / status only| I
    L -->|Yes| M[Same semantic node rerun]
    M --> E
```

## Proof matrix

| Capability | Committed eval | Local proof report | Current readback | Proof boundary |
| --- | --- | --- | --- | --- |
| Default ledger trace | `evals/tau_ledger_trace_agentic_eval.json` | `local/agentic-evals/tau-ledger-trace-agentic-evals-report.json` | `READY`, `mocked=false`, `live=true`, `trials=2` | Live local Tau CLI and ledger verification; not provider quality or full browser UX. |
| Required-node failure repair overlay | `evals/tau_pipeline_self_repair_agentic_eval.json` | `local/agentic-evals/tau-pipeline-self-repair-agentic-evals-report.json` | `READY`, `mocked=false`, `live=true`, `trials=2` | Live local Tau CLI plus `$pipeline-self-repair` safe-mode record/inspect; no GitHub ticket publication or watchdog dispatch. |
| Discord typed unblock receipts and ops-discord handoff | `evals/tau_discord_unblock_agentic_eval.json` | `local/agentic-evals/tau-discord-unblock-agentic-evals-report.json` | `READY`, `mocked=false`, `live=true`, `trials=2` | Live local Tau CLI plus `$ops-discord notify --dry-run` receipt validation; no real Discord network delivery. |
| Discord live notification delivery | `scripts/agentic-eval-tau-discord-unblock.py --discord-bot --channel-name horus --live-notify` | `local/agentic-evals/tau-discord-live-delivery-proof.json` | `PASS`, `mocked=false`, `live=true`, `transport=discord_bot`, `status=SENT`, `discord_message_id=1542989185848311861` | Live Tau path invoked `$ops-discord notify --discord-bot` against the `horus` Discord channel and preserved the message URL. |
| Browser-visible repair overlay | `evals/tau_repair_overlay_browser_agentic_eval.json` | `local/agentic-evals/tau-repair-overlay-browser-agentic-evals-report.json` | `READY`, `mocked=false`, `live=true`, `trials=2` | Live local Tau viewer rendered in headless Chrome; browser requests were read-only `GET`; the run also posted live Discord bot messages to `horus`. |
| Same semantic node rerun after repair | `evals/tau_same_node_rerun_agentic_eval.json` | `local/agentic-evals/tau-same-node-rerun-agentic-evals-report.json` | `READY`, `mocked=false`, `live=true`, `trials=2`, `coder` attempt count `2` | Live local Tau CLI with safe command-spec fixtures; proves scheduler/ledger semantics, not provider semantic quality or real ticket closure. |
| ops-discord notification idempotency | `evals/tau_discord_idempotency_agentic_eval.json` | `local/agentic-evals/tau-discord-idempotency-agentic-evals-report.json` | `READY`, `mocked=false`, `live=true`, `trials=2`, notification `status=DEDUPED` | Direct Tau repair notification path with `/bin/false` as a no-send sentinel; proves duplicate category detection before external notification execution. |
| ops-discord failure visibility | `evals/tau_discord_failure_path_agentic_eval.json` | `local/agentic-evals/tau-discord-failure-path-agentic-evals-report.json` | `READY`, `mocked=false`, `live=true`, `trials=2`, notification `status=CHANNEL_NOT_FOUND` | Live Discord bot failure path with an intentionally missing channel; proves failure details are preserved, not that delivery succeeded. |

Readback command used for the table:

```bash
python - <<'PY'
import json
for f in [
 'local/agentic-evals/tau-ledger-trace-agentic-evals-report.json',
 'local/agentic-evals/tau-pipeline-self-repair-agentic-evals-report.json',
 'local/agentic-evals/tau-discord-unblock-agentic-evals-report.json',
 'local/agentic-evals/tau-repair-overlay-browser-agentic-evals-report.json',
 'local/agentic-evals/tau-same-node-rerun-agentic-evals-report.json',
 'local/agentic-evals/tau-discord-idempotency-agentic-evals-report.json',
 'local/agentic-evals/tau-discord-failure-path-agentic-evals-report.json',
]:
    p=json.load(open(f))
    print(f"{f}: schema={p.get('schema')} readiness={p.get('readiness')} mocked={p.get('mocked')} live={p.get('live')} trials={p.get('trial_count')} cases={p.get('case_count')}")
PY
```

## What is implemented

- `ProjectDagContract` accepts a top-level `repair_policy` key.
- Required node failures in `src/tau_coding/project_dag.py` can call `$pipeline-self-repair record-failure`.
- Tau writes failed-step and repair projection artifacts under the DAG run directory.
- DAG receipts include `pipeline_self_repair` and `pipeline_self_repair_artifacts`.
- The DAG viewer projection maps repair state into `snapshot.corrections`, node-level `correction`, and `run_summary.repair`.
- The React overview renders a repair summary.
- `tau discord-receipt` creates and validates local typed receipts for questions, status messages, answers, and answer validation.
- `$pipeline-self-repair` projections that enter human-adjudication state through `repair_policy.discord.require_human_adjudication=true` or human/adjudication failure signals call `$ops-discord notify` and preserve the `ops_discord.notification_receipt.v1` path in the repair overlay.
- Tau writes an adjudication routing manifest for human-adjudication repair states and treats unknown adjudication categories as fail-closed.
- Same-receipt-dir reruns of a blocked project DAG are authorized only after `$pipeline-self-repair validate-ledger --require-agentic-eval --json` returns `PASS`; Tau archives the prior SQLite run store and offsets node attempt counts so the repaired semantic node resumes at attempt 2.
- The static viewer bundle renders `run_summary.repair` in the run overview; the browser proof reads `data-qid="dag:overview:repair"` and checks the blocking category, `ops-discord · SENT`, human-question state, Discord message URL, and read-only HTTP methods.

## What is not yet proven

- Real `$ticket` GitHub issue creation/update/reopen binding is not proven in this slice.
- Real `$project-watchdog` dispatch from a repair category is not proven in this slice.
- The same-node rerun proof closes the category with a retained eval fixture and `--no-ticket`; it does not prove a real GitHub issue lifecycle.
- The browser overlay proof uses a live local browser/server and live Discord bot delivery, but it does not prove provider semantic quality or human acceptance of the repair.
- Tau now attaches `tau_triage.code=tau_project_dag_missing_required_evidence` with a concrete `next_command` to missing-evidence repair projections, but the upstream `$pipeline-self-repair` category key still reflects `$triage-error`'s generic `unknown_unclassified_*` catalog output.

## Next visual/collaborative acceptance gates

1. Add a safe `$ticket` boundary proof that binds a stable `category_key` to a GitHub issue without duplicating tickets.
2. Add a `$project-watchdog` dry-run/receipt proof showing it can pick up the repair category.
3. Move Tau-specific failure codes upstream into `$triage-error` so `$pipeline-self-repair` category keys no longer contain `unknown_unclassified_*` for common project-DAG failures.

## Non-claim

This board does not claim Tau has completed the full repairable-DAG product goal. It shows the current implemented slice and the proof boundary that still needs to become visual and collaborative.
