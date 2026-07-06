# Zero-Trust Red-Team Suite

Tau's zero-trust red-team suite is a deterministic local containment check. It
does not call external providers, research services, Memory, GitHub, Docker, or
browsers.

Run it with:

```bash
uv run tau zero-trust-redteam --run-dir /tmp/tau-zero-trust-redteam
```

The command writes:

```text
/tmp/tau-zero-trust-redteam/zero-trust-redteam-receipt.json
```

with schema `tau.zero_trust_redteam_receipt.v1`.

## Current Attempts

The first suite passes only when Tau blocks each malicious fixture:

- controlled snippet in an external research query;
- foreign-person actor on an ITAR boundary;
- unverified human approval actor;
- cloud provider branch under a provider-deny policy;
- public mutation under a GitHub public-mutation deny policy;
- blocked signed-receipt verification in a review package.

Each attempt records the expected alert code and the observed alert codes.

## Non-Claims

This suite is not exhaustive malicious-agent coverage. It does not prove ITAR
compliance, runtime sandbox isolation, provider/model semantic quality, live
GitHub mutation safety, or Docker isolation.
