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
  journal_sequence: number;
  view: { mode: "LIVE" | "HISTORICAL"; sequence: number; sequence_created_at: string | null };
  snapshot_sha256: string;
  run_status: string;
  run_verdict: string | null;
  projection_state: string;
  nodes: LiveNode[];
  edges: Array<{ edge_id: string; state: string }>;
  terminals: Array<{ terminal_id: string; state: string }>;
  corrections: CorrectionProjection[];
  attention_items: Array<Record<string, JsonValue>>;
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
