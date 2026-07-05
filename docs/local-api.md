# Local API

Tau exposes a minimal local/self-hosted HTTP API for integration tests and
operator tooling:

```bash
uv run tau serve --host 127.0.0.1 --port 8768
```

The API is a thin adapter over existing Tau commands and receipt writers. It
does not add new trust semantics, does not allocate providers by itself, and
does not make Tau production-deployment ready.

## Endpoints

```text
GET  /health
POST /doctor
POST /zero-trust/preflight
POST /memory-evidence/preflight
POST /dag/run
GET  /runs/{id}
GET  /runs/{id}/status
GET  /runs/{id}/receipts
POST /runs/{id}/compliance-package
```

`{id}` is a URL-encoded local run directory path. This is intentionally a local
operator API, not a multi-tenant public API.

## Request Examples

Zero-trust preflight:

```json
{
  "policy_profile": "/path/to/policy-profile.json",
  "data_boundary": "/path/to/data-boundary.json",
  "dag_contract": "/path/to/dag-contract.json",
  "receipt": "/path/to/zero-trust-preflight-receipt.json"
}
```

Memory/evidence preflight:

```json
{
  "policy_profile": {
    "schema": "tau.policy_profile.v1",
    "profile_id": "itar-zero-trust-local-only",
    "default_decision": "deny",
    "memory": {"intent_required": true}
  },
  "data_boundary": {"schema": "tau.data_boundary.v1"},
  "memory_intent": {"schema": "memory.intent.v1", "memory_first": true},
  "evidence_case": {"schema": "memory.evidence_case.v1"},
  "receipt_dir": "/path/to/receipts"
}
```

Compliance package for a run:

```json
{
  "out": "/path/to/package",
  "force": false
}
```

## Non-Claims

This API does not prove:

```text
production deployment readiness
authentication or RBAC
tenant isolation
runtime sandbox enforcement
provider/model semantic quality
ITAR compliance
export-control legal sufficiency
```

Use it as a local integration surface for existing receipt-backed Tau lanes.
