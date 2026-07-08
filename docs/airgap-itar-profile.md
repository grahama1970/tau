# ITAR-Airgap Init Profile

`tau init --profile itar-airgap` creates a synthetic, local-only starter profile for external-review demos.

The profile is intentionally conservative:

- cloud LLM providers are denied;
- external search is denied;
- public GitHub mutation is denied;
- local model use is marked `allow_with_review`;
- Memory writes and signoff claims require human approval;
- the data boundary is marked `ITAR` for synthetic demonstration only.

## Command

```bash
uv run tau init --profile itar-airgap --out /tmp/tau-itar-airgap
uv run tau zero-trust-doctor \
  --policy-profile /tmp/tau-itar-airgap/.tau/policy-profile.json \
  --data-boundary /tmp/tau-itar-airgap/.tau/data-boundary.json
```

## Non-Claims

This profile does not prove ITAR compliance, export-control legal sufficiency, SCIF readiness, ATO readiness, airgap certification, model approval, or authorization to process real controlled technical data.

