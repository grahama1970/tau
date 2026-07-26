# Tau issue #151 proof notes

Ticket: <https://github.com/grahama1970/tau/issues/151>

Implemented Tau-side contract:

- `write_provider_routing_receipt()` writes `tau.provider_routing_receipt.v1`.
- The receipt calls the existing Graph Memory `/recall` boundary with
  `collections: ["provider_routes"]` and `recommendation: "provider_route"`.
- A returned `provider_route.candidates` orders candidates by Memory evidence.
- Tau filters candidates by credential and quota eligibility.
- A retryable provider result such as HTTP 429 produces a typed
  `provider_failover` event and attempts the next eligible provider.
- If Memory has no route, Tau records `DEGRADED` instead of a false PASS.
- If no provider is eligible, Tau records `BLOCKED` with `no_eligible_provider`.

Focused checks:

```text
uv run pytest -q tests/test_provider_config.py
..........................................
42 passed in 2.56s

uv run ruff check src/tau_coding/provider_config.py tests/test_provider_config.py
All checks passed!

uv run python -m py_compile src/tau_coding/provider_config.py
exit 0
```

Live local Memory check:

```text
uv run python - <<'PY'
from pathlib import Path
from tau_coding.provider_config import write_provider_routing_receipt
write_provider_routing_receipt(
    query='Tau issue 151 provider routing with cross-provider failover',
    receipt_path=Path('docs/proofs/tickets/issue-151-provider-routing-20260726/live-provider-routing.json'),
    memory_url='http://127.0.0.1:8601',
    candidates=[
        {'provider':'anthropic','model':'claude-sonnet-4-6','credential_ok':False},
        {'provider':'openai','model':'gpt-5.5','credential_ok':True},
        {'provider':'chutes','model':'Qwen/Qwen3-32B-TEE','credential_ok':True},
    ],
    timeout_seconds=10,
)
PY
```

Live receipt result:

- `schema`: `tau.provider_routing_receipt.v1`
- `request_payload.collections`: `["provider_routes"]`
- `found`: `false`
- `confidence`: `0.0`
- `status`: `DEGRADED`
- `alert_codes`: `["provider_route_missing"]`
- `provider_live`: `false`
- `selected`: `openai:gpt-5.5`

Interpretation:

- Fixture-backed tests exercise Memory outcome ordering, 429 failover, missing
  route degradation, and no-eligible-provider fail-closed behavior.
- The live local Memory service currently has no `provider_routes` recall
  surface for this query, so Tau emitted DEGRADED rather than claiming a
  learned route.
- This proves Tau's routing receipt and failover decision logic under stubbed
  provider invocation. It does not prove production SciLLM dispatch adoption or
  live provider/model semantic quality.
