import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "../App";
import { explanation, manifest, nodeInspector, snapshot } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

test("renders authoritative graph, inspectors, transaction, and proof boundary", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("explanations")
        ? explanation
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [snapshot.recent_events[0]] }
          : snapshot;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"', "Content-Type": "application/json" } });
  }));
  render(<App />);
  await waitFor(() => expect(screen.getByText("Run timeline")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Timeline" })).toHaveAttribute("aria-pressed", "true");
  expect(document.querySelector('[data-qid="dag:timeline:run"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:timeline:scale"]')).toHaveAttribute("data-scale-mode", "sequence");
  expect(document.querySelector('[data-qid="dag:timeline:execution:creator"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:timeline:proof:creator"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:timeline:control:terminal:human"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:timeline:role-swimlanes"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:timeline:role-clip:producer:command:creator"]')).toHaveAttribute("data-duration-mode", "point");
  await waitFor(() => expect(window.__tauRegisteredActions?.get("dag:workspace-view:timeline")).toMatchObject({
    action: "DAG_WORKSPACE_TIMELINE",
  }));
  fireEvent.click(screen.getByRole("button", { name: "Topology" }));
  await waitFor(() => expect(screen.getByText("Execution graph")).toBeInTheDocument());
  expect(window.location.search).toContain("workspace_view=topology");
  expect(document.querySelector('[data-qid="dag:graph:viewport-controls"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:graph:zoom-percent"]')).toHaveTextContent("%");
  fireEvent.click(document.querySelector('[data-qid="dag:graph:zoom-in"]') as Element);
  await waitFor(() => expect(window.__tauRegisteredActions?.get("dag:graph:zoom-in")).toMatchObject({
    action: "DAG_GRAPH_ZOOM_IN",
  }));
  expect(screen.getByRole("button", { name: /Source DAG/ })).toBeInTheDocument();
  expect(screen.getByText("Reviewer REVISE")).toBeInTheDocument();
  expect(screen.getByText(/Tau admission: AWAITING_RECEIPT/)).toBeInTheDocument();
  expect(screen.getByText("semantic correctness")).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:workspace:graph"]')).toHaveClass("graph-pane--with-transaction");
  expect(document.querySelector('[data-qid="dag:workspace:canvas"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:workspace:proof-boundary"]')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("attempt_dispatched")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Why" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("journal:6")).toBeInTheDocument();
});

test("timeline clips select authoritative subjects without leaving timeline view", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("nodes/publish/inspector")
        ? { ...nodeInspector, node_id: "publish", attempt: 0, attempt_id: null, projection_key: "sha256:publish-key", projection_sha256: "sha256:publish", attention: [] }
        : url.includes("nodes/creator/inspector")
          ? nodeInspector
          : url.includes("explanations")
            ? explanation
            : url.includes("events")
              ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [] }
              : snapshot;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"', "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await waitFor(() => expect(document.querySelector('[data-qid="dag:timeline:execution:publish"]')).toBeInTheDocument());
  expect(document.querySelector('[data-qid="dag:timeline:decision-required"]')).not.toBeInTheDocument();
  fireEvent.click(document.querySelector('[data-qid="dag:timeline:execution:publish"]') as Element);
  expect(document.querySelector('[data-qid="dag:timeline:selected-event"]')).toHaveAttribute("data-event-id", "execution:publish:attempt:0");
  expect(screen.getByText("Run timeline")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Node" })).toHaveAttribute("aria-pressed", "true");
  await waitFor(() => expect(fetchMock.mock.calls.some(([value]) => String(value).includes("/explanations/node/publish"))).toBe(true));
  await waitFor(() => expect(document.querySelector('[data-qid="dag:selected-node-inspector"] header strong')).toHaveTextContent("publish"));
  fireEvent.click(document.querySelector('[data-qid="dag:timeline:proof:publish"]') as Element);
  expect(document.querySelector('[data-qid="dag:timeline:selected-event"]')).toHaveAttribute("data-event-id", "proof_admission:publish:attempt:0");
});

test("timeline proof clips do not turn accepted from execution state alone", async () => {
  const executionSettledAwaitingProof = {
    ...snapshot,
    nodes: snapshot.nodes.map((node) => node.node_id === "creator"
      ? {
        ...node,
        scheduler: { ...node.scheduler, state: "settled" },
        runtime: { ...node.runtime, state: "EXITED", liveness: "EXITED" },
        admission: { state: "awaiting_receipt", accepted: false, receipt_refs: ["receipt-pending"] },
        result: { ...node.result, summary: "handler returned output awaiting receipt admission" },
      }
      : node),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("explanations")
        ? explanation
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [] }
          : executionSettledAwaitingProof;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"', "Content-Type": "application/json" } });
  }));
  render(<App />);
  await waitFor(() => expect(document.querySelector('[data-qid="dag:timeline:proof:creator"]')).toBeInTheDocument());
  expect(document.querySelector('[data-qid="dag:timeline:execution:creator"]')).toHaveClass("run-timeline__clip--accepted");
  expect(document.querySelector('[data-qid="dag:timeline:proof:creator"]')).toHaveClass("run-timeline__clip--warning");
  expect(document.querySelector('[data-qid="dag:timeline:proof:creator"]')).not.toHaveClass("run-timeline__clip--accepted");
  expect(screen.queryByText("handler returned output awaiting receipt admission")).not.toBeInTheDocument();
});

test("timeline execution ignores proof admission and failure takes precedence", async () => {
  const conflictingProjection = {
    ...snapshot,
    nodes: snapshot.nodes.map((node) => node.node_id === "creator"
      ? {
        ...node,
        scheduler: { ...node.scheduler, state: "settled" },
        runtime: { ...node.runtime, state: "FAILED", liveness: "EXITED" },
        admission: { state: "accepted", accepted: true, receipt_refs: ["receipt-accepted"] },
        result: { ...node.result, blocker_codes: [], summary: "result summary belongs to execution" },
      }
      : node),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("explanations")
        ? explanation
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [] }
          : conflictingProjection;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"', "Content-Type": "application/json" } });
  }));
  render(<App />);
  await waitFor(() => expect(document.querySelector('[data-qid="dag:timeline:execution:creator"]')).toBeInTheDocument());
  expect(document.querySelector('[data-qid="dag:timeline:execution:creator"]')).toHaveClass("run-timeline__clip--blocked");
  expect(document.querySelector('[data-qid="dag:timeline:proof:creator"]')).toHaveClass("run-timeline__clip--accepted");
  expect(screen.queryByText("result summary belongs to execution")).not.toBeInTheDocument();
});

test("timeline execution cannot be accepted by admission alone", async () => {
  const admissionAcceptedOnly = {
    ...snapshot,
    nodes: snapshot.nodes.map((node) => node.node_id === "creator"
      ? {
        ...node,
        scheduler: { ...node.scheduler, state: "scheduled" },
        runtime: { ...node.runtime, state: "NOT_STARTED", liveness: "NOT_STARTED" },
        admission: { state: "accepted", accepted: true, receipt_refs: ["receipt-accepted"] },
        result: { ...node.result, blocker_codes: [], summary: null },
      }
      : node),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("explanations")
        ? explanation
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [] }
          : admissionAcceptedOnly;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"', "Content-Type": "application/json" } });
  }));
  render(<App />);
  await waitFor(() => expect(document.querySelector('[data-qid="dag:timeline:execution:creator"]')).toBeInTheDocument());
  expect(document.querySelector('[data-qid="dag:timeline:execution:creator"]')).not.toHaveClass("run-timeline__clip--accepted");
  expect(document.querySelector('[data-qid="dag:timeline:proof:creator"]')).toHaveClass("run-timeline__clip--accepted");
});

test("timeline selectors remain unique when node ids sanitize to the same qid token", async () => {
  const collidingManifest = {
    ...manifest,
    graph: {
      ...manifest.graph,
      nodes: [
        { ...manifest.graph.nodes[0], node_id: "node a" },
        { ...manifest.graph.nodes[1], node_id: "node-a" },
        { ...manifest.graph.nodes[0], node_id: "node/a" },
      ],
    },
  };
  const collidingSnapshot = {
    ...snapshot,
    nodes: [
      { ...snapshot.nodes[0], node_id: "node a" },
      { ...snapshot.nodes[1], node_id: "node-a" },
      { ...snapshot.nodes[0], node_id: "node/a" },
    ],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest")
      ? collidingManifest
      : url.includes("explanations")
        ? explanation
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [] }
          : collidingSnapshot;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"', "Content-Type": "application/json" } });
  }));
  render(<App />);
  await waitFor(() => expect(document.querySelectorAll('[data-qid^="dag:timeline:execution:node-a"]').length).toBe(3));
  const qids = [...document.querySelectorAll('[data-qid^="dag:timeline:execution:node-a"]')]
    .map((element) => element.getAttribute("data-qid"));
  expect(new Set(qids).size).toBe(3);
  const eventIds = [...document.querySelectorAll('[data-qid^="dag:timeline:execution:node-a"]')]
    .map((element) => element.getAttribute("data-event-id"));
  expect(eventIds).toEqual(expect.arrayContaining([
    "execution:node a:attempt:1",
    "execution:node-a:attempt:0",
    "execution:node/a:attempt:1",
  ]));
});

test("unsupported timeline entity types fall back to the run causal subject", async () => {
  const unsupported = { ...snapshot.recent_events[0], entity_type: "scheduler-internal", entity_id: "private-scheduler" };
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("explanations")
        ? { ...explanation, subject: { kind: "RUN", id: "run-1" } }
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [{ ...unsupported, seq: 1 }, unsupported] }
          : { ...snapshot, recent_events: [unsupported] };
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"' } });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await waitFor(() => expect(document.querySelector('[data-qid="dag:event:8"]')).toBeInTheDocument());
  fireEvent.click(document.querySelector('[data-qid="dag:event:8"]') as Element);
  await waitFor(() => expect(fetchMock.mock.calls.some(([value]) => String(value).includes("/explanations/run/run-1?at_sequence=8"))).toBe(true));
  expect(fetchMock.mock.calls.some(([value]) => String(value).includes("scheduler-internal"))).toBe(false);
});

test("shows the selected external terminal in the live-state inspector", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("explanations")
        ? { ...explanation, subject: { kind: "TERMINAL", id: "human" }, projected_state: "pending" }
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [] }
          : snapshot;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"', "Content-Type": "application/json" } });
  }));
  render(<App />);
  await waitFor(() => expect(document.querySelector('[data-qid="dag:timeline:control:terminal:human"]')).toBeInTheDocument());

  fireEvent.click(document.querySelector('[data-qid="dag:timeline:control:terminal:human"]') as Element);
  fireEvent.click(screen.getByRole("button", { name: /Live State/ }));

  expect(screen.getByLabelText("live JSON")).toHaveTextContent('"terminal_id": "human"');
  expect(screen.getByLabelText("live JSON")).toHaveTextContent('"state": "pending"');
});

test("attention selection opens its immutable causal explanation", async () => {
  const attention = {
    schema: "tau.dag_attention_item.v1" as const,
    attention_id: "attention-1",
    severity: "ACTION_REQUIRED" as const,
    state: "OPEN" as const,
    reason_code: "run_blocked",
    subject: { kind: "RUN", id: "run-1" },
    opened_sequence: 8,
    resolved_sequence: null,
    required_action_code: "REVIEW_BLOCKED_RUN",
    causal_explanation_id: "explanation-attention",
  };
  const withAttention = {
    ...snapshot,
    attention_items: [attention],
    highest_priority_attention_id: attention.attention_id,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("explanations/attention")
        ? { ...explanation, subject: { kind: "ATTENTION", id: "attention-1" }, reason_code: "run_blocked" }
        : url.includes("explanations")
          ? explanation
          : url.includes("events")
            ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [] }
            : withAttention;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"' } });
  }));
  render(<App />);
  await waitFor(() => expect(screen.getByText("Human attention")).toBeInTheDocument());
  await waitFor(() => expect(document.querySelector('[data-qid="dag:timeline:decision-required"]')).toBeInTheDocument());
  expect(document.querySelector('[data-qid="dag:timeline:decision:attention-1"]')).toHaveAttribute("data-qs-action", "TAU_TIMELINE_SELECT_DECISION");
  expect(document.querySelector('[data-qid="dag:timeline:decision:attention-1"]')).toHaveAttribute("data-event-id", "human_decision:attention-1");
  fireEvent.click(document.querySelector('[data-qid="dag:timeline:decision:attention-1"]') as Element);
  await waitFor(() => expect(screen.getByText("REVIEW_BLOCKED_RUN · #8")).toBeInTheDocument());
  expect(document.querySelector('[data-qid="dag:causal:details"]')).toBeInTheDocument();
});

test("resolve-style workspace panes are data-backed and independently collapsible", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("explanations")
        ? explanation
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [] }
          : snapshot;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"', "Content-Type": "application/json" } });
  }));
  render(<App />);
  await waitFor(() => expect(document.querySelector('[data-qid="dag:pool:browser"]')).toBeInTheDocument());

  expect(document.querySelector('[data-qid="dag:pool:orchestration:run-1"]')).toHaveAttribute("data-run-status", "RUNNING");
  expect(document.querySelector('[data-qid="dag:layout:toggles"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:stream:controls"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:stream:status"]')).toHaveAttribute("data-stream-status", "CONNECTED");
  fireEvent.click(document.querySelector('[data-qid="dag:stream:toggle-ingestion"]') as Element);
  expect(document.querySelector('[data-qid="dag:stream:status"]')).toHaveAttribute("data-stream-status", "PAUSED");
  fireEvent.click(document.querySelector('[data-qid="dag:stream:toggle-ingestion"]') as Element);
  expect(document.querySelector('[data-qid="dag:stream:status"]')).toHaveAttribute("data-stream-status", "CONNECTED");
  fireEvent.change(screen.getByLabelText("Search current orchestration"), { target: { value: "producer" } });
  expect(document.querySelector('[data-qid="dag:pool:count"]')).toHaveTextContent("1");
  fireEvent.click(document.querySelector('[data-qid="dag:pool:filter:settled"]') as Element);
  expect(document.querySelector('[data-qid="dag:pool:count"]')).toHaveTextContent("0");

  fireEvent.click(document.querySelector('[data-qid="dag:layout:toggle-right"]') as Element);
  expect(document.querySelector('[data-qid="dag:workspace:inspector"]')).not.toBeInTheDocument();
  fireEvent.click(document.querySelector('[data-qid="dag:layout:toggle-right"]') as Element);
  expect(document.querySelector('[data-qid="dag:workspace:inspector"]')).toBeInTheDocument();

  fireEvent.click(document.querySelector('[data-qid="dag:layout:toggle-left"]') as Element);
  expect(document.querySelector('[data-qid="dag:pool:browser"]')).not.toBeInTheDocument();
  fireEvent.click(document.querySelector('[data-qid="dag:timeline:events:toggle"]') as Element);
  expect(document.querySelector('[data-qid="dag:event:8"]')).not.toBeInTheDocument();
});
