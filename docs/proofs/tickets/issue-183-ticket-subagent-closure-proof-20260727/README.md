# Issue 183 Ticket Subagent Closure Proof

This proof records the code-ticket closure evidence gate added for issue #183.

## Commands

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/ticket_closure_evidence.py src/tau_coding/subagent_receipt.py src/tau_coding/cli.py
PYTHONPATH=src uv run pytest -q tests/test_ticket_closure_evidence.py tests/test_subagent_receipt.py
PYTHONPATH=src uv run tau ticket-subagent-closure-proof --allow-live-filesystem --output docs/proofs/tickets/issue-183-ticket-subagent-closure-proof-20260727/ticket-subagent-closure-proof.json
jq '{status,mocked,live,provider_live,rejected_unit_only,accepted_live_e2e,rejected_code_subagent_without_e2e,accepted_code_subagent_live_e2e,accepted_non_code_without_e2e,live_artifact}' docs/proofs/tickets/issue-183-ticket-subagent-closure-proof-20260727/ticket-subagent-closure-proof.json
```

## Artifacts

- `ticket-subagent-closure-proof.json`: live local receipt proving code-ticket unit-only evidence is rejected and live non-mocked E2E evidence is accepted.
- `live-e2e-artifact.json`: non-mocked live filesystem artifact generated with a fresh run id and timestamp, then read back by the proof validator.

## Readback

```json
{
  "status": "PASS",
  "mocked": false,
  "live": true,
  "provider_live": false,
  "rejected_unit_only": true,
  "accepted_live_e2e": true,
  "rejected_code_subagent_without_e2e": true,
  "accepted_code_subagent_live_e2e": true,
  "accepted_non_code_without_e2e": true
}
```

## Boundary

This proves Tau rejects passing code-related subagent closure evidence when it only contains deterministic unit evidence, and accepts a live non-mocked E2E artifact readback. It does not prove provider semantic correctness or retrofit historical receipts.
