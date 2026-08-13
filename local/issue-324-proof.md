# Tau #324 closure proof

Issue: https://github.com/grahama1970/tau/issues/324

## Implemented

- Added `tau.python_host_request.v1`.
- Added `tau.python_host_admission_receipt.v1`.
- Added `tau.python_host_effect_receipt.v1`.
- Added `tau.python_host_result.v1`.
- Added host-owned dispatcher `src/tau_coding/runtime_backends/kernel_host_bridge.py`.
- Added tiny in-kernel client `src/tau_coding/runtime_backends/kernel_host_client.py`.
- Exposed bounded operations: `source.read`, `code.search`, `graph.query`, `artifact.put`, `evidence.emit`, and `progress.emit`.
- Added governed Memory `/recall` graph-query adapter with named profiles only.
- Added Tau `agentic-evals` fixture: `evals/python_host_bridge_agentic_eval.json`.

## Proof

mocked: no
live: yes for the Jupyter kernel canary and local Memory/GMO governed graph-query boundary

Commands run:

```bash
uv run ruff check src/tau_coding/runtime_backends/kernel_host_bridge.py src/tau_coding/runtime_backends/kernel_host_client.py src/tau_coding/runtime_backends/__init__.py tests/test_kernel_host_bridge.py tests/test_runtime_backend_contracts.py
```

Result: `All checks passed!`

```bash
uv run mypy src/tau_coding/runtime_backends/kernel_host_bridge.py src/tau_coding/runtime_backends/kernel_host_client.py
```

Result: `Success: no issues found in 2 source files`

```bash
uv run --extra python-workspace pytest tests/test_kernel_host_bridge.py tests/test_runtime_backend_contracts.py -q
```

Result: `64 passed`

```bash
/home/graham/workspace/experiments/agent-skills/skills/agentic-evals/run.sh run evals/python_host_bridge_agentic_eval.json --output /home/graham/workspace/experiments/tau/local/issue-324-agentic-evals-report-final.json
```

Result: `READY`, 2 cases, 4 trials.

Standalone live canary:

```text
/home/graham/workspace/experiments/tau/local/issue-324-live-canary-20260813T151931Z/summary.json
```

Result: `PASS`.

## Live Canary Checks

- `source.read`: true.
- `code.search`: true.
- `graph.query` through governed Memory boundary: true.
- `artifact.put`: true.
- `evidence.emit` candidate-only: true.
- `progress.emit` non-authoritative: true.
- Cancelled execution produced zero handler/effect execution: true.
- Independent `DagAttemptResult` admission happened only after validator readback: true.
- Host-call success remained `tau_admission_status=not_admitted`: true.

## Deterministic Negatives

- Stale kernel generation rejects before handler execution.
- Cancelled execution rejects before handler/effect execution.
- Absolute paths, `..`, and symlink escapes are blocked.
- Undeclared graph profiles, raw AQL parameters, and excessive depth budgets are blocked.
- Worktree, goal, policy, data-boundary, lease, execution-token, and execution-id mutations are blocked.
- Oversize source output uses an explicit truncated projection with complete artifact.
- Duplicate `artifact.put` resolves to one content-addressed artifact identity.
- `evidence.emit` remains candidate-only even for `PASS` text and high confidence.
- Direct ambient authority request kinds such as network, provider, GitHub, raw Memory socket, and database AQL are denied.
- Late request from execution N cannot attach to execution N+1.

## Remaining Boundary

This closes the #324 host-call bridge slice. It does not add Memory writes, arbitrary graph query authority, provider/GitHub access, DAG mutation, child-agent spawning, or OS-level sandboxing beyond the #323 recorded local-process profile.
