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
For course-correction receipts it also surfaces route fields such as `trigger`
and `required_next_action`; for GitHub read receipts it surfaces `uri`,
`github_read_kind`, `read_only`, and `mutation_allowed` so read evidence is
not confused with mutation authority.
For debugger receipts it surfaces `debug_adapter`, `debug_target`,
`adapter_available`, `log_artifact_count`, and `variable_redaction_count` so
debug evidence is inspectable without implying the debug conclusion is complete.
For commit-plan receipts it surfaces `dry_run`, `apply_requested`,
`apply_eligible`, `changed_file_count`, `group_count`,
`evidence_receipt_count`, `approval_required`, and `high_risk_path_count` so
reviewers can see whether a plan is only a dry-run proposal or apply-eligible.
For LSP receipts it surfaces `lsp_language_server`, `file_count`,
`diagnostic_count`, `diagnostics_increased`, `reference_count`,
`rename_symbol`, `rename_new_name`, `rename_applied`, `planned_edit_count`,
`policy_read_denied_count`, and `policy_write_denied_count` so diagnostics and
rename plans are visible without claiming semantic correctness.
For review findings receipts it surfaces `review_declared_verdict`,
`review_derived_verdict`, `reviewer`, `finding_count`,
`blocking_finding_count`, `revision_finding_count`, `p0_finding_count`,
`p1_finding_count`, and `required_action_count` so reviewer output is
inspectable without trusting the reviewer as correct.

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
