# Airgap ITAR Basic Demo

Run:

```bash
uv run tau demo airgap-itar-basic --out /tmp/tau-airgap-itar-basic
uv run tau run-status /tmp/tau-airgap-itar-basic
uv run tau proof-index build /tmp/tau-airgap-itar-basic \
  --out /tmp/tau-airgap-itar-basic/proof-index.jsonl
```

Expected result:

- demo harness receipt: `PASS`;
- run status: `PASS`;
- proof index: `PASS`;
- Sparta posture: `NOT_SIGNOFF_READY`;
- top blocker: `human_export_control_review_required`.

The demo uses synthetic data only. The default run does not require a live provider and uses explicit fixture mode for no-egress; use `--live-provider` or `--live-airgap-probe` to exercise those live boundaries.

## Non-Claims

This demo does not prove ITAR compliance, model approval, airgap certification, human approval, or operational readiness.

