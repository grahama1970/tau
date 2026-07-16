import { AlertTriangle, ShieldAlert } from "lucide-react";
import type { AttentionItem } from "../types";

type Props = {
  items: AttentionItem[];
  onSelect: (item: AttentionItem) => void;
};

export function AttentionRail({ items, onSelect }: Props) {
  if (items.length === 0) return null;
  return <section className="attention-rail" aria-label="Human attention" data-qid="dag:attention:rail">
    <header><ShieldAlert aria-hidden="true" size={15} /><strong>Human attention</strong></header>
    <div>
      {items.map((item) => <button
        key={item.attention_id}
        type="button"
        className={`attention-item attention-item--${item.severity.toLowerCase()}`}
        onClick={() => onSelect(item)}
      >
        <AlertTriangle aria-hidden="true" size={13} />
        <span>{item.reason_code}</span>
        <small>{item.required_action_code} · #{item.opened_sequence}</small>
      </button>)}
    </div>
  </section>;
}
