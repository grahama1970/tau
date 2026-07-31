# WebGPT Review Request: Tau Synchronized State And Next Iterations

## Objective
Review the current Tau and agent-skills state after synchronization and advise the next smallest high-leverage implementation steps. Focus on Tau as a memory-first, zero-trust, receipt-backed adaptive DAG harness.

## Current deterministic repo state

### Tau repo
```text
HEAD: fa7673ce011650bfa95d913d8d6dbc9e79f48d23
grahama1970/main: fa7673ce011650bfa95d913d8d6dbc9e79f48d23
ahead/behind: 0/0
tracked dirty count: 0
```

Former dirty tracked edits were restored out of the active tree and preserved as a local archive named `tau-dirty-tracked-20260706T173204Z`.

Archived filenames and hashes:

```text
b3993f508ecd19210581ae38b5afe3170f930460066301800ef7573e7f769428  dirty-tracked.patch
bcab8f36db4ea633ceee353c46fcd6a77081e3fd5abb0b66767b35531e5492c1  files/src/tau_coding/battle_live_handoff.py
f32ca2bf7c61c3f665cebe968d26ffc43d5e31fd9ee31f9eec54b66ad2586d51  files/tests/test_battle_live_handoff.py
308c23ed35943ee7ec5afa0a598cd00ac164ddaa3b6f06cc78b275a0ddee2602  files/tests/test_persona_dream_dream_packet_agent.py
```

### Agent-skills repo
```text
HEAD: 95383840bd9b263005c9ea369d8cd0b460669d73
origin/main: 089e912798f90e25b5e686bf88d7997fc9fc593a
ahead/behind: 0/39
tracked dirty count: 101
```

The active agent-skills checkout remains dirty and behind; a prior relevant merge was pushed to origin/main as `089e91279`.

## Recently pushed or verified Tau work
- Tau main includes zero-trust gates, research query gate, ITAR actor/access boundary, compliance package validation, expanded red-team, Docker sandbox policy/runtime slices, provider metadata propagation repair, DAG viewer link/export contract, and real-world sanity checks.
- Tau active checkout is synchronized and tracked-clean.
- Agent-skills main includes Tau DAG skill/docs and related skill updates, but the active checkout should be handled separately because it is dirty and behind.

## Proof already observed
- Tau clean merge proof previously: ruff import checks and targeted pytest reported 11 passed.
- Agent-skills clean merge proof previously: Tau wrapper doctor passed, Ask targeted pytest reported 102 passed, Watch UI typecheck passed.
- Current Tau sync proof: `HEAD == grahama1970/main == fa7673ce011650bfa95d913d8d6dbc9e79f48d23`; ahead/behind `0/0`; tracked dirty count `0`.

## Requested review questions
1. Given the current Tau state, is the next implementation slice still live Tau DAG viewer loading via `http://localhost:3002/#tau/dag?run=<run-dir-or-artifact-id>`, or should the `tau run` convenience command come first?
2. What is the smallest implementation contract for live DAG viewer loading that avoids dashboard theater and proves real artifact-backed rendering?
3. What exact local deterministic tests and browser/CDP proof should gate that slice?
4. After that slice, what are the next 2-3 iterative Tau updates in order: `tau run`, examples, threat model, Herdr-visible provider example, or WebGPT/research evidence review?
5. What should not be attempted yet because it would expand scope or require side-effect approval?

## Non-claims and remaining risk
- No claim of full roadmap completion.
- No live GitHub apply proof unless explicit mutation approval and receipts exist.
- No provider/model semantic quality proof.
- No claim that active agent-skills checkout is clean.
- Browser DAG UI currently has static fixture proof; live-run loading remains the intended next feature unless you advise otherwise.

Please answer as an implementation review: prioritized findings first, then an ordered next-step plan with acceptance gates.
