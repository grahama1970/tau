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

uv run tau omp-worker-doctor \
  --out omp-worker-doctor-receipt.json \
  --omp-bin fake-omp

uv run tau omp-worker-launch \
  --work-order work-order.json \
  --out omp-worker-launch-apply-receipt.json \
  --apply \
  --omp-bin fake-omp \
  --timeout-s 5

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

To run the doctor/apply-launch portion against an installed OMP binary instead
of the deterministic fixture, set `OMP_BIN`:

```bash
OMP_BIN="$(command -v omp)" examples/omp-worker/run.sh /tmp/tau-omp-worker-real-probe
```

For an explicit live OMP transport sanity check with no fallback to the fixture,
use `live-run.sh` and point `OMP_BIN` at the installed oh-my-pi/OMP-compatible
binary:

```bash
OMP_BIN="$(command -v omp)" examples/omp-worker/live-run.sh /tmp/tau-omp-worker-live
```

The live check fails immediately if `OMP_BIN` is unset. It runs
`omp-worker-doctor`, posts a bounded `omp --mode rpc --no-session` request
through `omp-worker-launch --apply`, and validates a prewritten
`tau.omp_worker_result.v1` with `omp-worker-validate`. This proves Tau can
drive the configured OMP-compatible RPC command and validate the result
contract. It still does not prove OMP performed coding work, worker
trustworthiness, semantic code correctness, provider/model quality, or full
sandbox isolation.

This example proves only the Tau-side receipt validation path. Unless
`OMP_WORKER_RESULT` points at a real worker artifact, it does not prove Tau
launched a real OMP worker. The default doctor receipt uses the deterministic
local `fake-omp` executable to prove the OMP command identity-probe path only.
The default apply launch uses the same deterministic local `fake-omp`
executable to prove Tau can invoke an OMP-compatible process and capture
stdout/stderr, require parseable RPC JSONL response frames, and fail closed
unless each response frame echoes the work-order metadata Tau sent in the
request. The
maintained sanity check requires the example receipt to show
`doctor_command_found:true`, `doctor_version_executed:true`,
`apply_launch_process_executed:true`, `apply_launch_exit_code:0`,
`apply_launch_stdout_jsonl_valid:true`, `apply_launch_response_frame_count:1`,
matching `response_metadata`, and hash-bound stdout/stderr descriptors. It does
not prove a real `oh-my-pi` binary was used, OMP accepted or ran the request
semantically, semantic code correctness, or provider/model quality.
