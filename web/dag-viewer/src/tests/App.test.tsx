import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "../App";
import { explanation, manifest, snapshot } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("renders authoritative graph, inspectors, transaction, and proof boundary", async () => {
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
  await waitFor(() => expect(screen.getByText("Execution graph")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: /Source DAG/ })).toBeInTheDocument();
  expect(screen.getByText("Reviewer REVISE")).toBeInTheDocument();
  expect(screen.getByText(/Tau admission: AWAITING_RECEIPT/)).toBeInTheDocument();
  expect(screen.getByText("semantic correctness")).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:workspace:graph"]')).toHaveClass("graph-pane--with-transaction");
  expect(document.querySelector('[data-qid="dag:workspace:canvas"]')).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:workspace:proof-boundary"]')).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Why" }));
  await waitFor(() => expect(screen.getByText("attempt_dispatched")).toBeInTheDocument());
  expect(screen.getByText("journal:6")).toBeInTheDocument();
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
  await waitFor(() => expect(document.querySelector('[data-qid="dag:node:human"]')).toBeInTheDocument());

  fireEvent.click(document.querySelector('[data-qid="dag:node:human"]') as Element);
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
  fireEvent.click(screen.getByText("run_blocked"));
  await waitFor(() => expect(screen.getByText("REVIEW_BLOCKED_RUN · #8")).toBeInTheDocument());
  expect(document.querySelector('[data-qid="dag:causal:details"]')).toBeInTheDocument();
});
