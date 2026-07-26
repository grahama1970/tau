# PDF Lab Second-Pass Review Route

Tau owns model transport for PDF Lab second-pass page review. A project such as
`pdf_oxide` supplies a hash-bound `tau.dag_contract.v1` work order and local
page artifacts; Tau verifies the contract, invokes SciLLM when requested, and
writes a terminal `pdf_lab.tau_second_pass_review_receipt.v1` receipt.

## Command

```bash
uv run tau pdf-lab-second-pass-review \
  --contract page_tau_second_pass_contract.json \
  --out tau_second_pass_review_receipt.json \
  --artifact-root /path/to/extracted/artifacts \
  --scillm-base-url http://localhost:4001 \
  --caller-skill pdf-lab \
  --apply \
  --request-timeout-s 900 \
  --timeout-diagnosis-mode live_canary \
  --timeout-diagnosis-timeout-s 30
```

Omit `--apply` for a hash-checking dry run. Dry runs do not invoke provider
transport.

## Accepted Work Order

The contract must use:

- `schema: tau.dag_contract.v1`
- `provider_sensitive: true`
- `requires_provider_route: true`
- `context.schema: pdf_lab.tau_second_pass_context.v1`
- `context.route_boundary.required_owner: tau`
- `context.route_boundary.pdf_oxide_direct_model_transport: forbidden`
- `context.input_artifacts.review_request_json`
- `context.input_artifacts.candidate_presets_json`
- `context.input_artifacts.page_before_json`

Each input artifact descriptor must include a relative or absolute `path` and a
SHA-256 digest. Tau checks these hashes before any live transport.

## Terminal Receipt

Tau writes:

- `tau_work_order_sha256`
- `route_owner: tau`
- `model_transport_invoked_by_tau`
- `provider_live`
- `model_transport_policy`
- `model_response_artifact`, when SciLLM returns a parseable response
- `review_validation_artifact`
- `terminal_result`

Allowed terminal results are `reviewed_clean`, `still_open`,
`blocked_substrate`, and `human_needed`. A provider route exhaustion, timeout,
quota error, missing auth token, invalid response, or hash mismatch is a
Tau-owned blocked receipt, not a pdf_oxide transport retry request.
