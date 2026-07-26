# Issue #175 Proof: Registered Documentation Sources

Date: 2026-07-26
Repo: grahama1970/tau
Ticket: https://github.com/grahama1970/tau/issues/175

## Change Summary

- Added `src/tau_coding/documentation_registry.py`.
- Added documentation-registry allowlist handling to generated security
  environment manifests in `src/tau_coding/security_context.py`.
- Added focused tests in `tests/test_documentation_registry.py`.
- Reused the #176 freshness-gate compatibility seam via
  `registry_docs_sources(registry)`.

## Acceptance Evidence

- Manifest/lockfile resolution:
  `test_documentation_registry_resolves_lock_versions_imports_and_sources`
  resolves `httpx==0.28.1` from `uv.lock`, direct dependency status from
  `pyproject.toml`, and actual import status from Python source.
- Registered docs before web search:
  `test_documentation_lookup_receipt_uses_registered_source_before_web_search`
  writes a lookup receipt with source URL, version, content hash, untrusted-data
  provenance, and `selected_before_general_web_search: true`.
- Version bump invalidation:
  `test_version_bump_changes_registry_cache_key_and_source` changes the locked
  version and observes a different cache key and source URL.
- URL allowlist refusal:
  `test_documentation_lookup_refuses_unregistered_url` records
  `unregistered_url_refused`.
- Egress allowlist:
  `test_documentation_registry_drives_environment_network_allowlist` verifies
  generated environment manifests record `network_policy: allowlisted` and the
  registry allowlist.

## Commands

```bash
uv run ruff check src/tau_coding/documentation_registry.py src/tau_coding/security_context.py src/tau_coding/knowledge_freshness.py src/tau_coding/provider_config.py src/tau_coding/provider_catalog.py src/tau_coding/project_dag.py tests/test_documentation_registry.py tests/test_knowledge_freshness.py tests/test_provider_config.py
```

Result: `All checks passed!`

```bash
uv run python -m py_compile src/tau_coding/documentation_registry.py src/tau_coding/security_context.py src/tau_coding/knowledge_freshness.py src/tau_coding/provider_config.py src/tau_coding/provider_catalog.py src/tau_coding/project_dag.py tests/test_documentation_registry.py tests/test_knowledge_freshness.py tests/test_provider_config.py
```

Result: exit code 0.

```bash
uv run pytest -q tests/test_documentation_registry.py tests/test_knowledge_freshness.py tests/test_provider_config.py
```

Result: `47 passed in 0.80s`

```bash
uv run pytest -q tests/test_security_context.py
```

Result: `5 passed in 0.54s`

```bash
git diff --check
```

Result: exit code 0.

## Evidence Boundary

- mocked: mixed. Metadata/content resolution in the registry tests uses fixture
  resolvers for deterministic source and hash behavior.
- live: yes for local code execution and manifest generation. The tests read
  real fixture project files and generate real Tau receipts/manifests.
- External Context7/fetcher services were not called in this proof run. The
  implemented registry records the registered rung and source provenance that
  downstream Context7/fetcher adapters can consume without falling through to
  general web search.
