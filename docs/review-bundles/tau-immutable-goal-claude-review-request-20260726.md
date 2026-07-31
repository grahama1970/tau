# Tau Immutable Goal Review Request For Claude

You are reviewing Tau against its immutable product goal, not against recent
commits, unit tests, or agent claims.

## Repository Targets

- Tau repository: <https://github.com/grahama1970/tau>
- Target branch: `main`
- Expected Tau `main` commit: `33d5cebe2b43dc94b6ae4eb8a4df5da56e9f76dd`
- Related agent-skills repository: <https://github.com/grahama1970/agent-skills>
- Expected agent-skills `main` commit containing updated Tau skill:
  `1dab4ecfcf62b8d7c2e41b562e68d353bfa5a55e`

## Primary Review Question

Has Tau met the immutable goal in `GOAL.md`?

## Immutable Goal Summary

Tau must let a human launch and supervise five canonical real, goal-locked
agent DAGs, from simple to durable and failure-recovering, while showing
truthful progress, accepted evidence, blockers, and required human decisions in
one easy-to-use interface.

Humans own the goal. Agents propose and execute bounded work. Tau decides what
counts as admissible progress.

## Do Not Treat As Sufficient Proof

- Git commits alone.
- Unit tests alone.
- Static fixtures alone.
- Receipt existence alone.
- Model or reviewer `PASS` prose.
- Post-run snapshots that do not prove live dynamic progress.
- Pi TUI parity work by itself.
- A clean `dag-receipt.verdict` when terminal reviewer or node receipts
  disagree.

## Tau-Specific Architecture To Preserve

- Memory-first routing.
- SciLLM as Tau's internal provider/model boundary.
- `$ask` as a convenience wrapper that compiles human requests into
  `tau.dag_contract.v1`.
- Tau-owned DAG execution, receipts, and progress state.
- Browser handlers routed through Surf/browser-oracle command specs.
- API/model handlers routed through Tau-owned SciLLM adapters.
- Herdr/monitor visibility.
- Fail-closed evidence gates.
- Typed receipts and immutable goal hashes.
- Human-owned approval and release boundaries.

## Recent Context

`README.md`, `PROJECT_KNOWLEDGE.md`, and `skills/tau/SKILL.md` were updated on
2026-07-26 to clarify that Pi TUI parity is only a usability slice and does not
satisfy `GOAL.md` by itself.

Known current blocker observed during documentation proof:

```text
skills/tau/run.sh doctor
-> TuiConfigError: Unknown TUI settings field: anthropic_extra_usage_warning
```

That blocker occurred while the Tau skill wrapper queried the Tau workflow
catalog. Treat it as a real runtime/config issue unless the repository now
proves otherwise.

## Review Tasks

1. Read `GOAL.md` completely.
2. Inspect current `README.md`, `PROJECT_KNOWLEDGE.md`, and
   `skills/tau/SKILL.md` claims.
3. Determine whether the five canonical DAGs are discoverable by a new
   evaluator without repository archaeology.
4. Determine whether all five DAGs can be launched through documented commands
   or controls.
5. Determine whether each DAG produces a recognizable useful result, not just a
   receipt.
6. Determine whether every DAG preserves goal version/hash through nodes,
   retries, restart, and handoff.
7. Determine whether the same React Flow viewer renders fresh authoritative
   runtime state for all five DAGs.
8. Determine whether live dynamic viewer progress is proven, including running,
   concurrent, blocked or approval-waiting, resumed, and completed states.
9. Determine whether crash-safe resume, no duplicate accepted effects, targeted
   repair, human approval, and rollback are proven for the advanced or
   human-gated DAGs.
10. Identify any false-green risk where `PASS`, `verified`, or `complete` is
    claimed without deterministic local proof.
11. Identify the smallest next executable slice that would most directly
    advance the immutable goal.

## Expected Return Format

Return:

- Verdict: `MET`, `NOT_MET`, or `NEEDS_ATTENTION`.
- Estimated completion percentage against `GOAL.md`.
- Top five missing proof artifacts or product gaps.
- Confirmed blockers or false-green risks.
- Whether the current Tau TUI/Pi parity work materially helps tomorrow's
  usability.
- Whether any Tau-specific custom features appear at risk of being overwritten
  by Pi parity work.
- The next concrete command or artifact Tau should produce.
- A concise rationale grounded in files, commands, and observed behavior.

## Evidence Discipline

For every positive claim, state:

- `mocked: yes|no`
- `live: yes|no`
- what was actually exercised
- what remains unverified

Reviewer judgment is advisory only. Do not close the immutable goal from this
review alone. Final closure requires deterministic local artifacts, command
results, inspected UI/browser proof where applicable, and human acceptance.
