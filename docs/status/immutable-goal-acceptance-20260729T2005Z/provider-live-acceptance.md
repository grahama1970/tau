# Provider-Live Packaged Workflow Acceptance

Issue #304 adds a one-command provider-live acceptance harness for the five
packaged workflow rungs. The command builds a fresh wheel unless `--wheel` is
provided, installs that wheel in a clean virtual environment, runs the public
installed `tau` entrypoint for all five workflow ids, probes the configured
provider boundary through `tau local-provider-readiness`, and writes a typed
receipt.

Run from a clean checkout:

```bash
uv run tau workflows acceptance-proof \
  --repo . \
  --output /tmp/tau-rungs-provider-live-receipt.json \
  --provider-url http://127.0.0.1:4001 \
  --model scillm-provider-boundary
```

Then verify the generated receipt against the current checkout:

```bash
uv run tau workflows verify-acceptance-proof \
  /tmp/tau-rungs-provider-live-receipt.json \
  --repo .
```

The verifier fails closed unless the receipt is `mocked=false`, `live=true`,
`provider_live=true`, bound to the current git commit and wheel sha256, lists
exactly `repository-readiness`, `tau-operator-reference`,
`repository-evidence-map`, `approved-release-bundle`, and
`durable-repository-qualification`, and records terminal evidence for every
rung.
