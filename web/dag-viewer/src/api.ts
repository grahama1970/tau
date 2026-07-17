import type { CausalExplanation, DagComparison, DagEventPage, DagManifest, DagQueryResult, DagSnapshot, ReceiptProjection } from "./types";

export type SnapshotTransition = "SAME_RUN" | "NEWER_GENERATION" | "REJECT";

type RunIdentity = { baseRunId: string; generation: number };

function parseRunIdentity(runId: string): RunIdentity | null {
  const marker = ":generation:";
  const markerIndex = runId.indexOf(marker);
  if (!runId || markerIndex < 0) return runId ? { baseRunId: runId, generation: 0 } : null;
  if (markerIndex === 0 || markerIndex !== runId.lastIndexOf(marker)) return null;
  const suffix = runId.slice(markerIndex + marker.length);
  if (!/^[1-9][0-9]*$/.test(suffix)) return null;
  const generation = Number(suffix);
  if (!Number.isSafeInteger(generation)) return null;
  return { baseRunId: runId.slice(0, markerIndex), generation };
}

function assertMatchingView(manifest: DagManifest, snapshot: DagSnapshot): void {
  if (manifest.run_id !== snapshot.run_id || manifest.plan_sha256 !== snapshot.plan_sha256) {
    throw new Error("viewer_generation_contract_mismatch");
  }
}

export async function loadMatchingManifest(snapshot: DagSnapshot, sequence: number | null = null): Promise<DagManifest> {
  const manifest = await loadManifest(sequence);
  assertMatchingView(manifest, snapshot);
  return manifest;
}

async function getJson<T>(path: string, init?: RequestInit): Promise<{ value: T | null; etag: string | null }> {
  const response = await fetch(path, init);
  if (response.status === 304) return { value: null, etag: response.headers.get("ETag") };
  if (!response.ok) throw new Error(`viewer_request_failed:${response.status}`);
  return { value: (await response.json()) as T, etag: response.headers.get("ETag") };
}

function sequenceQuery(sequence: number | null): string {
  return sequence === null ? "" : `?at_sequence=${sequence}`;
}

export async function loadManifest(sequence: number | null = null): Promise<DagManifest> {
  const result = await getJson<DagManifest>(`/api/v1/manifest${sequenceQuery(sequence)}`);
  if (!result.value) throw new Error("viewer_manifest_missing");
  return result.value;
}

export async function loadInitialState(sequence: number | null = null): Promise<{ manifest: DagManifest; snapshot: DagSnapshot; etag: string | null }> {
  const stateResult = await getJson<DagSnapshot>(`/api/v1/state${sequenceQuery(sequence)}`);
  if (!stateResult.value) throw new Error("viewer_initial_state_missing");
  const manifest = await loadMatchingManifest(stateResult.value, sequence);
  return { manifest, snapshot: stateResult.value, etag: stateResult.etag };
}

export async function pollState(etag: string | null, sequence: number | null = null): Promise<{ snapshot: DagSnapshot | null; etag: string | null }> {
  const headers = etag ? { "If-None-Match": etag } : undefined;
  const result = await getJson<DagSnapshot>(`/api/v1/state${sequenceQuery(sequence)}`, { headers });
  return { snapshot: result.value, etag: result.etag ?? etag };
}

export function classifySnapshotTransition(current: DagSnapshot, candidate: DagSnapshot, expectedSequence: number | null): SnapshotTransition {
  const expectedMode = expectedSequence === null ? "LIVE" : "HISTORICAL";
  if (candidate.view.mode !== expectedMode || candidate.plan_sha256 !== current.plan_sha256) return "REJECT";
  if (expectedSequence !== null) {
    return candidate.run_id === current.run_id && candidate.view.sequence === expectedSequence
      ? "SAME_RUN"
      : "REJECT";
  }
  if (candidate.run_id === current.run_id) {
    return candidate.journal_sequence >= current.journal_sequence ? "SAME_RUN" : "REJECT";
  }
  const currentIdentity = parseRunIdentity(current.run_id);
  const candidateIdentity = parseRunIdentity(candidate.run_id);
  if (
    !currentIdentity
    || !candidateIdentity
    || candidateIdentity.baseRunId !== currentIdentity.baseRunId
    || candidateIdentity.generation !== currentIdentity.generation + 1
  ) return "REJECT";
  return "NEWER_GENERATION";
}

export async function loadReceipt(receiptId: string, sequence: number | null = null): Promise<ReceiptProjection> {
  const result = await getJson<ReceiptProjection>(`/api/v1/receipts/${encodeURIComponent(receiptId)}${sequenceQuery(sequence)}`);
  if (!result.value) throw new Error("viewer_receipt_missing");
  return result.value;
}

export async function loadExplanation(
  kind: string,
  subjectId: string,
  sequence: number | null = null,
): Promise<CausalExplanation> {
  const subject = `${encodeURIComponent(kind.toLowerCase())}/${encodeURIComponent(subjectId)}`;
  const result = await getJson<CausalExplanation>(
    `/api/v1/explanations/${subject}${sequenceQuery(sequence)}`,
  );
  if (!result.value) throw new Error("viewer_explanation_missing");
  return result.value;
}

export async function loadJournalSequences(expectedRunId: string): Promise<number[]> {
  const sequences: number[] = [];
  let after = 0;
  while (true) {
    const page = await getJson<DagEventPage>(`/api/v1/events?after_sequence=${after}&limit=500`);
    if (!page.value) throw new Error("viewer_event_page_missing");
    if (page.value.run_id !== expectedRunId) throw new Error("viewer_event_run_mismatch");
    if (page.value.events.length === 0) break;
    for (const event of page.value.events) sequences.push(event.seq);
    after = page.value.events[page.value.events.length - 1].seq;
    if (page.value.events.length < 500) break;
  }
  return sequences;
}

export async function loadQuery(parameters: URLSearchParams): Promise<DagQueryResult> {
  const result = await getJson<DagQueryResult>(`/api/v1/query?${parameters.toString()}`);
  if (!result.value) throw new Error("viewer_query_missing");
  return result.value;
}

export async function loadComparison(parameters: URLSearchParams): Promise<DagComparison> {
  const result = await getJson<DagComparison>(`/api/v1/compare?${parameters.toString()}`);
  if (!result.value) throw new Error("viewer_comparison_missing");
  return result.value;
}
