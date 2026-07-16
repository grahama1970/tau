import { Filter, Search, X } from "lucide-react";
import type { DagQueryResult } from "../types";

export type FilterState = { q: string; entityKind: string; state: string };

export function FilterBar({ value, result, onChange, onApply, onClear, onSelect }: {
  value: FilterState;
  result: DagQueryResult | null;
  onChange: (next: FilterState) => void;
  onApply: () => void;
  onClear: () => void;
  onSelect: (kind: string, id: string) => void;
}) {
  return <section className="filter-bar" aria-label="Bounded projection filters" data-qid="dag:filters">
    <Filter aria-hidden="true" size={15} />
    <input
      aria-label="Filter IDs, codes, schemas, states, and previews"
      value={value.q}
      maxLength={200}
      placeholder="ID, code, schema, state"
      onChange={(event) => onChange({ ...value, q: event.target.value })}
      onKeyDown={(event) => { if (event.key === "Enter") onApply(); }}
    />
    <select aria-label="Entity kind" value={value.entityKind} onChange={(event) => onChange({ ...value, entityKind: event.target.value })}>
      <option value="">All entities</option>
      {['NODE', 'EDGE', 'TERMINAL', 'ROUTE', 'JOIN', 'CORRECTION', 'ATTENTION', 'EVENT', 'RECEIPT'].map((kind) => <option key={kind}>{kind}</option>)}
    </select>
    <input aria-label="Projected state" value={value.state} placeholder="State" onChange={(event) => onChange({ ...value, state: event.target.value })} />
    <button type="button" onClick={onApply}><Search size={14} />Apply</button>
    <button type="button" title="Clear filters" aria-label="Clear filters" onClick={onClear}><X size={14} /></button>
    <span className="filter-bar__scope">redacted projections only</span>
    {result && <div className="filter-results" data-qid="dag:filter:results">
      <strong>{result.total_match_count} matches · {result.result_count} shown</strong>
      {result.items.slice(0, 5).map((item) => <button key={`${item.entity_kind}:${item.entity_id}`} type="button" onClick={() => onSelect(item.entity_kind, item.entity_id)}>
        <span>{item.entity_kind}</span><code>{item.preview}</code><small>#{item.sequence}</small>
      </button>)}
    </div>}
  </section>;
}
