# Local Provider Readiness Receipt

`tau local-provider-readiness` records whether Tau can reach a configured local model/provider endpoint for air-gapped or local-only demos.

Example:

```bash
uv run tau local-provider-readiness \
  --provider-url http://127.0.0.1:4001 \
  --model local-kimi-k2.6 \
  --airgap-mode \
  --out /tmp/tau-local-provider-readiness.json
```

The command probes:

- `GET /health`
- `GET /v1/models`

The receipt fails closed when neither endpoint responds. For offline fixture demos only, `--allow-unavailable-demo` may be used; that records `live:false` and `provider_live:false`.

## Non-Claims

The receipt does not prove model approval for ITAR data, model semantic correctness, absence of all network egress, SCIF readiness, ATO readiness, or future provider availability.

