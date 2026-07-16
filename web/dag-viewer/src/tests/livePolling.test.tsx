import { expect, test, vi } from "vitest";
import { pollState, shouldReplaceSnapshot } from "../api";
import { snapshot } from "./fixtures";

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

test("rejects stale or cross-run replacement snapshots", () => {
  expect(shouldReplaceSnapshot(snapshot, { ...snapshot, journal_sequence: 9 }, null)).toBe(true);
  expect(shouldReplaceSnapshot(snapshot, { ...snapshot, journal_sequence: 7 }, null)).toBe(false);
  expect(shouldReplaceSnapshot(snapshot, { ...snapshot, run_id: "other-run" }, null)).toBe(false);
  const historical = { ...snapshot, view: { ...snapshot.view, mode: "HISTORICAL" as const, sequence: 4 }, journal_sequence: 4 };
  expect(shouldReplaceSnapshot(snapshot, historical, 4)).toBe(true);
  expect(shouldReplaceSnapshot(snapshot, historical, null)).toBe(false);
});
