# GitHub Review Access

This document records the recommended GitHub posture for the first Tau external
colleague review branch.

Recommended access model:

- external colleagues: read-only;
- internal implementers: write or maintain;
- `main`: protected;
- `external-review/airgap-sparta-v0`: review surface for the first synthetic
  air-gapped Sparta Explorer demo;
- `tau-embry-sparta-review-v0.1`: frozen tag after the first review bundle is
  accepted.

Recommended repository settings:

- protect `main`;
- require pull requests for `main`;
- require status checks before merge;
- require CODEOWNERS review for `docs/security`, `docs/briefs`, demo, and
  policy-boundary changes;
- keep live GitHub mutation disabled in the synthetic airgap demo;
- document milestones, labels, and issue backlog before creating live GitHub
  milestones, labels, or issues.

For PR 1, approved live GitHub mutation is limited to creating and pushing the
review branch. Milestones, labels, and issues are documented in
`docs/external-review-backlog.md` and require later explicit approval before
creation.
