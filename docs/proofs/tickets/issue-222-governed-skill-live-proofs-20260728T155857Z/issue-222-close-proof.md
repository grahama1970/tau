# Tau #222 Close Proof

Ticket: `grahama1970/tau#222`

## What Was Proved

The governed agent-skills execution adapter is present on current Tau main and
has retained proof for all three required representative classes:

- read-only: `review-code` native output normalized to `review_code.result.v1`
  and admitted `PASS` with Tau-derived verdict `REVISE`
- bounded mutation: `code-runner` native result normalized, admitted `PASS`, and
  out-of-allowlist mutation rejected as `BLOCKED`
- external/effectful: `create-evidence-case` persisted an effect, read it back,
  deduplicated duplicate keys, and admitted through the #218 effect ledger
- cancellation: owned descendant process killed and terminal cancellation
  receipt retained
- installed wheel: current Tau wheel resolved `TAU_AGENT_SKILLS_ROOT` to a
  separate checkout and ran governed admission from the wheel

The previous blocker in the reopened audit is removed: `create-evidence-case`
read-back is now on `grahama1970/agent-skills@main` at
`1b5a3fc83bfbd3b968b72e83d31231f23c6997ea`.

## Commands Run

```bash
uv run pytest -q tests/test_skill_execution_contract.py tests/test_governed_skill_execution.py
```

Result:

```text
14 passed in 0.59s
```

```bash
uv build --wheel --out-dir /tmp/tau-current-wheel-222
```

Result:

```text
Successfully built /tmp/tau-current-wheel-222/tau-0.1.0-py3-none-any.whl
```

```bash
TAU_AGENT_SKILLS_ROOT=/tmp/agent-skills-watchdog-181-20260728 \
/tmp/tau-current-wheel-222/venv/bin/python <installed-wheel-governed-admission-probe>
```

Result:

```text
admission_status=PASS
admission_ok=true
derived_verdict=REVISE
resolved_agent_skills_root=/tmp/agent-skills-watchdog-181-20260728
separate_checkout_exists=true
```

## Proof Artifacts

- `docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/closure-evidence.json`
- `docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/read-only/tau-admission-receipt.PASS.json`
- `docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/bounded-mutation/tau-admission-receipt.PASS.json`
- `docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/bounded-mutation/tau-admission-receipt.BLOCKED-negative.json`
- `docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/effectful/effectful-governed-proof-receipt.PASS.json`
- `docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/effectful/tau-admission-receipt.json`
- `docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/cancellation/cancellation-proof-receipt.json`
- `docs/proofs/tickets/issue-222-governed-skill-live-proofs-20260728T155857Z/installed-wheel-current-head/current-wheel-governed-admission.json`

## Scope

`mocked`: no

`live`: yes

`provider_live`: yes for retained live skill/provider runs; false for the
current installed-wheel import/admission probe.

This proves the generic execution/admission mechanism for the representative
skills and failure controls named by #222. It does not prove every skill in
agent-skills is compatible, safe, semantically correct, or provider-live.
