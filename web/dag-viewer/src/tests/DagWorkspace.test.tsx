import { render, screen } from "@testing-library/react";
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
