# OMP Worker Validation Example

This example shows Tau validating an `oh-my-pi` worker result as an untrusted
external coding-worker artifact.

Default run:

```bash
examples/omp-worker/run.sh /tmp/tau-omp-worker-example
```

The default path writes a fixture `tau.executor.omp.v1` work order and a fixture
`tau.omp_worker_result.v1`, then validates them with:

```bash
uv run tau omp-worker-launch \
  --work-order work-order.json \
  --out omp-worker-launch-receipt.json

uv run tau omp-worker-validate \
  --work-order work-order.json \
  --result omp-result.json \
  --out omp-worker-receipt.json
```

To validate a result produced by a real OMP worker, set `OMP_WORKER_RESULT` to a
JSON file with schema `tau.omp_worker_result.v1`:

```bash
OMP_WORKER_RESULT=/path/to/omp-result.json examples/omp-worker/run.sh /tmp/tau-omp-worker-live
```

This example proves only the Tau-side receipt validation path. Unless
`OMP_WORKER_RESULT` points at a real worker artifact, it does not prove Tau
launched OMP. The dry-run launch receipt proves only RPC request construction
and route gating; it does not prove OMP accepted or ran the request, semantic
code correctness, or provider/model quality.
