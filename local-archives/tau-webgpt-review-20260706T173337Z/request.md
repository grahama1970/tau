# WebGPT Review Request: Tau Synchronized State And Next Iterations

## Objective
Review the current Tau and agent-skills state after synchronization and advise the next smallest high-leverage implementation steps. Focus on Tau as a memory-first, zero-trust, receipt-backed adaptive DAG harness.

## Current deterministic repo state

### Tau repo
```text
HEAD: fa7673ce011650bfa95d913d8d6dbc9e79f48d23
grahama1970/main: fa7673ce011650bfa95d913d8d6dbc9e79f48d23
ahead behind: 0	0
tracked dirty count: 0
archive for former dirty tracked edits: /home/graham/workspace/experiments/tau/local-archives/tau-dirty-tracked-20260706T173204Z/
```

### Agent-skills repo
```text
HEAD: 95383840bd9b263005c9ea369d8cd0b460669d73
origin/main: 089e912798f90e25b5e686bf88d7997fc9fc593a
ahead behind: 0	39
tracked dirty count: 101
Note: active agent-skills checkout remains dirty and behind; prior relevant merge was pushed to origin/main as 089e91279.
```

## Recently pushed/verified Tau work
- Tau main includes zero-trust gates, research query gate, ITAR actor/access boundary, compliance package validation, expanded red-team, Docker sandbox policy/runtime slices, provider metadata propagation repair, DAG viewer link/export contract, and real-world sanity checks.
- Tau active checkout is now synchronized and tracked-clean. Former tracked dirty edits were archived as untracked local artifacts.
- Agent-skills main includes Tau DAG skill/docs and related skill updates, but active checkout is dirty and should be handled separately.

## Proof already observed
- Tau clean merge proof previously: ruff import checks and targeted pytest reported 11 passed.
- Agent-skills clean merge proof previously: Tau wrapper doctor passed, Ask targeted pytest reported 102 passed, Watch UI typecheck passed.
- Current Tau sync proof: HEAD == grahama1970/main == fa7673ce; ahead/behind 0 0; tracked dirty count 0.

## Requested review questions
1. Given the current Tau state, is the next implementation slice still live Tau DAG viewer loading via http://localhost:3002/#tau/dag?run=<run-dir-or-artifact-id>, or should tau run convenience command come first?
2. What is the smallest implementation contract for live DAG viewer loading that avoids dashboard theater and proves real artifact-backed rendering?
3. What exact local deterministic tests and browser/CDP proof should gate that slice?
4. After that slice, what are the next 2-3 iterative Tau updates in order: tau run, examples, threat model, Herdr-visible provider example, or WebGPT/research evidence review?
5. What should not be attempted yet because it would expand scope or require side-effect approval?

## Non-claims / remaining risk
- No claim of full roadmap completion.
- No live GitHub apply proof unless explicit mutation approval and receipts exist.
- No provider/model semantic quality proof.
- No claim that active agent-skills checkout is clean.
- Browser DAG UI currently has static fixture proof; live-run loading remains the intended next feature unless you advise otherwise.

Please answer as an implementation review: prioritized findings first, then an ordered next-step plan with acceptance gates.
