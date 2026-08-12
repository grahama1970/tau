import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "../App";
import { explanation, manifest, snapshot } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
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
