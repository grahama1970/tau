# Tau Canonical Workflow Slice 04

**Status:** Active
**Owner:** Human

## Immutable Goal

A human can stage a release bundle through sequential and concurrent work,
observe a deterministic revision retry, stop at an exact approval boundary,
and publish one rollback-protected side effect only after explicit approval.

## Locked Outcomes

- One `approved-release-bundle` workflow, topology `MIXED_RETRY_APPROVAL`.
- Three parallel branches after preparation.
- Release notes receive `REVISE` then `PASS` through Tau's artifact transaction.
- Policy failure is terminal and prevents assembly/publication.
- Missing approval produces `APPROVAL_REQUIRED` and no side effect.
- Resume preserves accepted work and performs the approved continuation once.
- Failed post-write verification removes the published target and records rollback.

## Completion Evidence

- Focused and regression backend proof: `119 passed`.
- Frontend proof: typecheck, production build, and `23 passed`.
- Retained canonical proof bundle:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/manifest.json`
  reports `PASS`, `mocked: false`, `live: true`, `provider_live: false`,
  and hash-binds the audit receipt, supplied proof JSON, and screenshots.
- Live browser proof:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/supplied-proofs/slice04_browser.json`
  reports `PASS` with 14/14 checks, GET-only traffic, desktop/mobile
  non-overlap, the visible `REVISE -> PASS` retry, approval wait, resume, and
  final result.
- Desktop screenshot:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/screenshots/slice04_browser-desktop.png`.
- Mobile screenshot:
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/screenshots/slice04_browser-mobile.png`.
- Installed-wheel coverage is retained in
  `experiments/goal-locked-subagents/proofs/canonical-workflows/issue-134-20260726T134804Z/audit/immutable-goal-audit.json`,
  which built and installed a wheel from source ref
  `669f833de65189e9c14c02f77c1df04da1ddf84e` and exercised the
  `approved-release-bundle` workflow through approval and repeated resume.

This slice proves the exercised local workflow, exact approval packet,
continuation, publication, and rollback contract. It does not prove provider or
model quality, deployment readiness, or the crash-safe targeted repair required
by canonical workflow 05.
