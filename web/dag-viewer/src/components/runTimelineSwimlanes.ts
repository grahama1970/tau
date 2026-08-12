import type { DagManifest, JsonValue, PlanNode } from "../types";
import { qidPart, type TimelineClip, type TimelineState } from "./runTimelineModel";

export type TimelineRoleClip = {
  id: string;
  qid: string;
  eventId: string;
  label: string;
  state: TimelineState;
  offsetPercent: number | null;
  durationMode: "duration" | "point";
  durationOffsetPercent: number | null;
  durationWidthPercent: number | null;
  edgeAnchor: "start" | "middle" | "end";
  durationLabel: string;
};

export type TimelineRoleLane = {
  id: string;
  qid: string;
  label: string;
  sublabel: string;
  clips: TimelineRoleClip[];
};

const durationFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

function stringConfig(config: Record<string, JsonValue>, keys: string[]): string | null {
  for (const key of keys) {
    const value = config[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function adapterLabel(node: PlanNode): string {
  return stringConfig(node.adapter.config, ["model", "model_id", "provider", "handler"]) ?? node.adapter.kind;
}

function parseTime(value: string | null): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function durationEnd(startMs: number | null, clip: TimelineClip): number | null {
  const finishedMs = parseTime(clip.finishedAt);
  if (startMs !== null && finishedMs !== null && finishedMs >= startMs) return finishedMs;
  if (startMs !== null && clip.durationSeconds !== null) return startMs + clip.durationSeconds * 1000;
  return null;
}

function offset(value: number, min: number, max: number): number {
  if (max <= min) return 50;
  return Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
}

function edgeAnchor(clipOffset: number | null, durationOffset: number | null, durationWidth: number | null): TimelineRoleClip["edgeAnchor"] {
  const start = durationOffset ?? clipOffset;
  if (start === null) return "start";
  const end = durationOffset !== null && durationWidth !== null ? durationOffset + durationWidth : start;
  if (end >= 94) return "end";
  if (start <= 6) return "start";
  return "middle";
}

export function buildTimelineRoleLanes(manifest: DagManifest, executionClips: TimelineClip[]): TimelineRoleLane[] {
  const clipByNodeId = new Map(
    executionClips
      .filter((clip) => clip.subject.kind === "NODE")
      .map((clip) => [clip.subject.id, clip]),
  );
  const spans = manifest.graph.nodes
    .map((node) => {
      const clip = clipByNodeId.get(node.node_id);
      const start = clip ? parseTime(clip.startedAt) : null;
      const end = clip ? durationEnd(start, clip) : null;
      return start !== null && end !== null && end >= start ? { start, end } : null;
    })
    .filter((span): span is { start: number; end: number } => span !== null);
  const domainStart = spans.length > 0 ? Math.min(...spans.map((span) => span.start)) : null;
  const domainEnd = spans.length > 0 ? Math.max(...spans.map((span) => span.end)) : null;
  const lanes = new Map<string, TimelineRoleLane>();

  for (const node of manifest.graph.nodes) {
    const clip = clipByNodeId.get(node.node_id);
    if (!clip) continue;
    const laneId = `${node.role}:${adapterLabel(node)}`;
    const lane = lanes.get(laneId) ?? {
      id: laneId,
      qid: `dag:timeline:role-lane:${qidPart(laneId)}`,
      label: node.role,
      sublabel: adapterLabel(node),
      clips: [],
    };
    const start = parseTime(clip.startedAt);
    const end = durationEnd(start, clip);
    const canRenderDuration = start !== null && end !== null && domainStart !== null && domainEnd !== null && end >= start;
    const durationOffsetPercent = canRenderDuration ? offset(start, domainStart, domainEnd) : null;
    const durationEndPercent = canRenderDuration ? offset(end, domainStart, domainEnd) : null;
    const durationWidthPercent = durationOffsetPercent !== null && durationEndPercent !== null
      ? Math.max(1.5, durationEndPercent - durationOffsetPercent)
      : null;
    lane.clips.push({
      id: `role:${node.node_id}`,
      qid: `dag:timeline:role-clip:${qidPart(laneId)}:${qidPart(node.node_id)}`,
      eventId: clip.eventId,
      label: node.node_id,
      state: clip.state,
      offsetPercent: clip.offsetPercent,
      durationMode: canRenderDuration ? "duration" : "point",
      durationOffsetPercent,
      durationWidthPercent,
      edgeAnchor: edgeAnchor(clip.offsetPercent, durationOffsetPercent, durationWidthPercent),
      durationLabel: canRenderDuration
        ? `${durationFormatter.format(Math.max(0, (end - start) / 1000))}s`
        : clip.positionLabel,
    });
    lanes.set(laneId, lane);
  }

  return [...lanes.values()].sort((left, right) => left.label.localeCompare(right.label) || left.sublabel.localeCompare(right.sublabel));
}
