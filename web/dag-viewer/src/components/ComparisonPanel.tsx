import { ArrowLeftRight, GitCompareArrows } from "lucide-react";
import type { CorrectionProjection, DagComparison, TransactionProjection } from "../types";

export type ComparisonInput = {
  kind: "SEQUENCE_PAIR" | "ATTEMPT_PAIR" | "CORRECTION_BEFORE_AFTER";
  left: string;
  right: string;
  nodeId: string;
  incidentId: string;
};

export function ComparisonPanel({ value, result, sequences, transaction, corrections, onChange, onCompare }: {
  value: ComparisonInput;
  result: DagComparison | null;
  sequences: number[];
  transaction: TransactionProjection | null;
  corrections: CorrectionProjection[];
  onChange: (value: ComparisonInput) => void;
  onCompare: () => void;
}) {
  const attempts = transaction?.attempts.map((item) => item.attempt) ?? [];
  return <section className="comparison-panel" aria-label="Exactly two comparison" data-qid="dag:comparison">
    <header><GitCompareArrows size={15} /><strong>Compare exactly two</strong><span>journal-derived · read-only</span></header>
    <div className="comparison-panel__controls">
      <select aria-label="Comparison kind" value={value.kind} onChange={(event) => {
        const kind = event.target.value as ComparisonInput['kind'];
        const left = kind === "ATTEMPT_PAIR" ? "1" : String(sequences[0] ?? "");
        const right = kind === "ATTEMPT_PAIR" ? String(attempts.at(-1) ?? 2) : String(sequences.at(-1) ?? "");
        onChange({ ...value, kind, left, right });
      }}>
        <option value="SEQUENCE_PAIR">Sequences</option>
        <option value="ATTEMPT_PAIR">Node attempts</option>
        <option value="CORRECTION_BEFORE_AFTER">Correction before/after</option>
      </select>
      {value.kind === "SEQUENCE_PAIR" && <>
        <select aria-label="Left sequence" value={value.left} onChange={(event) => onChange({ ...value, left: event.target.value })}>{sequences.map((sequence) => <option key={sequence} value={sequence}>#{sequence}</option>)}</select>
        <ArrowLeftRight size={14} />
        <select aria-label="Right sequence" value={value.right} onChange={(event) => onChange({ ...value, right: event.target.value })}>{sequences.map((sequence) => <option key={sequence} value={sequence}>#{sequence}</option>)}</select>
      </>}
      {value.kind === "ATTEMPT_PAIR" && <>
        <input aria-label="Comparison node" value={value.nodeId} onChange={(event) => onChange({ ...value, nodeId: event.target.value })} />
        <input type="number" min="1" aria-label="Left attempt" value={value.left} onChange={(event) => onChange({ ...value, left: event.target.value })} />
        <ArrowLeftRight size={14} />
        <input type="number" min="1" aria-label="Right attempt" value={value.right} onChange={(event) => onChange({ ...value, right: event.target.value })} />
      </>}
      {value.kind === "CORRECTION_BEFORE_AFTER" && <select aria-label="Correction incident" value={value.incidentId} onChange={(event) => onChange({ ...value, incidentId: event.target.value })}>
        {corrections.map((item) => <option key={item.incident_id}>{item.incident_id}</option>)}
      </select>}
      <button type="button" onClick={onCompare}>Compare</button>
    </div>
    <div className="comparison-panel__result" data-qid="dag:comparison:result">
      {!result ? <span>Select two authoritative states.</span> : <>
        <div><strong>LEFT</strong><code>#{result.left.sequence}</code><small>{JSON.stringify(result.left.reference)}</small></div>
        <div className="comparison-panel__changes"><strong>{result.changes.length} changes</strong>{result.changes.slice(0, 5).map((change) => <code key={change.field}>{change.change} {change.field}</code>)}</div>
        <div><strong>RIGHT</strong><code>#{result.right.sequence}</code><small>{JSON.stringify(result.right.reference)}</small></div>
      </>}
    </div>
  </section>;
}
