# Issue #105 Live DAG Viewer Parent Proof

Ticket: https://github.com/grahama1970/tau/issues/105

## Scope

Parent closure proof for the Tau-owned receipt-backed DAG viewer fleet.

The Tau-side ordered children were already reported complete on #105:

- #106 authoritative replay and live projection
- #107 loopback-only read-only server
- #108 packaged React Flow viewer

The remaining external blocker named on #105 was:

- https://github.com/grahama1970/agent-skills/issues/110

That external UX Lab wrapper child is now closed with proof:

- `grahama1970/agent-skills#110`: `CLOSED`
- agent-skills remote main: `3246fc052e02aef99a57e65406409e55bb663f1e`
- proof file:
  `docs/proofs/tickets/issue-110-tau-dag-view-delegation-20260726.md`

## Deterministic Checks

External wrapper proof command:

```bash
skills/ux-lab/tests/test_tau_dag_wrapper.sh
```

Result:

```text
PASS: TAU_BIN override delegates exact arguments
PASS: Tau is discovered from PATH
PASS: missing Tau blocks
PASS: wrong capability schema blocks
PASS: read_only=false blocks
PASS: Tau dag-view exit code is preserved
PASS: wrapper contains no copied viewer implementation
PASS: UX Lab runner remains a thin launcher
Results: 8 passed, 0 failed
```

Remote verification:

```text
agent-skills main: 3246fc052e02aef99a57e65406409e55bb663f1e refs/heads/main
agent-skills#110: CLOSED
```

## Evidence Classification

mocked: yes, for the UX Lab fake-Tau wrapper test.

live: yes, for GitHub issue state and remote ref verification.

What was actually exercised: parent dependency closure, UX Lab wrapper
delegation behavior, source-authority checks, and remote-ref verification.

What remains unverified here: new live browser screenshots of the Tau viewer
itself. Those belong to the already completed Tau child proof chain.
