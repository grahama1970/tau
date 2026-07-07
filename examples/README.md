# Tau Examples

Copyable examples for Tau's zero-trust, coding containment, and
visible-provider lanes.

| Example | What it exercises | Boundary |
| --- | --- | --- |
| [`zero-trust-basic`](zero-trust-basic/) | Local policy/data-boundary preflight through `tau zero-trust-doctor`. | No subagent dispatch, sandbox proof, compliance certification, or provider call. |
| [`coding-reliability-basic`](coding-reliability-basic/) | Hash-bound patch receipts, diagnostics, structured review findings, dry-run commit planning, and orchestration reliability. | Local receipt evidence only; no agent truthfulness, semantic code correctness, provider call, or GitHub mutation. |
| [`omp-worker`](omp-worker/) | Bounded `oh-my-pi` worker work order, dry-run RPC launch receipt, deterministic apply-launch receipt, and worker result validation. | Uses a local `fake-omp` fixture by default; no real OMP execution, semantic code correctness, or provider/model quality. |
| [`scillm-worker`](scillm-worker/) | Bounded SciLLM/OpenCode-serve work order, dry-run launch receipt, deterministic apply-launch receipt, auth redaction, and worker result validation. | Uses a local SciLLM-compatible fixture server by default; no live SciLLM/OpenCode execution, semantic code correctness, or provider/model quality. |
| [`itar-grade-containment`](itar-grade-containment/) | Controlled-boundary fail-closed checks, review package validation, and zero-trust red-team receipts. | Local containment evidence only; no ITAR compliance, legal identity, live Docker isolation, live provider execution, GitHub mutation, or Memory sync. |
| [`herdr-visible-provider`](herdr-visible-provider/) | Herdr-visible provider readiness through the real-world sanity lane. | Requires local Herdr/provider tooling; visible panes are evidence, not truth. |
