# ITAR-Grade Containment Demo

This example shows Tau blocking unsafe controlled-boundary actions before any
external side effect.

Run:

```bash
./run.sh
```

or:

```bash
./run.sh /tmp/tau-itar-grade-containment-demo
```

The demo writes receipts under the output directory.

## What It Exercises

1. An external research query that includes controlled artifact text.
2. An ITAR actor/access preflight with an unverified actor.
3. A Docker sandbox request that tries to mount the Docker socket.
4. A corrected Docker sandbox policy check that builds but does not execute a
   Docker command.
5. A package review-readiness validation that returns `review_ready:true` and
   `compliant:"NOT_CLAIMED"`.

## Non-Claims

This example does not prove ITAR compliance, legal identity, external research
truth, live Docker isolation, live provider execution, GitHub mutation, Memory
sync, or browser UI behavior.
