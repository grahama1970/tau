# Tau Secure Executor

Phase 2.2 makes Bubblewrap the first authoritative executor for secure Tau DAG
nodes.

## Execution Contract

For each secure handoff-loop node, Tau:

1. Selects grants for the compiled DAG node and current attempt.
2. Re-hashes the materialized policy profile and data boundary.
3. Checks a matching, unexpired `process.execute` grant.
4. Starts Bubblewrap with a new network namespace and no direct fallback.
5. Uses an empty `/work` and an empty-base environment.
6. Captures stdout and stderr as hashed sidecar artifacts.
7. Writes `tau.secure_execution_receipt.v1`.

The child receives only explicit Tau identifiers such as `TAU_RUN_ID`,
`TAU_DAG_ID`, `TAU_DAG_NODE_ID`, `TAU_DAG_ATTEMPT`, `TAU_GOAL_HASH`, and the
security-context hash. Tau does not copy the host environment in secure mode.

## Fail-Closed Conditions

Tau does not launch the command when:

- the backend is unsupported or cannot establish isolation;
- policy or boundary hashes changed;
- the `process.execute` grant is missing, expired, altered, or bound to another
  run, DAG, node, attempt, goal, policy, boundary, context, or command target;
- secure mode requests the bounded-ready-queue scheduler.

## First-Slice Limits

This slice intentionally supports system commands that can run in an empty
work directory. It does not yet provide grant-compiled source mounts, writable
output mounts, secret references, network allow grants, Docker backends, or
fresh grants for retry attempts. Those effects require their own enforcement
contracts rather than broad host access.

On a host where Bubblewrap cannot establish its network namespace, the expected
result is `BLOCKED`, `command_executed:false`, and
`sandbox_backend_unavailable`. That is containment evidence, not a successful
sandbox execution proof.

## Non-Claims

The secure execution receipt does not prove ITAR compliance, legal identity,
absence of kernel or Bubblewrap vulnerabilities, provider/model semantic
quality, or secure coverage of lower-level development commands.
