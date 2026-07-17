import { expect, test, vi } from "vitest";
import { classifySnapshotTransition, loadInitialState, pollState } from "../api";
import { manifest, snapshot } from "./fixtures";

test("sends ETag and treats 304 as no replacement", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    expect((init?.headers as Record<string, string>)["If-None-Match"]).toBe('"snapshot"');
    return new Response(null, { status: 304, headers: { ETag: '"snapshot"' } });
  });
  vi.stubGlobal("fetch", fetchMock);
  const result = await pollState('"snapshot"');
  expect(result.snapshot).toBeNull();
  expect(result.etag).toBe('"snapshot"');
});

test("classifies only an exact same-lineage successor as a generation handoff", () => {
  expect(classifySnapshotTransition(snapshot, { ...snapshot, journal_sequence: 9 }, null)).toBe("SAME_RUN");
  expect(classifySnapshotTransition(snapshot, { ...snapshot, journal_sequence: 7 }, null)).toBe("REJECT");
  expect(classifySnapshotTransition(snapshot, { ...snapshot, run_id: "run-1:generation:1", journal_sequence: 1 }, null)).toBe("NEWER_GENERATION");
  expect(classifySnapshotTransition({ ...snapshot, run_id: "run-1:generation:1" }, { ...snapshot, run_id: "run-1:generation:2", journal_sequence: 1 }, null)).toBe("NEWER_GENERATION");
  expect(classifySnapshotTransition(snapshot, { ...snapshot, run_id: "run-1:generation:2" }, null)).toBe("REJECT");
  expect(classifySnapshotTransition(snapshot, { ...snapshot, run_id: "other-run:generation:1" }, null)).toBe("REJECT");
  expect(classifySnapshotTransition(snapshot, { ...snapshot, run_id: "run-1:generation:01" }, null)).toBe("REJECT");
  expect(classifySnapshotTransition(snapshot, { ...snapshot, run_id: "run-1:generation:1:generation:2" }, null)).toBe("REJECT");
  expect(classifySnapshotTransition(snapshot, { ...snapshot, run_id: "run-1:generation:1", plan_sha256: "sha256:other" }, null)).toBe("REJECT");
  const historical = { ...snapshot, view: { ...snapshot.view, mode: "HISTORICAL" as const, sequence: 4 }, journal_sequence: 4 };
  expect(classifySnapshotTransition(snapshot, historical, 4)).toBe("SAME_RUN");
  expect(classifySnapshotTransition(snapshot, { ...historical, run_id: "run-1:generation:1" }, 4)).toBe("REJECT");
  expect(classifySnapshotTransition(snapshot, historical, null)).toBe("REJECT");
});

test("initial state rejects a manifest from another physical generation or plan", async () => {
  for (const mismatchedManifest of [
    { ...manifest, run_id: "run-1:generation:1" },
    { ...manifest, plan_sha256: "sha256:other" },
  ]) {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(snapshot), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(mismatchedManifest), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(loadInitialState()).rejects.toThrow("viewer_generation_contract_mismatch");
  }
});
