# Zero-Trust Basic Example

This example shows the first usable Tau zero-trust path: a DAG opts into a
policy profile and data boundary, then Tau runs the policy/data-boundary
preflight before any dispatch.

Run:

```bash
./run.sh
```

The script writes `out/zero-trust-preflight-receipt.json` and prints a compact
summary. The expected receipt is `expected-receipt.json`.

This example is intentionally boring. It proves only that Tau can inspect the
example policy and boundary and emit a passing zero-trust preflight receipt. It
does not prove DAG dispatch, sandbox enforcement, ITAR compliance,
export-control legal sufficiency, signed provenance, human identity
verification, provider/model semantic safety, or compliance package
completeness.
