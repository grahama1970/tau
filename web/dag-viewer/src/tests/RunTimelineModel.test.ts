import { expect, test } from "vitest";
import { buildTimelineModel, buildTimelineScale, type TimelineClip } from "../components/runTimelineModel";
import { buildTimelineRoleLanes } from "../components/runTimelineSwimlanes";
import { manifest, snapshot } from "./fixtures";

function clip(overrides: Partial<TimelineClip>): TimelineClip {
  return {
    id: "clip",
    qid: "dag:timeline:test:clip",
    eventId: "event",
    kind: "execution",
    action: "TAU_TIMELINE_SELECT_EXECUTION",
    title: "Select test clip",
    label: "clip",
    eyebrow: "state",
    meta: "meta",
    state: "pending",
    subject: { kind: "NODE", id: "clip", eventId: "event", timelineKind: "execution" },
    sequence: 1,
    attempt: 1,
    timestamp: null,
    startedAt: null,
    finishedAt: null,
    durationSeconds: null,
    receiptRefs: [],
    blockerCodes: [],
    offsetPercent: null,
    positionLabel: "seq #1",
    ...overrides,
  };
}

test("timeline scale falls back to sequence mode when any rendered clip lacks a timestamp", () => {
  const scale = buildTimelineScale([
    clip({ eventId: "a", sequence: 1, timestamp: "2026-01-01T00:00:00Z" }),
    clip({ eventId: "b", sequence: 6, timestamp: null }),
    clip({ eventId: "c", sequence: 11, timestamp: "2026-01-01T00:00:10Z" }),
  ]);

  expect(scale.mode).toBe("sequence");
  expect(scale.domainLabel).toBe("seq #1 - seq #11");
  expect(scale.ticks.map((tick) => tick.offsetPercent)).toEqual([0, 50, 100]);
});

test("timeline scale uses timestamp offsets only when every rendered clip has a valid timestamp", () => {
  const scale = buildTimelineScale([
    clip({ eventId: "a", timestamp: "2026-01-01T00:00:00Z" }),
    clip({ eventId: "b", timestamp: "2026-01-01T00:00:05Z" }),
    clip({ eventId: "c", timestamp: "2026-01-01T00:00:10Z" }),
  ]);

  expect(scale.mode).toBe("time");
  expect(scale.ticks.map((tick) => tick.offsetPercent)).toEqual([0, 50, 100]);
});

test("timeline model spreads equal-sequence clips for inspection without changing sequence mode", () => {
  const model = buildTimelineModel(manifest, {
    ...snapshot,
    nodes: snapshot.nodes.map((node) => ({ ...node, updated_sequence: 40 })),
    terminals: snapshot.terminals.map((terminal) => ({ ...terminal, state: "success" })),
    journal_sequence: 40,
  });

  expect(model.scale.mode).toBe("sequence");
  expect(model.scale.domainLabel).toBe("seq #40");
  expect(model.scale.ticks).toHaveLength(1);
  expect(model.scale.ticks[0].offsetPercent).toBe(50);
  const positioned = model.clips.filter((clip) => clip.offsetPercent !== null);
  expect(new Set(positioned.map((clip) => clip.offsetPercent))).toContain(0);
  expect(new Set(positioned.map((clip) => clip.offsetPercent))).toContain(100);
});

test("role swimlanes render proportional durations only from authoritative node timing", () => {
  const timed = {
    ...snapshot,
    nodes: snapshot.nodes.map((node) => node.node_id === "creator"
      ? {
        ...node,
        result: {
          ...node.result,
          started_at: "2026-01-01T00:00:00Z",
          finished_at: "2026-01-01T00:00:04Z",
          duration_seconds: 4,
        },
      }
      : node),
  };
  const model = buildTimelineModel(manifest, timed);
  const lanes = buildTimelineRoleLanes(manifest, model.tracks.execution);

  const producerClip = lanes.find((lane) => lane.label === "producer")?.clips[0];
  const consumerClip = lanes.find((lane) => lane.label === "consumer")?.clips[0];
  expect(producerClip).toMatchObject({
    eventId: "execution:creator:attempt:1",
    durationMode: "duration",
    durationOffsetPercent: 0,
    durationWidthPercent: 100,
    edgeAnchor: "end",
    durationLabel: "4s",
  });
  expect(consumerClip).toMatchObject({ durationMode: "point" });
});
