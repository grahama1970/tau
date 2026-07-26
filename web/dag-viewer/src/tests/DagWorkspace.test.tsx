import { act, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { DagWorkspace } from "../components/DagWorkspace";
import { manifest, snapshot } from "./fixtures";

test("keeps runtime-alive awaiting-receipt node non-green", () => {
  render(<div style={{ width: 900, height: 500 }}><DagWorkspace manifest={manifest} snapshot={snapshot} selectedId={null} onSelect={vi.fn()} /></div>);
  const node = screen.getByLabelText("creator, running, awaiting_receipt");
  expect(node).toHaveAttribute("data-admission-state", "awaiting_receipt");
  expect(node.className).not.toContain("accepted");
});

test("renders an external terminal targeted by a plan edge without mutating the DAG", () => {
  const sourceGraph = JSON.stringify(manifest.graph);
  const { container } = render(<div style={{ width: 900, height: 500 }}><DagWorkspace manifest={manifest} snapshot={snapshot} selectedId={null} onSelect={vi.fn()} /></div>);
  const terminal = container.querySelector('[data-qid="dag:node:human"]');

  expect(terminal).toBeInTheDocument();
  expect(terminal).toHaveAttribute("aria-label", "human, pending, not_applicable");
  expect(manifest.graph.edges.find((edge) => edge.edge_id === "publish-human")?.target.id).toBe("human");
  expect(JSON.stringify(manifest.graph)).toBe(sourceGraph);
});

test("renders a successful external terminal with the settled tone", () => {
  const successful = {
    ...snapshot,
    terminals: [
      { terminal_id: "human", state: "success", causal_explanation_id: "explanation-human" },
    ],
  };
  const { container } = render(<div style={{ width: 900, height: 500 }}><DagWorkspace manifest={manifest} snapshot={successful} selectedId={null} onSelect={vi.fn()} /></div>);
  const terminal = container.querySelector('[data-qid="dag:node:human"]');

  expect(terminal).toHaveAttribute("aria-label", "human, settled, not_applicable");
  expect(terminal).toHaveClass("tau-node--settled");
});

test("renders superseded nodes with a distinct state and tone", () => {
  const superseded = {
    ...snapshot,
    nodes: snapshot.nodes.map((node) => (
      node.node_id === "creator"
        ? {
          ...node,
          scheduler: { ...node.scheduler, state: "superseded" },
          admission: { ...node.admission, state: "not_applicable", accepted: false },
        }
        : node
    )),
  };
  render(<div style={{ width: 900, height: 500 }}><DagWorkspace manifest={manifest} snapshot={superseded} selectedId={null} onSelect={vi.fn()} /></div>);

  const node = screen.getByLabelText("creator, superseded, not_applicable");
  expect(node).toHaveAttribute("data-state", "superseded");
  expect(node).toHaveAttribute("data-node-state", "superseded");
  expect(node).toHaveClass("tau-node--superseded");
  expect(node).not.toHaveClass("tau-node--accepted");
});

test("renders completed duration and ticking in-flight elapsed time", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-01-01T00:02:00Z"));
  try {
    const timed = {
      ...snapshot,
      nodes: snapshot.nodes.map((node) => {
        if (node.node_id === "creator") {
          return {
            ...node,
            result: {
              ...node.result,
              started_at: "2026-01-01T00:00:00Z",
              finished_at: null,
              duration_seconds: null,
            },
          };
        }
        return {
          ...node,
          scheduler: { ...node.scheduler, state: "settled", attempt: 1 },
          admission: { ...node.admission, state: "accepted", accepted: true },
          result: {
            ...node.result,
            started_at: "2026-01-01T00:00:01Z",
            finished_at: "2026-01-01T00:01:15Z",
            duration_seconds: 74,
          },
        };
      }),
    };
    const { container } = render(<div style={{ width: 900, height: 500 }}><DagWorkspace manifest={manifest} snapshot={timed} selectedId={null} onSelect={vi.fn()} /></div>);
    const activeTiming = container.querySelector('[data-qid="dag:node:creator:timing"]');
    const completedTiming = container.querySelector('[data-qid="dag:node:publish:timing"]');

    expect(activeTiming).toHaveTextContent("elapsed");
    expect(activeTiming).toHaveTextContent("2m 00s");
    expect(completedTiming).toHaveTextContent("duration");
    expect(completedTiming).toHaveTextContent("1m 14s");

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(activeTiming).toHaveTextContent("2m 03s");
  } finally {
    vi.useRealTimers();
  }
});
