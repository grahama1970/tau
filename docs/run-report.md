# Static Run Report

Tau run reports render existing run artifacts into a single static HTML file.
They are for inspection and review, not live operational monitoring.

```bash
uv run tau report <run-dir> --out report.html
```

Use `--force` only when replacing an existing report is intentional:

```bash
uv run tau report <run-dir> --out report.html --force
```

The command writes:

```text
report.html
report.html.receipt.json
```

The receipt schema is:

```text
tau.run_report_receipt.v1
```

## Sections

The report is generated from `tau run-status` plus known run artifacts such as
`dag-receipt.json` and its `contract_path`.
The report receipt records `source_artifacts` with SHA-256 hashes and byte
counts for those source artifacts when they exist, so reviewers can identify
the exact DAG receipt and contract rendered into the static HTML.

Rendered sections:

```text
goal
policy
data boundary
memory intent
evidence case
DAG steps
coding evidence
receipts
blocked / allowed decisions
non-claims
```

The `coding evidence` section scans the run directory for Tau coding receipt
schemas such as patch, LSP, focused test-run, review findings, commit-plan,
debug, GitHub read, OMP/SciLLM worker, course-correction, and orchestration
reliability receipts. It records relative path, schema, status, `ok`,
`mocked`, `live`, `provider_live`, receipt SHA-256, goal hash, and any
policy/data-boundary hashes present on the receipt.

Missing source artifacts render as `null` or empty objects. The report must not
invent live status, metrics, approvals, or proof.

## Non-Claims

The report does not prove:

```text
ITAR compliance
export-control legal sufficiency
complete sandbox enforcement
human identity verification unless a provenance receipt exists
provider/model semantic quality
Memory fact truth
evidence-case sufficiency for closure
DAG or swarm trustworthiness
```

The report is static HTML. If a future UI serves or augments this report, that
UI needs its own browser/CDP visual proof.
