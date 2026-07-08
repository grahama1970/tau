# Airgap ITAR Basic Example

This directory contains synthetic fixtures for the Tau external-review airgap demo.

Use:

```bash
uv run tau init --profile itar-airgap --out /tmp/tau-itar-airgap
uv run tau zero-trust-doctor \
  --policy-profile /tmp/tau-itar-airgap/.tau/policy-profile.json \
  --data-boundary /tmp/tau-itar-airgap/.tau/data-boundary.json
```

The copied example policy and boundary in this directory are review fixtures. They are not legal or compliance determinations.

