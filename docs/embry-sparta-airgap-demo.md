# Embry-Sparta Airgap Demo

`tau demo embry-sparta-airgap` attaches the synthetic airgap ITAR demo to local Embry-OS service readiness checks.

Example:

```bash
uv run tau demo embry-sparta-airgap \
  --memory-url http://127.0.0.1:8601 \
  --scillm-url http://127.0.0.1:4001 \
  --model local-kimi-k2.6 \
  --out /tmp/tau-embry-sparta
```

Tau first writes `tau.embry_os_service_readiness_receipt.v1`. If Memory or SciLLM is unreachable, the command fails closed with `local_service_readiness_failed`.

When both local services are reachable, Tau runs the synthetic airgap ITAR demo and emits the Sparta posture contract. The expected posture remains `NOT_SIGNOFF_READY` with `human_export_control_review_required`.

## Non-Claims

This demo does not prove ITAR compliance, service semantic correctness, production readiness, ATO readiness, or SCIF readiness.

