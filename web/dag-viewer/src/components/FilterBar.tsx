import { Filter, Search, X } from "lucide-react";
import type { DagQueryResult, QueryItem } from "../types";
import { useRegisterAction } from "../useRegisterAction";

export type FilterState = { q: string; entityKind: string; state: string };

export function FilterBar({ value, result, onChange, onApply, onClear, onSelect }: {
  value: FilterState;
  result: DagQueryResult | null;
  onChange: (next: FilterState) => void;
  onApply: () => void;
  onClear: () => void;
  onSelect: (item: QueryItem) => void;
}) {
  useRegisterAction("dag:filters:query", {
    action: "DAG_FILTER_QUERY",
    label: "Filter Query",
    description: "Filter DAG entities by id, code, schema, state, or preview text.",
  });
  useRegisterAction("dag:filters:state", {
    action: "DAG_FILTER_STATE",
    label: "Filter State",
    description: "Filter DAG entities by projected state.",
  });

  return <section className="filter-bar" aria-label="Bounded projection filters" data-qid="dag:filters">
    <Filter aria-hidden="true" size={15} />
    <input
      data-qid="dag:filters:query"
      data-qs-action="DAG_FILTER_QUERY"
      title="Filter DAG entities"
      aria-label="Filter IDs, codes, schemas, states, and previews"
      value={value.q}
      maxLength={200}
      placeholder="ID, code, schema, state"
      onChange={(event) => onChange({ ...value, q: event.target.value })}
      onKeyDown={(event) => { if (event.key === "Enter") onApply(); }}
    />
    <select data-qid="dag:filters:entity-kind" data-qs-action="DAG_FILTER_ENTITY_KIND" title="Entity kind" aria-label="Entity kind" value={value.entityKind} onChange={(event) => onChange({ ...value, entityKind: event.target.value })}>
      <option value="">All entities</option>
      {['NODE', 'EDGE', 'TERMINAL', 'ROUTE', 'JOIN', 'CORRECTION', 'ATTENTION', 'EVENT', 'RECEIPT'].map((kind) => <option key={kind}>{kind}</option>)}
    </select>
    <input data-qid="dag:filters:state" data-qs-action="DAG_FILTER_STATE" title="Filter projected state" aria-label="Projected state" value={value.state} placeholder="State" onChange={(event) => onChange({ ...value, state: event.target.value })} />
    <button type="button" data-qid="dag:filters:apply" data-qs-action="DAG_APPLY_FILTERS" title="Apply filters" onClick={onApply}><Search size={14} />Apply</button>
    <button type="button" data-qid="dag:filters:clear" data-qs-action="DAG_CLEAR_FILTERS" title="Clear filters" aria-label="Clear filters" onClick={onClear}><X size={14} /></button>
    <span className="filter-bar__scope">redacted projections only</span>
    {result && <div className="filter-results" data-qid="dag:filter:results">
      <strong>{result.total_match_count} matches · {result.result_count} shown</strong>
      {result.items.slice(0, 5).map((item) => <button key={`${item.entity_kind}:${item.entity_id}`} type="button" data-qid={`dag:filter:result:${item.entity_kind}:${item.entity_id}`} data-qs-action="DAG_SELECT_FILTER_RESULT" title={`Inspect ${item.entity_kind} ${item.entity_id}`} onClick={() => onSelect(item)}>
        <span>{item.entity_kind}</span><code>{item.preview}</code><small>#{item.sequence}</small>
      </button>)}
    </div>}
  </section>;
}
