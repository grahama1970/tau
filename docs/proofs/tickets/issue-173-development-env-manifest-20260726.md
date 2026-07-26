# tau#173 Development Environment Manifest Proof

Ticket: https://github.com/grahama1970/tau/issues/173

## Change

Generated Tau environment manifests no longer claim empty secret exposure for
development-mode runs that inherit the host environment.

For generated manifests:

- `host_environment_inherited` records whether the execution mode inherits host
  environment names.
- `environment_variables_visible` records visible environment variable names.
- `secrets_visible` records sensitive-looking visible environment variable names.
- development inherited-host manifests use
  `environment_attestation=non_attesting_host_environment_inherited`.
- non-controlled generated manifests now use `network_policy=unrestricted`
  instead of the unimplemented `allowlisted` claim.

## Deterministic Checks

Commands run from `/tmp/tau-main-issue-137.7nchbf`:

```bash
uv run python -m py_compile src/tau_coding/security_context.py src/tau_coding/provenance.py tests/test_security_context.py tests/test_provenance.py tests/test_receipt_signing.py
uv run ruff check src/tau_coding/security_context.py src/tau_coding/provenance.py src/tau_coding/cli.py tests/test_security_context.py tests/test_provenance.py tests/test_receipt_signing.py tests/test_cli.py tests/test_package_validate.py
uv run pytest tests/test_security_context.py tests/test_provenance.py tests/test_receipt_signing.py tests/test_cli.py::test_cli_actor_and_environment_manifests_write_files tests/test_package_validate.py tests/test_project_dag.py::test_project_dag_controlled_boundary_requires_explicit_secure_mode tests/test_project_dag.py::test_project_dag_secure_relative_itar_boundary_blocks_before_dispatch_without_actor -q
git diff --check
```

Observed results:

- `py_compile`: exit 0
- `ruff check`: `All checks passed!`
- `pytest`: `34 passed`
- `git diff --check`: exit 0

## Local Proof Bundle

Artifact directory:

`docs/proofs/tickets/issue-173-development-env-manifest-20260726/`

Generated proof receipt:

`docs/proofs/tickets/issue-173-development-env-manifest-20260726/proof-receipt.json`

Receipt summary:

- `ok=true`
- `mocked=false`
- `live=true`
- `provider_live=false`
- `security_context_status=PASS`
- `signed_receipt_status=PASS`
- `network_policy=unrestricted`
- `host_environment_inherited=true`
- `secrets_visible=["GH_TOKEN", "SCILLM_API_KEY"]`
- `secret_value_leaks=[]`

The proof generator used a controlled process environment containing `GH_TOKEN`
and `SCILLM_API_KEY` names with synthetic values. The committed artifacts record
the variable names and prove the synthetic secret values were not written.

## Evidence Scope

- mocked: no
- live: yes, local manifest generation and local HMAC signing
- provider/model calls: no
- proves: generated development manifests no longer assert empty secret exposure
  when host environment is inherited, and signed receipts reference a manifest
  that records inherited secret variable names
- does not prove: sandbox enforcement, provider behavior, or correctness of
  caller-supplied external manifests
