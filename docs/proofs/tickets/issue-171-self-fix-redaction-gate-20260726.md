# Issue #171 Proof: Self-Fix GitHub Redaction Gate

Ticket: https://github.com/grahama1970/tau/issues/171

## Repair

- Extended `tau_coding.github_handoff.redact_github_projection` from a two-pattern scrubber into a named-class redactor for Linux/macOS home paths, GitHub, OpenAI, Anthropic, AWS access key IDs, Google API keys, Slack tokens, HuggingFace tokens, GitLab tokens, and bearer JWTs.
- Added `covered_secret_classes` and `uncovered_secret_classes` to the redaction receipt so the gate does not claim exhaustive secret detection.
- Routed self-fix GitHub close proof through a generated GitHub write projection, redacted projection, redaction receipt, and `proof.redacted.md`.
- Changed the self-fix `apply_github=True` path to fail closed with `github_redaction_failed` before closing the ticket when the redaction receipt is not `ok: true`.
- Registered `tau github-redact-projection` as a real CLI command and added it to the legacy callback passthrough exemption list so the redaction proof path is reachable from the CLI.

## Deterministic Proof

Artifacts are under:

`docs/proofs/tickets/issue-171-self-fix-redaction-gate-20260726/`

Command results:

- `uv run ruff format --check src/tau_coding/cli.py src/tau_coding/github_handoff.py src/tau_coding/self_fix_ticket_repair.py tests/test_cli.py tests/test_self_fix_ticket_repair.py`
  - Result: `5 files already formatted`
- `uv run ruff check src/tau_coding/cli.py src/tau_coding/github_handoff.py src/tau_coding/self_fix_ticket_repair.py tests/test_cli.py tests/test_self_fix_ticket_repair.py`
  - Result: `All checks passed!`
- `uv run python -m py_compile src/tau_coding/cli.py src/tau_coding/github_handoff.py src/tau_coding/self_fix_ticket_repair.py tests/test_cli.py tests/test_self_fix_ticket_repair.py`
  - Result: exit `0`
- `uv run pytest -q tests/test_self_fix_ticket_repair.py tests/test_cli.py -k 'github_redact_projection or ticket_repair'`
  - Result: `8 passed, 227 deselected`
- `uv run tau github-redact-projection --projection /tmp/tau-issue-171-secret-corpus-projection.json --out docs/proofs/tickets/issue-171-self-fix-redaction-gate-20260726/secret-corpus.redacted.json --receipt docs/proofs/tickets/issue-171-self-fix-redaction-gate-20260726/secret-corpus-redaction-receipt.json`
  - Result: receipt `ok: true`, `redaction_count: 10`

Proof receipts:

- `proof-summary.json`
- `secret-corpus-redaction-receipt.json`
- `secret-corpus.redacted.json`
- `pytest-focused.txt`
- `ruff-check.txt`
- `ruff-format-check.txt`
- `py-compile.txt`

## Secret-Corpus Coverage

`secret-corpus-redaction-receipt.json` reports these covered classes:

`sensitive_json_keys`, `linux_home_path`, `macos_home_path`, `github_token`, `openai_key`, `anthropic_key`, `aws_access_key_id`, `google_api_key`, `slack_token`, `huggingface_token`, `gitlab_token`, `bearer_jwt`.

The committed proof bundle stores only the redacted projection and receipt. The raw synthetic corpus was generated under `/tmp` and was not committed.

Declared uncovered classes:

`unknown provider-specific token formats`, `low-entropy example strings`, `secrets split across multiple fields`.

## Evidence Limits

mocked: no

live: no

What was exercised: deterministic local redaction, CLI invocation, self-fix GitHub write fail-closed behavior, lint, compile, and focused pytest.

What remains unverified: live GitHub mutation content after close and exhaustive detection of all possible secret formats.
