# Tau #323 closure proof

Issue: https://github.com/grahama1970/tau/issues/323

## Implemented

- Added `tau.python_workspace_request.v1`.
- Added `tau.python_workspace_receipt.v1`.
- Added `tau.python_execution_request.v1`.
- Added `tau.python_execution_receipt.v1`.
- Added `tau.python_kernel_control_receipt.v1`.
- Added `tau.python_package_manifest.v1`.
- Added an attempt-scoped Jupyter kernel workspace backend.
- Added a headless installed entrypoint: `tau-python-kernel-worker`.
- Added optional package profile: `tau[python-workspace]`.
- Added Tau `agentic-evals` fixture: `evals/python_workspace_kernel_agentic_eval.json`.

## Proof

mocked: no
live: yes for Jupyter kernel tests, direct canary, worker entrypoint canary, and installed-wheel canary

Commands run:

```bash
uv run ruff check src/tau_coding/runtime_backends/kernel.py src/tau_coding/runtime_backends/kernel_contracts.py src/tau_coding/runtime_backends/python_kernel_worker.py src/tau_coding/runtime_backends/__init__.py tests/test_python_kernel_backend.py tests/test_runtime_backend_contracts.py
```

Result: `All checks passed!`

```bash
uv run mypy src/tau_coding/runtime_backends/kernel.py src/tau_coding/runtime_backends/kernel_contracts.py src/tau_coding/runtime_backends/python_kernel_worker.py
```

Result: `Success: no issues found in 3 source files`

```bash
uv run --extra python-workspace pytest tests/test_python_kernel_backend.py tests/test_runtime_backend_contracts.py -q
```

Result: `58 passed`

```bash
/home/graham/workspace/experiments/agent-skills/skills/agentic-evals/run.sh run evals/python_workspace_kernel_agentic_eval.json --output /home/graham/workspace/experiments/tau/local/issue-323-agentic-evals-report-final.json
```

Result: `READY`, 3 cases, 6 trials.

```bash
uv build --wheel --out-dir local/issue-323-wheel-dist-20260813T150857Z
uv venv /tmp/tau-issue-323-wheel-venv-20260813T150857Z --python 3.14
uv pip install --python /tmp/tau-issue-323-wheel-venv-20260813T150857Z/bin/python 'local/issue-323-wheel-dist-20260813T150857Z/tau-0.1.0-py3-none-any.whl[python-workspace]'
/tmp/tau-issue-323-wheel-venv-20260813T150857Z/bin/tau-python-kernel-worker --canary-output-dir /home/graham/workspace/experiments/tau/local/issue-323-wheel-proof-20260813T150857Z/canary
```

Result: `/home/graham/workspace/experiments/tau/local/issue-323-wheel-proof-20260813T150857Z/summary.json` reports `PASS`.

## Coverage

- Attempt-scoped persistent namespace.
- Serialized cell execution.
- Jupyter message correlation.
- Structured content-addressed output artifacts with truncated projections.
- Late async output classification.
- Import failure evidence.
- Unknown feature fail-closed behavior.
- Binding mutation invalidation.
- Separate attempt and branch namespace isolation.
- Interrupt control receipt for an infinite loop.
- Restart-safe process ownership reconciliation that skips unrelated PID reuse.
- Explicit non-acceptance of successful Python execution as Tau acceptance.

## Remaining Boundary

This closes the focused #323 kernel backend slice. It does not implement #324 host calls, provider/model quality, or OS sandboxing beyond the recorded local-process security profile.
