# Issue #334 hardcoded-secret triage

Status: PASS

Real exposed credentials found: no

## Disposition table

- `scripts/run-herdr-native-event-smoke.py` — synthetic_local_smoke_token — matches: 1 — follow-up: No credential rotation needed; optional cleanup can use a generated smoke token.
- `tests/test_storage_redaction_boundaries.py` — synthetic_test_fixture_or_redaction_assertion — matches: 30 — follow-up: No credential rotation needed; future cleanup can reduce scanner noise by splitting scanner-shaped literals.
- `tests/test_tau_ai.py` — synthetic_test_fixture_or_redaction_assertion — matches: 47 — follow-up: No credential rotation needed; future cleanup can reduce scanner noise by splitting scanner-shaped literals.
- `tests/test_scillm_chat_review.py` — synthetic_test_fixture_or_redaction_assertion — matches: 17 — follow-up: No credential rotation needed; future cleanup can reduce scanner noise by splitting scanner-shaped literals.
- `tests/test_commit_plan.py` — synthetic_test_fixture_or_redaction_assertion — matches: 12 — follow-up: No credential rotation needed; future cleanup can reduce scanner noise by splitting scanner-shaped literals.
- `tests/test_battle_scillm_auth_preflight.py` — synthetic_test_fixture_or_redaction_assertion — matches: 10 — follow-up: No credential rotation needed; future cleanup can reduce scanner noise by splitting scanner-shaped literals.
- `tests/test_dag_route_memory.py` — synthetic_test_fixture_or_redaction_assertion — matches: 9 — follow-up: No credential rotation needed; future cleanup can reduce scanner noise by splitting scanner-shaped literals.
- `tests/test_immutable_goal_audit.py` — synthetic_test_fixture_or_redaction_assertion — matches: 5 — follow-up: No credential rotation needed; future cleanup can reduce scanner noise by splitting scanner-shaped literals.
- `tests/test_coding_worker_adapters.py` — synthetic_test_fixture_or_redaction_assertion — matches: 44 — follow-up: No credential rotation needed; future cleanup can reduce scanner noise by splitting scanner-shaped literals.
- `src/tau_coding/cli.py` — reviewed_no_real_secret_found — matches: 97 — follow-up: No follow-up.
- `src/tau_coding/security_audit_conformance.py` — fixed_ephemeral_runtime_token — matches: 13 — follow-up: Keep token generated at runtime; no follow-up ticket needed if tests and gitleaks pass.

## Code change

- Replaced `src/tau_coding/security_audit_conformance.py` module-level hardcoded audit token with `secrets.token_urlsafe(32)` generated inside the conformance run.
- Updated `tests/test_security_audit_conformance.py` to pass an explicit synthetic token into `_rbac_policy(...)` instead of importing a production module constant.

## Verification

- `uv run pytest -q tests/test_security_audit_conformance.py --tb=short --tau-suite=all` -> `6 passed`.
- `gitleaks detect --no-git --source src/tau_coding/security_audit_conformance.py ...` -> 0 findings.
- `gitleaks detect --no-git --source tests/test_security_audit_conformance.py ...` -> 0 findings.
