export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export type PlanNode = {
  node_id: string;
  role: string;
  adapter: { kind: string; config: Record<string, JsonValue> };
  retry_policy: { max_attempts: number };
};

export type PlanEdge = {
  edge_id: string;
  source_node_id: string;
  target: { id: string; kind: string };
};

export type PlanTerminal = {
  terminal_id: string;
  kind: string;
  origin: string;
};

export type ReceiptIndexEntry = {
  receipt_id: string;
  schema: string;
  path_display: string;
  sha256: string;
  available: boolean;
};

export type DagManifest = {
  schema: "tau.dag_view_manifest.v1";
  run_id: string;
  plan_id: string;
  plan_sha256: string;
  source_available: boolean;
  source_status: string;
  source_dag: JsonValue;
  dag_plan: JsonValue;
  graph: {
    nodes: PlanNode[];
    edges: PlanEdge[];
    terminals: PlanTerminal[];
    routes: Array<Record<string, JsonValue>>;
    joins: Array<Record<string, JsonValue>>;
  };
  receipt_index: ReceiptIndexEntry[];
  proof_scope: { proves: string[]; does_not_prove: string[] };
};

export type LiveNode = {
  node_id: string;
  node_kind: string;
  scheduler: { state: string; attempt: number; max_attempts: number };
  runtime: { state: string; liveness: string; confidence: string; last_event_id: string | null };
  admission: { state: string; accepted: boolean; receipt_refs: string[] };
  transaction: TransactionProjection | null;
  correction: CorrectionProjection | null;
  causal_explanation_id: string;
  updated_sequence: number;
};

export type CorrectionProjection = {
  incident_id: string;
  state: string;
  journal_sequence: number;
  incident: Record<string, JsonValue>;
  intent: Record<string, JsonValue> | null;
  action_receipt: Record<string, JsonValue> | null;
  verification: Record<string, JsonValue> | null;
  causal_explanation_id: string;
};

export type RouteProjection = {
  schema: "tau.dag_route_projection.v1";
  route_id: string;
  source_node_id: string;
  state: string;
  reason_code: string;
  decision_sequence: number | null;
  decision_receipt_id: string | null;
  selected_edge_ids: string[];
  skipped_edge_ids: string[];
  causal_explanation_id: string;
};

export type JoinProjection = {
  schema: "tau.dag_join_projection.v1";
  join_node_id: string;
  state: string;
  reason_code: string;
  deadline_state: string;
  decision: string | null;
  decision_sequence: number | null;
  incoming: Array<Record<string, JsonValue>>;
  causal_explanation_id: string;
};

export type AttentionItem = {
  schema: "tau.dag_attention_item.v1";
  attention_id: string;
  severity: "BLOCKER" | "ACTION_REQUIRED" | "WARNING";
  state: "OPEN" | "RESOLVED";
  reason_code: string;
  subject: { kind: string; id: string };
  opened_sequence: number;
  resolved_sequence: number | null;
  required_action_code: string;
  causal_explanation_id: string;
};

export type CausalExplanation = {
  schema: "tau.dag_causal_explanation.v1";
  explanation_id: string;
  run_id: string;
  as_of_sequence: number;
  subject: { kind: string; id: string };
  projected_state: string;
  reason_code: string;
  summary_code: string;
  trigger_sequence: number;
  references: Array<Record<string, JsonValue>>;
  chain: Array<{ step: number; relation: string; reference_id: string }>;
  proof_scope: { proves: string[]; does_not_prove: string[] };
};

export type TransactionAttempt = {
  attempt: number;
  producer_state?: string;
  candidate_manifest_sha256?: string;
  validator_status?: string;
  reviewer_verdict?: string;
  review_feedback_sha256?: string;
  revision_instruction?: string;
  feedback_consumed?: boolean;
};

export type TransactionProjection = {
  transaction_id?: string;
  current_attempt: number;
  max_attempts: number;
  state: string;
  accepted_manifest_sha256?: string;
  attempts: TransactionAttempt[];
};

export type JournalEvent = {
  seq: number;
  event_type: string;
  entity_type: string;
  entity_id: string;
  attempt_id: string | null;
  payload: Record<string, JsonValue>;
};

export type DagSnapshot = {
  schema: "tau.dag_view_snapshot.v2";
  run_id: string;
  plan_sha256: string;
  journal_sequence: number;
  view: { mode: "LIVE" | "HISTORICAL"; sequence: number; sequence_created_at: string | null };
  snapshot_sha256: string;
  run_status: string;
  run_verdict: string | null;
  projection_state: string;
  nodes: LiveNode[];
  edges: Array<{ edge_id: string; state: string; causal_explanation_id: string }>;
  terminals: Array<{ terminal_id: string; state: string; causal_explanation_id: string }>;
  routes: RouteProjection[];
  joins: JoinProjection[];
  corrections: CorrectionProjection[];
  attention_items: AttentionItem[];
  highest_priority_attention_id: string | null;
  recent_events: JournalEvent[];
  proof_scope: { proves: string[]; does_not_prove: string[] };
};

export type DagEventPage = {
  schema: "tau.dag_live_event.v1";
  run_id: string;
  after_sequence: number;
  events: JournalEvent[];
};

export type ReceiptProjection = {
  schema: "tau.dag_viewer_receipt_projection.v1";
  receipt_id: string;
  source_schema: string;
  source_sha256: string;
  receipt: JsonValue;
};

export type QueryItem = {
  entity_kind: string;
  entity_id: string;
  node_id: string | null;
  attempt: number | null;
  event_type: string | null;
  receipt_schema: string | null;
  state: string;
  attention_state: string | null;
  attention_severity: string | null;
  sequence: number;
  preview: string;
};

export type DagQueryResult = {
  schema: "tau.dag_view_query_result.v1";
  run_id: string;
  as_of_sequence: number;
  query: Record<string, JsonValue>;
  items: QueryItem[];
  next_cursor: string | null;
  result_count: number;
  total_match_count: number;
};

export type ComparisonSide = {
  run_id: string;
  reference: Record<string, JsonValue>;
  sequence: number;
  projection: Record<string, JsonValue>;
  truncated: boolean;
};

export type DagComparison = {
  schema: "tau.dag_view_comparison.v1";
  kind: "SEQUENCE_PAIR" | "ATTEMPT_PAIR" | "CORRECTION_BEFORE_AFTER";
  run_id: string;
  as_of_sequence: number;
  left: ComparisonSide;
  right: ComparisonSide;
  changes: Array<{
    field: string;
    change: "ADDED" | "REMOVED" | "CHANGED";
    left?: JsonValue;
    right?: JsonValue;
  }>;
  truncated: boolean;
  comparison_sha256: string;
};
