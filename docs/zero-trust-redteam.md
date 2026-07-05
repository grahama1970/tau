# Zero-Trust Red-Team Suite

Tau's zero-trust red-team suite is a deterministic adversarial containment
check. It does not run a raw agent swarm. It calls Tau's existing gates with
malicious or incompatible inputs and passes only when Tau blocks the path with
the expected alert.

## Command

```bash
uv run python scripts/run-zero-trust-redteam.py \
  --out-dir /tmp/tau-zero-trust-redteam
```

The command writes:

```text
/tmp/tau-zero-trust-redteam/zero-trust-redteam-receipt.json
```

Receipt schema:

```text
tau.zero_trust_redteam_receipt.v1
```

## Covered Attempts

The first suite covers these malicious or unsafe paths:

```text
skip_memory_intent
inline_fake_evidence
clarify_route_dispatch
evidence_case_boundary_mismatch
external_provider_request
external_research_request
public_repo_mutation_request
tampered_signed_receipt
sandbox_backend_missing
```

Each attempt is marked PASS only when the relevant Tau gate returns a blocked
receipt or verification result with the expected alert/error.

## Proof Boundary

This suite proves:

- Tau ran deterministic adversarial checks against zero-trust gates.
- Tau observed expected fail-closed alerts for the covered malicious paths.

It does not prove:

- ITAR compliance
- export-control legal sufficiency
- runtime sandbox enforcement on the host
- provider/model semantic safety
- coverage of every possible malicious agent path
- that a DAG or agent swarm is trustworthy

## Extension Rule

Add new malicious attempts by connecting them to a real Tau gate first. Do not
add a red-team case that only checks a string in a synthetic fixture. The suite
is useful only when it exercises the same validators, preflight receipts, or
runtime gates that production DAG dispatch would use.
