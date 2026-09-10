# Tau explainability package

This directory is the source-bound entry point for `$explain-project` questions about Tau.

Use it like this:

```bash
skills/explain-project/run.sh validate docs/explain/explainers.jsonl
skills/explain-project/run.sh list docs/explain/explainers.jsonl
skills/explain-project/run.sh ask docs/explain/explainers.jsonl --question "How does Tau execute a DAG durably?"
```

Each explainer record maps:

```text
question -> cockpit bullets -> Excalidraw node -> source range -> optional debugger stop
```

Editable boards live in `docs/explain/boards/`. They are deliberately Excalidraw-first so a developer can open and reshape the concept map before a polished SVG exists.

## Current concept diagrams

| Diagram ID | Board | Primary code |
| --- | --- | --- |
| `tau.agent-loop` | `docs/explain/boards/tau-agent-loop.excalidraw` | `src/tau_agent/loop.py`, `src/tau_agent/harness.py` |
| `tau.provider-boundary` | `docs/explain/boards/tau-provider-boundary.excalidraw` | `src/tau_ai/provider.py`, `src/tau_ai/events.py` |
| `tau.dag-runtime` | `docs/explain/boards/tau-dag-runtime.excalidraw` | `src/tau_coding/dag_runtime/model.py`, `scheduler.py`, `run_store.py`, `replay.py` |
| `tau.attempt-result-boundary` | `docs/explain/boards/tau-attempt-result-boundary.excalidraw` | `src/tau_coding/dag_runtime/attempt_result.py`, `scheduler.py` |
| `tau.native-agent-node` | `docs/explain/boards/tau-native-agent-node.excalidraw` | `src/tau_coding/dag_runtime/native_agent_dispatch.py`, `agent_node_adapter.py` |
| `tau.viewer-replay` | `docs/explain/boards/tau-viewer-replay.excalidraw` | `src/tau_coding/dag_runtime/replay.py`, `src/tau_coding/dag_viewer/` |
| `tau.dag-compilation` | `docs/explain/boards/tau-dag-compilation.excalidraw` | `src/tau_coding/dag_runtime/compiler.py`, `model.py` |
| `tau.scheduler-authority` | `docs/explain/boards/tau-scheduler-authority.excalidraw` | `src/tau_coding/dag_runtime/scheduler.py`, `transition.py` |
| `tau.journal-recovery` | `docs/explain/boards/tau-journal-recovery.excalidraw` | `src/tau_coding/dag_runtime/run_store.py`, `reconciliation.py`, `replay.py` |
| `tau.node-input-binding` | `docs/explain/boards/tau-node-input-binding.excalidraw` | `src/tau_coding/dag_runtime/node_input_manifest.py`, `artifact_reference.py`, `workspace_reads.py` |
| `tau.runtime-backend-boundary` | `docs/explain/boards/tau-runtime-backend-boundary.excalidraw` | `src/tau_coding/runtime_backends/contracts.py`, `registry.py`, `event_bridge.py` |
| `tau.worker-dispatch` | `docs/explain/boards/tau-worker-dispatch.excalidraw` | `src/tau_coding/dag_runtime/worker_assignment.py`, `resource_leases.py`, `worker_session_pool.py` |
| `tau.effect-settlement` | `docs/explain/boards/tau-effect-settlement.excalidraw` | `src/tau_coding/dag_runtime/effects.py`, `scheduler.py`, `reconciliation.py` |
| `tau.failure-repair` | `docs/explain/boards/tau-failure-repair.excalidraw` | `src/tau_coding/dag_runtime/triage_error_bridge.py`, `boundary_registry.py`, `correction.py` |

## Proof boundary

This package explains source structure and debugger/cockpit handoff points. It does not prove live provider availability, run a debugger, or claim Tau is developer-ready by itself.
