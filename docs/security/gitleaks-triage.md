# Gitleaks Triage for External Review

This note records the first PR security-scan triage for the `external-review/airgap-sparta-v0` branch.

## Result

The initial `gitleaks detect --source . --verbose --redact` run reported 66 findings, all under the `generic-api-key` rule.

Representative inspection found two false-positive classes:

- route-memory proof artifacts with ArangoDB-style `_key` document identifiers such as `tau-route-<hex>`;
- a synthetic `api_key` fixture in `tests/test_github_handoff.py` used to verify GitHub projection redaction.

The repository now includes `.gitleaks.toml` with a narrow allowlist for those two known false-positive patterns. It does not suppress whole proof directories.

## Non-Claims

This triage does not prove absence of secrets or controlled data.

Human security review is still required before sharing the external-review branch or tag with colleagues.
