# External Review Backlog

Live GitHub milestones, labels, and issues are not created in PR 1. This file
documents the desired backlog for later human-approved GitHub mutation.

## Milestone

`External Colleague Review v0`

## Labels

- `external-review`
- `airgap`
- `embry-os`
- `sparta-explorer`
- `itar`
- `cui`
- `compliance`
- `zero-trust`
- `receipt`
- `demo`
- `docs`
- `security`
- `provider-local`
- `scillm`
- `memory`
- `human-approval`
- `non-claim`

## Issues

### 1. Prepare GitHub for external colleague read-only review

Labels: `external-review`, `security`, `docs`

Acceptance criteria:

- `main` branch protection documented;
- `external-review/airgap-sparta-v0` branch exists;
- `tau-embry-sparta-review-v0.1` tag exists after review acceptance;
- `docs/non-claims.md` exists;
- `docs/demo-data-policy.md` exists;
- sensitive-material scan summary exists;
- no known real controlled/customer/company data is introduced in the review
  path.

### 2. Reposition README

Labels: `docs`, `embry-os`, `sparta-explorer`, `external-review`

Acceptance criteria:

- README first screen says Tau is the agentic harness for Embry-OS and Sparta
  Explorer;
- README explains Embry-OS role;
- README explains Sparta Explorer role;
- README explains Tau control-plane role;
- README links non-claims;
- README links the synthetic demo path.

### 3. Add `itar-airgap` Tau init profile

Labels: `airgap`, `itar`, `compliance`, `zero-trust`

Acceptance criteria:

- `tau init --profile itar-airgap` works;
- emits `tau.policy_profile.v1`;
- emits `tau.data_boundary.v1`;
- denies cloud LLM providers, external search, and public GitHub mutation;
- allows local scillm provider only;
- requires human approval for Memory writes and signoff claims;
- includes synthetic example README.

### 4. Add local scillm/Kimi provider readiness receipt

Labels: `provider-local`, `scillm`, `airgap`, `receipt`

Acceptance criteria:

- `tau.local_provider_readiness_receipt.v1` exists;
- records provider URL, model id, optional model weight hash, optional
  tokenizer hash, optional inference engine, and local/airgap mode;
- fails closed if provider is unavailable;
- explicitly does not claim model approval or ITAR suitability.

### 5. Add no-egress receipt

Labels: `airgap`, `security`, `receipt`

Acceptance criteria:

- `tau.airgap_no_egress_receipt.v1` exists;
- records network policy, DNS probe, outbound HTTP probe, allowed local
  endpoints, and unexpected egress findings;
- fails closed on unexpected egress;
- explicitly does not claim SCIF or ATO certification.

### 6. Add synthetic ITAR contract receipt

Labels: `itar`, `compliance`, `receipt`, `human-approval`

Acceptance criteria:

- `tau.itar_contract_receipt.v1` exists;
- accepts synthetic contract clause input and hashes the source artifact;
- identifies controlled-technical-data candidate passages;
- emits `allow`, `deny`, `approval_required`, or `insufficient_evidence`;
- routes final legal/export decision to a human;
- includes tests, examples, and non-claims.

### 7. Export Sparta Explorer posture contract

Labels: `sparta-explorer`, `receipt`, `demo`

Acceptance criteria:

- `sparta-posture-contract.json` export command exists;
- includes readiness, top blockers, evidence freshness, receipt links,
  model/provider state, airgap state, and required human action;
- chat cannot author final verdict.

### 8. Build one-command synthetic demo

Labels: `demo`, `airgap`, `itar`, `sparta-explorer`

Acceptance criteria:

- `uv run tau demo airgap-itar-basic --out /tmp/tau-demo` works;
- uses synthetic data only;
- produces policy, data-boundary, provider, airgap, ITAR, evidence, and posture
  receipts;
- run-status summarizes result;
- proof-index builds;
- demo intentionally fails closed with `approval_required`.

### 9. Create external reviewer packet

Labels: `external-review`, `docs`

Acceptance criteria:

- `docs/review-guide.md` exists;
- includes setup instructions, demo script, architecture summary, non-claims,
  known gaps, and reviewer questions.
