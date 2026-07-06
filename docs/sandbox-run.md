# Sandbox Run

Tau sandbox runs are a zero-trust execution gate for local commands. The rule is
fail closed: Tau must establish the requested sandbox boundary before it runs the
payload command.

Supported backends:

- `bwrap`: Bubblewrap with a network namespace.
- `docker`: strict Docker policy plus optional command execution.
- `docker-sbx`: Docker Sandboxes-style backend label using the same strict
  Docker policy surface.

If Tau cannot establish the requested backend boundary, it writes a blocked
`tau.sandbox_run_receipt.v1` and does not execute the command.

## Command

```bash
uv run tau sandbox-run \
  --policy-profile experiments/goal-locked-subagents/fixtures/zero-trust-policy.json \
  --data-boundary experiments/goal-locked-subagents/fixtures/itar-data-boundary.json \
  --out /tmp/tau-sandbox/sandbox-receipt.json \
  -- /usr/bin/python3 -c 'print("only runs if sandboxed")'
```

Docker backend:

```bash
uv run tau sandbox-run \
  --backend docker \
  --image python@sha256:<digest> \
  --policy-profile experiments/goal-locked-subagents/fixtures/zero-trust-policy.json \
  --data-boundary experiments/goal-locked-subagents/fixtures/itar-data-boundary.json \
  --out /tmp/tau-sandbox/docker-sandbox-receipt.json \
  -- python --version
```

## Required Policy Shape

`sandbox-run` requires a zero-trust local-only posture:

```text
network.default = deny
providers.cloud_llm = deny
research.external_search = deny
github.public_mutation = deny
data_boundary.external_provider_allowed = false
data_boundary.external_research_allowed = false
data_boundary.public_repo_allowed = false
```

If any of those checks fail, Tau blocks before probing the backend or running the
payload command.

Docker and Docker-Sandbox backends also require:

```text
image pinned by sha256 digest
network = none
not privileged
no host network
no docker.sock mount
read-only rootfs
cap-drop ALL
no-new-privileges
non-root user
no broad $HOME mount
```

## Receipt

The receipt schema is:

```text
tau.sandbox_run_receipt.v1
```

Important fields:

```json
{
  "schema": "tau.sandbox_run_receipt.v1",
  "status": "BLOCKED",
  "command_executed": false,
  "backend": {
    "name": "bwrap",
    "available": true,
    "probe": {
      "ok": false,
      "stderr": "..."
    }
  },
  "alert_codes": ["sandbox_backend_unavailable"]
}
```

## Proof Boundary

This lane can prove:

- Tau checked zero-trust sandbox policy before command execution.
- Tau blocked command execution when sandbox isolation could not be established.
- When a receipt is `PASS`, Tau executed the command through the recorded
  sandbox backend.
- Docker policy rejects unsafe container settings before Docker execution.

It does not prove:

- ITAR compliance
- export-control legal sufficiency
- human identity verification
- provider/model semantic safety
- security against kernel, backend, or host escape vulnerabilities
- network isolation unless the receipt is `PASS` and the backend probe passed
- Docker Sandboxes microVM availability unless the backend receipt records it

## Current Host Behavior

On hosts where Bubblewrap cannot create a network namespace, the correct result
is a blocked receipt with:

```text
sandbox_backend_unavailable
command_executed:false
```

That is not a failed sandbox escape. It is the expected fail-closed behavior
when Tau cannot establish the boundary.
