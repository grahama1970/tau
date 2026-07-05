# Compliance Evidence Package

Tau compliance evidence packaging collects existing run receipts and source
artifacts into one review directory. It does not make a run compliant, and it
does not upgrade agent claims into truth.

Use it after zero-trust preflight and memory/evidence-case gates have produced
stable receipts:

```bash
uv run tau compliance-package <run-dir> --out <package-dir>
```

Use `--force` only when replacing an existing package directory is intentional:

```bash
uv run tau compliance-package <run-dir> --out <package-dir> --force
```

## Package Contents

The package writes:

```text
package-manifest.json
dag-receipt.json
dag-contract.json
goal.json
policy-profile.json
data-boundary.json
zero-trust-preflight-receipt.json
memory-intent-gate-receipt.json
evidence-case-gate-receipt.json
evidence-validation-receipt.json
command-policy-receipts/
research-source-receipts/
approval-receipts/
herdr-lease-receipts/
github-apply-policy-receipts/
browser-cdp-proof-receipts/
sandbox-receipts/
non-claims.md
```

Some entries may be absent when the source run did not produce that receipt.
Absence is recorded in `package-manifest.json` under `missing_expected_items`.

## Manifest

`package-manifest.json` uses:

```text
tau.compliance_evidence_package.v1
```

It records:

```text
run_dir
package_dir
item_count
missing_expected_items
items[].kind
items[].path
items[].sha256
items[].source_path
items[].source_sha256
items[].schema
```

The manifest is a review index. It is not a validator for evidence sufficiency.

## Non-Claims

The package writes `non-claims.md` and repeats the same boundaries in the
manifest proof scope.

It does not prove:

```text
ITAR compliance
export-control legal sufficiency
complete sandbox enforcement
human identity verification unless a provenance receipt exists
provider/model semantic quality
that Memory facts are true
that an evidence case is sufficient for closure
that a DAG or agent swarm is trustworthy
```

## Trust Model

This package assumes agent output is an untrusted claim. It collects receipts so
a human or downstream reviewer can inspect what Tau checked, copied, derived, or
could not find.

The package itself does not authorize side effects, change goals, promote
Memory to truth, or close a high-stakes workflow.
