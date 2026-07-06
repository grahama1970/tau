# T’au — A Harness That Doesn’t Trust Its Agents

<p align="center">
  <img
    src="docs/assets/tau-header.webp"
    alt="T’au agentic harness console in a science-fiction workspace"
    style="max-width: 100%; height: auto; display: block;"
  />
</p>

> **Agents can work. T’au decides what counts.**

Most agent harnesses ask: *How do I make the agent smarter?*  
T’au asks a different question: **What do I do when the agent is wrong?**

Agents hallucinate. They drift. They overclaim. They route bad answers through
reviewers, DAGs, and swarms as if consensus equals truth. T’au does not try to
fix agents. It wraps them in hard stops, receipts, evidence gates, and audit
trails so that bad steps are caught before they count.

Tau is not an ITAR compliance system, a secure autonomous agent platform, or a
replacement for legal/security review. It is a memory-first, zero-trust
containment harness that can operate inside an approved high-stakes environment.

---

## What T’au does

T’au treats every agent output as an **untrusted claim**. For zero-trust DAGs,
that claim must pass through narrow gates before work continues:

- **Policy/data-boundary preflight** — Is this work allowed under the declared
  policy and classification boundary?
- **Memory/evidence gate** — Did Graph Memory produce a planner-only intent and,
  when required, a separate evidence case compatible with the active policy?
- **DAG contract** — Is the observed step inside the allowed route map?
- **Evidence manifest** — Do named artifacts exist, match their hashes, carry the
  expected schemas, and preserve the goal hash?
- **Command/sandbox policy** — Is this local execution allowed, and can Tau
  establish the required containment boundary?

If a required gate blocks, the step stops. Legacy DAGs can still run without the
zero-trust gates unless they opt into a `policy_profile`; high-stakes work should
opt in.

The core model is:

```text
agent output = untrusted claim
memory intent = why this work is being routed
evidence case = what supports the route
DAG contract = allowed claim path
receipt = recorded claim
validator/policy = narrow yes/no gate
human = owner of goals and high-risk approvals
```

---

## Try it in two minutes

```bash
git clone https://github.com/grahama1970/tau.git
cd tau
uv sync
uv run tau doctor
```

Scaffold a zero-trust project:

```bash
uv run tau init --profile zero-trust --out /tmp/tau-starter
```

Run the checked-in preflight example:

```bash
cd examples/zero-trust-basic
./run.sh
```

This writes `out/zero-trust-preflight-receipt.json`. Open it. It proves only
that T’au inspected the example policy/data-boundary pair and emitted a passing
preflight receipt. DAG dispatch, sandbox enforcement, provider safety, ITAR
compliance, and legal sufficiency are separate claims that need their own
receipts.

Run the focused proof suite:

```bash
uv run pytest \
  tests/test_policy_profile.py \
  tests/test_memory_evidence_gate.py \
  tests/test_project_dag.py \
  tests/test_evidence_manifest.py \
  -q
```

Run the broader simple/medium real-world sanity harness:

```bash
scripts/run-real-world-sanity.sh --levels simple,medium
```

See [Real-World Sanity Checks](docs/real-world-sanity-checks.md) for the simple,
medium, and advanced proof lanes.

---

## What a bounded gate looks like

A normal chat agent might say “I fixed it” and keep talking. In T’au, a bounded
step emits a schema-valid receipt and stops. A blocked Memory intent gate looks
like this:

```json
{
  "schema": "tau.memory_intent_gate_receipt.v1",
  "ok": false,
  "status": "BLOCKED",
  "memory_first": true,
  "intent_schema": "memory.intent.v1",
  "planner_only": true,
  "route": "CLARIFY",
  "confidence": 0.91,
  "evidence_case_required": false,
  "alert_codes": ["intent_clarify_required"],
  "proof_scope": {
    "proves": [
      "Tau inspected Graph Memory intent before DAG dispatch.",
      "Tau did not let a subagent route start from ungrounded prompt text."
    ],
    "does_not_prove": [
      "Memory facts are true.",
      "The evidence case is sufficient for closure.",
      "ITAR compliance.",
      "Semantic model quality."
    ]
  }
}
```

The orchestrator does not trust the agent. It checks the receipt, blocks or
routes according to policy, writes artifacts, and stops at the next bounded
condition. Long-running behavior comes from repeated bounded invocations — not
from an unbounded model loop left to wander.

---

## The three layers

| Layer | What it does |
| --- | --- |
| **Coding runtime** | The `tau` CLI, Textual TUI, provider config, local tools, sessions, and print-mode execution. |
| **Agentic harness** | Goal-locked handoffs, project DAGs, evidence manifests, command policies, GitHub projections, and fail-closed receipts. |
| **Zero-trust control plane** | Policy preflight, Graph Memory intent/evidence gates, sandbox receipts, provenance, review packages, reports, and adversarial checks. |

---

## Core commands

| Command | What it does |
| --- | --- |
| `uv run tau doctor` | Check local paths, tools, providers, and proof lanes. |
| `uv run tau init --profile zero-trust` | Scaffold a starter policy, data boundary, command policy, and DAG template. |
| `uv run tau zero-trust-doctor` | Validate policy/data-boundary compatibility before dispatch. |
| `uv run tau dag-run <contract>` | Run a project DAG through bounded command-loop or ready-queue execution. |
| `uv run tau evidence-validate <manifest>` | Check evidence paths, hashes, schemas, validators, and goal hashes. |
| `uv run tau compliance-package <run-dir> --out <dir>` | Collect receipts and artifacts into a reviewable package. |
| `uv run tau report <run-dir> --out report.html` | Render a static HTML inspection report from run artifacts. |
| `uv run tau serve --host 127.0.0.1 --port 8768` | Start the minimal local/self-hosted API. |
| `uv run tau sandbox-run ... -- <command>` | Run a command only if the sandbox boundary can be established. |
| `uv run python scripts/run-zero-trust-redteam.py --out-dir <dir>` | Run deterministic adversarial checks against Tau’s current gates. |

For the full command reference, run:

```bash
uv run tau --help
```

---

## Zero-trust path

For zero-trust DAGs, the intended pre-dispatch path is:

```text
zero_trust_preflight
  -> memory_intent/evidence_case_gate
  -> evidence_manifest_preflight
  -> command_policy_dispatch
  -> bounded DAG execution
  -> run-status/report/package
```

A zero-trust DAG can pass inline objects or contract-relative JSON paths:

```json
{
  "policy_profile": "policy-profile.json",
  "data_boundary": "data-boundary.json",
  "memory_intent": "memory-intent.json",
  "evidence_case": "evidence-case.json"
}
```

Read more:

- [Zero-Trust Policy/Data-Boundary Preflight](docs/zero-trust-policy.md)
- [Memory/Evidence-Case Gate](docs/memory-evidence-gate.md)
- [Compliance Evidence Package](docs/compliance-package.md)
- [Static Run Report](docs/run-report.md)
- [Local API](docs/local-api.md)

---

## Review packages and reports

After a run, bundle the receipts and artifacts into a reviewable package:

```bash
uv run tau compliance-package /path/to/run-dir --out /tmp/tau-package
```

For a human-readable summary:

```bash
uv run tau report /path/to/run-dir --out /tmp/tau-report.html
```

For local API access:

```bash
uv run tau serve --host 127.0.0.1 --port 8768
```

The package/report/API surface helps humans inspect what Tau checked, copied,
derived, blocked, or could not find. It does not make the run compliant.

---

## Sandboxing and provenance

T’au can record declared actors and environments, then wrap receipts in a local
HMAC signature envelope to detect tampering:

```bash
uv run tau actor-manifest --run-id example --actor coder:agent:worker --out actor.json
uv run tau environment-manifest --run-id example --network-policy deny --out environment.json
uv run tau sign-receipt --receipt receipt.json --key local.key --out signed-receipt.json
uv run tau verify-signed-receipt --signed-receipt signed-receipt.json --key local.key
```

This is local tamper detection. It is not public-key identity proof, legal
attestation, export-control eligibility proof, or compliance certification.

Tau can also sandbox local commands with Bubblewrap when the host supports the
required boundary:

```bash
uv run tau sandbox-run \
  --policy-profile examples/zero-trust-basic/policy-profile.json \
  --data-boundary examples/zero-trust-basic/data-boundary.json \
  --out /tmp/tau-sandbox-receipt.json \
  -- /usr/bin/python3 -c 'print("only runs if sandboxed")'
```

If the sandbox boundary cannot be established, T’au writes a blocked receipt and
does not run the payload.

Read more: [Provenance and Signing](docs/provenance-and-signing.md),
[Sandbox Run](docs/sandbox-run.md).

---

## Herdr-visible provider work

Tau can use Herdr-visible provider panes for live subagent work. In that lane,
provider work is visible but still untrusted:

```text
work order -> Herdr pane -> visible log -> provider receipt -> Tau validator
```

The canonical truth is the receipt, not the pane text. Herdr workspace cleanup is
lease-gated and verified before Tau claims cleanup succeeded. For zero-trust
local-only work, cloud/provider routes should be denied before dispatch.

---

## GitHub, research, and browser proof

GitHub transport is dry-run by default. Live mutation requires explicit apply
commands and the relevant approval, policy, redaction, and preflight receipts.

Research is review-required. Tau can validate `tau.research_source_packet.v1` and
emit `tau.research_source_receipt.v1`, but a research receipt is design input,
not closure proof. External research should be denied by default for controlled
data unless a human-approved, sanitized research path is configured.

Browser/CDP proof is a local proof-page and screenshot lane. It can prove that a
specific Tau proof page rendered through the configured browser tool. It does not
prove production chat UX acceptance.

---

## Interface snapshots

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

---

## What T’au is not

T’au is **not** an ITAR compliance system, a secure autonomous agent platform, or
a replacement for legal or security review. It does not claim:

- agents are reliable;
- reviewer agents are trust anchors;
- DAG or swarm consensus is proof;
- memory facts are automatically true;
- research sources are closure evidence;
- route memory proves future route correctness;
- local browser proof is production chat readiness;
- GitHub policy proof is live GitHub mutation safety;
- sandbox proof is legal/export-control sufficiency.

Its strongest feature is that it keeps these non-claims visible.

---

## Research influences

T’au’s adaptive DAG direction draws on graph reasoning, dependency-aware parallel
scheduling, structured multi-agent workflows, bounded swarm/stigmergy research,
and multi-agent failure research. These papers are design context, not proof of
correctness:

- [Graph of Thoughts](https://arxiv.org/abs/2308.09687)
- [Adaptive Graph of Thoughts](https://arxiv.org/abs/2502.05078)
- [An LLM Compiler for Parallel Function Calling](https://arxiv.org/abs/2312.04511)
- [MetaGPT](https://arxiv.org/abs/2308.00352)
- [SwarmSys](https://arxiv.org/abs/2510.10047)
- [AMRO-S](https://arxiv.org/abs/2603.12933)
- [The Bystander Effect in Multi-Agent Reasoning](https://arxiv.org/abs/2605.10698)

Design inspiration: [Distributed Cognition video](https://youtu.be/DsfxdwZdNf0)

T’au’s claims come from local tests, live/non-mocked receipts, committed
artifacts, and explicit `proof_scope` boundaries.

---

## Docker stack

```bash
docker compose --profile cli run --rm tau --help
```

| Service | Purpose |
| --- | --- |
| `tau` | One-shot CLI/TUI/harness container. |
| `tau-cron` | Scheduler that invokes one bounded handoff tick per interval. |
| external `embry-memory` | Memory daemon, usually `http://host.docker.internal:8601`. |
| external `scillm` | Optional SciLLM proxy, usually `http://host.docker.internal:4001`. |
| external `ux-lab` | Browser integration viewer, usually `http://host.docker.internal:3002/#tau`. |

`tau-cron` is not an unbounded subagent. It wakes up, runs one bounded tick,
writes receipts, sleeps, and repeats. Malformed handoffs, stale goal hashes,
missing routes, and unavailable services fail closed into receipts.

---

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

---

## Evidence discipline

Every T’au result should answer:

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

---

## Upstream

This repository is a fork of `alejandro-ao/tau`, pushed under `grahama1970/tau`
for memory-first, goal-locked, zero-trust harness experiments. The original T’au
architecture docs remain under `docs/` where still useful.
