# Issue 120 Proof: Tau-Owned PDF Lab Second-Pass Review Route

Ticket: <https://github.com/grahama1970/tau/issues/120>

## Change

- Added `tau pdf-lab-second-pass-review`.
- Added `pdf_lab.tau_second_pass_review_receipt.v1` terminal receipts.
- Bound PDF Lab page artifacts by SHA-256 before transport.
- Reused Tau's existing `scillm-chat-review` route for `/v1/chat/completions`.
- Added `review_validation.json` with exact candidate ID accounting.

## Deterministic Checks

```text
uv run python -m py_compile src/tau_coding/pdf_lab_second_pass_review.py src/tau_coding/cli.py tests/test_scillm_chat_review.py
exit 0

uv run ruff check src/tau_coding/pdf_lab_second_pass_review.py src/tau_coding/cli.py tests/test_scillm_chat_review.py
All checks passed!

uv run pytest tests/test_scillm_chat_review.py::test_scillm_chat_review_cli_apply_writes_receipt tests/test_scillm_chat_review.py::test_pdf_lab_second_pass_review_cli_apply_writes_tau_owned_receipt -q
2 passed in 1.77s
```

## Live Page28 Receipt

Source branch: `grahama1970/pdf_oxide@codex/pdf-lab-next-page-20260721`

Input summary:

```text
schema: pdf_lab.second_pass.review_request.v1
model: vlm-free2
candidate_count: 18
prompt_chars: 39238
original failure: scillm_review_error.json error_type=ReadTimeout
```

Live command:

```text
uv run tau pdf-lab-second-pass-review --contract docs/proofs/tickets/issue-120-pdf-lab-second-pass-20260726/page28_tau_second_pass_contract.json --out /tmp/tau-issue-120-20260726T143501Z/live/tau_second_pass_review_receipt.json --artifact-root /tmp/tau-issue-120-20260726T143501Z/page28-artifacts --scillm-base-url http://localhost:4001 --caller-skill pdf-lab --apply --auth-token <redacted-dev-proxy-key> --request-timeout-s 900 --timeout-diagnosis-mode live_canary --timeout-diagnosis-timeout-s 30
exit 1
```

The exit code is expected for the non-clean terminal receipt. The live transport
was invoked by Tau and failed closed on route exhaustion rather than timing out
inside pdf_oxide.

Receipt summary:

```json
{
  "ok": false,
  "status": "BLOCKED",
  "live": true,
  "provider_live": false,
  "model_transport_invoked_by_tau": true,
  "terminal_result": "blocked_substrate",
  "blocked_reason": "scillm_chat_review_route_exhausted",
  "http_status": 400,
  "candidate_count": 18,
  "seen_count": 0,
  "tau_work_order_sha256": "sha256:84f6747016fb0c7973859b01a97f83c9be456855327e36f3f5f1f6a5338d9919"
}
```

Committed artifacts:

- `docs/proofs/tickets/issue-120-pdf-lab-second-pass-20260726/page28_tau_second_pass_contract.json`
- `docs/proofs/tickets/issue-120-pdf-lab-second-pass-20260726/page28_tau_second_pass_live_receipt.json`
- `docs/proofs/tickets/issue-120-pdf-lab-second-pass-20260726/page28_scillm_chat_review_receipt.json`
- `docs/proofs/tickets/issue-120-pdf-lab-second-pass-20260726/page28_scillm_chat_review_error.json`
- `docs/proofs/tickets/issue-120-pdf-lab-second-pass-20260726/page28_review_validation.json`

## Evidence Scope

mocked: no for the live page28 receipt.

live: yes for the page28 Tau/SciLLM call; yes for `localhost:4001` auth/liveness
precheck; no for the focused pytest fixture server.

This proves Tau now owns the PDF Lab model transport boundary and emits an
explicit Tau-owned terminal blocked receipt when the provider route is
exhausted. It does not prove the model's semantic review quality or authorize a
pdf_oxide patch.
