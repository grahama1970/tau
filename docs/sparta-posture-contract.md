# Sparta Posture Contract Export

`tau sparta-posture-export` converts local Tau receipts into a JSON contract Sparta Explorer can render.

Example:

```bash
uv run tau sparta-posture-export \
  --run-dir /tmp/tau-airgap-itar-basic \
  --out /tmp/tau-airgap-itar-basic/sparta-posture-contract.json
```

The contract includes:

- readiness status;
- top blockers;
- evidence freshness;
- receipt links;
- required human actions;
- chat boundary rules.

The synthetic ITAR demo is expected to export `NOT_SIGNOFF_READY` with `human_export_control_review_required`.

## Non-Claims

The posture contract does not prove ITAR compliance, human approval, operational readiness, or that chat may author a final signoff verdict.

