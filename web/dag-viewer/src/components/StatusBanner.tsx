import { AlertTriangle, CircleCheck, Radio, Unplug } from "lucide-react";
import type { ReactNode } from "react";
import type { DagManifest, DagSnapshot, JsonValue } from "../types";

function goalStatement(source: JsonValue): string {
  if (!source || typeof source !== "object" || Array.isArray(source)) return "Goal unavailable";
  const goal = source.goal;
  if (!goal || typeof goal !== "object" || Array.isArray(goal)) return "Goal unavailable";
  if (typeof goal.statement === "string") return goal.statement;
  return typeof goal.summary === "string" ? goal.summary : "Goal unavailable";
}

function runIdentity(runId: string): { logicalRunId: string; generation: number } {
  const marker = ":generation:";
  const markerIndex = runId.indexOf(marker);
  if (!runId || markerIndex < 0 || markerIndex !== runId.lastIndexOf(marker)) {
    return { logicalRunId: runId, generation: 0 };
  }
  const suffix = runId.slice(markerIndex + marker.length);
  if (!/^[1-9][0-9]*$/.test(suffix)) return { logicalRunId: runId, generation: 0 };
  return { logicalRunId: runId.slice(0, markerIndex), generation: Number(suffix) };
}

export function StatusBanner({
  manifest,
  snapshot,
  connected,
  actions,
}: {
  manifest: DagManifest;
  snapshot: DagSnapshot;
  connected: boolean;
  actions?: ReactNode;
}) {
  const accepted = snapshot.run_status === "PASS";
  const Icon = !connected ? Unplug : accepted ? CircleCheck : snapshot.run_status === "BLOCKED" ? AlertTriangle : Radio;
  const goal = manifest.goal.summary ?? goalStatement(manifest.source_dag);
  const identity = runIdentity(snapshot.run_id);
  return (
    <header className="status-banner" data-qid="dag:status:banner">
      <div className="status-banner__identity">
        <Icon aria-hidden="true" size={18} />
        <div>
          <strong data-qid="dag:status:logical-run-id">{identity.logicalRunId}</strong>
          <span data-qid="dag:status:goal" title={goal}>{goal}</span>
        </div>
      </div>
      <div className="status-banner__right">
        <div className="status-banner__state">
          <span>{connected ? snapshot.view.mode : "DISCONNECTED"}</span>
          <span data-qid="dag:status:physical-generation">
            generation {identity.generation} · journal {snapshot.journal_sequence} · {snapshot.projection_state} · {snapshot.run_status}{snapshot.run_verdict ? ` · ${snapshot.run_verdict}` : ""}
          </span>
        </div>
        {actions}
      </div>
    </header>
  );
}
