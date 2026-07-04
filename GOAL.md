# Tau Adaptive DAG Goal

## Active Goal

Implement Tau's adaptive DAG updates as bounded, receipt-backed runtime features
without drifting into unbounded autonomy, dashboard theater, hidden model
reasoning, or provider-dependent claims.

The current implementation track is:

1. `tau.dag_signal_receipt.v1` local signal receipts.
2. `tau.dag_expansion_proposal.v1` validation-only expansion proposals.
3. Independent dissent reviewer motif.
4. Quality-gated route-memory reinforcement.
5. Bounded ready-node scheduler hardening for local, non-mutating branches.

Only the next smallest receipt-backed slice should be implemented at a time.

## Research Translation

The research inspiration is architectural, not an instruction to copy external
algorithms verbatim.

- Graph reasoning becomes explicit DAG contracts, observed edges, receipts, and
  artifacts, not hidden chain-of-thought.
- Adaptive reasoning becomes bounded expansion proposals that must pass schema,
  goal, route, and evidence validation before any future run can use them.
- Distributed or swarm intelligence becomes local receipt-derived route signals,
  not free-form agent chat or consensus-as-proof.
- Compiler-style parallelism remains bounded ready-node scheduling with explicit
  dependencies, local subprocess receipts, timeouts, and no mutating branch
  concurrency until branch locks exist.
- Reviewer discipline remains central: creator nodes produce artifacts;
  reviewer, validator, and goal-guardian nodes evaluate evidence against the
  immutable goal.

Reference inspirations:

- Graph of Thoughts: https://arxiv.org/abs/2308.09687
- Adaptive Graph of Thoughts: https://arxiv.org/abs/2502.05078
- LLMCompiler: https://arxiv.org/abs/2312.04511
- MetaGPT: https://arxiv.org/abs/2308.00352
- SwarmSys: https://arxiv.org/abs/2510.10047
- AMRO-S: https://arxiv.org/abs/2603.12933
- Bystander Effect in multi-agent reasoning: https://arxiv.org/abs/2605.10698

## Implemented Baseline

The current baseline includes:

- `tau dag-run` for `tau.dag_contract.v1`.
- Creator-reviewer DAG execution through the existing handoff command loop.
- Local non-mutating bounded ready-node scheduling via
  `--scheduler bounded-ready-queue`.
- E2E real-world sanity checks for simple, medium, complex, concurrent, and
  negative DAG paths.
- `tau dag-signals <dag-receipt-or-run-dir>` producing
  `tau.dag_signal_receipt.v1`.

`tau.dag_signal_receipt.v1` is observational only. It may identify local
reinforcement candidates or negative signals, but it must not mutate routes,
write Memory, rewrite DAG contracts, call providers, or apply expansions.

## Next Slice: Expansion Validation Only

Implement next:

```text
tau dag-expansion-validate \
  --dag-contract <dag-contract.json|yaml> \
  --proposal <dag-expansion-proposal.json|yaml> \
  --receipt <dag-expansion-validation-receipt.json> \
  --preview <expanded-dag.preview.json>
```

Required outputs:

- `tau.dag_expansion_proposal.v1` schema.
- `tau.dag_expansion_validation_receipt.v1` schema.
- Validation receipt.
- Preview expanded DAG only when validation passes.

Do not automatically apply the expansion to a running DAG. Do not mutate the
source DAG contract. Do not route or dispatch the expanded DAG inside this
command.

## Expansion Authority Rules

Allowed proposal authors for the first expansion slice:

- `reviewer`
- `goal-guardian`
- `validator`
- `planner`, only before a run

Disallowed proposal authors:

- `creator`
- `coder`
- `worker`
- provider nodes
- artifact-producing branches that are trying to extend their own work

Workers may report blockers or missing evidence. They may not expand their own
branch.

## Initial Hard Limits

Use these limits exactly for the first expansion validation slice:

```yaml
max_new_nodes: 2
max_depth_delta: 1
max_new_edges: 4
allow_new_executors: false
allow_target_change: false
allow_goal_change: false
allow_terminal_node_change: false
allow_command_spec_change: false
```

Goal changes remain human-only.

## Allowed Expansion Types

Allowed in the first validation slice:

- reviewer node
- validator node
- goal-guardian node
- research-auditor node, only if already routable and non-mutating

Disallowed in the first validation slice:

- new coder branch
- new creator branch
- new provider branch
- new GitHub mutation branch
- new artifact creator branch
- new executor type
- command-spec changes

## Required Proof Bar

Every adaptive DAG slice must provide:

- Focused unit tests.
- Deterministic local command proof.
- A committed receipt artifact or explicit proof artifact.
- Explicit `mocked`, `live`, and `provider_live` boundaries.
- `proof_scope.proves` and `proof_scope.does_not_prove`.

For the expansion validation slice, tests must cover:

- Valid reviewer or goal-guardian proposal passes.
- Creator or worker expansion proposal fails.
- Goal hash change fails.
- Target change fails.
- Terminal-node change fails.
- New executor fails.
- Command-spec change fails.
- Too many new nodes fails.
- Too many new edges fails.
- Excess depth delta fails.
- Disallowed new worker/provider/coder branch fails.
- Valid proposal writes preview and receipt.
- Invalid proposal writes receipt and no preview.

## Non-Claims

Until separately implemented and proven, do not claim:

- adaptive DAG expansion is applied automatically;
- route mutation is live;
- Memory route learning is active;
- provider/model semantic quality is proven;
- mutating parallel branches are safe;
- branch locks exist;
- GitHub mutation paths are covered;
- hidden chain-of-thought is evaluated;
- consensus or reviewer agreement is proof.

## Stop Conditions

Stop and ask for human direction before continuing if:

- the next implementation step would expand beyond expansion validation-only;
- a feature would mutate DAG routes, Memory, GitHub, provider state, or command
  specs without a separate explicit approval;
- proof is only mocked but the claim would imply runtime behavior;
- a test failure requires changing the stated authority or hard-limit rules;
- repository dirty state prevents committing only relevant files;
- the task would require external architecture review or WebGPT code before a
  safe local implementation can proceed.

## Commit Rule

After each focused implementation slice:

1. Run the narrowest useful proof.
2. Stage only relevant files.
3. Inspect the staged set.
4. Commit immediately after proof passes.
5. Push the commit.
6. Report the commit SHA, exact files, commands, artifacts, and remaining
   non-claims.
