import { useEffect, useMemo, useRef, useState } from "react";
import { Braces, FileCheck2, FileJson2, RadioTower } from "lucide-react";
import { loadInitialState, loadManifest, loadReceipt, pollState, shouldReplaceSnapshot } from "./api";
import { DagWorkspace } from "./components/DagWorkspace";
import { EventTimeline } from "./components/EventTimeline";
import { JsonInspector } from "./components/JsonInspector";
import { ReceiptInspector } from "./components/ReceiptInspector";
import { StatusBanner } from "./components/StatusBanner";
import { TransactionAttempts } from "./components/TransactionAttempts";
import type { DagManifest, DagSnapshot, JsonValue, ReceiptProjection } from "./types";

type InspectorTab = "source" | "plan" | "live" | "receipt";
const tabs: Array<{ id: InspectorTab; label: string; icon: typeof Braces }> = [
  { id: "source", label: "Source DAG", icon: FileJson2 },
  { id: "plan", label: "DagPlan", icon: Braces },
  { id: "live", label: "Live State", icon: RadioTower },
  { id: "receipt", label: "Receipt", icon: FileCheck2 },
];

export default function App() {
  const [manifest, setManifest] = useState<DagManifest | null>(null);
  const [snapshot, setSnapshot] = useState<DagSnapshot | null>(null);
  const etagRef = useRef<string | null>(null);
  const [connected, setConnected] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<InspectorTab>("source");
  const [receiptId, setReceiptId] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ReceiptProjection | null>(null);

  useEffect(() => {
    let active = true;
    loadInitialState().then((initial) => {
      if (!active) return;
      setManifest(initial.manifest);
      setSnapshot(initial.snapshot);
      etagRef.current = initial.etag;
      setSelectedId(initial.manifest.graph.nodes[0]?.node_id ?? null);
      setConnected(true);
    }).catch((reason: unknown) => {
      if (!active) return;
      setError(reason instanceof Error ? reason.message : "viewer_initialization_failed");
      setConnected(false);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!manifest) return;
    let active = true;
    let timer: number | null = null;

    const schedule = () => {
      if (active) timer = window.setTimeout(poll, 750);
    };
    const poll = async () => {
      try {
        const next = await pollState(etagRef.current);
        if (!active) return;
        if (next.snapshot) {
          const current = snapshot;
          if (!current || shouldReplaceSnapshot(current, next.snapshot)) {
            const refreshedManifest = await loadManifest();
            if (!active) return;
            setManifest(refreshedManifest);
            setSnapshot(next.snapshot);
            etagRef.current = next.etag;
          }
        } else {
          etagRef.current = next.etag;
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
  }, [manifest?.plan_sha256, snapshot]);

  const selectedLive = useMemo(() => snapshot?.nodes.find((node) => node.node_id === selectedId) ?? null, [selectedId, snapshot]);
  const transaction = selectedLive?.transaction ?? snapshot?.nodes.find((node) => node.transaction)?.transaction ?? null;

  const selectReceipt = (id: string) => {
    setReceiptId(id || null);
    setReceipt(null);
    if (!id) return;
    loadReceipt(id).then(setReceipt).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "receipt_load_failed"));
  };

  if (error && (!manifest || !snapshot)) return <main className="fatal-state"><h1>Tau Live DAG</h1><p>{error}</p></main>;
  if (!manifest || !snapshot) return <main className="loading-state"><RadioTower aria-hidden="true" /><span>Loading authoritative DAG projection</span></main>;

  const inspectorValue: JsonValue = tab === "source"
    ? manifest.source_dag
    : tab === "plan"
      ? manifest.dag_plan
      : selectedLive ?? snapshot;

  return <main className="dag-app">
    <StatusBanner snapshot={snapshot} connected={connected} />
    <section className="dag-app__workspace">
      <div className={`graph-pane${transaction ? " graph-pane--with-transaction" : ""}`} data-qid="dag:workspace:graph">
        <div className="pane-heading"><strong>Execution graph</strong><span>read-only · source DAG unchanged</span></div>
        <div className="graph-canvas" data-qid="dag:workspace:canvas">
          <DagWorkspace manifest={manifest} snapshot={snapshot} selectedId={selectedId} onSelect={setSelectedId} />
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
            : <JsonInspector value={inspectorValue} label={`${tab} JSON`} />}
        </div>
        <footer className="proof-boundary" data-qid="dag:workspace:proof-boundary">
          <div><strong>Proves</strong>{snapshot.proof_scope.proves.map((claim) => <span key={claim}>{claim}</span>)}</div>
          <div><strong>Does not prove</strong>{snapshot.proof_scope.does_not_prove.map((claim) => <span key={claim}>{claim}</span>)}</div>
        </footer>
      </aside>
    </section>
    <EventTimeline events={snapshot.recent_events} onSelect={setSelectedId} />
  </main>;
}
