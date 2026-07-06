# Zero-Trust Policy/Data-Boundary Preflight

Zero-trust policy/data-boundary preflight is a gate, not compliance
certification. It blocks missing or incompatible classification metadata before
DAG dispatch. It does not prove ITAR compliance, sandbox isolation, signed
provenance, or legal sufficiency.

This slice adds three schemas:

- `tau.policy_profile.v1`
- `tau.data_boundary.v1`
- `tau.zero_trust_preflight_receipt.v1`

The research pre-query gate adds:

- `tau.research_query_safety_receipt.v1`

## Policy Profile

A policy profile states default-deny run policy for a high-stakes DAG lane. The
initial fixture is:

```bash
experiments/goal-locked-subagents/fixtures/zero-trust-policy.json
```

The default fixture denies network by default, denies cloud LLM providers,
denies external research, requires approval for Memory writes, denies public
GitHub mutation, and requires a data boundary.

## Data Boundary

A data boundary declares the classification and external-access limits for the
work. The initial ITAR-shaped fixture is:

```bash
experiments/goal-locked-subagents/fixtures/itar-data-boundary.json
```

The classification vocabulary is:

```text
public
internal
CUI
ITAR
EAR
classified-not-allowed
```

`classified-not-allowed` always blocks. Missing classification blocks when the
active policy requires a data boundary.

## DAG Gate

Legacy DAGs without `policy_profile` keep their existing behavior. A DAG opts
into the zero-trust gate by adding `policy_profile` and, when the policy
requires it, `data_boundary`.

```json
{
  "policy_profile": "zero-trust-policy.json",
  "data_boundary": {
    "schema": "tau.data_boundary.v1",
    "classification": "public",
    "export_controlled": false,
    "itar": false,
    "technical_data": false,
    "foreign_person_access": "allowed",
    "external_provider_allowed": false,
    "external_research_allowed": false,
    "public_repo_allowed": false
  }
}
```

If the preflight blocks, Tau writes:

```text
zero-trust-preflight-receipt.json
dag-receipt.json
```

The DAG receipt includes a `tau.dag_error.v1` course-correction payload with
`failure_code` such as `missing_data_boundary`, `missing_classification`, or
`external_provider_denied`.

## CLI

Use `zero-trust-doctor` to inspect a policy/boundary pair without dispatching a
DAG:

```bash
uv run tau zero-trust-doctor \
  --policy-profile experiments/goal-locked-subagents/fixtures/zero-trust-policy.json \
  --data-boundary experiments/goal-locked-subagents/fixtures/itar-data-boundary.json
```

The receipt proves only deterministic preflight inspection. It does not prove
runtime sandbox enforcement, signed receipts, human identity verification,
provider/model semantic safety, or compliance package completeness.

## Research Query Gate

Use `research-query-gate` before sending any controlled-boundary context to
Brave, WebGPT, Dogpile, ArXiv, Perplexity, or another external research lane:

```bash
uv run tau research-query-gate \
  --query "Find public NIST publications on secure research workflow review" \
  --method brave-search \
  --policy-profile policy-profile.json \
  --data-boundary data-boundary.json \
  --authorization research-query-authorization.json \
  --receipt research-query-safety-receipt.json
```

For controlled work, the gate blocks when the data boundary disallows external
research, policy denies external search, the query includes controlled-data
markers, the query copies text from a declared controlled artifact, or the
authorization packet is missing, expired, method/boundary-mismatched, or not
bound to the exact sanitized query hash through `sanitized_query_sha256` or
`query_sha256`.

The gate is deliberately pre-query only. It writes
`tau.research_query_safety_receipt.v1` and does not call external research
services, write Memory, mutate GitHub, or prove legal/export compliance.

For DAG visualization, Tau should reuse the source-backed React Flow pattern
already proven in Scillm's Transport room (`#scillm/dag-harness`) rather than
inventing a detached dashboard. Any Tau DAG viewer must show DAG contracts,
receipts, gates, fanout/join edges, and blocked course-correction payloads from
real artifacts; visible statuses must be receipt-backed or explicitly marked
`missing`/`intended`, and UI acceptance requires a fresh browser screenshot.
