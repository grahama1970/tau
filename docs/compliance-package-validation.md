# Compliance Package Validation

Tau compliance packages are review packages, not compliance certifications.

`compliance-package-validate` checks whether a package is ready for a reviewer:

```bash
uv run tau compliance-package-validate package/ \
  --receipt package-validation-receipt.json
```

The command writes `tau.compliance_package_validation_receipt.v1`.

## ITAR Local-Only Policy

The first policy is `itar-local-only`. It requires:

- `data-boundary.json`
- `policy-profile.json`
- `zero-trust-preflight-receipt.json`
- `memory-intent-gate-receipt.json`
- `evidence-case-gate-receipt.json`
- `evidence-validation-receipt.json`
- `sandbox-run-receipt.json`
- `actor-access-manifest.json`
- `environment-manifest.json`
- `signed-receipt-verification.json`
- `itar-access-preflight-receipt.json`
- `non-claims.md`

Critical receipts must be `PASS` or `VALID`. JSON artifacts must have the
expected schema. Goal hashes are checked for cross-package consistency.

For `itar-local-only`, the validator also checks the package contents, not only
file names:

- `data-boundary.json` must declare `classification:"ITAR"`,
  `itar:true`, `technical_data:true`, `export_controlled:true`,
  `external_provider_allowed:false`, `public_repo_allowed:false`, and
  `foreign_person_access:"prohibited"`.
- `policy-profile.json` must default to deny, require a data boundary, and
  deny or explicitly gate cloud provider use and public GitHub mutation.
- `actor-access-manifest.json` must describe a trusted, verified human actor
  with `eligibility.us_person:"verified"`, `foreign_person:false`, current
  export-control training metadata, and ITAR boundary approval.
- `signed-receipt-verification.json` must include at least one verified signed
  receipt, using one of `verified_count`, `verified_receipt_count`,
  `valid_signature_count`, or `signature_count`.
- Any receipt that cites `data_boundary_sha256` must cite the actual hash of
  the packaged `data-boundary.json`.
- If `coding-evidence-receipts/` is present, every JSON receipt in it must use
  a supported Tau coding evidence schema, have `status:"PASS"` or
  `status:"VALID"`, not set `ok:false`, not be marked `mocked:true`, and must
  participate in the same goal-hash and data-boundary-hash consistency checks
  as the critical package receipts.

The validation receipt uses:

```json
{
  "review_ready": true,
  "compliant": "NOT_CLAIMED"
}
```

It never emits `compliant:true`.

## Non-Claims

Package validation does not prove ITAR compliance, export-control legal
sufficiency, legal identity, truth of package contents, or provider/model
semantic quality. It only proves that Tau inspected the package shape and found
the configured review-readiness artifacts present and internally consistent.
