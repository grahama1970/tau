# Runtime Backend Contracts

Tau compiles every DAG node into an explicit `tau.runtime_requirement.v1`.
The requirement names the selected backend, interaction mode, required backend
capabilities, session scope, and acceptable observation sources. Backend choice
is therefore part of the immutable `DagPlan`; ambient terminal state does not
select a backend.

Project DAG nodes may set `runtime_backend` explicitly. During migration,
command-backed `executor: local` nodes default to `local`, and
`executor: provider` nodes default to the existing `herdr` provider lane. The
resolved backend is always persisted in `DagPlan`. Other executable executor
labels must declare `runtime_backend`; virtual and human nodes compile with
`backend: none` because they do not launch a runtime endpoint.

Commandless nodes that declare `persistent_subagent` require an interactive
runtime and default to `herdr` when `runtime_backend` is omitted. A node with an
inline `command_spec` remains a bounded one-shot Tau tick: the command wrapper
must emit the persistent-surface receipts, while the surface itself remains
context rather than proof. A DAG may name another registered interactive backend
explicitly; capability negotiation still blocks a backend that cannot satisfy
the requirement.

Runtime implementations register through `RuntimeBackendRegistry` and publish
`tau.runtime_backend_capabilities.v1`. Capability negotiation fails closed with
`tau.runtime_capability_decision.v1` when a backend is unknown or cannot satisfy
the compiled requirement. Backends positively declare
`supported_session_scopes`; an undeclared or misspelled scope is rejected rather
than relying on a denylist.

The current bounded-ready-queue also checks the compiled requirement before
using a legacy dispatch adapter. A local command adapter accepts only the
canonical local one-shot requirement, and the existing provider adapter accepts
only its canonical Herdr one-shot requirement. Interactive, persistent, unknown,
or otherwise mismatched requirements block before command artifacts or a
subprocess are created. Later issue slices replace these migration checks with
registered runtime implementations.

The common contract family also defines endpoint leases, submit receipts,
runtime events and state projections, reconciliation receipts, and Git worktree
leases. Delivery, runtime observation, and DAG completion remain separate:

Endpoint leases bind `goal_hash` to `DagPlan.runtime_goal_hash`, the complete
SHA-256 digest of the canonical goal binding. This remains available when a
legacy project declared a non-cryptographic goal label or a generic DAG omitted
`goal_hash`; backends must not copy those raw source values into a lease.

```text
backend accepted work order != node executed
runtime says done != receipt admitted
validated receipt -> Tau may advance the DAG
```

This first slice defines and validates contracts only. It does not migrate local
subprocess execution, implement Herdr or tmux adapters, persist runtime events,
or prove that a backend enforces its declared capabilities. Those steps are
tracked by the remaining children of issue #84.
