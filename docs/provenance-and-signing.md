# Provenance and Receipt Signing

Tau treats agent output as an untrusted claim. Provenance and signing add a
narrower review layer: they record who or what was declared for a run, what
environment controls were declared, and whether a receipt input changed after a
local shared-key signature envelope was written.

This is not an identity or compliance system.

## Schemas

This lane adds:

```text
tau.actor_manifest.v1
tau.environment_manifest.v1
tau.signed_receipt.v1
tau.signed_receipt_verification.v1
```

`tau.actor_manifest.v1` records declared run actors:

```json
{
  "schema": "tau.actor_manifest.v1",
  "run_id": "example-run",
  "actors": [
    {
      "actor_id": "coder",
      "actor_type": "agent",
      "roles": ["worker"],
      "trusted": false,
      "verified": false
    }
  ]
}
```

`tau.environment_manifest.v1` records declared environment controls:

```json
{
  "schema": "tau.environment_manifest.v1",
  "run_id": "example-run",
  "network_policy": "deny",
  "provider_access": "denied",
  "mounted_paths": [],
  "secrets_visible": [],
  "tool_versions": {}
}
```

`tau.signed_receipt.v1` is a local HMAC-SHA256 envelope over receipt input
hashes. Verification with the same local key can detect changed input files.
When `--actor-manifest` or `--environment-manifest` is supplied, signing first
validates those manifests against `tau.actor_manifest.v1` and
`tau.environment_manifest.v1`. Invalid provenance metadata produces a BLOCKED
signed-receipt envelope and no signature.

## Commands

Create an actor manifest:

```bash
uv run tau actor-manifest \
  --run-id example-run \
  --actor coder:agent:worker \
  --actor graham:human:approver \
  --out /tmp/tau-proof/actor-manifest.json
```

Create an environment manifest:

```bash
uv run tau environment-manifest \
  --run-id example-run \
  --network-policy deny \
  --provider-access denied \
  --mounted-path /tmp/tau-proof \
  --tool-version tau=0.1.0 \
  --out /tmp/tau-proof/environment-manifest.json
```

Sign a receipt:

```bash
uv run tau sign-receipt \
  --receipt /tmp/tau-proof/receipt.json \
  --key /tmp/tau-proof/local-signing-key.txt \
  --actor-manifest /tmp/tau-proof/actor-manifest.json \
  --environment-manifest /tmp/tau-proof/environment-manifest.json \
  --out /tmp/tau-proof/signed-receipt.json
```

Verify the envelope and current input hashes:

```bash
uv run tau verify-signed-receipt \
  --signed-receipt /tmp/tau-proof/signed-receipt.json \
  --key /tmp/tau-proof/local-signing-key.txt \
  --out /tmp/tau-proof/signed-receipt-verification.json
```

## Proof Boundary

This lane proves only narrow local properties:

- Tau can write actor and environment manifests with closed vocabulary fields.
- Tau can compute a local shared-key signature over receipt input hashes.
- Tau can detect changed signed input files when verification uses the same
  local key.

It does not prove:

- public-key non-repudiation
- human legal identity
- US-person or export-control eligibility
- ITAR compliance
- runtime sandbox enforcement
- provider/model semantic safety
- that a signed receipt claim is true

## High-Stakes Rule

Use this lane as a precondition for later high-stakes evidence packaging, not as
closure proof. A receipt can be signed and still be false. Tau still needs
policy/data-boundary gates, memory/evidence gates, evidence manifests,
side-effect approvals, sandbox enforcement, and human review for the relevant
claim.
