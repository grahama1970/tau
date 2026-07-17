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
  expect(screen.getAllByText("Determine whether this checkout is ready for focused work.")).toHaveLength(2);
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
    left: { run_id: "run-1", reference: { kind: "ATTEMPT", node_id: "creator", attempt: 1, attempt_id: "attempt-1" }, sequence: 1, projection: {}, truncated: false },
    right: { run_id: "run-1", reference: { kind: "ATTEMPT", node_id: "creator", attempt: 2, attempt_id: "attempt-2" }, sequence: 8, projection: {}, truncated: false },
    changes: [{ field, change: "CHANGED", left: null, right: null }], truncated: false, comparison_sha256: "sha256:comparison",
  }), { status: 200 });
  await act(async () => comparisonResolvers[1](response("$.newer")));
  await waitFor(() => expect(screen.getByText("CHANGED $.newer")).toBeInTheDocument());
  await act(async () => comparisonResolvers[0](response("$.stale")));
  expect(screen.queryByText("CHANGED $.stale")).not.toBeInTheDocument();
  expect(screen.getByText("CHANGED $.newer")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Inspect left comparison side" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([value]) => String(value).includes("/explanations/attempt/attempt-1?at_sequence=1"))).toBe(true));
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
  await waitFor(() => expect(screen.getByText("attempt_dispatched")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Why" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("journal:6")).toBeInTheDocument();
});

test("timeline and comparison selections synchronize the authoritative sequence and subject", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://viewer.test");
    const selected = url.searchParams.get("at_sequence");
    const projected = selected
      ? { ...snapshot, journal_sequence: Number(selected), view: { ...snapshot.view, mode: "HISTORICAL" as const, sequence: Number(selected) } }
      : snapshot;
    const payload = url.pathname.includes("manifest")
      ? manifest
      : url.pathname.includes("explanations")
        ? { ...explanation, as_of_sequence: Number(selected ?? 8) }
        : url.pathname.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [{ ...snapshot.recent_events[0], seq: 1 }, snapshot.recent_events[0]] }
          : url.pathname.includes("compare")
            ? {
              schema: "tau.dag_view_comparison.v1", kind: "SEQUENCE_PAIR", run_id: "run-1", as_of_sequence: 8,
              left: { run_id: "run-1", reference: { kind: "SEQUENCE", sequence: 1 }, sequence: 1, projection: {}, truncated: false },
              right: { run_id: "run-1", reference: { kind: "SEQUENCE", sequence: 8 }, sequence: 8, projection: {}, truncated: false },
              changes: [], truncated: false, comparison_sha256: "sha256:comparison",
            }
            : projected;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: `"${selected ?? "live"}"` } });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await waitFor(() => expect(document.querySelector('[data-qid="dag:event:8"]')).toBeInTheDocument());

  fireEvent.click(document.querySelector('[data-qid="dag:event:8"]') as Element);
  expect(window.location.search).toContain("at_sequence=8");
  await waitFor(() => expect(screen.getByRole("button", { name: "Why" })).toHaveAttribute("aria-pressed", "true"));

  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Inspect left comparison side" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Inspect left comparison side" }));
  expect(window.location.search).toContain("at_sequence=1");
  await waitFor(() => expect(fetchMock.mock.calls.some(([value]) => String(value).includes("/explanations/run/run-1?at_sequence=1"))).toBe(true));
});

test("causal receipt references open the committed receipt inspector", async () => {
  const receiptEntry = { receipt_id: "receipt-1", schema: "tau.test_receipt.v1", path_display: "receipts/test.json", sha256: "sha256:receipt", available: true };
  const withReceipt = { ...manifest, receipt_index: [receiptEntry] };
  const withReceiptReference = { ...explanation, references: [{ kind: "RECEIPT", relation: "SUPPORTED_BY", reference_id: "receipt-1" }] };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("/receipts/")
      ? { schema: "tau.dag_viewer_receipt_projection.v1", receipt_id: "receipt-1", source_schema: "tau.test_receipt.v1", source_sha256: "sha256:receipt", receipt: { status: "PASS" } }
      : url.includes("manifest")
        ? withReceipt
        : url.includes("explanations")
          ? withReceiptReference
          : url.includes("events")
            ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [] }
            : snapshot;
    return new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"' } });
  }));
  render(<App />);
  await waitFor(() => expect(screen.getByRole("button", { name: "receipt-1" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "receipt-1" }));
  await waitFor(() => expect(screen.getByLabelText("Receipt JSON")).toHaveTextContent('"status": "PASS"'));
  expect(screen.getByRole("button", { name: "Receipt" })).toHaveAttribute("aria-pressed", "true");
});

test("out-of-order and prefix-stale receipt responses cannot replace current evidence", async () => {
  const entries = ["receipt-a", "receipt-b"].map((receiptId) => ({
    receipt_id: receiptId, schema: "tau.test_receipt.v1", path_display: `${receiptId}.json`, sha256: `sha256:${receiptId}`, available: true,
  }));
  const resolvers = new Map<string, Array<(response: Response) => void>>();
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/receipts/")) {
      const id = url.includes("receipt-a") ? "receipt-a" : "receipt-b";
      return new Promise<Response>((resolve) => resolvers.set(id, [...(resolvers.get(id) ?? []), resolve]));
    }
    const payload = url.includes("manifest")
      ? { ...manifest, receipt_index: entries }
      : url.includes("explanations")
        ? explanation
        : url.includes("events")
          ? { schema: "tau.dag_live_event.v1", run_id: "run-1", after_sequence: 0, events: [{ ...snapshot.recent_events[0], seq: 1 }, snapshot.recent_events[0]] }
          : snapshot;
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { ETag: '"one"' } }));
  }));
  render(<App />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Receipt" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Receipt" }));
  const selector = screen.getByLabelText("Committed receipt");
  fireEvent.change(selector, { target: { value: "receipt-a" } });
  await waitFor(() => expect(resolvers.get("receipt-a")).toHaveLength(1));
  fireEvent.change(selector, { target: { value: "receipt-b" } });
  await waitFor(() => expect(resolvers.get("receipt-b")).toHaveLength(1));

  await act(async () => resolvers.get("receipt-b")?.[0](new Response(JSON.stringify({
    schema: "tau.dag_viewer_receipt_projection.v1", receipt_id: "receipt-b", source_schema: "tau.test_receipt.v1", source_sha256: "sha256:b", receipt: { marker: "CURRENT" },
  }), { status: 200 })));
  await waitFor(() => expect(screen.getByLabelText("Receipt JSON")).toHaveTextContent("CURRENT"));
  await act(async () => resolvers.get("receipt-a")?.[0](new Response(JSON.stringify({
    schema: "tau.dag_viewer_receipt_projection.v1", receipt_id: "receipt-a", source_schema: "tau.test_receipt.v1", source_sha256: "sha256:a", receipt: { marker: "STALE" },
  }), { status: 200 })));
  expect(screen.getByLabelText("Receipt JSON")).not.toHaveTextContent("STALE");

  fireEvent.change(selector, { target: { value: "receipt-a" } });
  await waitFor(() => expect(resolvers.get("receipt-a")).toHaveLength(2));
  fireEvent.change(screen.getByLabelText("Committed journal sequence"), { target: { value: "1" } });
  await act(async () => resolvers.get("receipt-a")?.[1](new Response(JSON.stringify({
    schema: "tau.dag_viewer_receipt_projection.v1", receipt_id: "receipt-a", source_schema: "tau.test_receipt.v1", source_sha256: "sha256:a", receipt: { marker: "WRONG_PREFIX" },
  }), { status: 200 })));
  expect(screen.queryByText("WRONG_PREFIX")).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Committed journal sequence"), { target: { value: "live" } });
  await waitFor(() => expect(screen.getByText("following journal head")).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Committed receipt"), { target: { value: "receipt-a" } });
  await waitFor(() => expect(resolvers.get("receipt-a")).toHaveLength(3));
  window.history.pushState({}, "", "/?at_sequence=1");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await act(async () => resolvers.get("receipt-a")?.[2](new Response(JSON.stringify({
    schema: "tau.dag_viewer_receipt_projection.v1", receipt_id: "receipt-a", source_schema: "tau.test_receipt.v1", source_sha256: "sha256:a", receipt: { marker: "HISTORY_STALE" },
  }), { status: 200 })));
  expect(screen.queryByText("HISTORY_STALE")).not.toBeInTheDocument();
});

test("live polling reinitializes an exact successor and invalidates stale evidence", async () => {
  const receiptEntry = {
    receipt_id: "base-receipt",
    schema: "tau.test_receipt.v1",
    path_display: "base-receipt.json",
    sha256: "sha256:base-receipt",
    available: true,
  };
  const baseManifest = { ...manifest, receipt_index: [receiptEntry] };
  const successorRunId = "run-1:generation:1";
  const successorManifest = { ...manifest, run_id: successorRunId, receipt_index: [] };
  const successorSnapshot = {
    ...snapshot,
    run_id: successorRunId,
    journal_sequence: 15,
    snapshot_sha256: "sha256:successor",
    view: { ...snapshot.view, sequence: 15 },
    recent_events: [{ ...snapshot.recent_events[0], seq: 15 }],
  };
  let stateCalls = 0;
  let resolveReceipt: ((response: Response) => void) | null = null;
  const initialLocation = window.location.href;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/receipts/")) {
      return new Promise<Response>((resolve) => { resolveReceipt = resolve; });
    }
    if (url.includes("/api/v1/state")) {
      stateCalls += 1;
      if (stateCalls >= 4) return Promise.resolve(new Response(null, { status: 304, headers: { ETag: '"successor"' } }));
      const value = stateCalls === 1 ? snapshot : successorSnapshot;
      return Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { ETag: stateCalls === 1 ? '"base"' : '"successor"' } }));
    }
    const successor = stateCalls > 1;
    const payload = url.includes("manifest")
      ? successor ? successorManifest : baseManifest
      : url.includes("explanations")
        ? { ...explanation, run_id: successor ? successorRunId : snapshot.run_id, as_of_sequence: successor ? 15 : 8 }
        : url.includes("events")
          ? {
            schema: "tau.dag_live_event.v1",
            run_id: successor ? successorRunId : snapshot.run_id,
            after_sequence: 0,
            events: successor ? successorSnapshot.recent_events : snapshot.recent_events,
          }
          : snapshot;
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
  }));

  render(<App />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Receipt" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Receipt" }));
  fireEvent.change(screen.getByLabelText("Committed receipt"), { target: { value: "base-receipt" } });
  await waitFor(() => expect(resolveReceipt).not.toBeNull());
  await waitFor(() => expect(screen.getByText(successorRunId)).toBeInTheDocument(), { timeout: 2500 });
  expect(window.location.href).toBe(initialLocation);
  expect(screen.getByRole("button", { name: "Why" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("option", { name: "base-receipt.json" })).not.toBeInTheDocument();

  await act(async () => resolveReceipt?.(new Response(JSON.stringify({
    schema: "tau.dag_viewer_receipt_projection.v1",
    receipt_id: "base-receipt",
    source_schema: "tau.test_receipt.v1",
    source_sha256: "sha256:base-receipt",
    receipt: { marker: "STALE_GENERATION" },
  }), { status: 200 })));
  expect(screen.queryByText("STALE_GENERATION")).not.toBeInTheDocument();
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
