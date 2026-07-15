import type { ReceiptIndexEntry, ReceiptProjection } from "../types";
import { JsonInspector } from "./JsonInspector";

export function ReceiptInspector({ entries, selected, onSelect, projection }: {
  entries: ReceiptIndexEntry[];
  selected: string | null;
  onSelect: (receiptId: string) => void;
  projection: ReceiptProjection | null;
}) {
  return <div className="receipt-inspector">
    <label htmlFor="receipt-select">Committed receipt</label>
    <select id="receipt-select" value={selected ?? ""} onChange={(event) => onSelect(event.target.value)}>
      <option value="">Select receipt</option>
      {entries.map((entry) => <option key={entry.receipt_id} value={entry.receipt_id}>{entry.path_display}</option>)}
    </select>
    {projection ? <JsonInspector value={projection as unknown as Record<string, never>} label="Receipt JSON" /> : <p className="empty-inspector">No committed receipt selected.</p>}
  </div>;
}
