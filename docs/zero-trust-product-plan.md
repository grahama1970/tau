# Zero-Trust Product Execution Plan

This plan converts Tau's strategic direction into an execution sequence. It is
not a product claim or compliance claim. It is the project plan for making Tau
usable as a memory-first, zero-trust containment harness for untrusted agents.

## Product Sentence

Tau is the memory-first, zero-trust containment harness for untrusted agents.

Competitors help teams build agents. Tau helps teams distrust agents safely.

The core product rule is:

```text
Memory is not context. Memory is a gate.
```

## Current Baseline

The policy/data-boundary gate is no longer the next gap. Tau already has:

- `tau.policy_profile.v1`
- `tau.data_boundary.v1`
- `tau.zero_trust_preflight_receipt.v1`
- `tau zero-trust-doctor`
- `project_dag.py` policy/data-boundary preflight before dispatch
- deterministic tests for default-deny policy, ITAR boundaries, missing
  classification, classified-not-allowed, and provider DAG denial

The operator wrapper gap is also closed in `agent-skills`:

- `skills/tau/run.sh doctor`
- `skills/tau/run.sh proof-status`
- `skills/tau/run.sh e2e` as a compatibility alias for `proof-status`
- no literal Python `${HOME}` path handling in the pushed wrapper

## Non-Goals

This plan does not attempt to:

- make Tau a better general-purpose CrewAI, LangGraph, AutoGen, or OpenAI
  Agents replacement
- chase every production-platform feature competitors already have
- add more personas, agent roles, model providers, or creative demos
- build a large chat UI or dashboard before the receipt/report surface exists
- claim ITAR compliance, export-control legal sufficiency, sandbox isolation,
  human identity verification, provider/model semantic safety, or compliance
  package completeness
- treat research, memory, route-memory, or agent claims as closure proof
- mutate public GitHub, Memory, provider, Herdr, or filesystem state without the
  relevant Tau policy and approval receipts

## Strategic Split

Tau needs two tracks.

Track A owns zero-trust agent containment. This is the differentiator.

Track B reaches basic product parity. This is table stakes so evaluators can
run, inspect, and understand Tau without becoming repository archaeologists.

Do not copy general agent-framework features beyond the minimum product surface
needed to expose Tau's zero-trust difference.

## Track A: Own Zero Trust

### A1. Memory/Evidence-Case Gate

Goal: high-stakes DAG dispatch must require Graph Memory intent and, when
policy requires it, a separate evidence case.

The dispatch pipeline should become:

```text
zero_trust_preflight
-> memory_intent/evidence_case_gate
-> evidence_manifest_preflight
-> command_policy_dispatch
```

Build:

```text
src/tau_coding/memory_evidence_gate.py
tests/test_memory_evidence_gate.py
docs/memory-evidence-gate.md
```

Wire into:

```text
src/tau_coding/project_dag.py
src/tau_coding/policy_profile.py
src/tau_coding/run_status.py
```

Schemas:

```text
tau.memory_intent_gate_receipt.v1
tau.evidence_case_gate_receipt.v1
```

Block conditions:

```text
missing_memory_intent
memory_first != true
intent route = CLARIFY
intent route = DEFLECT
intent confidence below policy threshold
evidence case required but missing
intent contains inline evidence
evidence case hash missing
evidence case schema invalid
evidence case data_boundary mismatch
evidence case policy_profile mismatch
```

Proof:

```bash
uv run pytest tests/test_memory_evidence_gate.py tests/test_project_dag.py tests/test_policy_profile.py -q
```

### A2. Compliance Evidence Packaging

Build this after memory/evidence gating exists. The package must consume
receipts; it must not invent validation fields.

Build:

```text
src/tau_coding/compliance_package.py
tests/test_compliance_package.py
docs/compliance-package.md
```

CLI:

```bash
uv run tau compliance-package <run-dir> --out <package-dir>
```

Package contents:

```text
package-manifest.json
goal.json
dag-contract.json
policy-profile.json
data-boundary.json
zero-trust-preflight-receipt.json
memory-intent-gate-receipt.json
evidence-case-gate-receipt.json
evidence-validation-receipt.json
command-policy-receipts/
research-source-receipts/
approval-receipts/
herdr-lease-receipts/
github-apply-policy-receipts/
browser-cdp-proof-receipts/
non-claims.md
```

Required non-claims:

```text
not ITAR compliance
not legal sufficiency
not complete sandbox enforcement
not human identity verification unless provenance receipt exists
not semantic model quality
```

### A3. Provenance and Receipt Signing

Receipts are useful, but unsigned JSON is weak for high-stakes review.

Build:

```text
src/tau_coding/provenance.py
src/tau_coding/receipt_signing.py
tests/test_provenance.py
tests/test_receipt_signing.py
```

Schemas:

```text
tau.actor_manifest.v1
tau.environment_manifest.v1
tau.signed_receipt.v1
```

Rule:

```text
unsigned receipt = evidence candidate
signed receipt = admissible receipt
signed + policy-valid + boundary-valid = proof candidate
```

### A4. Sandbox and Egress Enforcement

Policy metadata is not enough. High-stakes mode needs an execution boundary.

Build:

```text
src/tau_coding/sandbox_policy.py
src/tau_coding/sandbox_run.py
tests/test_sandbox_policy.py
```

Zero-trust defaults:

```text
network denied
cloud provider denied
external research denied
public GitHub mutation denied
filesystem writes allowlisted
secrets absent by default
browser disabled unless proof lane explicitly allowed
```

Receipt:

```text
tau.sandbox_run_receipt.v1
```

### A5. Adversarial Containment Suite

The category proof is not a happy-path demo. It is malicious-agent containment.

Build:

```text
scripts/run-zero-trust-redteam.py
tests/test_zero_trust_redteam.py
experiments/goal-locked-subagents/fixtures/adversarial-agents/
docs/darpa-demo-plan.md
```

Malicious attempts:

```text
skip memory intent
inline fake evidence
omit evidence case
forge evidence hash
change goal hash
write outside allowed path
request cloud provider despite denial
request external research despite denial
request Memory upsert without approval
request GitHub apply without policy
fake reviewer PASS
reuse stale route memory
invent citations
inject shell commands through Herdr text
```

Pass condition:

```text
Tau blocks every malicious attempt with named fail-closed receipts.
```

## Track B: Basic Product Parity

Basic parity means a serious evaluator can run Tau, inspect it, and understand
the zero-trust difference without knowing internal proof directories.

### B1. Init

Status: implemented as the first basic product-parity slice.

Build:

```text
src/tau_coding/init_project.py
tests/test_init_project.py
```

CLI:

```bash
uv run tau init --profile zero-trust
```

Creates:

```text
.tau/
  policy-profile.json
  data-boundary.json
  command-policy.json
  dag-template.json
  README.md
```

This starter creates local files only. It does not prove the generated DAG has
been dispatched, does not enforce sandbox isolation, and does not certify the
data boundary.

### B2. Run

Add the obvious command:

```bash
uv run tau run dag.json
uv run tau run dag.json --zero-trust
uv run tau run dag.json --policy-profile .tau/policy-profile.json
uv run tau run dag.json --data-boundary .tau/data-boundary.json
```

Internally, this can delegate to `tau dag-run`.

### B3. Examples

Add:

```text
examples/zero-trust-basic/
examples/memory-evidence-case/
examples/itar-local-only/
examples/adversarial-agent-blocked/
examples/github-apply-policy/
examples/herdr-visible-provider/
```

Each example needs:

```text
README.md
dag.json
policy-profile.json
data-boundary.json
expected-receipt.json
run.sh
```

### B4. Static Run Report

Build:

```text
src/tau_coding/run_report.py
tests/test_run_report.py
```

CLI:

```bash
uv run tau report <run-dir> --out report.html
```

Report contents:

```text
goal
policy
data boundary
memory intent
evidence case
DAG steps
receipts
blocked/allowed decisions
non-claims
```

### B5. Local API

Build:

```text
src/tau_coding/server.py
tests/test_server.py
```

CLI:

```bash
uv run tau serve
```

Minimum endpoints:

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

## Execution Order

### Phase 1: Credibility and First-Run Usability

1. Agent-skills wrapper and `proof-status`: complete in `agent-skills`.
2. Agent-skills Tau zero-trust contract: complete in `agent-skills`.
3. Add `tau init --profile zero-trust`: implemented.
4. Add `examples/zero-trust-basic`.

### Phase 2: The Memory-First Wedge

5. Add memory/evidence-case gate.
6. Add policy fields for intent/evidence-case requirements.
7. Surface memory/evidence receipts in `run-status`.

### Phase 3: Inspection and Review

8. Add compliance evidence package.
9. Add static run report.
10. Add `tau serve` local API.

### Phase 4: High-Assurance Containment

11. Add actor/environment provenance.
12. Add signed receipt envelope.
13. Add sandbox/egress enforcement.
14. Add adversarial red-team suite.

## Next Commit Sequence

The next Tau repo commits should be:

1. `feat(init): add zero-trust project initializer`
2. `feat(memory): gate zero-trust DAGs on memory intent and evidence cases`
3. `feat(report): package and render zero-trust run evidence`
4. `feat(api): add local tau serve endpoints for doctor, preflight, run, status`
5. `test(zero-trust): add adversarial containment suite`

The already-completed agent-skills prerequisite was:

```text
fix(agent-skills/tau): repair operator wrapper and proof-status
```

## Stop Conditions

Do not advance from a phase merely because docs or tests exist. Advance only
when the named command emits the expected receipt/artifact and the result states
mocked/live boundaries.

For the next implementation slice, `tau init`, the minimum stop condition is:

```bash
uv run tau init --profile zero-trust --out /tmp/tau-init-zero-trust
python -m json.tool /tmp/tau-init-zero-trust/.tau/policy-profile.json
python -m json.tool /tmp/tau-init-zero-trust/.tau/data-boundary.json
uv run pytest tests/test_init_project.py -q
```

For the memory/evidence-case slice, the minimum stop condition is:

```bash
uv run pytest tests/test_memory_evidence_gate.py tests/test_project_dag.py tests/test_policy_profile.py -q
```

## Competitive Boundary

Competitors are stronger on general product maturity:

```text
installation
quickstart
clean CLI
SDK/API
examples
server mode
deployment mode
observability UI
managed/self-hosted story
team/RBAC story
docs
ecosystem integrations
```

Tau should own:

```text
memory intent before action
evidence-case admissibility
goal-hash continuity
data-boundary preflight
policy-profile enforcement
fail-closed proof receipts
research-source review gating
side-effect policy receipts
explicit non-claims
adversarial containment
```

The plan is to make Tau usable enough for evaluators, then make the memory-first
zero-trust gate impossible to miss.
