import type { DagManifest, DagSnapshot } from "../types";

export const manifest: DagManifest = {
  schema: "tau.dag_view_manifest.v1",
  run_id: "run-1",
  plan_id: "plan-1",
  plan_sha256: "sha256:plan",
  source_available: true,
  source_status: "AVAILABLE",
  source_dag: { schema: "tau.generic_dag_spec.v1" },
  dag_plan: { schema: "tau.dag_plan.v1" },
  graph: {
    nodes: [
      { node_id: "creator", role: "producer", adapter: { kind: "command", config: {} }, retry_policy: { max_attempts: 2 } },
      { node_id: "publish", role: "consumer", adapter: { kind: "command", config: {} }, retry_policy: { max_attempts: 1 } },
    ],
    edges: [
      { edge_id: "creator-publish", source_node_id: "creator", target: { id: "publish", kind: "node" } },
      { edge_id: "publish-human", source_node_id: "publish", target: { id: "human", kind: "terminal" } },
    ],
    terminals: [{ terminal_id: "human", kind: "external", origin: "declared" }], routes: [], joins: [],
  },
  receipt_index: [],
  proof_scope: { proves: ["journal projection"], does_not_prove: ["semantic correctness"] },
};

export const snapshot: DagSnapshot = {
  schema: "tau.dag_live_snapshot.v1",
  run_id: "run-1",
  journal_sequence: 8,
  snapshot_sha256: "sha256:snapshot",
  run_status: "RUNNING",
  run_verdict: null,
  projection_state: "LIVE",
  nodes: [
    {
      node_id: "creator", node_kind: "command",
      scheduler: { state: "running", attempt: 1, max_attempts: 2 },
      runtime: { state: "ALIVE", liveness: "ALIVE", confidence: "PROCESS", last_event_id: null },
      admission: { state: "awaiting_receipt", accepted: false, receipt_refs: [] },
      transaction: {
        transaction_id: "tx-1", current_attempt: 1, max_attempts: 2, state: "AWAITING_RECEIPT",
        attempts: [{ attempt: 1, producer_state: "PASS", validator_status: "PASS", reviewer_verdict: "REVISE" }],
      }, updated_sequence: 8,
    },
    {
      node_id: "publish", node_kind: "command",
      scheduler: { state: "pending", attempt: 0, max_attempts: 1 },
      runtime: { state: "UNKNOWN", liveness: "UNKNOWN", confidence: "UNKNOWN", last_event_id: null },
      admission: { state: "not_started", accepted: false, receipt_refs: [] }, transaction: null, updated_sequence: 8,
    },
  ],
  edges: [{ edge_id: "creator-publish", state: "pending" }, { edge_id: "publish-human", state: "pending" }],
  terminals: [{ terminal_id: "human", state: "pending" }], attention_items: [],
  recent_events: [{ seq: 8, event_type: "dag_diagnostic_event_appended", entity_type: "node", entity_id: "creator", attempt_id: null, payload: { phase: "reviewer" } }],
  proof_scope: { proves: ["journal projection"], does_not_prove: ["semantic correctness"] },
};
