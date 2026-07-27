# Issue 187 Secure Execution Proof

Commit: `bc049af1b53dfb8fd38828e2e1c5aca7b05e51ba`

Commands:

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/secure_execution_conformance.py src/tau_coding/secure_executor.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/secure_execution_conformance.py src/tau_coding/secure_executor.py src/tau_coding/cli.py
PYTHONPATH=src uv run pytest tests/test_secure_executor.py tests/test_sandbox_policy.py
PYTHONPATH=src uv run tau secure-execution-conformance --allow-live-sandbox --output docs/proofs/tickets/issue-187-secure-execution-conformance-20260727/secure-execution-conformance.json
```

Readback:

- `secure-execution-conformance.json`: `status=PASS`
- `mocked=false`, `live=true`, `provider_live=false`
- selected backend: `docker`
- backend selection reason: `bwrap_probe_blocked_docker_digest_available`
- failed checks: `[]`
- checks true: positive isolated command, host environment not inherited, undeclared read denied, path escape denied, secret access denied, undeclared write denied, undeclared egress denied, wrong-attempt grant denied before execution, expired grant denied before execution, no direct subprocess fallback.

