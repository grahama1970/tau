# Tau Planner/Orchestrator Visible DAG Proof Plan

Date: 2026-07-01

## Objective

Prove a bounded Tau planner/orchestrator workflow where:

1. `planner` creates a `tau.dag_run_spec.v1`.
2. `orchestrator` consumes that spec.
3. `orchestrator` executes the DAG with required subagents.
4. `planner`, `orchestrator`, `coder`, and `reviewer` are visible in the same Herdr workspace.
5. `coder` and `reviewer` emit canonical node receipts.
6. Tau emits a final `tau.dag_run_receipt.v1`.

Out of scope:

- Tailscale.
- GitHub ticket closure.
- Production repository mutation.
- Unbounded autonomous repair.
- General semantic coding quality beyond the scratch fixture.

## Required Subagents

| Role | Contract | Ownership |
| --- | --- | --- |
| `planner` | `agents/planner/persona.yaml` | Emits DAG spec only. |
| `orchestrator` | `agents/orchestrator/persona.yaml` | Executes DAG spec and emits final receipt. |
| `coder` | `agents/coder/persona.yaml` | Modifies only the scratch target file and emits coder receipt. |
| `reviewer` | `agents/reviewer/persona.yaml` | Reviews scratch target plus coder receipt and emits reviewer receipt. |

All four subagents must comply with `$best-practices-subagent` by declaring:

- `schema: oc_subagent.persona.v1`
- `role`
- `does_not_own`
- `dag_spec`
- `primary_skills`
- `tool_policy`
- `memory_policy`
- `delegated_access_skills`
- `help_policy`
- `turn_contract`
- `status_reporting`
- `retry_policy`
- `output_contract`
- `artifact_contract`
- `proof_tasks`

## Proof Commands

Plan:

```bash
uv run tau provider-dag-plan \
  --label tau-planner-orchestrator-subagents-visible-proof \
  --run-root experiments/goal-locked-subagents/proofs/planner-orchestrator-subagents-visible-proof \
  --max-attempts 2
```

Execute:

```bash
uv run tau provider-dag-orchestrate <dag-spec.json> \
  --receipt-timeout-seconds 300
```

Inspect:

```bash
uv run tau provider-dag-inspect <run-dir>
```

Validate:

```bash
uv run pytest tests/test_provider_pane_poc.py tests/test_visible_dag_poc.py -q
uv run ruff check --select I,F src/tau_coding/cli.py src/tau_coding/provider_pane_poc.py src/tau_coding/provider_dag_poc.py tests/test_provider_pane_poc.py tests/test_visible_dag_poc.py
```

## PASS Criteria

The proof passes only if the final run receipt satisfies all of these:

- `schema == "tau.dag_run_receipt.v1"`
- `mocked == false`
- `live == true`
- `status == "PASS"`
- `dag_spec` exists and has `schema == "tau.dag_run_spec.v1"`
- `provider_readiness_receipt` exists
- `provider_sessions.codex.ready == true`
- `provider_sessions.opencode.ready == true`
- `provider_sessions.*.visible_prompt_is_gate == false`
- `visible_subagents` contains exactly `planner`, `orchestrator`, `coder`, `reviewer`
- all four visible subagents have `visible == true`
- all four visible subagents share the same `workspace_id`
- all four visible subagents have `pane_id` and `terminal_id`
- coder node receipt exists and validates
- reviewer node receipt exists and validates
- reviewer starts only after coder receipt validation in `events.jsonl`
- `proof_scope.does_not_prove` explicitly includes ticket closure, Tailscale, production repo mutation, and unbounded autonomous repair

## Failure Handling

Fail closed with a retained receipt if:

- any required subagent contract is missing or contract-incomplete;
- planner cannot emit a valid `tau.dag_run_spec.v1`;
- structured readiness is missing or failed;
- any required Herdr pane is not visible;
- coder or reviewer receipt is missing or invalid;
- reviewer requests revision after `max_attempts`;
- any excluded action is attempted.

