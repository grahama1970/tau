# Tau Zero-Trust Threat Model

Tau treats agent work as untrusted until receipts, artifacts, policies, and
validators make a specific claim admissible. This threat model names the
failure modes Tau is designed to contain and the proof boundaries that still
matter.

## Assets

- Immutable human goals and goal hashes.
- DAG contracts, node routes, retries, and stop conditions.
- Local worktrees, generated artifacts, receipts, and proof ledgers.
- Memory intent/recall products and any future Memory writes.
- Herdr workspaces, panes, provider sessions, visible logs, and cleanup leases.
- GitHub projections, labels, comments, issue state, and apply approvals.
- Policy profiles, data boundaries, approval packets, and command specs.

## Actors

| Actor | Trust level | Notes |
| --- | --- | --- |
| Human operator | Highest local authority | Owns immutable goal changes and high-risk approvals. |
| Tau runtime | Trusted computing base | Must validate inputs and write receipts before acting. |
| Project agent | Untrusted worker | May propose DAGs, code, docs, and next steps. |
| Reviewer agent | Untrusted checker | Useful signal, not a trust anchor. |
| Provider/model | Untrusted generator | May hallucinate, omit evidence, or follow hostile context. |
| Herdr pane text | Untrusted telemetry | Visible and useful, but never canonical proof. |
| External research | Design input | Requires receipts and reviewer reconciliation before it affects work. |

## Threats And Controls

| Threat | Example | Tau control | Remaining boundary |
| --- | --- | --- | --- |
| Agent hallucination | Agent claims tests passed without a receipt. | Required receipts, command logs, evidence manifests, and explicit non-claims. | Semantic quality still needs review and deterministic checks. |
| Goal drift | Subagent rewrites the goal or routes to unrelated work. | Immutable goal hash validation and human-only goal-change packets. | Human must reject bad goal-change proposals. |
| False consensus | Multiple agents agree without evidence. | Receipts and validators outrank agent consensus. | Independent dissent motifs need continued hardening. |
| Responsibility diffusion | No agent owns a failed route. | DAG receipts record node attempts, errors, route, and course correction. | Operator still decides acceptance and scope changes. |
| Memory poisoning | Untrusted content alters future recall or routing. | Memory writes require approval policy; route-memory sync is separate from local signals. | Strong Memory identity/provenance controls remain future work. |
| Evidence spoofing | Artifact path exists but hash/schema/goal does not match. | `tau.evidence_manifest.v1` validates hash, schema, kind, validator namespace, and goal hash. | Artifact semantic truth is not proven by metadata alone. |
| Receipt spoofing | Provider writes a receipt for the wrong work order. | Provider node receipts bind goal, node, attempt, workspace, pane, terminal, and work-order hash. | Provider/model text remains untrusted. |
| Herdr pane injection | Visible terminal text imitates success. | Pane output is evidence, not truth; Tau requires canonical JSON receipts. | Herdr protocol/API hardening remains an ongoing dependency. |
| Provider exfiltration | DAG uses cloud provider despite local-only policy. | Policy/data-boundary preflight blocks incompatible provider usage. | Runtime sandbox and network enforcement are separate proof rungs. |
| Research leakage | Sensitive goal sent to external search. | External research is policy-gated and receipt-backed. | Operator must configure research policy correctly. |
| GitHub public mutation | Agent posts sensitive data or closes wrong issue. | GitHub transport is dry-run by default; apply requires policy, approval, preflight, and redaction receipts. | Live GitHub mutation remains a high-risk approval boundary. |
| Filesystem escape | Command writes outside allowed scope. | Command-spec policy and branch locks constrain local command lanes. | This is not a kernel sandbox. |
| Sandbox backend failure | Host isolation is missing or misconfigured. | Sandbox receipts must fail closed when required backend is unavailable. | Tau does not claim platform isolation without runtime proof. |
| Human approval forgery | Agent fabricates an approval packet. | Approval-gate receipts validate action, target, expiry, and packet fields. | Strong public-key identity/non-repudiation remains future work. |
| Cleanup damage | GC closes current or unrelated workspaces. | Herdr cleanup requires leases/approval, protects focused workspaces, and verifies absence after apply. | Arbitrary non-Tau cleanup is not proven. |

## Admissibility Rule

Tau should treat a claim as admissible only when the relevant layer has a
receipt or artifact that names:

- schema and version;
- goal hash and target;
- actor, node, attempt, or approval identity;
- input artifact hashes;
- command or provider evidence;
- status, verdict, and errors;
- proof scope and non-claims.

An admissible claim is still not global truth. It only means the claim passed
the relevant Tau gate for the stated proof boundary.

## Non-Claims

This threat model does not prove:

- ITAR or export-control legal sufficiency;
- provider/model semantic quality;
- future route correctness;
- public-key legal identity;
- arbitrary production repository mutation safety;
- runtime sandbox isolation unless a sandbox receipt exists;
- unbounded autonomous operation.
