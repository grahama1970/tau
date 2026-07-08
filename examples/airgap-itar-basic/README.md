# Airgap ITAR Basic Example

This directory contains synthetic fixtures for the Tau external-review airgap demo.

One-command demo:

```bash
uv run tau demo airgap-itar-basic --out /tmp/tau-airgap-itar-basic
uv run tau run-status /tmp/tau-airgap-itar-basic
uv run tau proof-index build /tmp/tau-airgap-itar-basic \
  --out /tmp/tau-airgap-itar-basic/proof-index.jsonl
```

Profile-only setup:

```bash
uv run tau init --profile itar-airgap --out /tmp/tau-itar-airgap
uv run tau zero-trust-doctor \
  --policy-profile /tmp/tau-itar-airgap/.tau/policy-profile.json \
  --data-boundary /tmp/tau-itar-airgap/.tau/data-boundary.json
```

The copied example policy and boundary in this directory are review fixtures. They are not legal or compliance determinations.

Expected posture: harness `PASS`, Sparta posture `NOT_SIGNOFF_READY`, top blocker `human_export_control_review_required`.
