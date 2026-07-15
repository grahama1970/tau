import type { DagManifest, DagSnapshot, ReceiptProjection } from "./types";

async function getJson<T>(path: string, init?: RequestInit): Promise<{ value: T | null; etag: string | null }> {
  const response = await fetch(path, init);
  if (response.status === 304) return { value: null, etag: response.headers.get("ETag") };
  if (!response.ok) throw new Error(`viewer_request_failed:${response.status}`);
  return { value: (await response.json()) as T, etag: response.headers.get("ETag") };
}

export async function loadManifest(): Promise<DagManifest> {
  const result = await getJson<DagManifest>("/api/v1/manifest");
  if (!result.value) throw new Error("viewer_manifest_missing");
  return result.value;
}

export async function loadInitialState(): Promise<{ manifest: DagManifest; snapshot: DagSnapshot; etag: string | null }> {
  const stateResult = await getJson<DagSnapshot>("/api/v1/state");
  if (!stateResult.value) throw new Error("viewer_initial_state_missing");
  const manifest = await loadManifest();
  return { manifest, snapshot: stateResult.value, etag: stateResult.etag };
}

export async function pollState(etag: string | null): Promise<{ snapshot: DagSnapshot | null; etag: string | null }> {
  const headers = etag ? { "If-None-Match": etag } : undefined;
  const result = await getJson<DagSnapshot>("/api/v1/state", { headers });
  return { snapshot: result.value, etag: result.etag ?? etag };
}

export function shouldReplaceSnapshot(current: DagSnapshot, candidate: DagSnapshot): boolean {
  return candidate.run_id === current.run_id
    && candidate.journal_sequence >= current.journal_sequence;
}

export async function loadReceipt(receiptId: string): Promise<ReceiptProjection> {
  const result = await getJson<ReceiptProjection>(`/api/v1/receipts/${encodeURIComponent(receiptId)}`);
  if (!result.value) throw new Error("viewer_receipt_missing");
  return result.value;
}
