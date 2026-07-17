# Tau Canonical Workflow Slice 03

**Status:** Active
**Owner:** Human
**Goal ID:** tau-canonical-workflow-slice-03
**Goal Version:** 1

## Immutable Goal

From one command, a human can generate a validated repository evidence map
while watching documentation, test, and package analyses run concurrently and
join only after every required branch is accepted.

## Completion Criteria

1. Tau exposes exactly three packaged canonical workflows.
2. `repository-evidence-map` runs one inventory node, three concurrent analysis
   nodes, and one publishing join.
3. Every branch is bound to the same inventory hash and human goal hash.
4. The join validates all accepted branch schemas and hashes before atomically
   publishing JSON and Markdown results.
5. A missing required test surface blocks with `test_surface_missing`, never
   dispatches the publisher, and produces no result.
6. The existing viewer visibly shows all three branches running in one
   authoritative snapshot without reload.
7. Positive, negative, desktop, mobile, and installed-wheel proof reports
   `mocked: false`, `live: true`, and `provider_live: false`.

## Locked Graph

```text
inventory-repository
  +-> analyze-documentation -+
  +-> analyze-tests ---------+-> publish-evidence-map
  +-> analyze-package -------+
```

No routes, retries, approvals, side effects, provider calls, network calls,
scheduler changes, viewer changes, or fourth workflow are in this slice.
