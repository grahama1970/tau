# ITAR-Shaped Actor/Access Boundary

Tau treats ITAR-shaped work as a controlled-boundary lane. The actor/access
preflight is a deterministic metadata gate, not a compliance certification.

## Command

```bash
uv run tau itar-access-preflight \
  --actor-manifest actor-access-manifest.json \
  --data-boundary data-boundary.json \
  --approval-packet approval-packet.json \
  --receipt itar-access-preflight-receipt.json
```

The command writes `tau.itar_access_preflight_receipt.v1`.

## Actor Manifest

```json
{
  "schema": "tau.actor_access_manifest.v1",
  "actor_id": "human:graham",
  "actor_type": "human",
  "roles": ["approver"],
  "trusted": true,
  "verified": true,
  "eligibility": {
    "us_person": "verified",
    "foreign_person": false,
    "export_control_training_current": true,
    "approved_for_boundary": ["ITAR"]
  }
}
```

## Fail-Closed Conditions

For an ITAR data boundary, Tau blocks when:

- the actor manifest is missing or malformed;
- the actor is not trusted or verified;
- `eligibility.us_person` is not `verified`;
- `eligibility.foreign_person` is true;
- export-control training metadata is not current;
- the actor is not approved for `ITAR`;
- a non-human actor declares the `approver` role;
- an approval packet names a different actor.

## Non-Claims

This gate does not prove ITAR compliance, legal identity, U.S.-person status,
human non-repudiation, export-control legal sufficiency, or runtime sandbox
enforcement. It only proves Tau refused to proceed without the declared metadata
required by the active controlled-boundary policy.
