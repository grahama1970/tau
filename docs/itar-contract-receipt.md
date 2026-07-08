# Synthetic ITAR Contract Receipt

`tau itar-contract-review` deterministically inspects a synthetic contract clause for configured controlled-data indicators.

Example:

```bash
uv run tau itar-contract-review \
  --clause examples/airgap-itar-basic/synthetic-contract-clause.txt \
  --policy-profile examples/airgap-itar-basic/policy-profile.json \
  --data-boundary examples/airgap-itar-basic/data-boundary.json \
  --out /tmp/tau-itar-contract-receipt.json
```

If the clause contains configured indicators such as design drawings, test procedures, manufacturing process notes, external release, foreign-person access, or export control language under an ITAR-shaped boundary, Tau returns `BLOCKED` with `decision: approval_required`.

The expected synthetic demo blocker is `human_export_control_review_required`.

## Non-Claims

The receipt does not prove ITAR compliance, legal sufficiency, correct USML classification, authorization to process real controlled technical data, human approval, or model semantic correctness.

