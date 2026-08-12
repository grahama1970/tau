import { ChevronLeft, ChevronRight, Radio } from "lucide-react";
import { useRegisterAction } from "../useRegisterAction";

type Props = {
  sequences: number[];
  selectedSequence: number | null;
  onSelect: (sequence: number | null) => void;
};

export function SequenceNavigator({ sequences, selectedSequence, onSelect }: Props) {
  useRegisterAction("dag:sequence:previous", {
    action: "DAG_SEQUENCE_PREVIOUS",
    label: "Previous Sequence",
    description: "Move to the previous committed journal sequence.",
  });
  useRegisterAction("dag:sequence:select", {
    action: "DAG_SELECT_SEQUENCE",
    label: "Select Sequence",
    description: "Select a committed journal sequence or the live head.",
  });
  useRegisterAction("dag:sequence:next", {
    action: "DAG_SEQUENCE_NEXT",
    label: "Next Sequence",
    description: "Move to the next committed journal sequence.",
  });
  useRegisterAction("dag:sequence:return-live", {
    action: "DAG_RETURN_LIVE",
    label: "Return Live",
    description: "Return the DAG viewer to the live journal head.",
  });

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
      <button type="button" data-qid="dag:sequence:previous" data-qs-action="DAG_SEQUENCE_PREVIOUS" title="Previous committed sequence" aria-label="Previous committed sequence" disabled={previous === null} onClick={() => previous !== null && onSelect(previous)}>
        <ChevronLeft aria-hidden="true" size={16} />
      </button>
      <select data-qid="dag:sequence:select" data-qs-action="DAG_SELECT_SEQUENCE" title="Committed journal sequence" aria-label="Committed journal sequence" value={selectedSequence ?? "live"} onChange={(event) => onSelect(event.target.value === "live" ? null : Number(event.target.value))}>
        <option value="live">Live head</option>
        {sequences.map((sequence) => <option key={sequence} value={sequence}>Sequence {sequence}</option>)}
      </select>
      <button type="button" data-qid="dag:sequence:next" data-qs-action="DAG_SEQUENCE_NEXT" title="Next committed sequence" aria-label="Next committed sequence" disabled={next === null} onClick={() => next !== null && onSelect(next)}>
        <ChevronRight aria-hidden="true" size={16} />
      </button>
      {selectedSequence !== null && <button type="button" className="sequence-navigator__live" data-qid="dag:sequence:return-live" data-qs-action="DAG_RETURN_LIVE" title="Return to live sequence" onClick={() => onSelect(null)}><Radio aria-hidden="true" size={14} />Return live</button>}
    </div>
  </nav>;
}
