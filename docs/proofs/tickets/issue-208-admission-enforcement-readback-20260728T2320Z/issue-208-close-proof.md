# Issue #208 Close Proof

Issue: https://github.com/grahama1970/tau/issues/208

Current implementation readback:

```text
git merge-base --is-ancestor 47477277 HEAD
ancestor:0
```

Harness added for retained proof:

```text
scripts/admission-enforcement-live.py
```

Live harness command:

```text
PYTHONPATH=src uv run python scripts/admission-enforcement-live.py docs/proofs/tickets/issue-208-admission-enforcement-readback-20260728T2320Z
```

Observed live receipt fields:

```text
schema: tau.admission_enforcement_harness_receipt.v1
mocked: false
live: true
provider_live: false
ok: true
run_status: BLOCKED
run_verdict: RECEIPT_NOT_ADMITTED
node_status: BLOCKED
node_verdict: RECEIPT_NOT_ADMITTED
accepted_pass_state_present: false
system_settlement_admission_rows: 1
```

SQLite readback:

```text
sqlite3 docs/proofs/tickets/issue-208-admission-enforcement-readback-20260728T2320Z/dag-run.sqlite3 \
  "SELECT run_id,node_id,receipt_kind,sha256 FROM receipt_admissions; SELECT status,verdict FROM dag_runs WHERE run_id='run-enforce-live';"

run-enforce-live|liar|system_settlement|sha256:5246aeeecb96630e945f9cfb9c8457e36050aedc76ba90000f0394b7c38b4b9a
BLOCKED|RECEIPT_NOT_ADMITTED
```

Focused deterministic regression:

```text
uv run pytest -q tests/test_dag_runtime_admission_table.py::test_enforcement_blocks_pass_claim_with_torn_receipt
```

Observed result:

```text
1 passed in 0.45s
```

Retained artifacts:

```text
docs/proofs/tickets/issue-208-admission-enforcement-readback-20260728T2320Z/admission-enforcement-receipt.json
docs/proofs/tickets/issue-208-admission-enforcement-readback-20260728T2320Z/bypass-spec.json
docs/proofs/tickets/issue-208-admission-enforcement-readback-20260728T2320Z/dag-run.sqlite3
docs/proofs/tickets/issue-208-admission-enforcement-readback-20260728T2320Z/receipts/liar/attempt.json
docs/proofs/tickets/issue-208-admission-enforcement-readback-20260728T2320Z/receipts/liar/attempt-46ff070d55d8eb396e7ef536d157a2cf-system-settlement.json
```

Scope boundary:

This proves the scheduler-level admission invariant for a synthetic PASS
writer whose receipt cannot be admitted. It does not claim provider/model
semantic quality or parent #72 closure.
