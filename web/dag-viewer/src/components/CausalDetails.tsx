import { GitBranch, Link2 } from "lucide-react";
import type { CausalExplanation } from "../types";

export function CausalDetails({ explanation }: { explanation: CausalExplanation | null }) {
  if (!explanation) {
    return <p className="empty-inspector">Select a graph or attention subject to inspect its cause.</p>;
  }
  return <section className="causal-details" data-qid="dag:causal:details">
    <header>
      <GitBranch aria-hidden="true" size={16} />
      <div><strong>{explanation.subject.id}</strong><span>{explanation.subject.kind}</span></div>
      <span>#{explanation.trigger_sequence}</span>
    </header>
    <dl>
      <div><dt>State</dt><dd>{explanation.projected_state}</dd></div>
      <div><dt>Reason</dt><dd>{explanation.reason_code}</dd></div>
      <div><dt>As of</dt><dd>sequence {explanation.as_of_sequence}</dd></div>
    </dl>
    <ol>
      {explanation.references.map((reference, index) => <li key={`${index}:${String(reference.reference_id)}`}>
        <Link2 aria-hidden="true" size={12} />
        <span>{String(reference.kind)} · {String(reference.relation)}</span>
        <code>{String(reference.reference_id)}</code>
      </li>)}
    </ol>
  </section>;
}
