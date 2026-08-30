# Tau Refinement Proposals

Tau refinement proposals are preview-first change records for continual-harness
improvements. They let an observation propose a persistent Memory document or a
supplemental prompt update without silently mutating Tau's immutable goal, base
prompt, policy, routes, evidence requirements, executable skills, or Memory.

V1 supports two apply adapters:

- governed Memory documents through the configured HTTP Memory `/upsert` and
  `/list` endpoints;
- local, session, or project supplemental prompt resources stored as versioned
  JSON files.

Executable skills, worker profiles, DAG templates, routes, provider profiles,
base prompts, evidence requirements, and immutable goals may be represented as
proposals, but v1 refuses to apply them.

## Lifecycle

The local ledger is `tau.refinement_ledger.v1`. Each transition is journaled by
proposal id, proposal hash, idempotency key, state, and timestamp. The lifecycle
is closed:

```text
OBSERVED
-> PROPOSED
-> DIFF_RENDERED
-> VALIDATED
-> APPROVED | REJECTED | EXPIRED
-> APPLIED
-> VERIFIED | VERIFICATION_FAILED
-> ACCEPTED | ROLLED_BACK | ROLLBACK_BLOCKED
```

Preview and validation read the target before and after rendering the diff and
must report no mutation. Apply requires a `tau.refinement_decision.v1` that
matches the exact proposal hash, idempotency key, target reference hash, before
hash, after hash, goal hash, policy version, data-boundary version, redaction
version, and approval class.

## CLI

```bash
tau refinement-preview \
  --proposal proposal.json \
  --ledger-dir .tau/refinements \
  --diff proposal.diff.json \
  --receipt preview-receipt.json

tau refinement-apply \
  --proposal proposal.json \
  --decision decision.json \
  --ledger-dir .tau/refinements \
  --receipt apply-receipt.json

tau refinement-verify \
  --proposal proposal.json \
  --ledger-dir .tau/refinements \
  --receipt verify-receipt.json

tau refinement-rollback \
  --proposal proposal.json \
  --ledger-dir .tau/refinements \
  --receipt rollback-receipt.json

tau refinement-view --ledger-dir .tau/refinements
```

Issue #319 proof:

```bash
uv run tau refinement-conformance \
  --out .tmp/issue319-refinement-proof-final.json \
  --work-dir .tmp/issue319-refinement-proof-final \
  --memory-url http://127.0.0.1:8601
```

The proof receipt records preview no-mutation, approved Memory and prompt
apply/readback, idempotent replay, fail-closed tampering, Memory conflict
blocking, verification-failure rollback, Memory outage degradation, immutable
target refusal, malicious recalled instruction refusal, and read-only viewer
rendering.
