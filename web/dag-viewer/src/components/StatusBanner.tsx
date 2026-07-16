import { AlertTriangle, CircleCheck, Radio, Unplug } from "lucide-react";
import type { DagSnapshot } from "../types";

export function StatusBanner({ snapshot, connected }: { snapshot: DagSnapshot; connected: boolean }) {
  const accepted = snapshot.run_status === "PASS";
  const Icon = !connected ? Unplug : accepted ? CircleCheck : snapshot.run_status === "BLOCKED" ? AlertTriangle : Radio;
  return (
    <header className="status-banner" data-qid="dag:status:banner">
      <div className="status-banner__identity">
        <Icon aria-hidden="true" size={18} />
        <div><strong>{snapshot.run_id}</strong><span>journal {snapshot.journal_sequence}</span></div>
      </div>
      <div className="status-banner__state">
        <span>{connected ? snapshot.view.mode : "DISCONNECTED"}</span>
        <span>{snapshot.projection_state} · {snapshot.run_status}{snapshot.run_verdict ? ` · ${snapshot.run_verdict}` : ""}</span>
      </div>
    </header>
  );
}
