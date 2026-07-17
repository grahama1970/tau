import type { DagManifest, DagSnapshot, JsonValue } from "../types";

type Props = {
  manifest: DagManifest;
  snapshot: DagSnapshot;
};

const textValue = (value: JsonValue | undefined) =>
  typeof value === "string" || typeof value === "number" ? String(value) : null;

const artifactRows = (value: Record<string, JsonValue> | null) => {
  const artifacts = value?.artifacts;
  if (!Array.isArray(artifacts)) return [];
  return artifacts.filter(
    (artifact): artifact is Record<string, JsonValue> =>
      typeof artifact === "object" && artifact !== null && !Array.isArray(artifact),
  );
};

const sourceGoalSummary = (value: JsonValue): string | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const goal = value.goal;
  if (!goal || typeof goal !== "object" || Array.isArray(goal)) return null;
  return textValue(goal.summary) ?? textValue(goal.statement);
};

export function RunOverview({ manifest, snapshot }: Props) {
  const workflow = manifest.workflow;
  const blocker = snapshot.run_summary.highest_priority_blocker;
  const result = snapshot.run_summary.final_result;
  const activeNodes = snapshot.run_summary.active_node_ids;
  const current = activeNodes.length > 0
    ? activeNodes.join(", ")
    : snapshot.run_status === "RUNNING"
      ? "Waiting for admissible work"
      : "Run settled";

  return (
    <section className="run-overview" data-qid="dag:overview" aria-label="Run overview">
      <div data-qid="dag:overview:workflow">
        <span>Workflow</span>
        <strong>{workflow?.title ?? "Uncatalogued DAG"}</strong>
        <small>{workflow ? `${workflow.topology} · ${workflow.workflow_id}` : "No workflow metadata"}</small>
      </div>
      <div data-qid="dag:overview:goal">
        <span>Human goal</span>
        <strong>{manifest.goal.summary ?? sourceGoalSummary(manifest.source_dag) ?? "Goal summary unavailable"}</strong>
      </div>
      <div data-qid="dag:overview:current">
        <span>Current</span>
        <strong>{current}</strong>
      </div>
      <div data-qid="dag:overview:result">
        <span>Result</span>
        {result ? (
          <>
            <strong>{textValue(result.summary) ?? "Accepted result"}</strong>
            {textValue(result.status) && <small>{textValue(result.status)}</small>}
            {artifactRows(result).map((artifact, index) => (
              <small key={`${textValue(artifact.sha256) ?? "artifact"}-${index}`} className="run-overview__artifact">
                {textValue(artifact.kind) ?? "artifact"} · {textValue(artifact.path_display) ?? textValue(artifact.path) ?? "path unavailable"} · {textValue(artifact.sha256) ?? "hash unavailable"}
              </small>
            ))}
          </>
        ) : <strong>No accepted final result</strong>}
      </div>
      {blocker && (
        <div className="run-overview__blocker" data-qid="dag:overview:blocker">
          <span>Exact blocker</span>
          <strong>{blocker.node_id}</strong>
          <code>{blocker.codes.length > 0 ? blocker.codes.join(", ") : "blocker_code_unavailable"}</code>
        </div>
      )}
    </section>
  );
}
