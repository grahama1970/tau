# Tau Canonical Workflow Slice 05

**Status:** Complete
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

- Focused backend regression: `40 passed in 47.39s` in
  `/tmp/tau-slice05-focused.log`.
- Live browser receipt: `/tmp/tau-durable-qualification-browser-proof.json`,
  `15/15` checks, GET-only traffic, desktop/mobile geometry, recovery ordering,
  targeted repair, approval wait, and one publication effect.
- Screenshots: `/tmp/tau-durable-qualification-desktop.png` and
  `/tmp/tau-durable-qualification-mobile.png`.
- Installed-wheel receipt: `/tmp/tau-durable-qualification-wheel-proof.json`,
  with all five workflow IDs, reused accepted branches, and
  `publication_effect_count: 1`.

The crash proof uses Tau's existing diagnostic boundary immediately after a
result is durably staged. A hard process loss before staging remains fail-closed
as effect-uncertain and is not claimed as automatically rerunnable.
