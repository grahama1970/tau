# Canonical Workflows

Tau ships five packaged DAG workflows so an evaluator can run the product
ladder without authoring a DAG by hand.

List the catalog:

```bash
uv run tau workflows list
uv run tau workflows list --json
```

The workflows are ordered by rung:

| Rung | Workflow | Topology | Purpose |
| --- | --- | --- | --- |
| 1 | `repository-readiness` | `LINEAR` | Inspect one repository and publish a readiness report. |
| 2 | `tau-operator-reference` | `MULTI_STEP_SEQUENTIAL` | Build and validate a Tau operator reference from local sources and public CLI probes. |
| 3 | `repository-evidence-map` | `FAN_OUT_FAN_IN` | Analyze documentation, tests, and package metadata concurrently, then publish one evidence map. |
| 4 | `approved-release-bundle` | `MIXED_RETRY_APPROVAL` | Prepare release evidence, revise release notes, and stop at a human approval gate before publishing. |
| 5 | `durable-repository-qualification` | `DURABLE_MIXED_REPAIR_APPROVAL` | Qualify evidence, preserve accepted work across recovery, repair one branch, and publish after exact approval. |

Run rung 1:

```bash
uv run tau workflows run repository-readiness \
  --repo /path/to/repository \
  --goal "Determine whether this checkout is ready for focused work." \
  --require-clean \
  --run-dir /tmp/tau-repository-readiness \
  --open-viewer
```

Run rung 2:

```bash
uv run tau workflows run tau-operator-reference \
  --repo /path/to/tau \
  --run-dir /tmp/tau-operator-reference \
  --open-viewer
```

Run rung 3:

```bash
uv run tau workflows run repository-evidence-map \
  --repo /path/to/repository \
  --goal "Map this repository for focused work." \
  --require-tests \
  --run-dir /tmp/tau-repository-evidence-map \
  --open-viewer
```

Run rung 4:

```bash
uv run tau workflows run approved-release-bundle \
  --repo /path/to/repository \
  --goal "Publish an approved local release bundle." \
  --publish-path /tmp/tau-approved-release-published/release.json \
  --run-dir /tmp/tau-approved-release-bundle \
  --open-viewer
uv run tau workflows approve /tmp/tau-approved-release-bundle \
  --approval-packet /path/to/human-approval.json
uv run tau workflows resume /tmp/tau-approved-release-bundle
```

Run rung 5:

```bash
uv run tau workflows run durable-repository-qualification \
  --repo /path/to/repository \
  --goal "Qualify this repository through durable recovery." \
  --publish-path /tmp/tau-durable-qualified \
  --run-dir /tmp/tau-durable-qualification \
  --open-viewer
uv run tau workflows repair /tmp/tau-durable-qualification --node qualify-tests
uv run tau workflows resume /tmp/tau-durable-qualification
uv run tau workflows approve /tmp/tau-durable-qualification \
  --approval-packet /path/to/human-approval.json
uv run tau workflows resume /tmp/tau-durable-qualification
```

These workflows are proof rungs for the active immutable goal. Running a rung
does not by itself prove the full immutable goal is complete.
