# Memory Evidence Case Example

This example shows Tau's memory-first gate as a copyable local workflow:

```text
Graph Memory /intent
-> Graph Memory /create-evidence-case
-> tau.memory_intent_gate_receipt.v1
-> tau.evidence_case_gate_receipt.v1
```

Run it:

```bash
examples/memory-evidence-case/run.sh /tmp/tau-memory-evidence-case
```

The example writes:

```text
policy-profile.json
data-boundary.json
memory-intent.json
evidence-case.json
memory-intent-gate-receipt.json
evidence-case-gate-receipt.json
demo-receipt.json
```

This is local receipt evidence only. It does not prove Memory facts are true,
the evidence case is sufficient for closure, ITAR compliance, legal sufficiency,
provider/model quality, or semantic code correctness.
