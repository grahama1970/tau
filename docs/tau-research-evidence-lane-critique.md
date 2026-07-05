# Tau Research Evidence Lane Critique

This critique assesses whether Tau makes its ArXiv / distributed-intelligence
research influences explicit and whether the repository has a durable path for
paper or video evidence.

## Assessment

The critique is materially correct for Tau before the current documentation
patch: Tau had a research/evidence lane and surrounding skill tooling, but the
README did not directly cite the specific ArXiv papers discussed as inspiration
for adaptive DAGs, distributed non-human intelligence, route signals, and
bounded swarm constraints.

It is no longer accurate to say Tau has no direct ArXiv references after the
current patch. The README now links to primary ArXiv references and points to
`docs/adaptive-dag-research-references.md` as a dedicated critique surface.

The YouTube video has a conservative design-reference entry now:
`Distributed Cognition: The New Science of Non-Biological Intelligence`
at `https://www.youtube.com/watch?v=DsfxdwZdNf0`. Tau records the title and URL
as design context only. Transcript-specific claims, channel/date metadata, and
detailed concept extraction still require a separate transcript/source receipt
before they can be used as implementation evidence.

## What Was Missing

Before this patch, Tau was research-paper-ready but not research-paper-explicit.

Present:

- A hard-stop research/escalation path in the project model: if proof keeps
  failing, Tau should preserve a help bundle, use Memory routing, and treat
  research output as design input rather than closure proof.
- An external research receipt lane: research-auditor work should require
  explicit authorization and source-bearing receipts before routing forward.
- Agent-skills tooling for paper/video/web research, including `arxiv` and
  `dogpile`.

Missing before remediation:

- Concrete README citations for the ArXiv papers that shaped the adaptive DAG
  direction.
- A stable file that maps each cited paper to Tau design constraints.
- A formal citation for the YouTube video, if it is intended as a design input.

## Related Tooling

The surrounding agent-skills ecosystem can support research intake:

- `skills/arxiv` is for arXiv search and paper extraction into Memory. Its
  contract requires a dynamic context file before arXiv work so paper selection
  is tied to the current implementation goal rather than generic research.
- `skills/dogpile` is a multi-source research aggregator over web, ArXiv,
  GitHub, YouTube, Wayback, and other lanes. It is useful for broad critique
  bundles, but Dogpile output is still review input, not Tau closure proof.

Tau should not silently treat either skill as proof. Research must become a
source-bearing receipt, then be reviewed against local repository evidence and
deterministic tests.

## Recommended Research / Paper Evidence Lane

Tau may consume ArXiv, paper, or video evidence only through this kind of
receipt-backed sequence:

1. Memory-routed research decision or explicit human research request.
2. Explicit authorization packet for fresh external research.
3. Source-bearing receipt such as `tau.external_research_receipt.v1` or
   `tau.research_source_receipt.v1`.
4. Reviewer validation that the research is relevant and not overclaimed.
5. Deterministic local proof before any closure or implementation claim.

Research output should be classified as:

- `design_input`: may inform a plan or schema.
- `implementation_constraint`: may shape local invariants.
- `evidence_candidate`: must still be verified by local artifacts.
- `not_closure_proof`: cannot close a task by itself.

## Current Remediation

This documentation patch adds:

- README section: `Research Influence: Adaptive DAGs`.
- Dedicated reference file:
  `docs/adaptive-dag-research-references.md`.
- Runtime receipt command: `tau research-source-receipt`, which validates a
  `tau.research_source_packet.v1` source packet and writes
  `tau.research_source_receipt.v1`.

The new reference surface cites:

- Graph of Thoughts.
- Adaptive Graph of Thoughts.
- An LLM Compiler for Parallel Function Calling.
- MetaGPT.
- SwarmSys.
- AMRO-S.
- The Bystander Effect in Multi-Agent Reasoning.

The Tau mapping is explicit: graph reasoning becomes receipt-backed DAG nodes;
adaptive expansion remains bounded and validated; parallel scheduling stays
dependency-aware; route reinforcement becomes local signal receipts; and
unstructured swarm consensus is not proof.

The runtime receipt lane is intentionally validation-only. It does not fetch
papers, call WebGPT/Dogpile/ArXiv, or allow research to mutate a DAG. It makes
research source metadata durable and review-required before a reviewer or
research-auditor can use it as design input.

## Remaining Gap

The YouTube video is now recorded as design context with title and URL. It still
needs transcript/source verification before Tau should cite specific claims from
the video:

```text
title: Distributed Cognition: The New Science of Non-Biological Intelligence
url: https://www.youtube.com/watch?v=DsfxdwZdNf0
video_id: DsfxdwZdNf0
retrieved_at: 2026-07-05
inspired_concepts:
  - distributed cognition framing for role-separated orchestration
tau_mapping:
  - planner/worker/reviewer/route-memory/human-goal separation
  - explicit receipts and validators remain required
does_not_prove:
  - runtime correctness
  - implementation readiness
  - closure
  - transcript-specific factual claims
```

Until transcript/source evidence exists, Tau should not cite detailed video
claims as evidence.
