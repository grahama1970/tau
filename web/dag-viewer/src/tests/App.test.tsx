import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "../App";
import { manifest, snapshot } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("renders authoritative graph, inspectors, transaction, and proof boundary", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest") ? manifest : snapshot;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"', "Content-Type": "application/json" } });
  }));
  render(<App />);
  await waitFor(() => expect(screen.getByText("Execution graph")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: /Source DAG/ })).toBeInTheDocument();
  expect(screen.getByText("Reviewer REVISE")).toBeInTheDocument();
  expect(screen.getByText(/Tau admission: AWAITING_RECEIPT/)).toBeInTheDocument();
  expect(screen.getByText("semantic correctness")).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:workspace:graph"]')).toHaveClass("graph-pane--with-transaction");
  expect(document.querySelector('[data-qid="dag:workspace:canvas"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:workspace:proof-boundary"]')).toBeInTheDocument();
});

test("shows the selected external terminal in the live-state inspector", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("manifest") ? manifest : snapshot;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"', "Content-Type": "application/json" } });
  }));
  render(<App />);
  await waitFor(() => expect(document.querySelector('[data-qid="dag:node:human"]')).toBeInTheDocument());

  fireEvent.click(document.querySelector('[data-qid="dag:node:human"]') as Element);
  fireEvent.click(screen.getByRole("button", { name: /Live State/ }));

  expect(screen.getByLabelText("live JSON")).toHaveTextContent('"terminal_id": "human"');
  expect(screen.getByLabelText("live JSON")).toHaveTextContent('"state": "pending"');
});
