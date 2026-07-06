# Docker Sandbox Policy Gate

Tau's first Docker sandbox slice is a strict policy/command-builder gate. It
does not execute Docker. It writes `tau.sandbox_run_receipt.v1` with
`command_executed:false`.

Run it with:

```bash
uv run tau docker-sandbox-check \
  --image python@sha256:<64-hex-digest> \
  --receipt docker-sandbox-receipt.json \
  --command python --version
```

## Required Policy

The gate blocks unless the Docker request is constrained:

- image pinned by `sha256` digest;
- network is `none`;
- no host network;
- not privileged;
- no Docker socket mount;
- no broad `$HOME` mount;
- read-only root filesystem;
- `--cap-drop ALL`;
- `no-new-privileges:true`;
- non-root user.

When the policy passes, the receipt includes the Docker command Tau would run in
a later execution rung. The first slice deliberately does not run it.

## Non-Claims

This gate does not prove runtime sandbox isolation, Docker daemon availability,
Docker Sandboxes microVM availability, successful command execution, or ITAR
compliance. It proves only that Tau refused unsafe Docker policy before command
execution.
