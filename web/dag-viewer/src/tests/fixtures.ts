import type { CausalExplanation, DagManifest, DagSnapshot } from "../types";

export const manifest: DagManifest = {
  schema: "tau.dag_view_manifest.v1",
  run_id: "run-1",
  plan_id: "plan-1",
  plan_sha256: "sha256:plan",
  source_available: true,
  source_status: "AVAILABLE",
  source_dag: {
    schema: "tau.generic_dag_spec.v1",
    goal: { statement: "Keep the human-owned goal immutable." },
  },
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
  schema: "tau.dag_view_snapshot.v2",
  run_id: "run-1",
  plan_sha256: "sha256:plan",
  journal_sequence: 8,
  view: { mode: "LIVE", sequence: 8, sequence_created_at: "2026-01-01T00:00:00Z" },
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
      }, correction: null, causal_explanation_id: "explanation-creator", updated_sequence: 8,
    },
    {
      node_id: "publish", node_kind: "command",
      scheduler: { state: "pending", attempt: 0, max_attempts: 1 },
      runtime: { state: "UNKNOWN", liveness: "UNKNOWN", confidence: "UNKNOWN", last_event_id: null },
      admission: { state: "not_started", accepted: false, receipt_refs: [] }, transaction: null, correction: null,
      causal_explanation_id: "explanation-publish", updated_sequence: 8,
    },
  ],
  edges: [
    { edge_id: "creator-publish", state: "pending", causal_explanation_id: "explanation-edge-1" },
    { edge_id: "publish-human", state: "pending", causal_explanation_id: "explanation-edge-2" },
  ],
  terminals: [{ terminal_id: "human", state: "pending", causal_explanation_id: "explanation-human" }],
  routes: [], joins: [], corrections: [], attention_items: [], highest_priority_attention_id: null,
  recent_events: [{ seq: 8, event_type: "dag_diagnostic_event_appended", entity_type: "node", entity_id: "creator", attempt_id: null, payload: { phase: "reviewer" } }],
  proof_scope: { proves: ["journal projection"], does_not_prove: ["semantic correctness"] },
};

export const explanation: CausalExplanation = {
  schema: "tau.dag_causal_explanation.v1",
  explanation_id: "explanation-creator",
  run_id: "run-1",
  as_of_sequence: 8,
  subject: { kind: "NODE", id: "creator" },
  projected_state: "running",
  reason_code: "attempt_dispatched",
  summary_code: "node_running",
  trigger_sequence: 6,
  references: [
    { kind: "JOURNAL_EVENT", relation: "CAUSED_BY", reference_id: "journal:6", journal_sequence: 6 },
  ],
  chain: [{ step: 1, relation: "CAUSED_BY", reference_id: "journal:6" }],
  proof_scope: { proves: ["prefix-derived"], does_not_prove: ["semantic correctness"] },
};
