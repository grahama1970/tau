# Tau Immutable Goal

**Status:** Active
**Owner:** Human

## Goal

Tau lets a human launch and supervise a small ladder of real, goal-locked agent
DAGs, from simple to durable and failure-recovering, while showing truthful
progress, accepted evidence, blockers, and required human decisions in one
easy-to-use interface.

Humans decide what must happen. Agents propose and execute bounded work. Tau
decides what counts as admissible progress.

## Required Product Outcome

A human can choose and run five canonical Tau DAGs without repository
archaeology. Each DAG produces a recognizable useful result, preserves the
human-owned goal across every node and attempt, and exposes its current state in
the same progress viewer.

The five-DAG ladder must increase in both node count and topology complexity:

1. **Simple linear DAG** - a minimal sequential path produces and validates one
   useful artifact.
2. **Multi-step sequential DAG** - several dependent stages transform accepted
   output into a final validated result.
3. **Concurrent fan-out/fan-in DAG** - a sequential setup stage releases
   independent workers concurrently, then a join validates their combined
   result.
4. **Mixed sequential/concurrent DAG** - sequential preparation feeds parallel
   branches; typed conditions can send individual branches through
   `PASS`, `REVISE`, retry, or terminal-failure routes; accepted branches join
   before an exact human-gated side effect with rollback protection.
5. **Durable mixed-topology DAG** - multiple sequential and concurrent phases
   survive interruption, resume accepted work without duplicate effects, rerun
   only affected nodes or branches, and stop at a final human release boundary.

The DAGs are product workflows, not test fixtures. Receipts, commits, tickets,
and screenshots may support their results, but none of those are the result by
themselves.

## Shared DAG Invariants

Every canonical DAG must:

- start from an explicit human-owned goal and completion criteria;
- preserve the active goal version and hash through every handoff, retry,
  restart, branch, and subagent;
- reject agent attempts to change the goal, broaden scope, invent routes, or
  claim completion without required evidence;
- use bounded execution with explicit terminal states;
- distinguish model claims from independently accepted evidence;
- make retries, route decisions, joins, approvals, side effects, and recovery
  inspectable;
- fail closed with a precise blocker and next required human decision;
- preserve accepted work across retries and restart;
- state what each proof demonstrates and what remains unverified.

## Dynamic React Flow Progress Outcome

The human's primary question is:

> What is Tau doing toward my goal right now, what has actually been accepted,
> what is blocked, and do I need to decide anything?

One shared React Flow viewer must answer that question for all five DAGs from
authoritative runtime state. The graph must update dynamically while a DAG is
running so the human can watch work move through sequential stages, concurrent
branches, joins, retries, approval waits, recovery, and completion without
manually reloading the page.

The React Flow view must show:

- the human goal and selected DAG;
- run identity and current overall state;
- graph structure and dependencies;
- active, pending, accepted, failed, blocked, skipped, cancelled, and
  superseded nodes;
- node attempts, elapsed time, retries, and current work;
- accepted outputs and their evidence;
- route and join decisions;
- exact blocker reasons and failed checks;
- pending human approvals or decisions;
- resume, recovery, and targeted-repair history;
- final result and explicit proof boundaries.

Node and edge changes must be driven by fresh authoritative run state. A static
fixture, post-run snapshot, replay-only visualization, DOM assertion, or manual
page refresh does not prove dynamic progress. Missing authoritative state is
displayed as missing or unavailable, never inferred as healthy, running, or
complete from model prose, Git history, or absent errors.

## Ease Of Use

A new evaluator must be able to:

1. discover the five canonical DAGs;
2. launch any DAG through one documented command or control;
3. open its progress view without locating receipt directories manually;
4. identify the active node, accepted work, blocker, and required decision;
5. inspect the final useful output and supporting evidence.

The normal path must not require editing JSON by hand, searching Git history,
or understanding Tau's internal proof-directory layout.

## Completion Criteria

This goal is complete only when:

- all five canonical DAGs execute against their intended real runtimes and
  produce their intended useful outputs;
- the ladder visibly progresses from a simple linear path to multi-step
  sequential, concurrent fan-out/fan-in, and mixed sequential/concurrent
  topologies;
- each DAG has a deterministic acceptance contract and a demonstrated negative
  or failure path;
- the advanced DAG demonstrates crash-safe resume, no duplicate accepted
  effects, and targeted repair that leaves unaffected accepted work untouched;
- the human-gated DAG demonstrates an exact approval boundary and rollback;
- the same viewer renders fresh authoritative progress for all five DAGs;
- the React Flow graph visibly updates during execution without manual reload,
  including sequential node transitions, simultaneous concurrent branches,
  joins, retries, blocked states, human approval waits, resume, and completion;
- viewer verification includes inspected desktop and mobile screenshots plus
  a browser workflow trace showing the same run advance through running,
  concurrent, blocked or approval-waiting, resumed, and completed states;
- a clean checkout can launch the DAGs and viewer using documented commands;
- final proof reports `mocked: no`, `live: yes`, what was exercised, and what
  remains unverified;
- the human accepts that the workflows and viewer make Tau's value and state
  understandable without repository archaeology.

## Non-Goals

Tau's goal is not to:

- maximize commits, branches, pull requests, issues, receipts, tests, or agents;
- treat Git activity as project progress;
- build a generic agent framework or an unbounded autonomous swarm;
- trust model consensus, reviewer prose, or a producer's `PASS` field;
- claim legal compliance, model truthfulness, or perfect sandbox isolation;
- build separate viewers or bespoke orchestration paths for each example;
- add adjacent features that do not directly unblock a completion criterion
  above.

## Critical-Path Rule

Every implementation task must name the completion criterion it advances and
the inspectable artifact or behavior it will produce. Work that advances none
of the criteria is a side quest and must not be performed.

Git commits preserve accepted work. They are retention evidence, not the goal,
the progress model, or proof of product completion.
