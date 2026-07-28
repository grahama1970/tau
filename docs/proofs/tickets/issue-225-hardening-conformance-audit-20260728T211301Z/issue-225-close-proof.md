# Tau #225 Close Proof

Ticket: `grahama1970/tau#225`

## Disposition

`NARROW #72`

This ticket asked for a current-head hardening conformance bundle and an honest
disposition for #72: `CLOSE`, `NARROW`, or `KEEP_OPEN/BLOCKED`.

The retained matrix chooses `NARROW`: #72 is not honestly closeable because four
proof-layer gaps remain and branch-protection `enforce_admins` is still deferred.
The completed runtime hardening work is captured in the phase matrix; #72 should
remain narrowed to the five-item remaining program in `closure-evidence.json`.

## Current-Head Repair Since Reopen

The reopened audit called out that #225's matrix had lived only in PR #228 and
that #222 was still pending. Both are now corrected:

- #225 matrix and closure evidence are on Tau main.
- #222 is closed with current-head proof at
  `docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/issue-222-close-proof.md`.
- #222's external dependency is on `grahama1970/agent-skills@main` at
  `1b5a3fc83bfbd3b968b72e83d31231f23c6997ea`.

## Commands Run

```bash
jq -e '.environment.dependency_fix | contains("agent-skills main")' \
  docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/closure-evidence.json
```

Result:

```text
true
```

```bash
jq -e '.admission_status=="PASS" and .admission_ok==true and .separate_checkout_exists==true' \
  docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/installed-wheel-current-head/current-wheel-governed-admission.json
```

Result:

```text
true
```

```bash
uv run pytest -q tests/test_skill_execution_contract.py tests/test_governed_skill_execution.py
```

Result:

```text
14 passed in 0.59s
```

## Proof Artifacts

- `docs/proofs/tickets/issue-225-hardening-conformance-audit-20260728T211301Z/phase-matrix.json`
- `docs/proofs/tickets/issue-225-hardening-conformance-audit-20260728T211301Z/closure-evidence.json`
- `docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/issue-222-close-proof.md`

## Scope

`mocked`: no

`live`: yes

`provider_live`: false for the matrix update; #222 retained provider-live proof
is cited separately.

This closes #225 as the current-head conformance/disposition child. It does not
close #72.
