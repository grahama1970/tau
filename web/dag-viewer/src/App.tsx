import { useMemo, useRef, useState } from "react";
import { RadioTower } from "lucide-react";
import { loadComparison } from "./api";
import { DagViewerShell } from "./components/DagViewerShell";
import type { ComparisonInput } from "./components/ComparisonPanel";
import type { FilterState } from "./components/FilterBar";
import type { LiveSyncStatus } from "./components/LiveSyncControls";
import type { TimelineSubject } from "./components/runTimelineModel";
import { parseWorkspaceView, type InspectorTab, type WorkspaceView } from "./dagViewerTypes";
import type { AttentionItem, CausalExplanation, ComparisonSide, DagComparison, DagManifest, DagQueryResult, DagSnapshot, JournalEvent, JsonValue, QueryItem, ReceiptProjection, SelectedNodeInspectorProjection } from "./types";
import { useDagViewerEffects } from "./useDagViewerEffects";
import { useRegisterAction } from "./useRegisterAction";

export default function App() {
  const initialUrl = new URLSearchParams(window.location.search);
  const initialSequence = initialUrl.get("at_sequence");
  const initialFilters: FilterState = {
    q: initialUrl.get("filter_q") ?? "",
    entityKind: initialUrl.get("filter_kind") ?? "",
    state: initialUrl.get("filter_state") ?? "",
  };
  const [manifest, setManifest] = useState<DagManifest | null>(null);
  const [snapshot, setSnapshot] = useState<DagSnapshot | null>(null);
  const etagsRef = useRef(new Map<string, string | null>());
  const requestGenerationRef = useRef(0);
  const explanationGenerationRef = useRef(0);
  const selectedNodeInspectorGenerationRef = useRef(0);
  const comparisonGenerationRef = useRef(0);
  const receiptGenerationRef = useRef(0);
  const receiptAuthorityRef = useRef("");
  const initializedRef = useRef(false);
  const [selectedSequence, setSelectedSequence] = useState<number | null>(initialSequence ? Number(initialSequence) : null);
  const [sequences, setSequences] = useState<number[]>([]);
  const [connected, setConnected] = useState(true);
  const [livePaused, setLivePaused] = useState(false);
  const [autoFollowLatest, setAutoFollowLatest] = useState(true);
  const [pollFailureCount, setPollFailureCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedTimelineEventId, setSelectedTimelineEventId] = useState<string | null>(null);
  const [tab, setTab] = useState<InspectorTab>("cause");
  const [receiptId, setReceiptId] = useState<string | null>(null);
  const [receiptAtSequence, setReceiptAtSequence] = useState<number | null>(null);
  const [receipt, setReceipt] = useState<ReceiptProjection | null>(null);
  const [selectedSubject, setSelectedSubject] = useState<{ kind: string; id: string } | null>(null);
  const [explanation, setExplanation] = useState<CausalExplanation | null>(null);
  const [selectedNodeInspector, setSelectedNodeInspector] = useState<SelectedNodeInspectorProjection | null>(null);
  const [filterDraft, setFilterDraft] = useState<FilterState>(initialFilters);
  const [appliedFilter, setAppliedFilter] = useState<FilterState>(initialFilters);
  const [queryResult, setQueryResult] = useState<DagQueryResult | null>(null);
  const [comparisonInput, setComparisonInput] = useState<ComparisonInput>({ kind: "SEQUENCE_PAIR", left: "", right: "", nodeId: "", incidentId: "" });
  const [comparison, setComparison] = useState<DagComparison | null>(null);
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView>(parseWorkspaceView(initialUrl));
  const [leftPaneOpen, setLeftPaneOpen] = useState(true);
  const [rightPaneOpen, setRightPaneOpen] = useState(true);
  const [bottomPaneOpen, setBottomPaneOpen] = useState(true);

  useRegisterAction("dag:workspace-view:timeline", {
    action: "DAG_WORKSPACE_TIMELINE",
    label: "Run Timeline",
    description: "Show the Tau run as a timeline-first operator surface.",
  });
  useRegisterAction("dag:workspace-view:topology", {
    action: "DAG_WORKSPACE_TOPOLOGY",
    label: "Topology",
    description: "Show the authoritative React Flow DAG topology.",
  });
  useRegisterAction("dag:inspector:node", {
    action: "DAG_INSPECT_NODE",
    label: "Node Inspector",
    description: "Inspect the selected node projection.",
  });
  useRegisterAction("dag:inspector:source", {
    action: "DAG_INSPECT_SOURCE",
    label: "Source DAG Inspector",
    description: "Inspect the source DAG JSON.",
  });
  useRegisterAction("dag:inspector:plan", {
    action: "DAG_INSPECT_PLAN",
    label: "DagPlan Inspector",
    description: "Inspect the compiled DagPlan JSON.",
  });
  useRegisterAction("dag:inspector:live", {
    action: "DAG_INSPECT_LIVE",
    label: "Live State Inspector",
    description: "Inspect the selected live state projection.",
  });
  useRegisterAction("dag:inspector:cause", {
    action: "DAG_INSPECT_CAUSE",
    label: "Causal Inspector",
    description: "Inspect why the selected subject is in its projected state.",
  });
  useRegisterAction("dag:inspector:receipt", {
    action: "DAG_INSPECT_RECEIPT",
    label: "Receipt Inspector",
    description: "Inspect committed receipt projections.",
  });

  const selectedLive = useMemo(() => snapshot?.nodes.find((node) => node.node_id === selectedId) ?? null, [selectedId, snapshot]);
  const selectedTerminal = useMemo(
    () => snapshot?.terminals.find((terminal) => terminal.terminal_id === selectedId) ?? null,
    [selectedId, snapshot],
  );
  const transaction = selectedLive?.transaction ?? snapshot?.nodes.find((node) => node.transaction)?.transaction ?? null;

  useDagViewerEffects({
    selectedSequence,
    manifest,
    snapshot,
    livePaused,
    selectedSubject,
    selectedLive,
    appliedFilter,
    receiptId,
    receiptAtSequence,
    sequences,
    transaction,
    selectedId,
    etagsRef,
    requestGenerationRef,
    explanationGenerationRef,
    selectedNodeInspectorGenerationRef,
    comparisonGenerationRef,
    receiptGenerationRef,
    receiptAuthorityRef,
    initializedRef,
    setManifest,
    setSnapshot,
    setSequences,
    setConnected,
    setPollFailureCount,
    setError,
    setSelectedId,
    setSelectedSubject,
    setSelectedSequence,
    setSelectedTimelineEventId,
    setTab,
    setReceiptId,
    setReceiptAtSequence,
    setReceipt,
    setExplanation,
    setSelectedNodeInspector,
    setFilterDraft,
    setAppliedFilter,
    setQueryResult,
    setComparisonInput,
    setComparison,
    setWorkspaceView,
  });

  const selectReceipt = (id: string, atSequence: number | null = selectedSequence) => {
    receiptGenerationRef.current += 1;
    receiptAuthorityRef.current = "";
    setReceiptId(id || null);
    setReceiptAtSequence(id ? atSequence : null);
    setReceipt(null);
    if (!id) return;
    setTab("receipt");
  };

  const selectSequence = (sequence: number | null) => {
    const url = new URL(window.location.href);
    if (sequence === null) url.searchParams.delete("at_sequence");
    else url.searchParams.set("at_sequence", String(sequence));
    window.history.pushState({}, "", url);
    setReceiptId(null);
    setReceiptAtSequence(null);
    setReceipt(null);
    setAutoFollowLatest(sequence === null);
    setSelectedSequence(sequence);
  };

  const toggleLivePaused = () => {
    setLivePaused((paused) => !paused);
    if (selectedSequence !== null) selectSequence(null);
  };

  const toggleAutoFollowLatest = () => {
    if (autoFollowLatest && selectedSequence === null) {
      setAutoFollowLatest(false);
      return;
    }
    if (selectedSequence !== null) selectSequence(null);
    setLivePaused(false);
    setAutoFollowLatest(true);
  };

  const selectGraphSubject = (id: string) => {
    const kind = manifest?.graph.terminals.some((terminal) => terminal.terminal_id === id)
      && !manifest?.graph.nodes.some((node) => node.node_id === id)
      ? "TERMINAL"
      : "NODE";
    setSelectedId(id);
    setSelectedTimelineEventId(null);
    setSelectedSubject({ kind, id });
    if (kind === "NODE") setTab("node");
  };

  const selectTimelineSubject = (subject: TimelineSubject) => {
    const normalized = subject.kind.toUpperCase();
    setSelectedTimelineEventId(subject.eventId ?? null);
    if (normalized === "NODE") {
      setSelectedId(subject.id);
      setSelectedSubject({ kind: "NODE", id: subject.id });
      setTab("node");
    } else if (normalized === "TERMINAL") {
      setSelectedId(subject.id);
      setSelectedSubject({ kind: "TERMINAL", id: subject.id });
      setTab("cause");
    } else {
      setSelectedSubject({ kind: normalized, id: subject.id });
      setTab("cause");
    }
  };

  const selectWorkspaceView = (view: WorkspaceView) => {
    const url = new URL(window.location.href);
    url.searchParams.set("workspace_view", view);
    window.history.pushState({}, "", url);
    setWorkspaceView(view);
  };

  const selectAttention = (item: AttentionItem) => {
    setSelectedTimelineEventId(null);
    setSelectedSubject({ kind: "ATTENTION", id: item.attention_id });
    if (item.subject.kind === "NODE" || item.subject.kind === "TERMINAL") {
      setSelectedId(item.subject.id);
    }
    setTab("cause");
  };

  const selectDecision = (kind: "ROUTE" | "JOIN", id: string) => {
    setSelectedTimelineEventId(null);
    setSelectedSubject({ kind, id });
    setTab("cause");
  };

  const applyFilter = () => {
    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries({ filter_q: filterDraft.q, filter_kind: filterDraft.entityKind, filter_state: filterDraft.state })) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    window.history.pushState({}, "", url);
    setAppliedFilter(filterDraft);
  };

  const clearFilter = () => {
    const empty = { q: "", entityKind: "", state: "" };
    setFilterDraft(empty);
    setAppliedFilter(empty);
    setQueryResult(null);
    const url = new URL(window.location.href);
    for (const key of ["filter_q", "filter_kind", "filter_state"]) url.searchParams.delete(key);
    window.history.pushState({}, "", url);
  };

  const selectQueryItem = (item: QueryItem) => {
    setSelectedTimelineEventId(null);
    selectSequence(item.sequence);
    if (item.entity_kind === "RECEIPT") selectReceipt(item.entity_id, item.sequence);
    else if (item.entity_kind === "NODE" || item.entity_kind === "TERMINAL") {
      selectGraphSubject(item.entity_id);
      setTab("cause");
    } else if (item.entity_kind === "EVENT") {
      const subject = item.node_id
        ? { kind: "NODE", id: item.node_id }
        : { kind: "RUN", id: snapshot?.run_id ?? "" };
      if (item.node_id) setSelectedId(item.node_id);
      setSelectedSubject(subject);
      setTab("cause");
    } else if (["EDGE", "ROUTE", "JOIN", "CORRECTION", "ATTENTION"].includes(item.entity_kind)) {
      setSelectedSubject({ kind: item.entity_kind, id: item.entity_id });
      setTab("cause");
    }
  };

  const selectEvent = (event: JournalEvent) => {
    setSelectedTimelineEventId(null);
    const candidateKind = event.entity_type.toUpperCase();
    const supportedKinds = new Set(["RUN", "NODE", "EDGE", "TERMINAL", "ROUTE", "JOIN", "ATTEMPT", "CORRECTION", "ATTENTION"]);
    const kind = supportedKinds.has(candidateKind) ? candidateKind : "RUN";
    const subjectId = kind === "RUN" ? snapshot?.run_id ?? event.entity_id : event.entity_id;
    selectSequence(event.seq);
    if (kind === "NODE" || kind === "TERMINAL") setSelectedId(event.entity_id);
    setSelectedSubject({ kind, id: subjectId });
    setTab("cause");
  };

  const selectComparisonSide = (side: ComparisonSide) => {
    setSelectedTimelineEventId(null);
    const kind = String(side.reference.kind ?? "");
    selectSequence(side.sequence);
    if (kind === "ATTEMPT" && typeof side.reference.node_id === "string") {
      setSelectedId(side.reference.node_id);
      setSelectedSubject({
        kind: "ATTEMPT",
        id: typeof side.reference.attempt_id === "string" ? side.reference.attempt_id : `${side.reference.node_id}:attempt:${String(side.reference.attempt ?? "")}`,
      });
      setTab("cause");
    } else if (kind === "CORRECTION" && typeof side.reference.incident_id === "string") {
      setSelectedSubject({ kind: "CORRECTION", id: side.reference.incident_id });
      setTab("cause");
    } else if (kind === "SEQUENCE") {
      setSelectedSubject({ kind: "RUN", id: snapshot?.run_id ?? side.run_id });
      setTab("cause");
    }
  };

  const runComparison = () => {
    if (!snapshot) return;
    const generation = ++comparisonGenerationRef.current;
    const expectedRunId = snapshot.run_id;
    const expectedSequence = snapshot.journal_sequence;
    const parameters = new URLSearchParams({ kind: comparisonInput.kind });
    parameters.set("at_sequence", String(expectedSequence));
    if (comparisonInput.kind === "SEQUENCE_PAIR") {
      parameters.set("left_sequence", comparisonInput.left || String(sequences[0] ?? ""));
      parameters.set("right_sequence", comparisonInput.right || String(sequences.at(-1) ?? ""));
    } else if (comparisonInput.kind === "ATTEMPT_PAIR") {
      parameters.set("node_id", comparisonInput.nodeId);
      parameters.set("left_attempt", comparisonInput.left || "1");
      parameters.set("right_attempt", comparisonInput.right || String(selectedLive?.scheduler.attempt ?? 2));
    } else {
      parameters.set("incident_id", comparisonInput.incidentId);
    }
    loadComparison(parameters).then((result) => {
      if (
        generation === comparisonGenerationRef.current
        && result.run_id === expectedRunId
        && result.as_of_sequence === expectedSequence
      ) setComparison(result);
    }).catch((reason: unknown) => {
      if (generation === comparisonGenerationRef.current) {
        setError(reason instanceof Error ? reason.message : "comparison_load_failed");
      }
    });
  };

  if (error && (!manifest || !snapshot)) return <main className="fatal-state"><h1>Tau Live DAG</h1><p>{error}</p></main>;
  if (!manifest || !snapshot) return <main className="loading-state"><RadioTower aria-hidden="true" /><span>Loading authoritative DAG projection</span></main>;

  const inspectorValue: JsonValue = tab === "source"
    ? manifest.source_dag
    : tab === "plan"
      ? manifest.dag_plan
      : selectedLive ?? selectedTerminal ?? snapshot;
  const liveSyncStatus: LiveSyncStatus = connected ? "CONNECTED" : pollFailureCount >= 3 ? "OFFLINE" : "RECONNECTING";

  return <DagViewerShell
    manifest={manifest}
    snapshot={snapshot}
    connected={connected}
    liveSyncStatus={liveSyncStatus}
    livePaused={livePaused}
    autoFollowLatest={autoFollowLatest}
    selectedSequence={selectedSequence}
    sequences={sequences}
    selectedId={selectedId}
    selectedTimelineEventId={selectedTimelineEventId}
    tab={tab}
    receiptId={receiptId}
    receipt={receipt}
    selectedNodeInspector={selectedNodeInspector}
    explanation={explanation}
    filterDraft={filterDraft}
    queryResult={queryResult}
    comparisonInput={comparisonInput}
    comparison={comparison}
    workspaceView={workspaceView}
    leftPaneOpen={leftPaneOpen}
    rightPaneOpen={rightPaneOpen}
    bottomPaneOpen={bottomPaneOpen}
    transaction={transaction}
    inspectorValue={inspectorValue}
    onToggleLivePaused={toggleLivePaused}
    onToggleAutoFollowLatest={toggleAutoFollowLatest}
    onToggleLeftPane={() => setLeftPaneOpen((open) => !open)}
    onToggleRightPane={() => setRightPaneOpen((open) => !open)}
    onToggleBottomPane={() => setBottomPaneOpen((open) => !open)}
    onSelectSequence={selectSequence}
    onSelectAttention={selectAttention}
    onSelectDecision={selectDecision}
    onFilterDraftChange={setFilterDraft}
    onApplyFilter={applyFilter}
    onClearFilter={clearFilter}
    onSelectQueryItem={selectQueryItem}
    onSelectWorkspaceView={selectWorkspaceView}
    onSelectTimelineSubject={selectTimelineSubject}
    onSelectGraphSubject={selectGraphSubject}
    onSetTab={setTab}
    onSelectReceipt={selectReceipt}
    onComparisonInputChange={(value) => { comparisonGenerationRef.current += 1; setComparisonInput(value); setComparison(null); }}
    onRunComparison={runComparison}
    onSelectComparisonSide={selectComparisonSide}
    onSelectEvent={selectEvent}
  />;
}
