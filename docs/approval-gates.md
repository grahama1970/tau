# Tau Approval Gates

Tau uses explicit approval packets before gated mutation or closure actions.
The gate is separate from the action: it only decides whether a requested action
is approved.

## CLI

```bash
uv run tau approval-gate-check \
  --approval-packet approval.json \
  --requested-action working_tree_mutation \
  --run-dir proof/run
```

Supported actions:

- `dag_expansion_apply`
- `github_apply`
- `working_tree_mutation`
- `github_ticket_closure`
- `herdr_cleanup_apply`
- `memory_upsert`
- `provider_branch_scheduling`

## Approval Packet

```json
{
  "schema": "tau.human_approval_packet.v1",
  "approved": true,
  "action": "working_tree_mutation",
  "actor": {
    "id": "human:graham",
    "auth_method": "manual"
  },
  "target": {"id": "scratch-run"},
  "reason": "Approve bounded scratch mutation proof only.",
  "evidence": ["run-receipt.json"],
  "nonce": "approval-nonce-001",
  "signature": "manual-signature-record",
  "expires_at": "2026-07-04T00:00:00Z"
}
```

`actor.auth_method` must be one of `manual`, `local-signature`, or
`github-comment`. `nonce` and `signature` are required provenance fields.
Tau records whether a signature field is present, but this gate does not perform
cryptographic signature verification. `expires_at` is optional. When present,
Tau parses it as an ISO-8601 timestamp and fails closed after that time. Invalid
timestamps also fail closed.

## Receipt

The command writes `tau.approval_gate_receipt.v1` to:

```text
<run-dir>/approval-gate-receipt.json
```

`PASS` means the packet explicitly approves the requested action. `BLOCKED`
means the requested action must not run.

Receipts include `approval_packet_sha256`, the SHA-256 of the exact approval
packet file Tau evaluated. Downstream mutation or closure commands can record
and compare this hash before crossing a gate.

`tau run-status <run-dir>` exposes the approval packet path plus compact packet
summary fields: approved action, actor id, actor auth method, target id,
evidence count, nonce, signature presence, expiration timestamp, and approval
packet SHA-256.

## Boundary

Approval-gate receipts do not execute mutation, close GitHub tickets, verify
cryptographic signature validity, or approve production repository mutation by
themselves. They are preconditions that downstream commands must check before
crossing those boundaries.
