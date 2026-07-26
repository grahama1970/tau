# Tau Canonical Workflow Slice 05

**Status:** Active
**Owner:** Human

## Immutable Goal

A human can qualify a repository through durable sequential and concurrent
work, interrupt and resume the run, repair only an affected branch, preserve
unaffected accepted work, and publish one idempotent result only after exact
human approval.

## Locked Outcomes

- One `durable-repository-qualification` workflow with seven nodes.
- Three qualification branches execute concurrently after repository capture.
- A blocked test branch resumes only after an exact goal/request-bound repair packet.
- Accepted capture, documentation, and package work is reused unchanged.
- Publication is an exact approval continuation with an atomic idempotency ledger.
- The shared GET-only React Flow viewer shows interruption, repair, approval, and resume.

## Completion Evidence

- Retained canonical proof bundle:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/manifest.json`
  reports `PASS`, `mocked: false`, `live: true`, `provider_live: false`,
  and hash-binds the audit receipt, supplied proof JSON, and screenshots.
- Live browser receipt:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/supplied-proofs/slice05_browser.json`,
  `15/15` checks, GET-only traffic, desktop/mobile geometry, recovery ordering,
  targeted repair, approval wait, and one publication effect.
- Screenshots:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/screenshots/slice05_browser-desktop.png`
  and
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/screenshots/slice05_browser-mobile.png`.
- Installed-wheel receipt:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/supplied-proofs/slice05_wheel.json`,
  with all five workflow IDs, reused accepted branches, and
  `publication_effect_count: 1`.

The crash proof uses Tau's existing diagnostic boundary immediately after a
result is durably staged. A hard process loss before staging remains fail-closed
as effect-uncertain and is not claimed as automatically rerunnable.
