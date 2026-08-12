import { useMemo } from "react";
import { Minus, Plus, RotateCcw, Scan } from "lucide-react";
import { Background, MarkerType, ReactFlow, ReactFlowProvider, useReactFlow, useViewport, type Edge, type Node } from "@xyflow/react";
import { layoutDag } from "../layout/dagLayout";
import type { DagManifest, DagSnapshot } from "../types";
import { useRegisterAction } from "../useRegisterAction";
import { TauEdge } from "./TauEdge";
import { TauNode, type TauNodeData } from "./TauNode";

const nodeTypes = { tauNode: TauNode };
const edgeTypes = { tauEdge: TauEdge };

type Props = {
  manifest: DagManifest;
  snapshot: DagSnapshot;
  selectedId: string | null;
  onSelect: (id: string) => void;
};

function GraphViewportControls() {
  const flow = useReactFlow();
  const viewport = useViewport();
  const zoomPercent = Math.round(viewport.zoom * 100);

  useRegisterAction("dag:graph:zoom-in", {
    action: "DAG_GRAPH_ZOOM_IN",
    label: "Zoom In",
    description: "Zoom into the Tau topology graph.",
  });
  useRegisterAction("dag:graph:zoom-out", {
    action: "DAG_GRAPH_ZOOM_OUT",
    label: "Zoom Out",
    description: "Zoom out of the Tau topology graph.",
  });
  useRegisterAction("dag:graph:reset-view", {
    action: "DAG_GRAPH_RESET_VIEW",
    label: "Reset Graph View",
    description: "Reset the topology graph viewport to 100 percent zoom.",
  });
  useRegisterAction("dag:graph:fit-view", {
    action: "DAG_GRAPH_FIT_VIEW",
    label: "Fit Graph View",
    description: "Fit all topology graph nodes into the viewport.",
  });

  return <div className="graph-viewport-controls" data-qid="dag:graph:viewport-controls" aria-label="Topology viewport controls">
    <button
      type="button"
      data-qid="dag:graph:zoom-in"
      data-qs-action="DAG_GRAPH_ZOOM_IN"
      title="Zoom in"
      aria-label="Zoom in"
      onClick={() => void flow.zoomIn({ duration: 120 })}
    ><Plus aria-hidden="true" size={14} /></button>
    <span data-qid="dag:graph:zoom-percent" title="Current graph zoom">{zoomPercent}%</span>
    <button
      type="button"
      data-qid="dag:graph:zoom-out"
      data-qs-action="DAG_GRAPH_ZOOM_OUT"
      title="Zoom out"
      aria-label="Zoom out"
      onClick={() => void flow.zoomOut({ duration: 120 })}
    ><Minus aria-hidden="true" size={14} /></button>
    <button
      type="button"
      data-qid="dag:graph:reset-view"
      data-qs-action="DAG_GRAPH_RESET_VIEW"
      title="Reset graph viewport to 100 percent"
      aria-label="Reset graph viewport"
      onClick={() => void flow.setViewport({ x: 0, y: 0, zoom: 1 }, { duration: 120 })}
    ><RotateCcw aria-hidden="true" size={14} /><span>100%</span></button>
    <button
      type="button"
      data-qid="dag:graph:fit-view"
      data-qs-action="DAG_GRAPH_FIT_VIEW"
      title="Fit graph to viewport"
      aria-label="Fit graph to viewport"
      onClick={() => void flow.fitView({ padding: 0.28, minZoom: 0.48, maxZoom: 1 })}
    ><Scan aria-hidden="true" size={14} /></button>
  </div>;
}

function Workspace({ manifest, snapshot, selectedId, onSelect }: Props) {
  const stateByNode = useMemo(() => new Map(snapshot.nodes.map((node) => [node.node_id, node])), [snapshot]);
  const edgeState = useMemo(() => new Map(snapshot.edges.map((edge) => [edge.edge_id, edge.state])), [snapshot]);
  const terminalState = useMemo(
    () => new Map(snapshot.terminals.map((terminal) => [terminal.terminal_id, terminal.state])),
    [snapshot.terminals],
  );
  const terminalCause = useMemo(
    () => new Map(
      snapshot.terminals.map((terminal) => [terminal.terminal_id, terminal.causal_explanation_id]),
    ),
    [snapshot.terminals],
  );
  const edges = useMemo<Edge[]>(() => manifest.graph.edges.map((edge) => ({
    id: edge.edge_id,
    source: edge.source_node_id,
    target: edge.target.id,
    sourceHandle: "output",
    targetHandle: "input",
    type: "tauEdge",
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    data: { state: edgeState.get(edge.edge_id) ?? "pending" },
    animated: edgeState.get(edge.edge_id) === "success",
  })), [edgeState, manifest.graph.edges]);
  const terminalIds = useMemo(
    () => new Set(manifest.graph.edges.map((edge) => edge.target.id)),
    [manifest.graph.edges],
  );
  const nodes = useMemo<Node<TauNodeData>[]>(() => layoutDag(
    [
      ...manifest.graph.nodes.map((node) => ({
        id: node.node_id,
        type: "tauNode",
        position: { x: 0, y: 0 },
        selected: node.node_id === selectedId,
        data: {
          label: node.node_id,
          role: node.role,
          kind: node.adapter.kind,
          live: stateByNode.get(node.node_id) ?? null,
        },
      })),
      ...manifest.graph.terminals
        .filter((terminal) => terminalIds.has(terminal.terminal_id))
        .filter((terminal) => !manifest.graph.nodes.some((node) => node.node_id === terminal.terminal_id))
        .map((terminal) => {
          const state = terminalState.get(terminal.terminal_id) ?? "pending";
          const schedulerState = state === "success" ? "settled" : state;
          return {
            id: terminal.terminal_id,
            type: "tauNode",
            position: { x: 0, y: 0 },
            selected: terminal.terminal_id === selectedId,
            data: {
              label: terminal.terminal_id,
              role: `${terminal.kind} terminal`,
              kind: terminal.kind,
              live: {
                node_id: terminal.terminal_id,
                node_kind: "terminal",
                scheduler: { state: schedulerState, attempt: 0, max_attempts: 1 },
                runtime: { state: "UNKNOWN", liveness: "UNKNOWN", confidence: "UNKNOWN", last_event_id: null },
                admission: { state: "not_applicable", accepted: false, receipt_refs: [] },
                transaction: null,
                correction: null,
                causal_explanation_id: terminalCause.get(terminal.terminal_id) ?? "",
                updated_sequence: snapshot.journal_sequence,
              },
            },
          };
        }),
    ],
    edges,
  ) as Node<TauNodeData>[], [edges, manifest.graph.nodes, manifest.graph.terminals, selectedId, snapshot.journal_sequence, stateByNode, terminalCause, terminalIds, terminalState]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      fitView
      fitViewOptions={{ padding: 0.28, minZoom: 0.48, maxZoom: 0.92 }}
	      minZoom={0.45}
	      maxZoom={2.5}
	      zoomOnScroll
	      panOnDrag
	      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      onNodeClick={(_, node) => onSelect(node.id)}
      aria-label="Tau DAG execution graph"
	    >
	      <Background color="#263140" gap={24} size={1} />
	      <GraphViewportControls />
	    </ReactFlow>
  );
}

export function DagWorkspace(props: Props) {
  return <ReactFlowProvider><Workspace {...props} /></ReactFlowProvider>;
}
