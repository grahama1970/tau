import { GitBranch, RadioTower } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import type { InspectorTab, WorkspaceView } from "../dagViewerTypes";
import { inspectorTabs } from "../dagViewerTypes";
import type { LiveSyncStatus } from "./LiveSyncControls";
import type { TimelineSubject } from "./runTimelineModel";
import type { AttentionItem, CausalExplanation, ComparisonSide, DagComparison, DagManifest, DagQueryResult, DagSnapshot, JournalEvent, JsonValue, QueryItem, ReceiptProjection, SelectedNodeInspectorProjection, TransactionProjection } from "../types";
import { AttentionRail } from "./AttentionRail";
import { CausalDetails } from "./CausalDetails";
import { ComparisonPanel, type ComparisonInput } from "./ComparisonPanel";
import { DecisionRail } from "./DecisionRail";
import { DagWorkspace } from "./DagWorkspace";
import { EventTimeline } from "./EventTimeline";
import { FilterBar, type FilterState } from "./FilterBar";
import { JsonInspector } from "./JsonInspector";
import { LiveSyncControls } from "./LiveSyncControls";
import { OrchestrationPool } from "./OrchestrationPool";
import { ReceiptInspector } from "./ReceiptInspector";
import { RunOverview } from "./RunOverview";
import { RunTimeline } from "./RunTimeline";
import { SequenceNavigator } from "./SequenceNavigator";
import { SelectedNodeInspector } from "./SelectedNodeInspector";
import { StatusBanner } from "./StatusBanner";
import { TransactionAttempts } from "./TransactionAttempts";
import { WorkspaceToggles } from "./WorkspaceToggles";

type Props = {
  manifest: DagManifest;
  snapshot: DagSnapshot;
  connected: boolean;
  liveSyncStatus: LiveSyncStatus;
  livePaused: boolean;
  autoFollowLatest: boolean;
  selectedSequence: number | null;
  sequences: number[];
  selectedId: string | null;
  selectedTimelineEventId: string | null;
  tab: InspectorTab;
  receiptId: string | null;
  receipt: ReceiptProjection | null;
  selectedNodeInspector: SelectedNodeInspectorProjection | null;
  explanation: CausalExplanation | null;
  filterDraft: FilterState;
  queryResult: DagQueryResult | null;
  comparisonInput: ComparisonInput;
  comparison: DagComparison | null;
  workspaceView: WorkspaceView;
  leftPaneOpen: boolean;
  rightPaneOpen: boolean;
  bottomPaneOpen: boolean;
  transaction: TransactionProjection | null;
  inspectorValue: JsonValue;
  onToggleLivePaused: () => void;
  onToggleAutoFollowLatest: () => void;
  onToggleLeftPane: () => void;
  onToggleRightPane: () => void;
  onToggleBottomPane: () => void;
  onSelectSequence: (sequence: number | null) => void;
  onSelectAttention: (item: AttentionItem) => void;
  onSelectDecision: (kind: "ROUTE" | "JOIN", id: string) => void;
  onFilterDraftChange: Dispatch<SetStateAction<FilterState>>;
  onApplyFilter: () => void;
  onClearFilter: () => void;
  onSelectQueryItem: (item: QueryItem) => void;
  onSelectWorkspaceView: (view: WorkspaceView) => void;
  onSelectTimelineSubject: (subject: TimelineSubject) => void;
  onSelectGraphSubject: (id: string) => void;
  onSetTab: Dispatch<SetStateAction<InspectorTab>>;
  onSelectReceipt: (id: string, atSequence?: number | null) => void;
  onComparisonInputChange: (value: ComparisonInput) => void;
  onRunComparison: () => void;
  onSelectComparisonSide: (side: ComparisonSide) => void;
  onSelectEvent: (event: JournalEvent) => void;
};

export function DagViewerShell(props: Props) {
  const appClass = `dag-app${props.leftPaneOpen ? "" : " dag-app--left-collapsed"}${props.bottomPaneOpen ? "" : " dag-app--journal-collapsed"}`;

  return <main className={appClass}>
    <StatusBanner
      manifest={props.manifest}
      snapshot={props.snapshot}
      connected={props.connected}
      actions={<>
        <LiveSyncControls
          status={props.liveSyncStatus}
          paused={props.livePaused}
          autoFollow={props.autoFollowLatest}
          historical={props.selectedSequence !== null}
          sequence={props.snapshot.journal_sequence}
          onTogglePaused={props.onToggleLivePaused}
          onToggleAutoFollow={props.onToggleAutoFollowLatest}
        />
        <WorkspaceToggles
          leftOpen={props.leftPaneOpen}
          rightOpen={props.rightPaneOpen}
          bottomOpen={props.bottomPaneOpen}
          onToggleLeft={props.onToggleLeftPane}
          onToggleRight={props.onToggleRightPane}
          onToggleBottom={props.onToggleBottomPane}
        />
      </>}
    />
    {props.leftPaneOpen && <OrchestrationPool manifest={props.manifest} snapshot={props.snapshot} selectedSequence={props.selectedSequence} onSelectLive={() => props.onSelectSequence(null)} />}
    <RunOverview manifest={props.manifest} snapshot={props.snapshot} />
    <SequenceNavigator sequences={props.sequences} selectedSequence={props.selectedSequence} onSelect={props.onSelectSequence} />
    <AttentionRail items={props.snapshot.attention_items} onSelect={props.onSelectAttention} />
    <DecisionRail routes={props.snapshot.routes} joins={props.snapshot.joins} onSelect={props.onSelectDecision} />
    <FilterBar value={props.filterDraft} result={props.queryResult} onChange={props.onFilterDraftChange} onApply={props.onApplyFilter} onClear={props.onClearFilter} onSelect={props.onSelectQueryItem} />
    <section className={`dag-app__workspace${props.rightPaneOpen ? "" : " dag-app__workspace--inspector-collapsed"}`}>
      <div className={`graph-pane${props.transaction ? " graph-pane--with-transaction" : ""}${props.workspaceView === "timeline" ? " graph-pane--timeline" : ""}`} data-qid="dag:workspace:graph">
        <div className="pane-heading pane-heading--workspace">
          <div>
            <strong>{props.workspaceView === "timeline" ? "Run timeline" : "Execution graph"}</strong>
            <span>{props.workspaceView === "timeline" ? "timeline primary · topology preserved" : "read-only · source DAG unchanged"}</span>
          </div>
          <div className="workspace-tabs" aria-label="Workspace view">
            <button type="button" className={props.workspaceView === "timeline" ? "active" : ""} data-qid="dag:workspace-view:timeline" data-qs-action="DAG_WORKSPACE_TIMELINE" title="Show run timeline" aria-pressed={props.workspaceView === "timeline"} onClick={() => props.onSelectWorkspaceView("timeline")}><RadioTower aria-hidden="true" size={14} />Timeline</button>
            <button type="button" className={props.workspaceView === "topology" ? "active" : ""} data-qid="dag:workspace-view:topology" data-qs-action="DAG_WORKSPACE_TOPOLOGY" title="Show topology graph" aria-pressed={props.workspaceView === "topology"} onClick={() => props.onSelectWorkspaceView("topology")}><GitBranch aria-hidden="true" size={14} />Topology</button>
          </div>
        </div>
        <div className="graph-canvas" data-qid="dag:workspace:canvas">
          {props.workspaceView === "timeline"
            ? <RunTimeline manifest={props.manifest} snapshot={props.snapshot} selectedId={props.selectedId} selectedTimelineEventId={props.selectedTimelineEventId} onSelect={props.onSelectTimelineSubject} />
            : <DagWorkspace manifest={props.manifest} snapshot={props.snapshot} selectedId={props.selectedId} onSelect={props.onSelectGraphSubject} />}
        </div>
        {props.transaction && <TransactionAttempts transaction={props.transaction} />}
      </div>
      {props.rightPaneOpen && <aside className="inspector-pane" data-qid="dag:workspace:inspector">
        <nav className="inspector-tabs" aria-label="DAG inspectors">
          {inspectorTabs.map((item) => {
            const Icon = item.icon;
            return <button key={item.id} type="button" className={props.tab === item.id ? "active" : ""} data-qid={`dag:inspector:${item.id}`} data-qs-action={`DAG_INSPECT_${item.id.toUpperCase()}`} title={`Inspect ${item.label}`} aria-pressed={props.tab === item.id} onClick={() => props.onSetTab(item.id)}><Icon aria-hidden="true" size={14} />{item.label}</button>;
          })}
        </nav>
        <div className="inspector-content" data-qid="dag:workspace:inspector-content">
          {props.tab === "receipt"
            ? <ReceiptInspector entries={props.manifest.receipt_index} selected={props.receiptId} onSelect={props.onSelectReceipt} projection={props.receipt} />
            : props.tab === "node"
              ? <SelectedNodeInspector projection={props.selectedNodeInspector} />
              : props.tab === "cause"
                ? <CausalDetails explanation={props.explanation} onReceipt={props.onSelectReceipt} />
                : <JsonInspector value={props.inspectorValue} label={`${props.tab} JSON`} />}
        </div>
        <footer className="proof-boundary" data-qid="dag:workspace:proof-boundary">
          <div><strong>Proves</strong>{props.snapshot.proof_scope.proves.map((claim) => <span key={claim}>{claim}</span>)}</div>
          <div><strong>Does not prove</strong>{props.snapshot.proof_scope.does_not_prove.map((claim) => <span key={claim}>{claim}</span>)}</div>
        </footer>
      </aside>}
    </section>
    <ComparisonPanel value={props.comparisonInput} result={props.comparison} sequences={props.sequences.filter((sequence) => sequence <= props.snapshot.journal_sequence)} transaction={props.transaction} corrections={props.snapshot.corrections} onChange={props.onComparisonInputChange} onCompare={props.onRunComparison} onSelectSide={props.onSelectComparisonSide} />
    <EventTimeline events={props.snapshot.recent_events} collapsed={!props.bottomPaneOpen} onToggle={props.onToggleBottomPane} onSelect={props.onSelectEvent} />
  </main>;
}
