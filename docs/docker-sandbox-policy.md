# Docker Sandbox Policy And Runtime Gate

Tau's Docker sandbox lane starts with a strict policy/command-builder gate and
can now run an explicitly requested runtime command. It writes
`tau.sandbox_run_receipt.v1` in both modes.

Policy-only check:

```bash
uv run tau docker-sandbox-check \
  --image python@sha256:<64-hex-digest> \
  --receipt docker-sandbox-receipt.json \
  --command python --version
```

Runtime execution:

```bash
uv run tau docker-sandbox-run \
  --image busybox@sha256:<64-hex-digest> \
  --receipt sandbox-run-receipt.json \
  --timeout 20 \
  --command sh -c 'id -u; echo tau-sandbox-runtime'
```

`docker-sandbox-check --execute` is equivalent to `docker-sandbox-run`.

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
- non-root user;
- positive timeout when execution is requested.

When the policy-only check passes, the receipt includes the Docker command Tau
would run and records `command_executed:false`. When runtime execution is
requested, Tau runs only after policy passes, captures stdout/stderr artifacts,
records the Docker container id from `--cidfile`, and blocks on Docker
unavailability, timeout, or non-zero exit.

## Non-Claims

The policy-only check does not prove runtime sandbox isolation, Docker daemon
availability, Docker Sandboxes microVM availability, successful command
execution, or ITAR compliance. A runtime receipt proves only that Tau executed a
specific constrained Docker command and captured its local artifacts; it still
does not prove Docker Sandboxes microVM isolation, ITAR compliance, legal
sufficiency, provider/model semantic quality, or arbitrary agent safety.
