import { PanelBottom, PanelLeft, PanelRight } from "lucide-react";
import { useRegisterAction } from "../useRegisterAction";

type Props = {
  leftOpen: boolean;
  rightOpen: boolean;
  bottomOpen: boolean;
  onToggleLeft: () => void;
  onToggleRight: () => void;
  onToggleBottom: () => void;
};

export function WorkspaceToggles({ leftOpen, rightOpen, bottomOpen, onToggleLeft, onToggleRight, onToggleBottom }: Props) {
  useRegisterAction("dag:layout:toggle-left", {
    action: "DAG_TOGGLE_ORCHESTRATION_POOL",
    label: "Toggle Orchestration Pool",
    description: "Show or hide the orchestration browser panel.",
  });
  useRegisterAction("dag:layout:toggle-bottom", {
    action: "DAG_TOGGLE_JOURNAL_DRAWER",
    label: "Toggle Journal Drawer",
    description: "Show or hide the journal event drawer.",
  });
  useRegisterAction("dag:layout:toggle-right", {
    action: "DAG_TOGGLE_INSPECTOR",
    label: "Toggle Inspector",
    description: "Show or hide the node inspector panel.",
  });

  return <nav className="workspace-toggles" aria-label="Workspace panels" data-qid="dag:layout:toggles">
    <button
      type="button"
      className={leftOpen ? "active" : ""}
      data-qid="dag:layout:toggle-left"
      data-qs-action="DAG_TOGGLE_ORCHESTRATION_POOL"
      title="Toggle orchestration browser"
      aria-pressed={leftOpen}
      onClick={onToggleLeft}
    ><PanelLeft aria-hidden="true" size={14} />Orchestrations</button>
    <button
      type="button"
      className={bottomOpen ? "active" : ""}
      data-qid="dag:layout:toggle-bottom"
      data-qs-action="DAG_TOGGLE_JOURNAL_DRAWER"
      title="Toggle journal drawer"
      aria-pressed={bottomOpen}
      onClick={onToggleBottom}
    ><PanelBottom aria-hidden="true" size={14} />Journal</button>
    <button
      type="button"
      className={rightOpen ? "active" : ""}
      data-qid="dag:layout:toggle-right"
      data-qs-action="DAG_TOGGLE_INSPECTOR"
      title="Toggle inspector"
      aria-pressed={rightOpen}
      onClick={onToggleRight}
    ><PanelRight aria-hidden="true" size={14} />Inspector</button>
  </nav>;
}
