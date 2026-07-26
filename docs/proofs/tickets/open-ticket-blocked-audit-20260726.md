# Open Ticket Blocked Audit

Timestamp: 2026-07-26T22:32:39Z

Repository: `grahama1970/tau`

## Scope

This audit records the current state of open Tau GitHub tickets after the
regression-ticket sweep and the #159/#160 prerequisite slices were pushed to
`main`.

Goal criterion checked: every open ticket is either resolved/closed or explicitly
blocked with a concrete blocker.

## Commands

```text
$ gh issue list --repo grahama1970/tau --state open --limit 100 --json number,title,labels --jq '{open_count:length, blocked_label_count:([.[] | select(([.labels[].name] | index("maintainer-blocked")) and ([.labels[].name] | index("needs-human")))] | length), nonblocked: [.[] | select((([.labels[].name] | index("maintainer-blocked")) | not) or ((([.labels[].name] | index("needs-human")) | not))) | {number,title,labels:[.labels[].name]}]}'
{"blocked_label_count":19,"nonblocked":[],"open_count":19}
```

```text
$ for n in 160 159 158 157 156 154 153 151 150 149 141 140 105 95 94 84 83 72 47; do
>   gh issue view "$n" --repo grahama1970/tau --json number,comments --jq '[.number, ([.comments[] | select((.body | test("(?i)(concrete blocker|blocked disposition|# blocker|blocker:|blocked rather than|blocked because|remains blocked|blocked until|blocked-by-dependency|blocked/deferred)")))] | length)] | @tsv'
> done
160	1
159	1
158	1
157	1
156	1
154	1
153	1
151	1
150	1
149	1
141	1
140	1
105	1
95	1
94	1
84	1
83	1
72	2
47	1
```

```text
$ git rev-parse HEAD
f7679bd0eaa3d4d2bdc09471a8f9b1070c161d3a

$ git ls-remote origin refs/heads/main
f7679bd0eaa3d4d2bdc09471a8f9b1070c161d3a	refs/heads/main
```

## Open Ticket Inventory

All open tickets are labeled both `maintainer-blocked` and `needs-human`.

- #160: roundtable/competition escalation; blocked on real panel runner, per-seat payload equality, round cap, convergence, and dissent synthesis.
- #159: anti-spiral ladder; blocked on full Memory/search ladder dependencies after detector slice landed.
- #158: voice I/O; blocked on external voice service architecture and TUI voice adapter contract.
- #157: browser handler; blocked on first-class Surf/browser-oracle node schema and receipt contract.
- #156: continuous codebase ingestion; blocked on idle scheduler plus Memory provenance/validity prerequisites.
- #154: Memory episode provenance; Tau-side source evidence slice exists, but full graph behavior is Memory-owned.
- #153: bi-temporal Memory validity; blocked on Memory graph schema/query behavior.
- #151: model/provider routing; blocked on Memory multi-hop routing and SciLLM provider-router design.
- #150: memory-first TUI epic; blocked on open child tickets.
- #149: tool-call recommender; blocked on Memory-owned `tool_chains` graph/retrieval surface.
- #141: TUI-to-TUI SSE/idle queue; blocked on peer-envelope/server contract and queue persistence schema.
- #140: memory-backed skill selector; blocked on prompt/Memory architecture change and graph retrieval contract.
- #105: live DAG viewer parent; blocked on external agent-skills child issue.
- #95: runtime conformance/adversarial matrix; blocked by #84 dependency gates.
- #94: durable endpoint reconciliation; blocked by #84 dependency gates.
- #84: runtime fleet parent; blocked on remaining children #94 and #95.
- #83: backend-neutral runtime parent; blocked as a multi-PR fleet parent.
- #72: runtime hardening epic; blocked as a program-level dependency tracker with open children.
- #47: persona-dream Phase 06 loop; blocked/deferred pending redesign against current Tau DAG runtime and proof-retention rules.

## Evidence Classification

mocked: no

live: yes, GitHub issue state was queried through `gh`.

What this proves:

- There are 19 open issues in `grahama1970/tau`.
- Every open issue currently has both `maintainer-blocked` and `needs-human`.
- Every open issue has at least one blocker/disposition comment matching the
  concrete-blocker audit vocabulary.
- The current local ticket worktree and remote `main` agreed before this audit
  document was written.

What this does not prove:

- The product-level Tau immutable goal is complete.
- The blocked feature/epic tickets are implemented.
- The external dependencies named by blocked issues are resolved.
- Human acceptance of the blocker dispositions.
