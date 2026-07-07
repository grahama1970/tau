# Zero-Trust Basic Example

This example exercises Tau's local zero-trust preflight without dispatching a
subagent. It proves policy and data-boundary compatibility only.

```bash
./examples/zero-trust-basic/run.sh
```

or:

```bash
./examples/zero-trust-basic/run.sh /tmp/tau-zero-trust-basic
```

The script writes a `tau.zero_trust_preflight_receipt.v1` receipt under `/tmp`
or the requested output directory and prints the receipt path plus compact
status fields.

This does not prove ITAR compliance, sandbox enforcement, provider/model
semantic safety, GitHub mutation, or human identity verification.
