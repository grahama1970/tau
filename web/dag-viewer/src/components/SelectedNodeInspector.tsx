import { Clipboard, Eye, ShieldAlert } from "lucide-react";
import type { InspectorSection, JsonValue, SelectedNodeInspectorProjection } from "../types";
import { JsonInspector } from "./JsonInspector";

type Props = {
  projection: SelectedNodeInspectorProjection | null;
};

const sections: Array<{ key: keyof SelectedNodeInspectorProjection; label: string }> = [
  { key: "contract", label: "Contract" },
  { key: "accepted_inputs", label: "Accepted Inputs" },
  { key: "completion_boundary", label: "Completion Boundary" },
  { key: "review_scope", label: "Review Scope" },
  { key: "workspace_freshness", label: "Workspace Freshness" },
  { key: "worker", label: "Worker" },
  { key: "accepted_evidence_and_artifacts", label: "Accepted Evidence and Artifacts" },
  { key: "diagnostics", label: "Diagnostics" },
];

function copyStable(value: string) {
  if (!value) return;
  void navigator.clipboard?.writeText(value);
}

function sectionStatus(section: InspectorSection): string {
  return section.status === "available" ? "available" : section.status.replaceAll("_", " ");
}

export function SelectedNodeInspector({ projection }: Props) {
  if (!projection) {
    return <div className="selected-node-inspector selected-node-inspector--empty">
      <Eye aria-hidden="true" />
      <span>Backend selected-node projection unavailable.</span>
    </div>;
  }
  const stableValues = [
    ["Run", projection.run_id],
    ["Node", projection.node_id],
    ["Attempt", projection.attempt_id ?? String(projection.attempt)],
    ["Plan", projection.plan_sha256],
    ["Projection", projection.projection_sha256],
  ];
  return <div className="selected-node-inspector" data-qid="dag:selected-node-inspector">
    <header>
      <div>
        <strong>{projection.node_id}</strong>
        <span>attempt {projection.attempt || "not started"} · journal {projection.journal_sequence}</span>
      </div>
      <code>{projection.projection_key}</code>
    </header>
    {projection.attention.length > 0 && <div className="selected-node-inspector__attention">
      {projection.attention.map((item) => <span key={`${item.section}:${item.code}`}>
        <ShieldAlert aria-hidden="true" size={13} />{item.severity} · {item.code}
      </span>)}
    </div>}
    <div className="selected-node-inspector__copy" aria-label="Stable selected-node identifiers">
      {stableValues.map(([label, value]) => <button
        key={label}
        type="button"
        title={`Copy ${label}`}
        aria-label={`Copy ${label}`}
        onClick={() => copyStable(value)}
      >
        <Clipboard aria-hidden="true" size={13} /><span>{label}</span><code>{value}</code>
      </button>)}
    </div>
    <div className="selected-node-inspector__sections">
      {sections.map(({ key, label }) => {
        const section = projection[key] as InspectorSection;
        return <section key={key} data-section-status={section.status}>
          <h3><span>{label}</span><code>{sectionStatus(section)}</code></h3>
          <JsonInspector value={section as JsonValue} label={`${label} JSON`} />
        </section>;
      })}
    </div>
    <footer>
      <span>read-only backend projection</span>
      <span>mutation controls: {projection.mutation_controls.length}</span>
    </footer>
  </div>;
}
