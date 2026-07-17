import { AlertTriangle, CircleCheck, Radio, Unplug } from "lucide-react";
import type { DagManifest, DagSnapshot, JsonValue } from "../types";

function goalStatement(source: JsonValue): string {
  if (!source || typeof source !== "object" || Array.isArray(source)) return "Goal unavailable";
  const goal = source.goal;
  if (!goal || typeof goal !== "object" || Array.isArray(goal)) return "Goal unavailable";
  return typeof goal.statement === "string" ? goal.statement : "Goal unavailable";
}

export function StatusBanner({
  manifest,
  snapshot,
  connected,
}: {
  manifest: DagManifest;
  snapshot: DagSnapshot;
  connected: boolean;
}) {
  const accepted = snapshot.run_status === "PASS";
  const Icon = !connected ? Unplug : accepted ? CircleCheck : snapshot.run_status === "BLOCKED" ? AlertTriangle : Radio;
  const goal = goalStatement(manifest.source_dag);
  return (
    <header className="status-banner" data-qid="dag:status:banner">
      <div className="status-banner__identity">
        <Icon aria-hidden="true" size={18} />
        <div>
          <strong>{snapshot.run_id}</strong>
          <span data-qid="dag:status:goal" title={goal}>{goal}</span>
        </div>
      </div>
      <div className="status-banner__state">
        <span>{connected ? snapshot.view.mode : "DISCONNECTED"}</span>
        <span>journal {snapshot.journal_sequence} · {snapshot.projection_state} · {snapshot.run_status}{snapshot.run_verdict ? ` · ${snapshot.run_verdict}` : ""}</span>
      </div>
    </header>
  );
}
