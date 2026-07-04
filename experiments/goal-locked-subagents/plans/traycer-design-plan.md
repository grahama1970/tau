# Traycer Design Plan

**Status:** Design plan, not implementation
**Implementation scope:** Slice 1 only: `tau traycer validate`
**Out of scope for first implementation:** `chain-validate`, `dag-validate`, `tail`, `herdr-watch`

## Problem Statement

Tau subagents can produce locally valid work while still drifting from the
human-approved goal, target, route, or evidence contract. Today, Tau already
validates final handoff packets and bounded command-loop continuity after a
subagent exits, but it does not yet provide an independent trace-level monitor
that can evaluate a subagent's observable declarations, evidence claims, and
final handoff as a separate receipt-backed artifact.

Traycer adds that missing primitive. Slice 1 is an offline JSONL invariant
monitor:

```bash
uv run tau traycer validate \
  --trace creator.trace.jsonl \
  --handoff creator-final-handoff.json \
  --active-goal-hash sha256:active-goal \
  --start-handoff start-handoff.json \
  --receipt monitor-receipt.json
```

The command produces only `tau.monitor_receipt.v1`. It does not rewrite
`tau.agent_handoff.v1`, mutate route state, inspect hidden chain-of-thought, or
use Herdr.

## Non-Goals

- Do not inspect or score private hidden chain-of-thought.
- Do not add a top-level `monitor` field to `tau.agent_handoff.v1` in Slice 1.
- Do not rewrite or generate handoffs from `tau traycer validate`.
- Do not implement Herdr live watch, pane input, steering, or process control.
- Do not implement DAG validation in Slice 1.
- Do not add the feature to real-world sanity before deterministic fixtures are stable.
- Do not treat UI/dashboard state as authority; receipts and trace artifacts are authority.

## Existing Tau Alignment

Tau already has the right architectural shape:

1. Subagents work and recommend next steps.
2. Tau validates handoff JSON, goal hash, route continuity, target continuity, and receipts.
3. Only the human may change the immutable goal.
4. Receipt artifacts carry audit detail; routing packets stay compact.

Traycer should preserve that shape:

1. Per-agent Traycer checks one subagent's trace and final handoff.
2. Later chain/DAG Traycer checks multi-agent workflow drift.
3. The orchestrator, not Traycer, owns route mutation and course correction.

## Slice 1 CLI Contract

Command:

```bash
uv run tau traycer validate \
  --trace <trace.jsonl> \
  --handoff <final-handoff.json> \
  --active-goal-hash <sha256:...> \
  [--required-evidence <required-evidence.json> | --start-handoff <start-handoff.json>] \
  [--advisory-final-handoff-evidence] \
  --receipt <monitor-receipt.json>
```

Strict mode requires either `--required-evidence` or `--start-handoff`.

Advisory mode is explicit:

```bash
uv run tau traycer validate \
  --trace creator.trace.jsonl \
  --handoff creator-final-handoff.json \
  --active-goal-hash sha256:active-goal \
  --advisory-final-handoff-evidence \
  --receipt monitor-receipt.json
```

When advisory mode derives evidence requirements from the observed final handoff,
the receipt must record:

```json
{
  "evidence_authority": "final_handoff_fallback",
  "authority_warning": "required evidence was derived from the observed agent's final handoff"
}
```

## Required Evidence Authority

Precedence:

1. `--required-evidence <json>`
2. `--start-handoff <json>` via `required_evidence` or goal-helper evidence policy
3. final handoff `required_evidence`, only with `--advisory-final-handoff-evidence`

The final handoff must not be the sole authority in strict mode because the
observed subagent could weaken the evidence requirement in the same packet being
judged.

## Slice 1 Schemas

Schema directory:

```text
experiments/goal-locked-subagents/schemas/traycer/
```

Initial schema files:

```text
tau.subagent_trace.v1.schema.json
tau.evidence_claim.v1.schema.json
tau.monitor_alert.v1.schema.json
tau.monitor_receipt.v1.schema.json
tau.required_evidence.v1.schema.json
```

### tau.subagent_trace.v1

Shared envelope for observable trace rows:

```json
{
  "schema": "tau.subagent_trace.v1",
  "run_id": "20260704T000000Z-example",
  "trace_id": "trace-0001",
  "sequence": 1,
  "ts": "2026-07-04T16:12:03Z",
  "agent": {
    "name": "creator",
    "executor": "local",
    "pane_id": null
  },
  "goal": {
    "goal_id": "tau-traycer",
    "goal_version": 1,
    "goal_hash": "sha256:active-goal"
  },
  "github": {
    "repo": "grahama1970/tau",
    "target": "issue:44"
  },
  "phase": "intent",
  "event": {
    "kind": "intent_declared",
    "summary": "Implement offline monitor fixtures"
  },
  "links": {
    "parent_trace_id": null,
    "artifact_ids": []
  }
}
```

### tau.evidence_claim.v1

Evidence claims can either be independent JSON files or trace row event payloads.
The monitor should normalize both forms into receipt evidence summaries.

```json
{
  "schema": "tau.evidence_claim.v1",
  "run_id": "20260704T000000Z-example",
  "claim_id": "ev-0001",
  "sequence": 31,
  "ts": "2026-07-04T16:18:11Z",
  "agent": {
    "name": "creator"
  },
  "goal": {
    "goal_hash": "sha256:active-goal"
  },
  "claim": {
    "type": "test_result",
    "statement": "Focused tests passed",
    "artifact": "/tmp/proof/pytest.stdout.txt",
    "verifier": {
      "kind": "command",
      "command": "uv run pytest tests/test_traycer_validate.py -q",
      "exit_code": 0
    },
    "supports_required_evidence": [
      "focused_tests_pass"
    ],
    "confidence": "deterministic"
  }
}
```

### tau.monitor_alert.v1

Alerts must cite trace ids and artifacts. No orphan prose.

```json
{
  "schema": "tau.monitor_alert.v1",
  "run_id": "20260704T000000Z-example",
  "alert_id": "alert-0001",
  "alert_sha256": "sha256:...",
  "ts": "2026-07-04T16:19:02Z",
  "observed_agent": "creator",
  "severity": "BLOCK",
  "violation": {
    "code": "goal_hash_mismatch",
    "message": "Draft handoff changed goal.goal_hash",
    "evidence_trace_ids": [
      "trace-0011",
      "trace-0012"
    ],
    "deterministic": true
  },
  "recommended_action": {
    "type": "reroute",
    "next_agent": "goal-guardian",
    "reason": "Goal boundary must be reconciled before work continues"
  }
}
```

### tau.monitor_receipt.v1

The monitor receipt is the source of truth for Slice 1.

```json
{
  "schema": "tau.monitor_receipt.v1",
  "ok": true,
  "status": "PASS",
  "run_id": "20260704T000000Z-example",
  "observed_agent": "creator",
  "active_goal_hash": "sha256:active-goal",
  "evidence_authority": "start_handoff",
  "trace": {
    "path": "experiments/.../creator.trace.jsonl",
    "sha256": "sha256:...",
    "event_count": 42,
    "last_sequence": 42
  },
  "final_handoff": {
    "path": "experiments/.../creator-final-handoff.json",
    "sha256": "sha256:..."
  },
  "alerts": [],
  "summary": {
    "max_severity": "PASS",
    "warning_count": 0,
    "review_alert_count": 0,
    "reroute_alert_count": 0,
    "blocking_alert_count": 0
  },
  "verdict": {
    "status": "PASS",
    "next_allowed": true,
    "recommended_next_agent": "reviewer"
  },
  "does_not_prove": [
    "hidden chain-of-thought correctness",
    "semantic code quality beyond declared evidence",
    "human acceptance of goal changes"
  ]
}
```

### tau.required_evidence.v1

Required evidence policy is independent of the observed final handoff.

```json
{
  "schema": "tau.required_evidence.v1",
  "goal_hash": "sha256:active-goal",
  "required": [
    {
      "id": "focused_tests_pass",
      "description": "Focused deterministic tests passed",
      "acceptable_claim_types": [
        "test_result",
        "command_exit"
      ],
      "required_confidence": "deterministic"
    }
  ]
}
```

## Verdict Semantics

| Max severity | ok | status | next_allowed | Meaning |
| --- | --- | --- | --- | --- |
| none | true | PASS | true | Normal continuation allowed |
| WARN | true | PASS | true | Continue; reviewer should inspect monitor receipt |
| REVIEW | false | REVIEW | false | Explicit review required before continuation |
| REROUTE | false | REROUTE | false | Current route is not trusted |
| BLOCK | false | BLOCKED | false | Hard invariant violation |

Default behavior:

- WARN never makes `ok:false`.
- REVIEW/REROUTE/BLOCK make `ok:false`.
- Optional future flag: `--fail-on-warn`.

## Slice 1 Invariants

`tau traycer validate` must check at least:

1. Trace file exists and every JSONL row parses.
2. Trace rows have monotonic integer `sequence` values.
3. Trace row `goal.goal_hash` matches `--active-goal-hash`.
4. Final handoff `goal.goal_hash` matches `--active-goal-hash`.
5. Trace and final handoff target remain stable unless a future policy explicitly allows mutation.
6. Final handoff schema is `tau.agent_handoff.v1`.
7. Final handoff `previous_subagent` matches the observed agent when the observed agent is known.
8. Final handoff `next_agent.name` is routable under the existing handoff validator.
9. Required evidence is satisfied by `tau.evidence_claim.v1` rows or artifacts, not prose.
10. Malformed JSONL, goal mismatch, target mutation, invalid handoff JSON, and non-routable next agent fail closed.

## Handoff Integration

Slice 1 does not change the handoff schema. It uses existing fields only.

`context.artifacts[]` reference:

```json
{
  "kind": "monitor_receipt",
  "schema": "tau.monitor_receipt.v1",
  "path": "experiments/.../monitor-receipt.json",
  "sha256": "sha256:..."
}
```

`result.evidence[]` reference:

```json
{
  "kind": "monitor_verdict",
  "receipt": "experiments/.../monitor-receipt.json",
  "receipt_sha256": "sha256:...",
  "status": "PASS",
  "max_severity": "WARN",
  "blocking_alert_count": 0
}
```

Unresolved REVIEW, REROUTE, or BLOCK alerts may be cited by alert id/hash in
`result.evidence[]`. Resolved WARN alerts stay in the monitor receipt only.

## Tests

Test file:

```text
tests/test_traycer_validate.py
```

Required cases:

1. Valid trace plus valid final handoff returns `ok:true`, `status:"PASS"`.
2. WARN alert does not make `ok:false`.
3. REVIEW alert makes `ok:false`, `status:"REVIEW"`, `next_allowed:false`.
4. REROUTE alert makes `ok:false`, `status:"REROUTE"`, `next_allowed:false`.
5. BLOCK alert makes `ok:false`, `status:"BLOCKED"`, `next_allowed:false`.
6. Trace goal hash mismatch produces BLOCK.
7. Final handoff goal hash mismatch produces BLOCK.
8. Target changed between trace and final handoff produces BLOCK.
9. Missing required evidence produces REVIEW or BLOCK based on policy.
10. Malformed JSONL row produces BLOCK.
11. Final handoff evidence fallback is rejected in strict mode.
12. Final handoff evidence fallback is allowed only with `--advisory-final-handoff-evidence`.
13. Monitor receipt includes trace hash, final handoff hash, event count, last sequence, alert counts, and `does_not_prove`.
14. CLI writes the receipt exactly at `--receipt`.

## Proof Ladder

Slice 1 proof artifact:

```text
experiments/goal-locked-subagents/proofs/traycer-slice-1-offline-<timestamp>/
  valid.trace.jsonl
  invalid-goal-drift.trace.jsonl
  final-handoff.json
  start-handoff.json
  required-evidence.json
  monitor-receipt.json
  manifest.json
```

Focused proof commands:

```bash
uv run ruff check --select I,F src/tau_coding/traycer tests/test_traycer_validate.py
uv run pytest tests/test_traycer_validate.py -q
uv run tau traycer validate \
  --trace experiments/goal-locked-subagents/proofs/traycer-slice-1-offline-<timestamp>/valid.trace.jsonl \
  --handoff experiments/goal-locked-subagents/proofs/traycer-slice-1-offline-<timestamp>/final-handoff.json \
  --start-handoff experiments/goal-locked-subagents/proofs/traycer-slice-1-offline-<timestamp>/start-handoff.json \
  --active-goal-hash sha256:active-goal \
  --receipt experiments/goal-locked-subagents/proofs/traycer-slice-1-offline-<timestamp>/monitor-receipt.json
```

Receipt claims must include:

```json
{
  "mocked": false,
  "live": false,
  "proves": [
    "offline trace and final handoff validation",
    "required evidence authority handling",
    "monitor receipt creation"
  ],
  "does_not_prove": [
    "live Herdr monitoring",
    "DAG drift detection",
    "hidden chain-of-thought correctness",
    "semantic code quality beyond declared evidence"
  ]
}
```

Do not add Slice 1 to the real-world sanity suite until the deterministic
fixtures and receipt schema have stabilized.

## Docs Changes

Add or update:

```text
docs/traycer-monitor.md
docs/run-status.md
docs/real-world-sanity-checks.md
```

Slice 1 docs should describe:

- offline-only scope
- evidence authority precedence
- verdict semantics
- handoff reference pattern
- proof boundaries
- why hidden chain-of-thought is out of scope

## Implementation Package Layout

Use a package:

```text
src/tau_coding/traycer/
  __init__.py
  models.py
  evidence.py
  receipts.py
  validate.py
  cli.py
```

Later files:

```text
src/tau_coding/traycer/chain.py
src/tau_coding/traycer/dag.py
src/tau_coding/traycer/tail.py
src/tau_coding/traycer/herdr_watch.py
```

Wire Slice 1 through the existing CLI positional dispatch as:

```bash
tau traycer validate ...
```

No Typer/app refactor is required for Slice 1.

## Later Roadmap

### Slice 2: chain-validate

Validate an ordered handoff/monitor receipt chain as a degenerate DAG:

```bash
uv run tau traycer chain-validate \
  --handoffs receipts/*/final-handoff.json \
  --monitor-receipts receipts/*/monitor-receipt.json \
  --receipt chain-monitor-receipt.json
```

Checks:

- prior `next_agent.name` equals next `previous_subagent`
- shared goal hash
- shared target
- no hidden unresolved REVIEW/REROUTE/BLOCK alerts
- final handoff references chain monitor receipt

### Slice 3: dag-validate

Validate explicit DAG contract:

```bash
uv run tau traycer dag-validate \
  --dag-contract tau-dag-contract.json \
  --node-receipts receipts/*/monitor-receipt.json \
  --handoffs receipts/*/final-handoff.json \
  --receipt dag-monitor-receipt.json
```

Future schema:

```text
tau.dag_contract.v1
tau.dag_monitor_receipt.v1
```

DAG checks:

- all node receipts share goal hash
- all node receipts share target unless target mutation is explicitly allowed
- every observed node is allowed
- every observed edge is allowed
- every required node ran
- every required join ran after dependencies
- DAG-level required evidence is satisfied by receipts
- no branch claims completion before predecessors complete
- final handoff references DAG monitor receipt

### Slice 4: tail

Tail a live JSONL trace file without Herdr:

```bash
uv run tau traycer tail \
  --trace /tmp/live-subagent.trace.jsonl \
  --active-goal-hash sha256:active-goal
```

### Slice 5: herdr-watch

Use Herdr as transport/control, not authority:

```bash
uv run tau traycer herdr-watch \
  --pane w1:p3 \
  --active-goal-hash sha256:active-goal
```

Herdr can provide pane/process/event transport and semantic state reporting.
Traycer still evaluates Tau JSONL traces and receipts as the authoritative data.

## Commit Scope Guidance

Safe first implementation commit should include only Traycer Slice 1 files:

```text
experiments/goal-locked-subagents/schemas/traycer/*.schema.json
src/tau_coding/traycer/*
tests/test_traycer_validate.py
docs/traycer-monitor.md
experiments/goal-locked-subagents/proofs/traycer-slice-1-offline-*/manifest.json
```

Do not include unrelated dirty files or broad cleanup. Do not include
`PROJECT_KNOWLEDGE.md` if it contains pre-existing unrelated edits.
