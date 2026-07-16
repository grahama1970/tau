import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "../App";
import { explanation, manifest, snapshot } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

test("filters bounded projections and renders exactly-two comparison", async () => {
  window.history.replaceState({}, "", "/?filter_q=creator&filter_kind=NODE");
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("/api/v1/query")
      ? {
        schema: "tau.dag_view_query_result.v1", run_id: "run-1", as_of_sequence: 8,
        query: { q: "creator", entity_kind: "NODE" }, next_cursor: null, result_count: 1, total_match_count: 1,
        items: [{ entity_kind: "NODE", entity_id: "creator", node_id: "creator", attempt: 1, event_type: null, receipt_schema: null, state: "running", attention_state: null, attention_severity: null, sequence: 8, preview: "NODE · creator · running" }],
      }
      : url.includes("/api/v1/compare")
        ? {
          schema: "tau.dag_view_comparison.v1", kind: "SEQUENCE_PAIR", run_id: "run-1", as_of_sequence: 8,
          left: { run_id: "run-1", reference: { kind: "SEQUENCE", sequence: 1 }, sequence: 1, projection: {}, truncated: false },
          right: { run_id: "run-1", reference: { kind: "SEQUENCE", sequence: 8 }, sequence: 8, projection: {}, truncated: false },
          changes: [{ field: "$.nodes", change: "CHANGED", left: [], right: [] }], truncated: false, comparison_sha256: "sha256:comparison",
        }
        : url.includes("manifest")
          ? manifest
          : url.includes("explanations")
            ? explanation
            : url.includes("events")
              ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [{ ...snapshot.recent_events[0], seq: 1 }, snapshot.recent_events[0]] }
              : snapshot;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"' } });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);

  await waitFor(() => expect(screen.getByText("1 matches · 1 shown")).toBeInTheDocument());
  expect(screen.getByText("redacted projections only")).toBeInTheDocument();
  expect(window.location.search).toContain("filter_q=creator");
  await waitFor(() => expect(screen.getByLabelText("Left sequence")).toHaveValue("1"));
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  await waitFor(() => expect(screen.getByText("1 changes")).toBeInTheDocument());
  expect(fetchMock.mock.calls.some(([value]) => String(value).includes("kind=SEQUENCE_PAIR"))).toBe(true);
  expect(fetchMock.mock.calls.some(([value]) => String(value).includes("at_sequence=8"))).toBe(true);
});

test("stale comparison response cannot replace a newer request", async () => {
  const comparisonResolvers: Array<(response: Response) => void> = [];
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/v1/compare")) {
      return new Promise<Response>((resolve) => comparisonResolvers.push(resolve));
    }
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("explanations")
        ? explanation
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [{ ...snapshot.recent_events[0], seq: 1 }, snapshot.recent_events[0]] }
          : snapshot;
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"' } }));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await waitFor(() => expect(screen.getByLabelText("Comparison kind")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  await waitFor(() => expect(comparisonResolvers).toHaveLength(1));
  fireEvent.change(screen.getByLabelText("Comparison kind"), { target: { value: "ATTEMPT_PAIR" } });
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  await waitFor(() => expect(comparisonResolvers).toHaveLength(2));

  const response = (field: string) => new Response(JSON.stringify({
    schema: "tau.dag_view_comparison.v1", kind: "ATTEMPT_PAIR", run_id: "run-1", as_of_sequence: 8,
    left: { run_id: "run-1", reference: { kind: "ATTEMPT", attempt: 1 }, sequence: 1, projection: {}, truncated: false },
    right: { run_id: "run-1", reference: { kind: "ATTEMPT", attempt: 2 }, sequence: 8, projection: {}, truncated: false },
    changes: [{ field, change: "CHANGED", left: null, right: null }], truncated: false, comparison_sha256: "sha256:comparison",
  }), { status: 200 });
  await act(async () => comparisonResolvers[1](response("$.newer")));
  await waitFor(() => expect(screen.getByText("CHANGED $.newer")).toBeInTheDocument());
  await act(async () => comparisonResolvers[0](response("$.stale")));
  expect(screen.queryByText("CHANGED $.stale")).not.toBeInTheDocument();
  expect(screen.getByText("CHANGED $.newer")).toBeInTheDocument();
});

test("journal-prefix navigation clears and invalidates an in-flight comparison", async () => {
  let resolveComparison: ((response: Response) => void) | null = null;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/v1/compare")) {
      return new Promise<Response>((resolve) => { resolveComparison = resolve; });
    }
    const payload = url.includes("manifest")
      ? manifest
      : url.includes("explanations")
        ? explanation
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [{ ...snapshot.recent_events[0], seq: 1 }, snapshot.recent_events[0]] }
          : snapshot;
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"' } }));
  }));
  render(<App />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Compare" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  await waitFor(() => expect(resolveComparison).not.toBeNull());
  fireEvent.change(screen.getByLabelText("Committed journal sequence"), { target: { value: "1" } });
  await act(async () => resolveComparison?.(new Response(JSON.stringify({
    schema: "tau.dag_view_comparison.v1", kind: "SEQUENCE_PAIR", run_id: "run-1", as_of_sequence: 8,
    left: { run_id: "run-1", reference: {}, sequence: 1, projection: {}, truncated: false },
    right: { run_id: "run-1", reference: {}, sequence: 8, projection: {}, truncated: false },
    changes: [{ field: "$.future", change: "CHANGED" }], truncated: false, comparison_sha256: "sha256:comparison",
  }), { status: 200 })));
  expect(screen.queryByText("CHANGED $.future")).not.toBeInTheDocument();
  expect(screen.getByText("Select two authoritative states.")).toBeInTheDocument();
});

test("browser history restores filter URL state and results", async () => {
  window.history.replaceState({}, "", "/?filter_q=old&filter_kind=NODE");
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("/api/v1/query")
      ? {
        schema: "tau.dag_view_query_result.v1", run_id: "run-1", as_of_sequence: 8,
        query: {}, next_cursor: null, result_count: 1, total_match_count: 1,
        items: [{ entity_kind: "EVENT", entity_id: "event:1", node_id: null, attempt: null, event_type: "new", receipt_schema: null, state: "new", attention_state: null, attention_severity: null, sequence: 1, preview: "EVENT · new" }],
      }
      : url.includes("manifest")
        ? manifest
        : url.includes("explanations")
          ? explanation
          : url.includes("events")
            ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [] }
            : snapshot;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"' } });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await waitFor(() => expect(screen.getByLabelText("Filter IDs, codes, schemas, states, and previews")).toHaveValue("old"));
  window.history.pushState({}, "", "/?filter_q=new&filter_kind=EVENT");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await waitFor(() => expect(screen.getByLabelText("Filter IDs, codes, schemas, states, and previews")).toHaveValue("new"));
  expect(screen.getByLabelText("Entity kind")).toHaveValue("EVENT");
  await waitFor(() => expect(fetchMock.mock.calls.some(([value]) => String(value).includes("q=new") && String(value).includes("entity_kind=EVENT"))).toBe(true));
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
