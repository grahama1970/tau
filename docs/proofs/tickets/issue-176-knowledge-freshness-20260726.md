# Issue #176 Proof: Proactive Knowledge Freshness Gate

Date: 2026-07-26
Repo: grahama1970/tau
Ticket: https://github.com/grahama1970/tau/issues/176

## Change Summary

- Added `src/tau_coding/knowledge_freshness.py`.
- Added explicit `model_knowledge_cutoffs` provider configuration metadata.
- Wired opt-in DAG `knowledge_freshness` pre-dispatch receipts into
  `src/tau_coding/project_dag.py`.
- Added DAG receipt indexes:
  - `knowledge_freshness_receipts`
  - `knowledge_provenance_by_node`
- Added focused tests in `tests/test_knowledge_freshness.py` and provider config
  tests in `tests/test_provider_config.py`.

## Acceptance Evidence

- Stale-set computation: `tests/test_knowledge_freshness.py` covers a dependency
  released after the model cutoff and excludes a dependency released before the
  cutoff.
- Imported-only scope: the stale-set test includes a locked dependency that is
  not imported and does not enter the stale set.
- Unknowns fail safe: the suite covers unknown model cutoff and unknown
  dependency release date as stale.
- Version-keyed cache reuse: the suite fetches `httpx==0.28.1` once, then reuses
  the version-keyed cache on the second computation.
- Pre-generation ordering and provenance: the live ready-queue test launches a
  local command-backed DAG node; the command reads `knowledge_provenance` from
  stdin before returning its handoff response.
- Unverified-knowledge flag: the live DAG test disables network fetch and
  records `knowledge_provenance.status == "unverified_knowledge"` instead of
  silently treating the dependency as fresh.

## Commands

```bash
uv run ruff check src/tau_coding/knowledge_freshness.py src/tau_coding/provider_config.py src/tau_coding/provider_catalog.py src/tau_coding/project_dag.py tests/test_knowledge_freshness.py tests/test_provider_config.py
```

Result: `All checks passed!`

```bash
uv run python -m py_compile src/tau_coding/knowledge_freshness.py src/tau_coding/provider_config.py src/tau_coding/provider_catalog.py src/tau_coding/project_dag.py tests/test_knowledge_freshness.py tests/test_provider_config.py
```

Result: exit code 0.

```bash
uv run pytest -q tests/test_knowledge_freshness.py tests/test_provider_config.py
```

Result: `42 passed in 0.63s`

```bash
git diff --check
```

Result: exit code 0.

## Evidence Boundary

- mocked: mixed. The direct computation test uses an injected fetcher for
  deterministic cache behavior.
- live: yes for the DAG wiring proof. The ready-queue test runs a real local
  subprocess and verifies provenance is present before the worker emits a node
  response.
- External documentation services were not called in the proof run. This proof
  verifies bounded fetch/cache/provenance behavior and fail-safe
  `unverified_knowledge`; it does not prove any specific external documentation
  source is semantically correct.
