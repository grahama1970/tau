import type { AttentionItem, DagManifest, DagSnapshot, JsonValue, LiveNode } from "../types";

export type TimelineKind = "execution" | "proof" | "decision" | "control";
export type TimelineState = "active" | "accepted" | "blocked" | "pending" | "warning" | "neutral";
export type TimelineSubject = { kind: string; id: string; eventId?: string; timelineKind?: TimelineKind };

export type TimelineClip = {
  id: string;
  qid: string;
  eventId: string;
  kind: TimelineKind;
  action: string;
  title: string;
  label: string;
  eyebrow: string;
  meta: string;
  state: TimelineState;
  subject: TimelineSubject;
  sequence: number | null;
  attempt: number | null;
  timestamp: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  durationSeconds: number | null;
  receiptRefs: string[];
  blockerCodes: string[];
  offsetPercent: number | null;
  positionLabel: string;
};

export type TimelineTracks = Record<TimelineKind, TimelineClip[]>;
export type TimelineScale = {
  mode: "time" | "sequence";
  label: string;
  domainLabel: string;
  ticks: Array<{ id: string; label: string; offsetPercent: number }>;
};

export type TimelineModel = {
  tracks: TimelineTracks;
  scale: TimelineScale;
  clips: TimelineClip[];
};

const countFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

export function qidPart(value: string): string {
  return value.replace(/[^a-zA-Z0-9:_-]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
}

function stableSuffix(value: string): string {
  let hash = 0;
  for (const character of value) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return hash.toString(36).slice(0, 6) || "0";
}

function qidTokenFactory(values: string[]): (value: string) => string {
  const collisions = new Set<string>();
  const seen = new Map<string, string>();
  for (const value of values) {
    const token = qidPart(value);
    const prior = seen.get(token);
    if (prior !== undefined && prior !== value) collisions.add(token);
    seen.set(token, value);
  }
  return (value) => {
    const token = qidPart(value);
    return collisions.has(token) ? `${token}:${stableSuffix(value)}` : token;
  };
}

function stateForExecutionNode(node: LiveNode | null): TimelineState {
  if (!node) return "pending";
  const states = [node.scheduler.state, node.runtime.state, ...node.result.blocker_codes].map((value) => value.toLowerCase());
  if (states.some((state) => ["blocked", "failed", "timed_out", "rejected"].includes(state))) return "blocked";
  if (states.includes("accepted") || states.includes("settled")) return "accepted";
  if (states.some((state) => ["running", "alive", "validating", "committing", "awaiting_receipt"].includes(state))) return "active";
  if (states.some((state) => ["retry_pending", "scheduled", "warning"].includes(state))) return "warning";
  return "pending";
}

function stateForProofNode(node: LiveNode | null): TimelineState {
  if (!node) return "pending";
  const admissionState = node.admission.state.toLowerCase();
  if (node.admission.accepted || ["accepted", "settled"].includes(admissionState)) return "accepted";
  if (["blocked", "failed", "rejected", "quarantined"].includes(admissionState)) return "blocked";
  if (node.admission.receipt_refs.length > 0 || ["awaiting_receipt", "validating", "reviewing"].includes(admissionState)) return "warning";
  return "pending";
}

function stateForText(value: string | null | undefined): TimelineState {
  const normalized = (value ?? "").toLowerCase();
  if (["accepted", "settled", "resolved", "complete", "completed", "pass", "passed"].some((token) => normalized.includes(token))) return "accepted";
  if (["blocked", "failed", "reject", "error", "quarantine"].some((token) => normalized.includes(token))) return "blocked";
  if (["running", "open", "awaiting", "active"].some((token) => normalized.includes(token))) return "active";
  if (["warning", "retry", "pending"].some((token) => normalized.includes(token))) return "warning";
  return "neutral";
}

function shortJson(value: JsonValue | null | undefined): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return `${countFormatter.format(value.length)} items`;
  return Object.keys(value).slice(0, 3).join(", ") || "object";
}

function executionMeta(node: LiveNode | null, role: string, maxAttempts: number): string {
  if (!node) return `${role} - not started`;
  const attempt = node.scheduler.attempt > 0 ? `${node.scheduler.attempt}/${maxAttempts}` : `0/${maxAttempts}`;
  return `${role} - attempt ${attempt} - ${node.runtime.liveness.toLowerCase()}`;
}

function proofMeta(node: LiveNode | null): string {
  if (!node) return "no live projection yet";
  const receiptCount = node.admission.receipt_refs.length;
  return `${receiptCount} receipt${receiptCount === 1 ? "" : "s"} - ${node.admission.state}`;
}

function stateForAttentionSeverity(severity: AttentionItem["severity"]): TimelineState {
  if (severity === "BLOCKER") return "blocked";
  if (severity === "ACTION_REQUIRED") return "active";
  return "warning";
}

function openHumanDecision(item: AttentionItem): boolean {
  return item.state === "OPEN" && ["ACTION_REQUIRED", "BLOCKER"].includes(item.severity);
}

function parseTimestamp(value: string | null): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatTick(mode: TimelineScale["mode"], value: number): string {
  if (mode === "sequence") return `seq #${Math.round(value)}`;
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function offsetFor(value: number, min: number, max: number): number {
  if (max <= min) return 0;
  return Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
}

export function buildTimelineScale(clips: TimelineClip[]): TimelineScale {
  const timestamps = clips.map((clip) => parseTimestamp(clip.timestamp));
  const useTime = clips.length > 0 && timestamps.every((value) => value !== null);
  const values = useTime
    ? timestamps.filter((value): value is number => value !== null)
    : clips.map((clip) => clip.sequence).filter((value): value is number => value !== null);
  const mode: TimelineScale["mode"] = useTime ? "time" : "sequence";
  if (values.length === 0) {
    return { mode, label: mode === "time" ? "Time scale" : "Sequence scale", domainLabel: "no positioned events", ticks: [] };
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const midpoint = min + (max - min) / 2;
  const rawTicks = min === max ? [min] : [min, midpoint, max];
  return {
    mode,
    label: mode === "time" ? "Time scale" : "Sequence scale",
    domainLabel: min === max ? formatTick(mode, min) : `${formatTick(mode, min)} - ${formatTick(mode, max)}`,
    ticks: rawTicks.map((value, index) => ({
      id: `${mode}:${index}:${value}`,
      label: formatTick(mode, value),
      offsetPercent: min === max ? 50 : offsetFor(value, min, max),
    })),
  };
}

function applyScale(clips: TimelineClip[], scale: TimelineScale): TimelineClip[] {
  if (scale.ticks.length === 0) return clips.map((clip) => ({ ...clip, offsetPercent: null, positionLabel: "sequence unknown" }));
  const domainValues = scale.mode === "time"
    ? clips.map((clip) => parseTimestamp(clip.timestamp)).filter((value): value is number => value !== null)
    : clips.map((clip) => clip.sequence).filter((value): value is number => value !== null);
  const domainMin = Math.min(...domainValues);
  const domainMax = Math.max(...domainValues);
  if (domainMin === domainMax) {
    const positioned = clips
      .filter((clip) => (scale.mode === "time" ? parseTimestamp(clip.timestamp) : clip.sequence) !== null)
      .sort((left, right) => (left.sequence ?? 0) - (right.sequence ?? 0) || (left.attempt ?? 0) - (right.attempt ?? 0) || left.eventId.localeCompare(right.eventId));
    const offsets = new Map(positioned.map((clip, index) => [clip.id, positioned.length === 1 ? 50 : (index / (positioned.length - 1)) * 100]));
    return clips.map((clip) => {
      const value = scale.mode === "time" ? parseTimestamp(clip.timestamp) : clip.sequence;
      if (value === null) return { ...clip, offsetPercent: null, positionLabel: "sequence unknown" };
      return { ...clip, offsetPercent: offsets.get(clip.id) ?? 50, positionLabel: formatTick(scale.mode, value) };
    });
  }
  return clips.map((clip) => {
    const value = scale.mode === "time" ? parseTimestamp(clip.timestamp) : clip.sequence;
    if (value === null) return { ...clip, offsetPercent: null, positionLabel: "sequence unknown" };
    return {
      ...clip,
      offsetPercent: offsetFor(value, domainMin, domainMax),
      positionLabel: formatTick(scale.mode, value),
    };
  });
}

function durationSeconds(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function withSubject(
  clip: Omit<TimelineClip, "offsetPercent" | "positionLabel" | "startedAt" | "finishedAt" | "durationSeconds">
    & Partial<Pick<TimelineClip, "startedAt" | "finishedAt" | "durationSeconds">>,
): TimelineClip {
  return {
    ...clip,
    subject: { ...clip.subject, eventId: clip.eventId, timelineKind: clip.kind },
    startedAt: clip.startedAt ?? null,
    finishedAt: clip.finishedAt ?? null,
    durationSeconds: clip.durationSeconds ?? null,
    offsetPercent: null,
    positionLabel: clip.sequence !== null ? `seq #${clip.sequence}` : "sequence unknown",
  };
}

export function buildTimelineModel(manifest: DagManifest, snapshot: DagSnapshot): TimelineModel {
  const liveNodes = new Map(snapshot.nodes.map((node) => [node.node_id, node]));
  const terminalStates = new Map(snapshot.terminals.map((terminal) => [terminal.terminal_id, terminal]));
  const nodeQid = qidTokenFactory(manifest.graph.nodes.map((node) => node.node_id));
  const terminalQid = qidTokenFactory(manifest.graph.terminals.map((terminal) => terminal.terminal_id));
  const execution = [
    ...manifest.graph.nodes.map((node) => {
      const live = liveNodes.get(node.node_id) ?? null;
      return withSubject({
        id: `execution:${node.node_id}`, qid: `dag:timeline:execution:${nodeQid(node.node_id)}`,
        eventId: `execution:${node.node_id}:attempt:${live?.scheduler.attempt ?? 0}`, kind: "execution",
        action: "TAU_TIMELINE_SELECT_EXECUTION", title: `Select execution node ${node.node_id}`,
        label: node.node_id, eyebrow: live?.scheduler.state ?? "planned",
        meta: executionMeta(live, node.role, node.retry_policy.max_attempts), state: stateForExecutionNode(live),
        subject: { kind: "NODE", id: node.node_id }, sequence: live?.updated_sequence ?? null,
        attempt: live?.scheduler.attempt ?? null, timestamp: live?.result.started_at ?? live?.result.finished_at ?? null,
        startedAt: live?.result.started_at ?? null, finishedAt: live?.result.finished_at ?? null,
        durationSeconds: durationSeconds(live?.result.duration_seconds),
        receiptRefs: [], blockerCodes: live?.result.blocker_codes ?? [],
      });
    }),
    ...manifest.graph.terminals.map((terminal) => {
      const live = terminalStates.get(terminal.terminal_id);
      return withSubject({
        id: `execution-terminal:${terminal.terminal_id}`, qid: `dag:timeline:execution:${terminalQid(terminal.terminal_id)}`,
        eventId: `execution-terminal:${terminal.terminal_id}`, kind: "execution",
        action: "TAU_TIMELINE_SELECT_EXECUTION", title: `Select terminal ${terminal.terminal_id}`,
        label: terminal.terminal_id, eyebrow: live?.state ?? "terminal", meta: `${terminal.kind} - ${terminal.origin}`,
        state: stateForText(live?.state), subject: { kind: "TERMINAL", id: terminal.terminal_id },
        sequence: null, attempt: null, timestamp: null, receiptRefs: [], blockerCodes: [],
      });
    }),
  ];
  const proof = manifest.graph.nodes.map((node) => {
    const live = liveNodes.get(node.node_id) ?? null;
    return withSubject({
      id: `proof:${node.node_id}`, qid: `dag:timeline:proof:${nodeQid(node.node_id)}`,
      eventId: `proof_admission:${node.node_id}:attempt:${live?.scheduler.attempt ?? 0}`, kind: "proof",
      action: "TAU_TIMELINE_SELECT_PROOF", title: `Inspect proof state for ${node.node_id}`,
      label: node.node_id, eyebrow: live?.admission.state ?? "not projected", meta: proofMeta(live),
      state: stateForProofNode(live), subject: { kind: "NODE", id: node.node_id },
      sequence: live?.updated_sequence ?? null, attempt: live?.scheduler.attempt ?? null, timestamp: null,
      receiptRefs: live?.admission.receipt_refs ?? [], blockerCodes: [],
    });
  });
  const decision = snapshot.attention_items.filter(openHumanDecision).map((item) => withSubject({
    id: `human-decision:${item.attention_id}`, qid: `dag:timeline:decision:${qidPart(item.attention_id)}`,
    eventId: `human_decision:${item.attention_id}`, kind: "decision", action: "TAU_TIMELINE_SELECT_DECISION",
    title: `Inspect required human decision ${item.attention_id}`, label: item.required_action_code,
    eyebrow: "HUMAN DECISION", meta: `${item.reason_code} - ${item.subject.kind.toLowerCase()} ${item.subject.id}`,
    state: stateForAttentionSeverity(item.severity), subject: { kind: "ATTENTION", id: item.attention_id },
    sequence: item.opened_sequence, attempt: null, timestamp: null, receiptRefs: [], blockerCodes: [item.reason_code],
  }));
  const control = [
    ...snapshot.attention_items.map((item) => withSubject({
      id: `attention:${item.attention_id}`, qid: `dag:timeline:control:attention:${qidPart(item.attention_id)}`,
      eventId: `control-attention:${item.attention_id}`, kind: "control", action: "TAU_TIMELINE_SELECT_CONTROL",
      title: `Inspect attention item ${item.attention_id}`, label: item.reason_code, eyebrow: item.severity,
      meta: `${item.state.toLowerCase()} - ${item.required_action_code}`, state: stateForAttentionSeverity(item.severity),
      subject: { kind: "ATTENTION", id: item.attention_id }, sequence: item.opened_sequence,
      attempt: null, timestamp: null, receiptRefs: [], blockerCodes: [item.reason_code],
    })),
    ...snapshot.routes.map((route) => withSubject({
      id: `route:${route.route_id}`, qid: `dag:timeline:control:route:${qidPart(route.route_id)}`,
      eventId: `control-route:${route.route_id}`, kind: "control", action: "TAU_TIMELINE_SELECT_CONTROL",
      title: `Inspect route ${route.route_id}`, label: route.route_id, eyebrow: route.state, meta: route.reason_code,
      state: stateForText(route.state), subject: { kind: "ROUTE", id: route.route_id }, sequence: route.decision_sequence,
      attempt: null, timestamp: null, receiptRefs: route.decision_receipt_id ? [route.decision_receipt_id] : [], blockerCodes: [],
    })),
    ...snapshot.joins.map((join) => withSubject({
      id: `join:${join.join_node_id}`, qid: `dag:timeline:control:join:${qidPart(join.join_node_id)}`,
      eventId: `control-join:${join.join_node_id}`, kind: "control", action: "TAU_TIMELINE_SELECT_CONTROL",
      title: `Inspect join ${join.join_node_id}`, label: join.join_node_id, eyebrow: join.state, meta: join.decision ?? join.reason_code,
      state: stateForText(join.state), subject: { kind: "JOIN", id: join.join_node_id }, sequence: join.decision_sequence,
      attempt: null, timestamp: null, receiptRefs: [], blockerCodes: [],
    })),
    ...snapshot.corrections.map((correction) => withSubject({
      id: `correction:${correction.incident_id}`, qid: `dag:timeline:control:correction:${qidPart(correction.incident_id)}`,
      eventId: `control-correction:${correction.incident_id}`, kind: "control", action: "TAU_TIMELINE_SELECT_CONTROL",
      title: `Inspect correction ${correction.incident_id}`, label: correction.incident_id, eyebrow: correction.state,
      meta: shortJson(correction.intent) || shortJson(correction.verification) || "correction", state: stateForText(correction.state),
      subject: { kind: "CORRECTION", id: correction.incident_id }, sequence: correction.journal_sequence,
      attempt: null, timestamp: null, receiptRefs: [], blockerCodes: [],
    })),
    ...snapshot.terminals.map((terminal) => withSubject({
      id: `terminal:${terminal.terminal_id}`, qid: `dag:timeline:control:terminal:${qidPart(terminal.terminal_id)}`,
      eventId: `control-terminal:${terminal.terminal_id}`, kind: "control", action: "TAU_TIMELINE_SELECT_CONTROL",
      title: `Inspect terminal ${terminal.terminal_id}`, label: terminal.terminal_id, eyebrow: terminal.state, meta: "external boundary",
      state: stateForText(terminal.state), subject: { kind: "TERMINAL", id: terminal.terminal_id },
      sequence: null, attempt: null, timestamp: null, receiptRefs: [], blockerCodes: [],
    })),
  ];
  if (snapshot.run_summary.final_result) {
    control.push(withSubject({
      id: "final-result", qid: "dag:timeline:control:final-result", eventId: `control-final-result:${snapshot.run_id}`,
      kind: "control", action: "TAU_TIMELINE_SELECT_CONTROL", title: "Inspect final result", label: "final result",
      eyebrow: snapshot.run_verdict ?? snapshot.run_status, meta: shortJson(snapshot.run_summary.final_result),
      state: stateForText(snapshot.run_verdict ?? snapshot.run_status), subject: { kind: "RUN", id: snapshot.run_id },
      sequence: snapshot.journal_sequence, attempt: null, timestamp: null, receiptRefs: [], blockerCodes: [],
    }));
  }
  const rawTracks = { execution, proof, decision, control } satisfies TimelineTracks;
  const rawClips = Object.values(rawTracks).flat();
  const scale = buildTimelineScale(rawClips);
  const scaledClips = applyScale(rawClips, scale);
  const byId = new Map(scaledClips.map((clip) => [clip.id, clip]));
  const tracks = Object.fromEntries(Object.entries(rawTracks).map(([kind, clips]) => [kind, clips.map((clip) => byId.get(clip.id) ?? clip)])) as TimelineTracks;
  return { tracks, scale, clips: scaledClips };
}
