# Tau #180 Durable Qualification Browser Proof

Issue: https://github.com/grahama1970/tau/issues/180

Source ref under test:

```text
0f948d4499ff2da74821cff1651c20cf0cf2119a
```

## Command

```text
PYTHONPATH=src uv run python scripts/run-durable-qualification-browser-proof.py --output docs/proofs/tickets/issue-180-durable-qualification-browser-20260727/durable-qualification-browser-proof.json --desktop-screenshot docs/proofs/tickets/issue-180-durable-qualification-browser-20260727/durable-qualification-desktop.png --mobile-screenshot docs/proofs/tickets/issue-180-durable-qualification-browser-20260727/durable-qualification-mobile.png
```

## Retained Artifacts

- `durable-qualification-browser-proof.json`
- `durable-qualification-desktop.png`
- `durable-qualification-mobile.png`

## Observed Result

```text
status: PASS
mocked: false
live: true
provider_live: false
request_methods: GET
desktop: 1440x1000 sha256:5ac9dd66b2e692873fa105714dc3b195d2ea2dd4ea25122acbd83957a94e6fe6
mobile: 390x1898 sha256:1b3fd986541f63ead6f6b1f096482adc16629b517062a73bf2d4f495ac71fdaf
```

All proof JSON checks were true:

```text
approval_required_visible
desktop_layout_non_overlapping
final_result_visible
goal_visible
journal_recovery_order
mobile_layout_non_overlapping
no_manual_reload
parallel_branches_running
publication_effect_count_one
read_only_requests
recovery_takeover_visible
repaired_branch_running
targeted_repair_blocker_visible
unaffected_branches_accepted
workflow_title_visible
```

The retained observations show the durable workflow moving through:

```text
discover/initial run -> parallel branches -> targeted repair blocker
-> recovery generation -> approval blocker -> recovery generation
-> publish -> finalize -> run_completed at journal sequence 152
```

## Proof Boundary

This proves a live local browser/viewer session can show a durable Tau DAG that
blocks, recovers, requires approval, resumes, and reaches an accepted final
result without manual reload, with retained desktop and mobile captures.

This does not prove provider semantic correctness; no provider call was made in
this proof.
