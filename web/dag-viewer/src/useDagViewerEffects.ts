import { useEffect, type Dispatch, type SetStateAction } from "react";
import { classifySnapshotTransition, loadExplanation, loadInitialState, loadJournalSequences, loadMatchingManifest, loadQuery, loadReceipt, loadSelectedNodeInspector, pollState } from "./api";
import type { ComparisonInput } from "./components/ComparisonPanel";
import type { FilterState } from "./components/FilterBar";
import { parseWorkspaceView, type InspectorTab, type WorkspaceView } from "./dagViewerTypes";
import type { CausalExplanation, DagComparison, DagManifest, DagQueryResult, DagSnapshot, LiveNode, ReceiptProjection, SelectedNodeInspectorProjection, TransactionProjection } from "./types";

type MutableRef<T> = { current: T };
type Subject = { kind: string; id: string } | null;

type Props = {
  selectedSequence: number | null;
  manifest: DagManifest | null;
  snapshot: DagSnapshot | null;
  livePaused: boolean;
  selectedSubject: Subject;
  selectedLive: LiveNode | null;
  appliedFilter: FilterState;
  receiptId: string | null;
  receiptAtSequence: number | null;
  sequences: number[];
  transaction: TransactionProjection | null;
  selectedId: string | null;
  etagsRef: MutableRef<Map<string, string | null>>;
  requestGenerationRef: MutableRef<number>;
  explanationGenerationRef: MutableRef<number>;
  selectedNodeInspectorGenerationRef: MutableRef<number>;
  comparisonGenerationRef: MutableRef<number>;
  receiptGenerationRef: MutableRef<number>;
  receiptAuthorityRef: MutableRef<string>;
  initializedRef: MutableRef<boolean>;
  setManifest: Dispatch<SetStateAction<DagManifest | null>>;
  setSnapshot: Dispatch<SetStateAction<DagSnapshot | null>>;
  setSequences: Dispatch<SetStateAction<number[]>>;
  setConnected: Dispatch<SetStateAction<boolean>>;
  setPollFailureCount: Dispatch<SetStateAction<number>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setSelectedId: Dispatch<SetStateAction<string | null>>;
  setSelectedSubject: Dispatch<SetStateAction<Subject>>;
  setSelectedSequence: Dispatch<SetStateAction<number | null>>;
  setSelectedTimelineEventId: Dispatch<SetStateAction<string | null>>;
  setTab: Dispatch<SetStateAction<InspectorTab>>;
  setReceiptId: Dispatch<SetStateAction<string | null>>;
  setReceiptAtSequence: Dispatch<SetStateAction<number | null>>;
  setReceipt: Dispatch<SetStateAction<ReceiptProjection | null>>;
  setExplanation: Dispatch<SetStateAction<CausalExplanation | null>>;
  setSelectedNodeInspector: Dispatch<SetStateAction<SelectedNodeInspectorProjection | null>>;
  setFilterDraft: Dispatch<SetStateAction<FilterState>>;
  setAppliedFilter: Dispatch<SetStateAction<FilterState>>;
  setQueryResult: Dispatch<SetStateAction<DagQueryResult | null>>;
  setComparisonInput: Dispatch<SetStateAction<ComparisonInput>>;
  setComparison: Dispatch<SetStateAction<DagComparison | null>>;
  setWorkspaceView: Dispatch<SetStateAction<WorkspaceView>>;
};

export function useDagViewerEffects(props: Props) {
  useEffect(() => {
    const onPopState = () => {
      const parameters = new URLSearchParams(window.location.search);
      const raw = parameters.get("at_sequence");
      const restored = {
        q: parameters.get("filter_q") ?? "",
        entityKind: parameters.get("filter_kind") ?? "",
        state: parameters.get("filter_state") ?? "",
      };
      props.setWorkspaceView(parseWorkspaceView(parameters));
      props.receiptGenerationRef.current += 1;
      props.receiptAuthorityRef.current = "";
      props.setReceiptId(null);
      props.setReceiptAtSequence(null);
      props.setReceipt(null);
      props.setSelectedSequence(raw ? Number(raw) : null);
      props.setFilterDraft(restored);
      props.setAppliedFilter(restored);
      props.setQueryResult(null);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    let active = true;
    const generation = ++props.requestGenerationRef.current;
    loadInitialState(props.selectedSequence).then((initial) => {
      if (!active || generation !== props.requestGenerationRef.current) return;
      props.setManifest(initial.manifest);
      props.setSnapshot(initial.snapshot);
      props.etagsRef.current.set(props.selectedSequence === null ? "live" : `historical:${props.selectedSequence}`, initial.etag);
      if (!props.initializedRef.current) {
        props.setSelectedId(initial.manifest.graph.nodes[0]?.node_id ?? null);
        props.setSelectedSubject(initial.manifest.graph.nodes[0] ? { kind: "NODE", id: initial.manifest.graph.nodes[0].node_id } : null);
        props.initializedRef.current = true;
      }
      props.setConnected(true);
      props.setError(null);
      return loadJournalSequences(initial.snapshot.run_id);
    }).then((loadedSequences) => {
      if (active && generation === props.requestGenerationRef.current && loadedSequences) props.setSequences(loadedSequences);
    }).catch((reason: unknown) => {
      if (!active) return;
      props.setError(reason instanceof Error ? reason.message : "viewer_initialization_failed");
      props.setConnected(false);
    });
    return () => { active = false; };
  }, [props.selectedSequence]);

  useEffect(() => {
    if (!props.manifest || props.selectedSequence !== null || props.livePaused) return;
    let active = true;
    let timer: number | null = null;
    const generation = props.requestGenerationRef.current;

    const schedule = () => {
      if (active) timer = window.setTimeout(poll, 750);
    };
    const poll = async () => {
      try {
        const next = await pollState(props.etagsRef.current.get("live") ?? null);
        if (!active || generation !== props.requestGenerationRef.current) return;
        if (next.snapshot && props.snapshot) {
          const transition = classifySnapshotTransition(props.snapshot, next.snapshot, null);
          if (transition === "SAME_RUN") {
            const refreshedManifest = await loadMatchingManifest(next.snapshot);
            if (!active || generation !== props.requestGenerationRef.current) return;
            props.setManifest(refreshedManifest);
            props.setSnapshot(next.snapshot);
            props.etagsRef.current.set("live", next.etag);
            loadJournalSequences(next.snapshot.run_id).then((items) => {
              if (active && generation === props.requestGenerationRef.current) props.setSequences(items);
            }).catch(() => undefined);
          } else if (transition === "NEWER_GENERATION") {
            const refreshed = await loadInitialState();
            if (
              !active
              || generation !== props.requestGenerationRef.current
              || refreshed.snapshot.run_id !== next.snapshot.run_id
              || classifySnapshotTransition(props.snapshot, refreshed.snapshot, null) !== "NEWER_GENERATION"
            ) return;
            const nextGeneration = ++props.requestGenerationRef.current;
            props.explanationGenerationRef.current += 1;
            props.selectedNodeInspectorGenerationRef.current += 1;
            props.comparisonGenerationRef.current += 1;
            props.receiptGenerationRef.current += 1;
            props.receiptAuthorityRef.current = "";
            props.etagsRef.current.clear();
            props.etagsRef.current.set("live", refreshed.etag);
            props.setManifest(refreshed.manifest);
            props.setSnapshot(refreshed.snapshot);
            props.setSequences([]);
            props.setSelectedId(refreshed.manifest.graph.nodes[0]?.node_id ?? null);
            props.setSelectedSubject(refreshed.manifest.graph.nodes[0] ? { kind: "NODE", id: refreshed.manifest.graph.nodes[0].node_id } : null);
            props.setSelectedTimelineEventId(null);
            props.setTab("cause");
            props.setReceiptId(null);
            props.setReceiptAtSequence(null);
            props.setReceipt(null);
            props.setExplanation(null);
            props.setQueryResult(null);
            props.setComparison(null);
            props.setComparisonInput({ kind: "SEQUENCE_PAIR", left: "", right: "", nodeId: "", incidentId: "" });
            props.setError(null);
            loadJournalSequences(refreshed.snapshot.run_id).then((items) => {
              if (active && nextGeneration === props.requestGenerationRef.current) props.setSequences(items);
            }).catch(() => undefined);
          }
        } else {
          props.etagsRef.current.set("live", next.etag);
        }
        props.setConnected(true);
        props.setPollFailureCount(0);
      } catch {
        if (active) {
          props.setConnected(false);
          props.setPollFailureCount((count) => count + 1);
        }
      } finally {
        schedule();
      }
    };

    schedule();
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [props.livePaused, props.manifest?.plan_sha256, props.selectedSequence, props.snapshot]);

  useEffect(() => {
    let active = true;
    const generation = ++props.explanationGenerationRef.current;
    if (!props.selectedSubject) {
      props.setExplanation(null);
      return () => { active = false; };
    }
    const expectedRunId = props.snapshot?.run_id;
    loadExplanation(props.selectedSubject.kind, props.selectedSubject.id, props.selectedSequence)
      .then((value) => {
        if (active && generation === props.explanationGenerationRef.current && value.run_id === expectedRunId) props.setExplanation(value);
      })
      .catch((reason: unknown) => {
        if (active) props.setError(reason instanceof Error ? reason.message : "explanation_load_failed");
      });
    return () => { active = false; };
  }, [props.selectedSequence, props.selectedSubject, props.snapshot?.run_id]);

  useEffect(() => {
    const generation = ++props.selectedNodeInspectorGenerationRef.current;
    props.setSelectedNodeInspector(null);
    if (!props.selectedLive || !props.snapshot) return;
    const expectedRunId = props.snapshot.run_id;
    const expectedPlan = props.snapshot.plan_sha256;
    const expectedSequence = props.snapshot.journal_sequence;
    const expectedNode = props.selectedLive.node_id;
    const expectedAttempt = props.selectedLive.scheduler.attempt || null;
    let active = true;
    loadSelectedNodeInspector(expectedNode, expectedAttempt, props.selectedSequence)
      .then((value) => {
        if (
          active
          && generation === props.selectedNodeInspectorGenerationRef.current
          && value.run_id === expectedRunId
          && value.plan_sha256 === expectedPlan
          && value.node_id === expectedNode
          && value.journal_sequence === expectedSequence
          && (expectedAttempt === null || value.attempt === expectedAttempt)
        ) props.setSelectedNodeInspector(value);
      })
      .catch(() => {
        if (active && generation === props.selectedNodeInspectorGenerationRef.current) props.setSelectedNodeInspector(null);
      });
    return () => { active = false; };
  }, [props.selectedLive?.node_id, props.selectedLive?.scheduler.attempt, props.selectedSequence, props.snapshot?.run_id, props.snapshot?.snapshot_sha256]);

  useEffect(() => {
    props.comparisonGenerationRef.current += 1;
    props.setComparison(null);
  }, [props.selectedSequence, props.snapshot?.run_id, props.snapshot?.journal_sequence]);

  useEffect(() => {
    const generation = ++props.receiptGenerationRef.current;
    props.setReceipt(null);
    if (!props.receiptId || !props.snapshot) return;
    const expectedSequence = props.receiptAtSequence ?? props.snapshot.journal_sequence;
    if (props.snapshot.journal_sequence !== expectedSequence) return;
    const authorityKey = `${props.snapshot.run_id}:${expectedSequence}:${props.receiptId}`;
    props.receiptAuthorityRef.current = authorityKey;
    let active = true;
    loadReceipt(props.receiptId, props.receiptAtSequence).then((value) => {
      if (active && generation === props.receiptGenerationRef.current && props.receiptAuthorityRef.current === authorityKey && value.receipt_id === props.receiptId) props.setReceipt(value);
    }).catch((reason: unknown) => {
      if (active && generation === props.receiptGenerationRef.current) props.setError(reason instanceof Error ? reason.message : "receipt_load_failed");
    });
    return () => {
      active = false;
      if (props.receiptAuthorityRef.current === authorityKey) props.receiptAuthorityRef.current = "";
    };
  }, [props.receiptAtSequence, props.receiptId, props.snapshot?.run_id, props.snapshot?.snapshot_sha256]);

  useEffect(() => {
    if (!props.snapshot || !Object.values(props.appliedFilter).some(Boolean)) {
      props.setQueryResult(null);
      return;
    }
    let active = true;
    const parameters = new URLSearchParams();
    parameters.set("at_sequence", String(props.snapshot.journal_sequence));
    if (props.appliedFilter.q) parameters.set("q", props.appliedFilter.q);
    if (props.appliedFilter.entityKind) parameters.set("entity_kind", props.appliedFilter.entityKind);
    if (props.appliedFilter.state) parameters.set("state", props.appliedFilter.state);
    loadQuery(parameters).then((result) => {
      if (active && props.snapshot && result.run_id === props.snapshot.run_id && result.as_of_sequence === props.snapshot.journal_sequence) props.setQueryResult(result);
    }).catch((reason: unknown) => {
      if (active) props.setError(reason instanceof Error ? reason.message : "query_load_failed");
    });
    return () => { active = false; };
  }, [props.appliedFilter, props.selectedSequence, props.snapshot?.snapshot_sha256]);

  useEffect(() => {
    props.setComparisonInput((current) => ({
      ...current,
      left: current.left || String(props.sequences[0] ?? props.transaction?.attempts[0]?.attempt ?? ""),
      right: current.right || String(props.sequences.at(-1) ?? props.transaction?.attempts.at(-1)?.attempt ?? ""),
      nodeId: current.nodeId || props.snapshot?.nodes.find((node) => node.transaction)?.node_id || props.selectedId || "",
      incidentId: current.incidentId || props.snapshot?.corrections[0]?.incident_id || "",
    }));
  }, [props.selectedId, props.sequences, props.snapshot?.corrections, props.snapshot?.nodes, props.transaction]);
}
