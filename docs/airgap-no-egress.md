# Airgap No-Egress Receipt

`tau airgap-no-egress` records a bounded no-egress probe bundle for synthetic airgap demos.

Example:

```bash
uv run tau airgap-no-egress \
  --out /tmp/tau-airgap-no-egress.json \
  --allow-local-endpoint 127.0.0.1:4001 \
  --allow-local-endpoint 127.0.0.1:8601
```

The command probes an external DNS target and an external HTTP target. If either probe succeeds, Tau returns `BLOCKED` with `unexpected_egress_detected`.

For fixture-only demos:

```bash
uv run tau airgap-no-egress \
  --out /tmp/tau-airgap-no-egress-demo.json \
  --assume-no-egress-demo
```

Fixture mode is explicit and records `live:false`; it is not airgap proof.

## Non-Claims

The receipt does not prove formal airgap certification, SCIF readiness, ATO readiness, absence of all covert channels, or future network behavior.

