# T’au — Memory-First Zero-Trust Agent Harness

<p align="center">
  <img
    src="docs/assets/tau-header.webp"
    alt="T’au agentic harness console in a science-fiction workspace"
    style="max-width: 100%; height: auto; display: block;"
  />
</p>

> **Agents can work. Tau decides what counts.**

T’au is an experimental, memory-first, zero-trust harness for untrusted agent
work. It assumes agents hallucinate, drift, overclaim, and can launder bad
answers through reviewers, DAGs, or swarms. Tau does not try to make agents
trustworthy. It makes their work bounded, inspectable, rejectable, and gated by
receipts.

The core rule is simple:

```text
agent output = untrusted claim
memory intent = why this work is being routed
evidence case = what supports the route
DAG contract = allowed claim path
receipt = recorded claim
validator/policy = narrow yes/no gate
human = owner of goals and high-risk approvals
```

Tau is not an ITAR compliance system, a secure autonomous agent platform, or a
replacement for legal/security review. It is a containment and audit harness that
can operate inside an approved high-stakes environment.

## Why Tau exists

Raw agents are not trustworthy. Agent DAGs and swarms are not trustworthy either.
A graph can organize hallucination just as easily as it can organize useful work.
Tau’s job is to make that graph useful without pretending it is proof.

Tau is useful when you need to ask:

- What goal was the agent supposed to preserve?
- What memory route or evidence case justified this work?
- What data boundary and policy profile applied?
- Which files, commands, tools, providers, or external services were allowed?
- What receipt did the agent produce?
- What validator accepted or rejected that receipt?
- What exactly does this proof not prove?

If you only need a quick agent demo, Tau is probably too strict. If a bad agent
step could leak data, mutate the wrong system, poison memory, fake evidence, or
claim closure without proof, Tau is the kind of harness you want around it.

## What Tau is

Tau has three related layers:

| Layer | Purpose |
| --- | --- |
| **Coding runtime** | The installable `tau` CLI, Textual TUI, provider config, local tools, sessions, and print-mode execution. |
| **Agentic harness** | Goal-locked handoffs, project DAGs, evidence manifests, command policies, GitHub projections, and fail-closed receipts. |
| **Zero-trust control plane** | Policy/data-boundary preflight, Graph Memory intent/evidence-case gates, sandbox receipts, provenance, package/report generation, and adversarial checks. |

The harness is deliberately separate from normal chat. A subagent does one
bounded step, emits a schema-valid handoff or receipt, names the next route, and
stops. Long-running behavior comes from an orchestrator repeatedly invoking
bounded steps, not from an unbounded model loop.

## What Tau is not

Tau does **not** claim:

- agents are reliable;
- reviewer agents are trust anchors;
- DAG or swarm consensus is proof;
- memory facts are automatically true;
- research sources are closure evidence;
- route memory proves future route correctness;
- local browser proof is production chat readiness;
- GitHub policy proof is live GitHub mutation safety;
- sandbox proof is legal/export-control sufficiency;
- Tau itself makes a system ITAR compliant.

Tau’s strongest feature is that it keeps these non-claims visible.

## Quickstart

Install and run the CLI:

```bash
git clone https://github.com/grahama1970/tau.git
cd tau
uv sync
uv run tau doctor
uv run tau --help
```

Create a local zero-trust starter project:

```bash
uv run tau init --profile zero-trust --out /tmp/tau-init-zero-trust
```

Run the checked-in zero-trust preflight example:

```bash
cd examples/zero-trust-basic
./run.sh
```

The example writes `out/zero-trust-preflight-receipt.json`. It proves only that
Tau can inspect the example policy/data-boundary pair and emit a passing
preflight receipt. It does not prove DAG dispatch, sandbox enforcement, ITAR
compliance, legal sufficiency, or provider/model safety.

Run focused proof tests:

```bash
uv run pytest \
  tests/test_policy_profile.py \
  tests/test_memory_evidence_gate.py \
  tests/test_project_dag.py \
  tests/test_evidence_manifest.py \
  -q
```

Run the broader real-world sanity harness when you need a receipt-backed suite:

```bash
scripts/run-real-world-sanity.sh --levels simple,medium
```

See [Tau Real-World Sanity Checks](docs/real-world-sanity-checks.md) for the
simple, medium, and advanced proof lanes.

## The zero-trust path

For zero-trust DAGs, Tau’s intended pre-dispatch path is:

```text
zero_trust_preflight
  -> memory_intent/evidence_case_gate
  -> evidence_manifest_preflight
  -> command_policy_dispatch
  -> bounded DAG execution
  -> run-status/report/package
```

### 1. Policy and data boundary

`tau.policy_profile.v1` and `tau.data_boundary.v1` tell Tau what kind of work is
allowed before dispatch. If a policy requires a data boundary and the DAG does
not provide one, Tau blocks before any subagent runs.

```bash
uv run tau zero-trust-doctor \
  --policy-profile examples/zero-trust-basic/policy-profile.json \
  --data-boundary examples/zero-trust-basic/data-boundary.json \
  --dag-contract examples/zero-trust-basic/dag.json \
  --receipt /tmp/tau-zero-trust-preflight.json
```

Read more: [Zero-Trust Policy/Data-Boundary Preflight](docs/zero-trust-policy.md).

### 2. Memory intent and evidence case

Tau is memory-first, but memory is not truth. Memory is a gate.

Tau consumes Graph Memory products such as `/intent` and
`/create-evidence-case`. `/intent` is a planner/routing artifact. It must not
inline-build the evidence case. Evidence comes from `/create-evidence-case` as a
separate artifact that can be hashed, inspected, and rejected.

A zero-trust DAG can provide:

```json
{
  "policy_profile": "policy-profile.json",
  "data_boundary": "data-boundary.json",
  "memory_intent": "memory-intent.json",
  "evidence_case": "evidence-case.json"
}
```

If policy requires memory intent or an evidence case and they are missing,
incompatible, low-confidence, deflected, or inline-contaminated with evidence,
Tau blocks the DAG before dispatch.

Read more: [Memory/Evidence-Case Gate](docs/memory-evidence-gate.md).

### 3. Evidence manifests

`tau.evidence_manifest.v1` binds evidence items to paths, SHA-256 hashes,
declared schemas, validator namespaces, and goal hashes. It checks narrow facts:
that an artifact exists, matches its hash, has the expected schema, and belongs
to the active goal.

```bash
uv run tau evidence-validate evidence-manifest.json \
  --receipt evidence-validation-receipt.json
```

This does not prove semantic correctness. It proves the manifest’s declared
artifact bindings passed Tau’s checks.

### 4. Command policy

Command specs are executable trust boundaries. Tau can reject command specs that
request denied commands, write outside allowed directories, require network, or
mutate state without policy approval.

This is important because `tau-dispatch-command.json` is not “configuration.” It
is local execution authority.

### 5. Bounded DAG execution

Run a project DAG through the harness:

```bash
uv run tau dag-run path/to/dag-contract.json \
  --receipt-dir /tmp/tau-dag-run \
  --agents-root path/to/agents
```

A DAG is not proof. It is a containment map: nodes, edges, retry limits, allowed
routes, required evidence, and fail-closed conditions. Tau’s job is to decide
whether each observed step stayed inside that map.

## Core commands

| Command | Use it for |
| --- | --- |
| `uv run tau doctor` | Read-only runtime preflight for local paths, tools, providers, and proof lanes. |
| `uv run tau init --profile zero-trust` | Create a local starter policy, data boundary, command policy, and DAG template. |
| `uv run tau zero-trust-doctor` | Validate policy/data-boundary compatibility before high-stakes DAG dispatch. |
| `uv run tau dag-run <contract>` | Run a project DAG through bounded command-loop or ready-queue scheduling. |
| `uv run tau evidence-validate <manifest>` | Validate typed evidence manifests by path, hash, schema, validator, and goal hash. |
| `uv run tau compliance-package <run-dir> --out <dir>` | Collect existing run receipts and artifacts into a review package. |
| `uv run tau report <run-dir> --out report.html` | Render a static single-file inspection report from existing run artifacts. |
| `uv run tau serve --host 127.0.0.1 --port 8768` | Start the minimal local/self-hosted API over existing Tau commands. |
| `uv run tau sandbox-run ... -- <command>` | Run a local command only if Tau can establish the requested sandbox boundary. |
| `uv run tau browser-cdp-proof --out-dir <dir>` | Produce a local Surf/browser screenshot proof page and receipt. |
| `uv run python scripts/run-zero-trust-redteam.py --out-dir <dir>` | Run deterministic adversarial checks against Tau’s current zero-trust gates. |

## Review packages, reports, and API

Tau can turn a run directory into a reviewable package:

```bash
uv run tau compliance-package /path/to/run-dir --out /tmp/tau-package
```

The package may include the DAG receipt, contract, goal, policy profile, data
boundary, zero-trust preflight receipt, memory/evidence receipts, evidence
validation receipt, approval receipts, Herdr lease receipts, GitHub policy
receipts, browser proof receipts, sandbox receipts, and a non-claims file.

Read more: [Compliance Evidence Package](docs/compliance-package.md).

For a human-readable static page:

```bash
uv run tau report /path/to/run-dir --out /tmp/tau-report.html
```

Read more: [Static Run Report](docs/run-report.md).

For local integration:

```bash
uv run tau serve --host 127.0.0.1 --port 8768
```

Read more: [Local API](docs/local-api.md).

## Sandboxing and provenance

Tau can record declared actor and environment manifests and wrap receipts in a
local HMAC signature envelope:

```bash
uv run tau actor-manifest --run-id example --actor coder:agent:worker --out actor.json
uv run tau environment-manifest --run-id example --network-policy deny --out environment.json
uv run tau sign-receipt --receipt receipt.json --key local.key --out signed-receipt.json
uv run tau verify-signed-receipt --signed-receipt signed-receipt.json --key local.key
```

This detects local tampering with signed receipt inputs. It is not public-key
identity proof, legal attestation, or compliance certification.

Read more: [Provenance and Receipt Signing](docs/provenance-and-signing.md).

For local command containment, Tau can use Bubblewrap when the host supports the
required network namespace:

```bash
uv run tau sandbox-run \
  --policy-profile examples/zero-trust-basic/policy-profile.json \
  --data-boundary examples/zero-trust-basic/data-boundary.json \
  --out /tmp/tau-sandbox-receipt.json \
  -- /usr/bin/python3 -c 'print("only runs if sandboxed")'
```

If Tau cannot establish the sandbox boundary, it writes a blocked receipt and
does not run the payload.

Read more: [Sandbox Run](docs/sandbox-run.md).

## Herdr-visible provider work

Tau can use Herdr-visible provider panes for live subagent work. In that lane,
provider work should be treated as visible but still untrusted:

```text
work order -> Herdr pane -> visible log -> provider receipt -> Tau validator
```

The canonical truth is the receipt, not the pane text. Herdr workspace cleanup is
lease-gated and verified before Tau claims cleanup succeeded.

Use the provider and Herdr lanes only when the active policy allows them. For
zero-trust/local-only work, cloud/provider routes should be denied before
dispatch.

## GitHub, research, and browser proof

GitHub transport is dry-run by default. Live mutation requires explicit apply
commands and the relevant approval, policy, redaction, and preflight receipts.

Research is also review-required. Tau can validate `tau.research_source_packet.v1`
and emit `tau.research_source_receipt.v1`, but a research receipt is design
input, not closure proof. External research should be denied by default for
controlled data unless a human-approved, sanitized research path is configured.

Browser/CDP proof is a local proof page and screenshot lane. It can prove that a
specific Tau proof page rendered through the configured browser tool. It does not
prove production chat UX acceptance.

## Interface snapshots

The checked-in images show current human-facing surfaces. They are visual proof
of specific rendering rungs, not proof of final product readiness.

### Textual TUI

<p align="center">
  <img
    src="docs/assets/tau-tui-memory-stage.webp"
    alt="T’au Textual TUI showing Memory pipeline stage text and handoff routing"
    style="max-width: 100%; height: auto; display: block;"
  />
</p>

### React chat integration viewer

<p align="center">
  <img
    src="docs/assets/tau-react-chat-memory-stage.webp"
    alt="T’au UX Lab chat viewer showing receipt-backed Memory stage feedback"
    style="max-width: 100%; height: auto; display: block;"
  />
</p>

Tau owns the contract at `ui/tau-chat-contract.json`. UX Lab can host an
integration viewer, but UX Lab is not the source of truth for Tau’s harness
receipts or production-readiness claims.

## Research influences

Tau’s adaptive DAG direction is influenced by graph reasoning, dependency-aware
parallel scheduling, role workflows, stigmergic routing, and multi-agent failure
research. The papers and references are design context only.

Primary references are collected in
[Adaptive DAG Research References](docs/adaptive-dag-research-references.md),
including:

- [Graph of Thoughts](https://arxiv.org/abs/2308.09687)
- [Adaptive Graph of Thoughts](https://arxiv.org/abs/2502.05078)
- [An LLM Compiler for Parallel Function Calling](https://arxiv.org/abs/2312.04511)
- [MetaGPT](https://arxiv.org/abs/2308.00352)
- [SwarmSys](https://arxiv.org/abs/2510.10047)
- [AMRO-S](https://arxiv.org/abs/2603.12933)
- [The Bystander Effect in Multi-Agent Reasoning](https://arxiv.org/abs/2605.10698)

The distributed-cognition video reference is design inspiration only until its
metadata/transcript is captured in a research-source receipt:
[Distributed Cognition video](https://youtu.be/DsfxdwZdNf0).

These references do not prove Tau correctness or benchmark parity. Tau’s claims
come from local tests, live/non-mocked receipts, committed artifacts, and explicit
`proof_scope` boundaries.

## Docker stack

Tau can also run through Docker Compose:

```bash
docker compose --profile cli run --rm tau --help
```

The Docker stack separates Tau-owned containers from external services:

| Service | Purpose |
| --- | --- |
| `tau` | One-shot CLI/TUI/harness container for local commands and smoke checks. |
| `tau-cron` | Scheduler that invokes one bounded `handoff-command-loop` tick per interval. |
| external `embry-memory` | Memory daemon, usually `http://host.docker.internal:8601`. |
| external `scillm` | Optional SciLLM proxy, usually `http://host.docker.internal:4001`. |
| external `ux-lab` | Browser integration viewer, usually `http://host.docker.internal:3002/#tau`. |

`tau-cron` is not an unbounded subagent. It wakes up, runs one bounded loop tick,
writes receipts, sleeps, and repeats. Malformed handoffs, stale goal hashes,
missing routes, and unavailable services fail closed into receipts.

## Repository map

```text
src/tau_ai/                         provider/model streaming layer
src/tau_agent/                      portable agent loop, events, tools, sessions
src/tau_coding/                     CLI app, TUI, harness, zero-trust gates
ui/tau-chat-contract.json           Tau-owned chat UX contract
experiments/goal-locked-subagents/  schemas, fixtures, proofs, command specs
experiments/loop2-alignment/        Loop2 and Memory/Brave alignment experiments
docs/                               architecture, proof lanes, and product plans
examples/zero-trust-basic/          copyable zero-trust preflight example
PROJECT_KNOWLEDGE.md                current project memory for humans and agents
```

Important harness files:

```text
src/tau_coding/project_dag.py
src/tau_coding/policy_profile.py
src/tau_coding/memory_evidence_gate.py
src/tau_coding/evidence_manifest.py
src/tau_coding/handoff_dispatch.py
src/tau_coding/github_handoff.py
src/tau_coding/research_source_receipt.py
src/tau_coding/compliance_package.py
src/tau_coding/sandbox_run.py
experiments/goal-locked-subagents/schemas/
experiments/goal-locked-subagents/agent-command-specs/
```

## Evidence discipline

Every Tau result should say:

```text
mocked: yes|no|mixed
live: yes|no|mixed
provider_live: yes|no|mixed
what was exercised
what remains unverified
artifact paths
```

Use `PROJECT_KNOWLEDGE.md` for the current proof ledger. Use
`docs/real-world-sanity-checks.md` for the non-mocked sanity suite. Do not treat
old proof artifacts as proof of new claims.

## Upstream

This repository is a fork of `alejandro-ao/tau`, pushed under
`grahama1970/tau` for memory-first, goal-locked, zero-trust harness experiments.
The original T’au architecture docs remain under `docs/` where still useful.
