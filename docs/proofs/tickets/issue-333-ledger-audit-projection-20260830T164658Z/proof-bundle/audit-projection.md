# Tau Run Ledger Audit Projection

- Schema: `tau.run_ledger_audit_projection.v1`
- Run: `issue-333-proof-run`
- DAG: `issue-333-ledger-audit-projection`
- Goal hash: `sha256:issue-333-ledger-audit-projection`
- Ledger head: `sha256:06e8bfe51a8f3dba54e784737a42f6cf10ee8703365441cda7f2f6295e51dc7a`
- Policy: `tau.run_ledger_audit_policy.v1`
- Verifier: `tau.run_ledger_audit_verifier.v1`
- External anchor: `NOT_CONFIGURED`

| Seq | Event ID | Actor | Action | Time | Outcome | Object | Evidence digest |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | event-001 | coder | write_artifact | 2026-08-30T16:00:00Z | PASS | results/creator-artifact.json | sha256:52713d354c253cdf6fcb6a96af562ac27f0cb2d16e28950c0c178d6553e604fb |
| 1 | event-002 | reviewer | verify_artifact | 2026-08-30T16:01:00Z | PASS | run-ledger.json | sha256:a702c8700270194c2ed39b3a4ffd5625d3354c133f93c5f96ddfab08d312450e |
| 2 | event-003 | tau | audit_write_failure | 2026-08-30T16:02:00Z | FAIL | secondary-audit-sink | sha256:bf0eae87842f5d7421bae3912cd055267bcb5d67a6f01e50f5e24ee7d29663ba |
| 3 | event-004 | human | human_acceptance | 2026-08-30T16:03:00Z | ACCEPTED | issue-333-proof-run | sha256:47e01fd94d978c80a38f72b3dc294fa3a6e2a05d39a0f3541e4a2bc7ece489be |
