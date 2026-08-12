import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { DagManifest, DagSnapshot } from "../types";
import { useRegisterAction } from "../useRegisterAction";
import { qidPart } from "./runTimelineModel";

type PoolStatus = "ALL" | "RUNNING" | "SETTLED" | "FAILED";

const statuses: PoolStatus[] = ["ALL", "RUNNING", "SETTLED", "FAILED"];

function projectedStatus(snapshot: DagSnapshot): Exclude<PoolStatus, "ALL"> {
  const normalized = `${snapshot.run_status} ${snapshot.run_verdict ?? ""} ${snapshot.projection_state}`.toUpperCase();
  if (normalized.includes("FAIL") || normalized.includes("BLOCK")) return "FAILED";
  if (normalized.includes("PASS") || normalized.includes("COMPLETE") || normalized.includes("SETTLED")) return "SETTLED";
  return "RUNNING";
}

function durationLabel(snapshot: DagSnapshot): string {
  const durations = snapshot.nodes
    .map((node) => node.result.duration_seconds)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (durations.length === 0) return "duration pending";
  const total = durations.reduce((sum, value) => sum + value, 0);
  return `${total.toFixed(total >= 10 ? 0 : 1)}s`;
}

function modelTerms(manifest: DagManifest): string {
  return manifest.graph.nodes.map((node) => `${node.role} ${node.adapter.kind}`).join(" ");
}

export function OrchestrationPool({
  manifest,
  snapshot,
  selectedSequence,
  onSelectLive,
}: {
  manifest: DagManifest;
  snapshot: DagSnapshot;
  selectedSequence: number | null;
  onSelectLive: () => void;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<PoolStatus>("ALL");
  const runStatus = projectedStatus(snapshot);
  const goal = manifest.goal.summary ?? manifest.goal.goal_hash ?? "goal unavailable";
  const searchable = `${snapshot.run_id} ${manifest.plan_id} ${goal} ${modelTerms(manifest)}`.toLowerCase();
  const visible = (status === "ALL" || status === runStatus) && searchable.includes(query.trim().toLowerCase());
  const activeSteps = snapshot.run_summary.active_node_ids.length;
  const settledSteps = snapshot.run_summary.accepted_node_ids.length;
  const qid = `dag:pool:orchestration:${qidPart(snapshot.run_id)}`;
  const filteredCount = visible ? 1 : 0;

  useRegisterAction("dag:pool:search", {
    action: "DAG_ORCHESTRATION_SEARCH",
    label: "Search Orchestrations",
    description: "Filter the orchestration browser by run id, goal, role, or adapter.",
  });
  useRegisterAction("dag:pool:select-current", {
    action: "DAG_SELECT_CURRENT_ORCHESTRATION",
    label: "Select Current Orchestration",
    description: "Return the orchestration browser to the live run head.",
  });
  useRegisterAction("dag:pool:filter:all", {
    action: "DAG_POOL_FILTER_ALL",
    label: "All Orchestrations",
    description: "Show every orchestration card available to the current viewer.",
  });
  useRegisterAction("dag:pool:filter:running", {
    action: "DAG_POOL_FILTER_RUNNING",
    label: "Running Orchestrations",
    description: "Show running orchestration cards.",
  });
  useRegisterAction("dag:pool:filter:settled", {
    action: "DAG_POOL_FILTER_SETTLED",
    label: "Settled Orchestrations",
    description: "Show settled orchestration cards.",
  });
  useRegisterAction("dag:pool:filter:failed", {
    action: "DAG_POOL_FILTER_FAILED",
    label: "Failed Orchestrations",
    description: "Show failed orchestration cards.",
  });

  const statusChips = useMemo(() => statuses, []);

  return <aside className="orchestration-pool" data-qid="dag:pool:browser" aria-label="Orchestration browser">
    <header>
      <div><strong>Orchestrations</strong><span>current run projection</span></div>
      <code data-qid="dag:pool:count">{filteredCount}</code>
    </header>
    <label className="orchestration-pool__search">
      <Search aria-hidden="true" size={14} />
      <input
        data-qid="dag:pool:search"
        data-qs-action="DAG_ORCHESTRATION_SEARCH"
        title="Search current orchestration"
        aria-label="Search current orchestration"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search run, goal, role"
      />
    </label>
    <div className="orchestration-pool__filters" aria-label="Status filters">
      {statusChips.map((filter) => <button
        key={filter}
        type="button"
        className={status === filter ? "active" : ""}
        data-qid={`dag:pool:filter:${filter.toLowerCase()}`}
        data-qs-action={`DAG_POOL_FILTER_${filter}`}
        title={`Show ${filter.toLowerCase()} orchestrations`}
        aria-pressed={status === filter}
        onClick={() => setStatus(filter)}
      >{filter}</button>)}
    </div>
    <div className="orchestration-pool__list">
      {visible
        ? <button
          type="button"
          className="orchestration-card active"
          data-qid={qid}
          data-qs-action="DAG_SELECT_CURRENT_ORCHESTRATION"
          data-run-status={runStatus}
          title={`Open ${snapshot.run_id}`}
          onClick={onSelectLive}
        >
          <span><strong>{snapshot.run_id}</strong><em>{runStatus}</em></span>
          <small>{goal}</small>
          <code>seq #{snapshot.journal_sequence} · {durationLabel(snapshot)}</code>
          <span><b>{activeSteps}</b> active · <b>{settledSteps}</b> accepted · {selectedSequence === null ? "live" : `prefix #${selectedSequence}`}</span>
        </button>
        : <p>No current orchestration matches the filter.</p>}
    </div>
  </aside>;
}
