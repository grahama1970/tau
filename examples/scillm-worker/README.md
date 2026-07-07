# SciLLM Worker Validation Example

This example shows Tau validating a SciLLM/OpenCode-serve worker result as an
untrusted external coding-worker artifact.

Default run:

```bash
examples/scillm-worker/run.sh /tmp/tau-scillm-worker-example
```

The default path writes a fixture `tau.executor.scillm_worker.v1` work order and
a fixture `tau.scillm_worker_result.v1`, then validates them with:

```bash
uv run tau scillm-worker-launch \
  --work-order work-order.json \
  --out scillm-worker-launch-receipt.json

uv run tau scillm-worker-launch \
  --work-order work-order.json \
  --out scillm-worker-launch-apply-receipt.json \
  --scillm-base-url http://127.0.0.1:<fixture-port> \
  --apply \
  --auth-token example-token \
  --request-timeout-s 5

uv run tau scillm-worker-validate \
  --work-order work-order.json \
  --result scillm-result.json \
  --out scillm-worker-receipt.json
```

The work order records the correct SciLLM coding-delegate route:

```text
surface: opencode_serve
endpoint: /v1/scillm/opencode/runs
agent: build
skills: memory,debugger,scillm
```

To validate a result produced by a real SciLLM/OpenCode serve worker, set
`SCILLM_WORKER_RESULT` to a JSON file with schema
`tau.scillm_worker_result.v1`:

```bash
SCILLM_WORKER_RESULT=/path/to/scillm-result.json examples/scillm-worker/run.sh /tmp/tau-scillm-worker-live
```

To launch a real local SciLLM/OpenCode serve request through Tau, use
`http://localhost:4001` and provide bearer auth explicitly or through the local
Scillm environment. Tau reads `SCILLM_MASTER_KEY`, `SCILLM_API_KEY`,
`SCILLM_AUTH_TOKEN`, or an env file named by `SCILLM_ENV_PATH`; it records only
the redacted auth source in the receipt:

```bash
SCILLM_ENV_PATH=/home/graham/workspace/experiments/scillm/.env \
uv run tau scillm-worker-launch \
  --work-order work-order.json \
  --out scillm-worker-launch-live-receipt.json \
  --scillm-base-url http://localhost:4001 \
  --caller-skill tau \
  --apply \
  --request-timeout-s 180
```

This example proves only the Tau-side receipt validation path. Unless
`SCILLM_WORKER_RESULT` points at a real worker artifact, it does not prove Tau
called a live SciLLM/OpenCode serve worker. The default apply launch posts to a
deterministic local SciLLM-compatible fixture server and proves Tau can send the
bounded request, redact auth from the receipt, and capture response JSON. It
does not prove a live SciLLM service was used, the OpenCode worker result is
truthful or sufficient for closure, semantic code correctness, or provider/model
quality.
