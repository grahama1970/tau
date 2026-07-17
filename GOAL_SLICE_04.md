# Tau Canonical Workflow Slice 04

**Status:** Complete
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
- Live browser proof: `/tmp/tau-approved-release-browser-proof.json` reports
  `PASS` with 13/13 checks, GET-only traffic, one navigation, desktop/mobile
  non-overlap, the visible `REVISE -> PASS` retry, approval wait, resume, and
  final result.
- Desktop screenshot: `/tmp/tau-approved-release-desktop.png`.
- Mobile screenshot: `/tmp/tau-approved-release-mobile.png`.
- Installed-wheel proof: `/tmp/tau-approved-release-wheel-proof.json` reports
  `PASS`, `mocked: false`, `live: true`, and `provider_live: false`.

This slice proves the exercised local workflow, exact approval packet,
continuation, publication, and rollback contract. It does not prove provider or
model quality, deployment readiness, or the crash-safe targeted repair required
by canonical workflow 05.
