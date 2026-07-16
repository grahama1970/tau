import { useMemo } from "react";
import { Background, Controls, MarkerType, ReactFlow, ReactFlowProvider, type Edge, type Node } from "@xyflow/react";
import { layoutDag } from "../layout/dagLayout";
import type { DagManifest, DagSnapshot } from "../types";
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

function Workspace({ manifest, snapshot, selectedId, onSelect }: Props) {
  const stateByNode = useMemo(() => new Map(snapshot.nodes.map((node) => [node.node_id, node])), [snapshot]);
  const edgeState = useMemo(() => new Map(snapshot.edges.map((edge) => [edge.edge_id, edge.state])), [snapshot]);
  const terminalState = useMemo(
    () => new Map(snapshot.terminals.map((terminal) => [terminal.terminal_id, terminal.state])),
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
                updated_sequence: snapshot.journal_sequence,
              },
            },
          };
        }),
    ],
    edges,
  ) as Node<TauNodeData>[], [edges, manifest.graph.nodes, manifest.graph.terminals, selectedId, snapshot.journal_sequence, stateByNode, terminalIds, terminalState]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      fitView
      fitViewOptions={{ padding: 0.16, minZoom: 0.68, maxZoom: 1 }}
      minZoom={0.55}
      maxZoom={1.4}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      onNodeClick={(_, node) => onSelect(node.id)}
      aria-label="Tau DAG execution graph"
    >
      <Background color="#263140" gap={24} size={1} />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
  );
}

export function DagWorkspace(props: Props) {
  return <ReactFlowProvider><Workspace {...props} /></ReactFlowProvider>;
}
