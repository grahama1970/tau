import { ChevronLeft, ChevronRight, Radio } from "lucide-react";

type Props = {
  sequences: number[];
  selectedSequence: number | null;
  onSelect: (sequence: number | null) => void;
};

export function SequenceNavigator({ sequences, selectedSequence, onSelect }: Props) {
  const selectedIndex = selectedSequence === null ? sequences.length : sequences.indexOf(selectedSequence);
  const previous = selectedIndex > 0 ? sequences[selectedIndex - 1] : null;
  const next = selectedSequence !== null && selectedIndex >= 0 && selectedIndex < sequences.length - 1
    ? sequences[selectedIndex + 1]
    : null;
  return <nav className="sequence-navigator" aria-label="Journal sequence navigator" data-qid="dag:sequence:navigator">
    <div className="sequence-navigator__mode">
      <Radio aria-hidden="true" size={15} />
      <strong>{selectedSequence === null ? "LIVE" : "HISTORICAL"}</strong>
      <span>{selectedSequence === null ? "following journal head" : `frozen at #${selectedSequence}`}</span>
    </div>
    <div className="sequence-navigator__controls">
      <button type="button" title="Previous committed sequence" aria-label="Previous committed sequence" disabled={previous === null} onClick={() => previous !== null && onSelect(previous)}>
        <ChevronLeft aria-hidden="true" size={16} />
      </button>
      <select aria-label="Committed journal sequence" value={selectedSequence ?? "live"} onChange={(event) => onSelect(event.target.value === "live" ? null : Number(event.target.value))}>
        <option value="live">Live head</option>
        {sequences.map((sequence) => <option key={sequence} value={sequence}>Sequence {sequence}</option>)}
      </select>
      <button type="button" title="Next committed sequence" aria-label="Next committed sequence" disabled={next === null} onClick={() => next !== null && onSelect(next)}>
        <ChevronRight aria-hidden="true" size={16} />
      </button>
      {selectedSequence !== null && <button type="button" className="sequence-navigator__live" onClick={() => onSelect(null)}><Radio aria-hidden="true" size={14} />Return live</button>}
    </div>
  </nav>;
}
