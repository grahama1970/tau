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

This example proves only the Tau-side receipt validation path. Unless
`SCILLM_WORKER_RESULT` points at a real worker artifact, it does not prove Tau
called SciLLM. The dry-run launch receipt proves only request construction and
route gating; it does not prove OpenCode serve accepted or ran the request,
semantic code correctness, or provider/model quality.
