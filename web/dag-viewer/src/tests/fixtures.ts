import type { CausalExplanation, DagManifest, DagSnapshot, SelectedNodeInspectorProjection } from "../types";

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
  goal: {
    kind: "full",
    goal_id: "repository-readiness:fixture",
    goal_version: 1,
    goal_hash: "sha256:goal",
    summary: "Determine whether this checkout is ready for focused work.",
    completion_criteria: ["Publish only after validation passes."],
  },
  workflow: {
    schema: "tau.workflow_metadata.v1",
    workflow_id: "repository-readiness",
    workflow_version: 1,
    title: "Repository Readiness",
    summary: "Inspect, validate, and publish repository readiness.",
    topology: "LINEAR",
    result_node_id: "publish",
    result_schema: "tau.repository_readiness_report.v1",
  },
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
      result: { summary: null, accepted_output: null, blocker_codes: [], started_at: null, finished_at: null, duration_seconds: null },
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
      result: { summary: null, accepted_output: null, blocker_codes: [], started_at: null, finished_at: null, duration_seconds: null },
      causal_explanation_id: "explanation-publish", updated_sequence: 8,
    },
  ],
  edges: [
    { edge_id: "creator-publish", state: "pending", causal_explanation_id: "explanation-edge-1" },
    { edge_id: "publish-human", state: "pending", causal_explanation_id: "explanation-edge-2" },
  ],
  terminals: [{ terminal_id: "human", state: "pending", causal_explanation_id: "explanation-human" }],
  routes: [], joins: [], corrections: [], attention_items: [], highest_priority_attention_id: null,
  run_summary: { active_node_ids: ["creator"], accepted_node_ids: [], highest_priority_blocker: null, final_result: null },
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

export const nodeInspector: SelectedNodeInspectorProjection = {
  schema: "tau.selected_node_inspector_projection.v1",
  run_id: "run-1",
  plan_id: "plan-1",
  plan_sha256: "sha256:plan",
  node_id: "creator",
  attempt: 1,
  attempt_id: "attempt-creator-1",
  journal_sequence: 8,
  projection_key: "sha256:selected-node-key",
  projection_sha256: "sha256:selected-node",
  view: { mode: "LIVE", sequence: 8, sequence_created_at: "2026-01-01T00:00:00Z" },
  contract: { status: "available", role: "producer", adapter: { kind: "command" }, required_evidence: ["tau.node_completion_boundary.v1"] },
  accepted_inputs: { status: "not_available", schema: "tau.node_input_manifest.v1", reason: "no_input_bindings_declared", bindings: [], omissions: [] },
  completion_boundary: {
    status: "available",
    schema: "tau.node_completion_boundary.v1",
    value: {
      checked_scope: [{ id: "scope", statement: "repo inspected" }],
      evidence_gaps: [{ id: "gap-1", statement: "follow-up validator needed" }],
      proves: [{ id: "prove-1", statement: "local artifact exists" }],
      does_not_prove: [{ id: "does-not", statement: "semantic quality" }],
    },
  },
  review_scope: { status: "available", schema: "tau.review_scope.v1", value: { state: "stale", reviewer_verdict: "FAIL", reviewed_nodes: ["creator"] } },
  workspace_freshness: { status: "available", schema: "tau.workspace_freshness.v1", value: { state: "unresolved", read_set: ["src/a.py"] } },
  worker: { status: "available", schema: "tau.worker_assignment.v1", value: { state: "quarantined", generation: 2 } },
  accepted_evidence_and_artifacts: {
    status: "available",
    items: [{ kind: "accepted_output", schema: "tau.node_completion_boundary.v1", sha256: "sha256:accepted" }],
    receipts: [{ receipt_id: "receipt-1", schema: "tau.test_receipt.v1", path_display: "receipts/test.json", sha256: "sha256:receipt", available: true }],
    missing_required_evidence: [],
  },
  diagnostics: { status: "available", authority: "diagnostic_only", can_settle_node: false, events: [{ seq: 7, event_type: "dag_diagnostic_event_appended" }] },
  attention: [
    { severity: "ACTION_REQUIRED", code: "stale_review", section: "review_scope" },
    { severity: "BLOCKER", code: "unresolved_stale_read", section: "workspace_freshness" },
    { severity: "BLOCKER", code: "quarantined_worker", section: "worker" },
  ],
  read_only: true,
  mutation_controls: [],
  proof_scope: { proves: ["backend projection"], does_not_prove: ["diagnostics settle nodes"] },
};
