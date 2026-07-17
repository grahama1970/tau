import { useEffect, useMemo, useRef, useState } from "react";
import { Braces, FileCheck2, FileJson2, GitBranch, RadioTower } from "lucide-react";
import { classifySnapshotTransition, loadComparison, loadExplanation, loadInitialState, loadJournalSequences, loadMatchingManifest, loadQuery, loadReceipt, pollState } from "./api";
import { AttentionRail } from "./components/AttentionRail";
import { CausalDetails } from "./components/CausalDetails";
import { DecisionRail } from "./components/DecisionRail";
import { DagWorkspace } from "./components/DagWorkspace";
import { ComparisonPanel, type ComparisonInput } from "./components/ComparisonPanel";
import { EventTimeline } from "./components/EventTimeline";
import { FilterBar, type FilterState } from "./components/FilterBar";
import { JsonInspector } from "./components/JsonInspector";
import { ReceiptInspector } from "./components/ReceiptInspector";
import { RunOverview } from "./components/RunOverview";
import { SequenceNavigator } from "./components/SequenceNavigator";
import { StatusBanner } from "./components/StatusBanner";
import { TransactionAttempts } from "./components/TransactionAttempts";
import type { AttentionItem, CausalExplanation, ComparisonSide, DagComparison, DagManifest, DagQueryResult, DagSnapshot, JournalEvent, JsonValue, QueryItem, ReceiptProjection } from "./types";

type InspectorTab = "source" | "plan" | "live" | "cause" | "receipt";
const tabs: Array<{ id: InspectorTab; label: string; icon: typeof Braces }> = [
  { id: "source", label: "Source DAG", icon: FileJson2 },
  { id: "plan", label: "DagPlan", icon: Braces },
  { id: "live", label: "Live State", icon: RadioTower },
  { id: "cause", label: "Why", icon: GitBranch },
  { id: "receipt", label: "Receipt", icon: FileCheck2 },
];

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
  const comparisonGenerationRef = useRef(0);
  const receiptGenerationRef = useRef(0);
  const receiptAuthorityRef = useRef("");
  const initializedRef = useRef(false);
  const [selectedSequence, setSelectedSequence] = useState<number | null>(initialSequence ? Number(initialSequence) : null);
  const [sequences, setSequences] = useState<number[]>([]);
  const [connected, setConnected] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<InspectorTab>("cause");
  const [receiptId, setReceiptId] = useState<string | null>(null);
  const [receiptAtSequence, setReceiptAtSequence] = useState<number | null>(null);
  const [receipt, setReceipt] = useState<ReceiptProjection | null>(null);
  const [selectedSubject, setSelectedSubject] = useState<{ kind: string; id: string } | null>(null);
  const [explanation, setExplanation] = useState<CausalExplanation | null>(null);
  const [filterDraft, setFilterDraft] = useState<FilterState>(initialFilters);
  const [appliedFilter, setAppliedFilter] = useState<FilterState>(initialFilters);
  const [queryResult, setQueryResult] = useState<DagQueryResult | null>(null);
  const [comparisonInput, setComparisonInput] = useState<ComparisonInput>({
    kind: "SEQUENCE_PAIR", left: "", right: "", nodeId: "", incidentId: "",
  });
  const [comparison, setComparison] = useState<DagComparison | null>(null);

  useEffect(() => {
    const onPopState = () => {
      const parameters = new URLSearchParams(window.location.search);
      const raw = parameters.get("at_sequence");
      const restored = {
        q: parameters.get("filter_q") ?? "",
        entityKind: parameters.get("filter_kind") ?? "",
        state: parameters.get("filter_state") ?? "",
      };
      receiptGenerationRef.current += 1;
      receiptAuthorityRef.current = "";
      setReceiptId(null);
      setReceiptAtSequence(null);
      setReceipt(null);
      setSelectedSequence(raw ? Number(raw) : null);
      setFilterDraft(restored);
      setAppliedFilter(restored);
      setQueryResult(null);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    let active = true;
    const generation = ++requestGenerationRef.current;
    loadInitialState(selectedSequence).then((initial) => {
      if (!active || generation !== requestGenerationRef.current) return;
      setManifest(initial.manifest);
      setSnapshot(initial.snapshot);
      etagsRef.current.set(selectedSequence === null ? "live" : `historical:${selectedSequence}`, initial.etag);
      if (!initializedRef.current) {
        setSelectedId(initial.manifest.graph.nodes[0]?.node_id ?? null);
        setSelectedSubject(
          initial.manifest.graph.nodes[0]
            ? { kind: "NODE", id: initial.manifest.graph.nodes[0].node_id }
            : null,
        );
        initializedRef.current = true;
      }
      setConnected(true);
      setError(null);
      return loadJournalSequences(initial.snapshot.run_id);
    }).then((loadedSequences) => {
      if (active && generation === requestGenerationRef.current && loadedSequences) setSequences(loadedSequences);
    }).catch((reason: unknown) => {
      if (!active) return;
      setError(reason instanceof Error ? reason.message : "viewer_initialization_failed");
      setConnected(false);
    });
    return () => { active = false; };
  }, [selectedSequence]);

  useEffect(() => {
    if (!manifest || selectedSequence !== null) return;
    let active = true;
    let timer: number | null = null;
    const generation = requestGenerationRef.current;

    const schedule = () => {
      if (active) timer = window.setTimeout(poll, 750);
    };
    const poll = async () => {
      try {
        const next = await pollState(etagsRef.current.get("live") ?? null);
        if (!active || generation !== requestGenerationRef.current) return;
        if (next.snapshot) {
          const current = snapshot;
          if (!current) return;
          const transition = classifySnapshotTransition(current, next.snapshot, null);
          if (transition === "SAME_RUN") {
            const refreshedManifest = await loadMatchingManifest(next.snapshot);
            if (!active || generation !== requestGenerationRef.current) return;
            setManifest(refreshedManifest);
            setSnapshot(next.snapshot);
            etagsRef.current.set("live", next.etag);
            loadJournalSequences(next.snapshot.run_id).then((items) => {
              if (active && generation === requestGenerationRef.current) setSequences(items);
            }).catch(() => undefined);
          } else if (transition === "NEWER_GENERATION") {
            const refreshed = await loadInitialState();
            if (
              !active
              || generation !== requestGenerationRef.current
              || refreshed.snapshot.run_id !== next.snapshot.run_id
              || classifySnapshotTransition(current, refreshed.snapshot, null) !== "NEWER_GENERATION"
            ) return;
            const nextRequestGeneration = ++requestGenerationRef.current;
            explanationGenerationRef.current += 1;
            comparisonGenerationRef.current += 1;
            receiptGenerationRef.current += 1;
            receiptAuthorityRef.current = "";
            etagsRef.current.clear();
            etagsRef.current.set("live", refreshed.etag);
            setManifest(refreshed.manifest);
            setSnapshot(refreshed.snapshot);
            setSequences([]);
            setSelectedId(refreshed.manifest.graph.nodes[0]?.node_id ?? null);
            setSelectedSubject(
              refreshed.manifest.graph.nodes[0]
                ? { kind: "NODE", id: refreshed.manifest.graph.nodes[0].node_id }
                : null,
            );
            setTab("cause");
            setReceiptId(null);
            setReceiptAtSequence(null);
            setReceipt(null);
            setExplanation(null);
            setQueryResult(null);
            setComparison(null);
            setComparisonInput({ kind: "SEQUENCE_PAIR", left: "", right: "", nodeId: "", incidentId: "" });
            setError(null);
            loadJournalSequences(refreshed.snapshot.run_id).then((items) => {
              if (active && nextRequestGeneration === requestGenerationRef.current) setSequences(items);
            }).catch(() => undefined);
          }
        } else {
          etagsRef.current.set("live", next.etag);
        }
        setConnected(true);
      } catch {
        if (active) setConnected(false);
      } finally {
        schedule();
      }
    };

    schedule();
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [manifest?.plan_sha256, selectedSequence, snapshot]);

  useEffect(() => {
    let active = true;
    const generation = ++explanationGenerationRef.current;
    if (!selectedSubject) {
      setExplanation(null);
      return () => { active = false; };
    }
    const expectedRunId = snapshot?.run_id;
    loadExplanation(selectedSubject.kind, selectedSubject.id, selectedSequence)
      .then((value) => {
        if (
          active
          && generation === explanationGenerationRef.current
          && value.run_id === expectedRunId
        ) setExplanation(value);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "explanation_load_failed");
      });
    return () => { active = false; };
  }, [selectedSequence, selectedSubject, snapshot?.run_id]);

  useEffect(() => {
    comparisonGenerationRef.current += 1;
    setComparison(null);
  }, [selectedSequence, snapshot?.run_id, snapshot?.journal_sequence]);

  useEffect(() => {
    const generation = ++receiptGenerationRef.current;
    setReceipt(null);
    if (!receiptId || !snapshot) return;
    const expectedRunId = snapshot.run_id;
    const expectedSequence = receiptAtSequence ?? snapshot.journal_sequence;
    if (snapshot.journal_sequence !== expectedSequence) return;
    const authorityKey = `${expectedRunId}:${expectedSequence}:${receiptId}`;
    receiptAuthorityRef.current = authorityKey;
    let active = true;
    loadReceipt(receiptId, receiptAtSequence).then((value) => {
      if (
        active
        && generation === receiptGenerationRef.current
        && receiptAuthorityRef.current === authorityKey
        && value.receipt_id === receiptId
      ) setReceipt(value);
    }).catch((reason: unknown) => {
      if (active && generation === receiptGenerationRef.current) {
        setError(reason instanceof Error ? reason.message : "receipt_load_failed");
      }
    });
    return () => {
      active = false;
      if (receiptAuthorityRef.current === authorityKey) receiptAuthorityRef.current = "";
    };
  }, [receiptAtSequence, receiptId, snapshot?.run_id, snapshot?.snapshot_sha256]);

  useEffect(() => {
    if (!snapshot || !Object.values(appliedFilter).some(Boolean)) {
      setQueryResult(null);
      return;
    }
    let active = true;
    const parameters = new URLSearchParams();
    parameters.set("at_sequence", String(snapshot.journal_sequence));
    if (appliedFilter.q) parameters.set("q", appliedFilter.q);
    if (appliedFilter.entityKind) parameters.set("entity_kind", appliedFilter.entityKind);
    if (appliedFilter.state) parameters.set("state", appliedFilter.state);
    loadQuery(parameters).then((result) => {
      if (
        active
        && result.run_id === snapshot.run_id
        && result.as_of_sequence === snapshot.journal_sequence
      ) setQueryResult(result);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "query_load_failed");
    });
    return () => { active = false; };
  }, [appliedFilter, selectedSequence, snapshot?.snapshot_sha256]);

  const selectedLive = useMemo(() => snapshot?.nodes.find((node) => node.node_id === selectedId) ?? null, [selectedId, snapshot]);
  const selectedTerminal = useMemo(
    () => snapshot?.terminals.find((terminal) => terminal.terminal_id === selectedId) ?? null,
    [selectedId, snapshot],
  );
  const transaction = selectedLive?.transaction ?? snapshot?.nodes.find((node) => node.transaction)?.transaction ?? null;

  useEffect(() => {
    setComparisonInput((current) => ({
      ...current,
      left: current.left || String(sequences[0] ?? transaction?.attempts[0]?.attempt ?? ""),
      right: current.right || String(sequences.at(-1) ?? transaction?.attempts.at(-1)?.attempt ?? ""),
      nodeId: current.nodeId || snapshot?.nodes.find((node) => node.transaction)?.node_id || selectedId || "",
      incidentId: current.incidentId || snapshot?.corrections[0]?.incident_id || "",
    }));
  }, [selectedId, sequences, snapshot?.corrections, snapshot?.nodes, transaction]);

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
    setSelectedSequence(sequence);
  };

  const selectGraphSubject = (id: string) => {
    const kind = manifest?.graph.terminals.some((terminal) => terminal.terminal_id === id)
      && !manifest?.graph.nodes.some((node) => node.node_id === id)
      ? "TERMINAL"
      : "NODE";
    setSelectedId(id);
    setSelectedSubject({ kind, id });
  };

  const selectAttention = (item: AttentionItem) => {
    setSelectedSubject({ kind: "ATTENTION", id: item.attention_id });
    if (item.subject.kind === "NODE" || item.subject.kind === "TERMINAL") {
      setSelectedId(item.subject.id);
    }
    setTab("cause");
  };

  const selectDecision = (kind: "ROUTE" | "JOIN", id: string) => {
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
    const kind = String(side.reference.kind ?? "");
    selectSequence(side.sequence);
    if (kind === "ATTEMPT" && typeof side.reference.node_id === "string") {
      setSelectedId(side.reference.node_id);
      setSelectedSubject({
        kind: "ATTEMPT",
        id: typeof side.reference.attempt_id === "string"
          ? side.reference.attempt_id
          : `${side.reference.node_id}:attempt:${String(side.reference.attempt ?? "")}`,
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

  return <main className="dag-app">
    <StatusBanner manifest={manifest} snapshot={snapshot} connected={connected} />
    <RunOverview manifest={manifest} snapshot={snapshot} />
    <SequenceNavigator sequences={sequences} selectedSequence={selectedSequence} onSelect={selectSequence} />
    <AttentionRail items={snapshot.attention_items} onSelect={selectAttention} />
    <DecisionRail routes={snapshot.routes} joins={snapshot.joins} onSelect={selectDecision} />
    <FilterBar value={filterDraft} result={queryResult} onChange={setFilterDraft} onApply={applyFilter} onClear={clearFilter} onSelect={selectQueryItem} />
    <section className="dag-app__workspace">
      <div className={`graph-pane${transaction ? " graph-pane--with-transaction" : ""}`} data-qid="dag:workspace:graph">
        <div className="pane-heading"><strong>Execution graph</strong><span>read-only · source DAG unchanged</span></div>
        <div className="graph-canvas" data-qid="dag:workspace:canvas">
          <DagWorkspace manifest={manifest} snapshot={snapshot} selectedId={selectedId} onSelect={selectGraphSubject} />
        </div>
        {transaction && <TransactionAttempts transaction={transaction} />}
      </div>
      <aside className="inspector-pane" data-qid="dag:workspace:inspector">
        <nav className="inspector-tabs" aria-label="DAG inspectors">
          {tabs.map((item) => {
            const Icon = item.icon;
            return <button
              key={item.id}
              type="button"
              className={tab === item.id ? "active" : ""}
              data-qid={`dag:inspector:${item.id}`}
              data-qs-action={`DAG_INSPECT_${item.id.toUpperCase()}`}
              title={`Inspect ${item.label}`}
              aria-pressed={tab === item.id}
              onClick={() => setTab(item.id)}
            ><Icon aria-hidden="true" size={14} />{item.label}</button>;
          })}
        </nav>
        <div className="inspector-content" data-qid="dag:workspace:inspector-content">
          {tab === "receipt"
            ? <ReceiptInspector entries={manifest.receipt_index} selected={receiptId} onSelect={selectReceipt} projection={receipt} />
            : tab === "cause"
              ? <CausalDetails explanation={explanation} onReceipt={selectReceipt} />
              : <JsonInspector value={inspectorValue} label={`${tab} JSON`} />}
        </div>
        <footer className="proof-boundary" data-qid="dag:workspace:proof-boundary">
          <div><strong>Proves</strong>{snapshot.proof_scope.proves.map((claim) => <span key={claim}>{claim}</span>)}</div>
          <div><strong>Does not prove</strong>{snapshot.proof_scope.does_not_prove.map((claim) => <span key={claim}>{claim}</span>)}</div>
        </footer>
      </aside>
    </section>
    <ComparisonPanel value={comparisonInput} result={comparison} sequences={sequences.filter((sequence) => sequence <= snapshot.journal_sequence)} transaction={transaction} corrections={snapshot.corrections} onChange={(value) => { comparisonGenerationRef.current += 1; setComparisonInput(value); setComparison(null); }} onCompare={runComparison} onSelectSide={selectComparisonSide} />
    <EventTimeline events={snapshot.recent_events} onSelect={selectEvent} />
  </main>;
}
