# Tau Local API Hardening Roadmap

Tau's local API surfaces are inspection and proof helpers, not a production
multi-tenant control plane. This roadmap defines the hardening path before any
API surface should be treated as high-trust infrastructure.

## Current Boundary

- Local/self-hosted API surfaces are for operator inspection.
- Receipts remain the canonical source of truth.
- API responses must not upgrade proof claims beyond the underlying receipt.
- Browser/UI proof requires CDP or screenshot evidence, not API JSON alone.

## Required Hardening Before Production Use

| Area | Required control | Reason |
| --- | --- | --- |
| Binding | Localhost-only by default. | Prevent accidental LAN exposure. |
| Authentication | Explicit auth for non-localhost binding. | Prevent unauthenticated proof/control access. |
| Authorization | Role-based access for read, dispatch, apply, cleanup, and approve. | Separate inspection from mutation. |
| mTLS option | Optional client certificate gate for operator deployments. | Bind API access to machine/user identity. |
| Request receipts | Every mutating or approval-like request writes a receipt. | Keep API actions replayable and reviewable. |
| Policy-bound endpoints | Endpoint handlers must load the relevant policy profile. | Avoid bypassing CLI gates through API paths. |
| Redaction | API responses redact secrets, local tokens, and sensitive path/context fields. | Prevent accidental leakage into browser logs. |
| Rate limits | Local per-endpoint throttles for expensive provider/browser operations. | Avoid runaway agent loops. |
| Audit index | API writes proof-index entries for new receipts. | Make API-produced evidence discoverable. |

## Endpoint Classes

| Class | Examples | Default posture |
| --- | --- | --- |
| Read-only inspection | run status, proof index, receipt summaries | Allowed on localhost. |
| Local execution | DAG run, sanity check, browser proof | Requires local policy and explicit command invocation. |
| External side effect | GitHub apply, Memory sync, provider-live run | Requires approval, policy, redaction, and receipt gates. |
| Cleanup | Herdr GC, run-owned cleanup | Requires lease/approval and post-verify receipt. |

## Non-Claims

This roadmap does not implement:

- authentication;
- RBAC;
- mTLS;
- production deployment hardening;
- tenant isolation;
- legal identity proof;
- new trust semantics for existing receipts.

Until those controls exist, API output is an inspection surface over local proof
artifacts, not an authority that can replace Tau validators.
