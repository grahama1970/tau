# Issue #157 Browser DAG Handler Proof

Ticket: https://github.com/grahama1970/tau/issues/157

## Scope

Implemented a first-class `browser` node kind for `tau.generic_dag_spec.v1`.
The node routes browser work through Surf, accepts browser-oracle-style tab
bindings, and writes:

- outer node receipt: `tau.generic_dag_node_receipt.v1`
- typed browser receipt: `tau.browser_dag_receipt.v1`
- hash-bound screenshot artifact when a screenshot operation succeeds

The existing `browser-cdp-proof` command remains available and uses the updated
Surf resolver.

## Deterministic Checks

```bash
uv run pytest -q tests/test_browser_dag_handler.py tests/test_browser_cdp_proof.py
```

Result:

```text
6 passed in 1.00s
```

```bash
uv run ruff check \
  src/tau_coding/browser_cdp_proof.py \
  src/tau_coding/generic_dag.py \
  tests/test_browser_dag_handler.py \
  tests/test_browser_cdp_proof.py
```

Result:

```text
All checks passed!
```

```bash
uv run python -m py_compile \
  src/tau_coding/browser_cdp_proof.py \
  src/tau_coding/generic_dag.py \
  tests/test_browser_dag_handler.py
```

Result: exit code 0.

```bash
git diff --check
```

Result: exit code 0.

## Live Surf Checks

Surf availability:

```bash
test -x /home/graham/workspace/experiments/agent-skills/skills/surf/run.sh
timeout 20 /home/graham/workspace/experiments/agent-skills/skills/surf/run.sh tab.list
```

Result: wrapper executable; `tab.list` returned active tabs.

Live unbound local-page DAG:

- receipt root: `docs/proofs/tickets/issue-157-browser-handler-20260726/live-unbound/`
- run receipt status: `PASS`
- node verdict: `PASS`
- screenshot: `live-unbound/main.png`
- screenshot sha256 in typed receipt:
  `sha256:af736764486df1b76430667c3cf5c1fec0d67fee31634e4cbb88d402c9c1fa24`

Live browser-oracle-style bound-tab DAG:

- receipt root: `docs/proofs/tickets/issue-157-browser-handler-20260726/live-bound/`
- run receipt status: `PASS`
- node verdict: `PASS`
- routed read command included `--tab-id 837362311`
- routed screenshot command included `--tab-id 837362311`
- screenshot: `live-bound/bound.png`
- screenshot sha256 in typed receipt:
  `sha256:8213e1fdbe93c7dc1655ab702e2122b0696691f21f5059d313d0562830edb95a`

## Evidence Limits

mocked: yes, for `tests/test_browser_dag_handler.py` Surf fixture coverage only.
live: yes, for the preserved Surf runs above against local pages and a bound tab.

The proof does not claim arbitrary browser UI correctness or provider/model
quality. It proves the browser DAG handler contract, Surf routing, fail-closed
unavailable behavior, wrapper resolution precedence, and screenshot hash binding.
