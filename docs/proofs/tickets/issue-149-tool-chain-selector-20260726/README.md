# Tau issue #149 proof notes

Ticket: <https://github.com/grahama1970/tau/issues/149>

Implemented Tau-side contract:

- `tau tool-chain-recall` writes `tau.tool_chain_selection_receipt.v1`.
- The receipt calls the existing Graph Memory `/recall` boundary with
  `collections: ["tool_chains"]` and `recommendation: "tool_chain"`.
- A returned `tool_chain.tools` plus connected `traversal_path` is the only PASS
  recommendation source.
- Missing or edge-disconnected tool-chain data is reported as `DEGRADED`.
- The receipt records `advisory_only: true` and `mutating_tools_invoked: []`.

Focused checks:

```text
uv run pytest -q tests/test_memory_acquisition.py
...........
11 passed in 5.28s

uv run ruff check src/tau_coding/memory_acquisition.py src/tau_coding/cli.py tests/test_memory_acquisition.py
All checks passed!

uv run python -m py_compile src/tau_coding/memory_acquisition.py src/tau_coding/cli.py
exit 0
```

Live local Memory check:

```text
uv run tau tool-chain-recall --query "Tau issue 149 proven tool-call chain recommendation for patch and focused test" --memory-url http://127.0.0.1:8601 --out docs/proofs/tickets/issue-149-tool-chain-selector-20260726/live-tool-chain-recall.json --timeout-seconds 10
```

Live receipt result:

- `schema`: `tau.tool_chain_selection_receipt.v1`
- `call.status_code`: `200`
- `request_payload.collections`: `["tool_chains"]`
- `found`: `false`
- `confidence`: `0.0`
- `status`: `DEGRADED`
- `alert_codes`: `["tool_chain_missing"]`
- `selected_tools`: `[]`
- `mutating_tools_invoked`: `[]`

Interpretation:

- Fixture-backed tests exercise the PASS path where Memory returns a connected
  ordered tool chain, the missing-edge degradation path, and the unreachable
  Memory degradation path.
- The live local Memory service currently has no `tool_chains` recall surface
  for this query, so Tau emitted a DEGRADED receipt rather than a false PASS.
- This proves Tau's consumer/receipt/fail-closed behavior. It does not prove
  Memory corpus completeness, Memory traversal/ranking implementation, or
  semantic optimality of any recommended tool sequence.
