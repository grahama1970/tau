# Adaptive DAG Research References

Tau treats this research as architecture inspiration, not as algorithms copied
verbatim. The operating translation is:

```text
not: one unbounded smarter agent
but: bounded DAGs + receipts + validators + route signals + human goal gates
```

These references are intended to make the research basis easy to critique.
They should not be read as proof that Tau implements every technique described
in the papers.

## Graph Reasoning

- [Graph of Thoughts: Solving Elaborate Problems with Large Language Models](https://arxiv.org/abs/2308.09687) introduces graph-structured LLM reasoning where generated units have dependency edges and can be combined, refined, or fed back through the graph.
  - Tau interpretation: DAG nodes should be receipt-backed work/evidence steps, not hidden chain-of-thought.
- [Adaptive Graph of Thoughts: Test-Time Adaptive Reasoning Unifying Chain, Tree, and Graph Structures](https://arxiv.org/abs/2502.05078) frames reasoning as a dynamic DAG of interdependent subproblems that can expand selectively.
  - Tau interpretation: adaptive expansion should be explicit, bounded, validated, and receipt-producing before any expanded DAG is run.

## Parallel DAG Scheduling

- [An LLM Compiler for Parallel Function Calling](https://arxiv.org/abs/2312.04511) uses planner/fetcher/executor separation to dispatch independent function calls in parallel while respecting dependencies.
  - Tau interpretation: ready-node concurrency belongs in a bounded scheduler with isolated artifact dirs, timeout/max-attempt limits, and join validation.

## Role Workflows And Verification

- [MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework](https://arxiv.org/abs/2308.00352) argues for structured role workflows and intermediate verification rather than naive agent chaining.
  - Tau interpretation: creator/reviewer/goal-guardian motifs are DAG patterns, and reviewer receipts must compare work against the immutable goal.

## Distributed / Stigmergic Routing

- [SwarmSys: Decentralized Swarm-Inspired Agents for Scalable and Adaptive Reasoning](https://arxiv.org/abs/2510.10047) explores explorer/worker/validator roles plus pheromone-inspired reinforcement for adaptive multi-agent coordination.
  - Tau interpretation: "pheromones" should be local route-signal receipts derived from validators and proof artifacts, not model confidence.
- [Efficient and Interpretable Multi-Agent LLM Routing via Ant Colony Optimization](https://arxiv.org/abs/2603.12933) models multi-agent routing as semantic-conditioned path selection with quality-gated routing-memory updates.
  - Tau interpretation: route memory updates should be quality-gated and auditable, with dry-run/proposal modes before persistent Memory sync.

## Video Design Context

- [Distributed Cognition: The New Science of Non-Biological Intelligence](https://www.youtube.com/watch?v=DsfxdwZdNf0) is recorded as a design-context video reference, not as closure proof.
  - Tau interpretation: distributed cognition is useful framing for separating planner, worker, reviewer, route-memory, and human-goal roles, but Tau still requires explicit DAG contracts, receipts, validators, and deterministic local proof.
  - Boundary: title and URL are recorded; transcript-specific claims, channel/date metadata, and detailed concept extraction require a separate transcript or source receipt before being used as implementation evidence.

## Cautionary Constraint

- [The Bystander Effect in Multi-Agent Reasoning: Quantifying Cognitive Loafing in Collaborative Interactions](https://arxiv.org/abs/2605.10698) warns that unstructured multi-agent interaction can degrade independent reasoning under social pressure.
  - Tau interpretation: consensus is not proof; independent validator receipts, dissent motifs, and fail-closed evidence gates are required.

## Design Constraints For Tau

1. The immutable goal stays outside agent control.
2. DAG contracts are the workflow authority; prose is not.
3. Receipts, evidence artifacts, and validators are the audit surface.
4. Adaptive expansion is proposal-first and bounded by policy.
5. Concurrent branches are non-mutating until branch locks exist.
6. Route learning starts as local signal receipts before Memory sync.
7. Human or goal-guardian review is required for goal/scope drift.
8. Hidden chain-of-thought is not an input to Tau validation.

## Current Implementation Boundary

Implemented or partially implemented in Tau:

- `tau.dag_contract.v1` project DAGs.
- Creator/reviewer loops with immutable-goal reviewer checks.
- Bounded ready-queue concurrency for local non-mutating branches.
- Failure receipts for timeout, non-JSON output, max-attempt exhaustion,
  malformed DAG contracts, route drift, and policy-blocked branches.
- Guarded adaptive expansion, branch-lock validation, and route-memory sync
  receipt surfaces.

Still separate proof rungs:

- Runtime DAG mutation during an active run.
- Provider-live retry chaos across all failure modes.
- Mutating/provider concurrent branches with enforced branch locks.
- Production route-learning policy.
- Semantic model quality.
