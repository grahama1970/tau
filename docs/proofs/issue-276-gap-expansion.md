# Issue #276 Gap Expansion Proof

## Scope

Ticket: <https://github.com/grahama1970/tau/issues/276>

Tau now bridges admitted `tau.node_completion_boundary.v1` evidence gaps into
bounded `tau.gap_candidate.v1` records and translates only eligible candidates
into existing `tau.dag_expansion_proposal.v1` proposals. The bridge does not
mutate the active graph directly and does not treat producer scope claims,
reviewer PASS, or confidence values as authoritative.

## Changed Surfaces

- `src/tau_coding/gap_expansion.py`
- `src/tau_coding/gap_expansion_conformance.py`
- `src/tau_coding/node_completion_boundary.py`
- `src/tau_coding/cli.py`
- `src/tau_coding/runtime_handshake.py`
- `tests/test_gap_expansion.py`

## Live Proof

Command:

```bash
uv run tau gap-expansion-conformance \
  --output /tmp/tau-issue276-gap-expansion-final-20260731T043442Z-1562612/summary.json \
  --allow-live-filesystem
```

Readback:

- Receipt: `/tmp/tau-issue276-gap-expansion-final-20260731T043442Z-1562612/summary.json`
- `status`: `PASS`
- `mocked`: `false`
- `live`: `true`
- `failed_checks`: `[]`
- generated child: `gap-coder-gap-validation`
- expanded scheduler completed nodes: `coder`, `gap-coder-gap-validation`, `reviewer`
- acceptance checks: 14/14 true, including `depth_out_of_envelope` with
  `requested_depth_delta=1` rejected by `max_depth=0`

The receipt includes bridge, proposal, validation, policy, apply, boundary,
duplicate, human-required, out-of-envelope, budget-exhausted, stale-lineage,
and expanded-DAG artifacts under the same proof root.

## Handshake Proof

Command:

```bash
uv run tau runtime-handshake \
  --output /tmp/tau-issue276-gap-expansion-final-20260731T043442Z-1562612/runtime-handshake.json
```

Readback:

- Receipt: `/tmp/tau-issue276-gap-expansion-final-20260731T043442Z-1562612/runtime-handshake.json`
- `status`: `PASS`
- `capabilities.gap_expansion_conformance.status`: `AVAILABLE`

## Focused Deterministic Checks

Command:

```bash
uv run pytest tests/test_gap_expansion.py tests/test_node_completion_boundary.py tests/test_dag_expansion.py -q
```

Readback:

- `44 passed in 2.13s`

Broader adjacent command:

```bash
uv run pytest \
  tests/test_gap_expansion.py \
  tests/test_node_completion_boundary.py \
  tests/test_dag_expansion.py \
  tests/test_dag_runtime_scheduler.py \
  tests/test_dag_runtime_run_store.py \
  tests/test_dag_plan.py \
  -q
```

Readback:

- `114 passed in 5.07s`

Lease-renewal regression command, added after GitHub CI exposed a slow-runner
timing failure:

```bash
uv run pytest \
  tests/test_dag_runtime_run_store.py::test_scheduler_renews_lease_while_node_is_running \
  tests/test_dag_runtime_run_store.py \
  -q
```

Readback:

- `31 passed in 4.40s`

Command:

```bash
uv run ruff check \
  src/tau_coding/runtime_handshake.py \
  tests/test_gap_expansion.py \
  src/tau_coding/gap_expansion.py \
  src/tau_coding/gap_expansion_conformance.py \
  src/tau_coding/node_completion_boundary.py \
  src/tau_coding/cli.py
```

Readback:

- `All checks passed!`

## Proof Boundaries

This proves local filesystem conformance for the gap-to-expansion bridge and
canonical scheduler execution of the revised DAG. It does not prove provider
semantic quality, distributed multi-host scheduler coordination, or a complete
human approval UI.
