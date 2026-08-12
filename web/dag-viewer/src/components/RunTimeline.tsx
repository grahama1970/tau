import { AlertTriangle, CheckCircle2, CircleDot, GitBranch, Minus, Plus, Route, ShieldCheck } from "lucide-react";
import { useCallback, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent as ReactMouseEvent } from "react";
import type { DagManifest, DagSnapshot } from "../types";
import { useRegisterAction } from "../useRegisterAction";
import { buildTimelineModel, type TimelineClip, type TimelineKind, type TimelineSubject } from "./runTimelineModel";
import { buildTimelineRoleLanes, type TimelineRoleLane } from "./runTimelineSwimlanes";

const TIMELINE_LABEL_WIDTH = 142;
const DEFAULT_STEP_WIDTH = 36;
const MIN_STEP_WIDTH = 16;
const MAX_STEP_WIDTH = 120;
const FIT_STEP_WIDTH = 24;
const DETAIL_STEP_WIDTH = 80;

const trackDefinitions: Array<{ kind: TimelineKind; label: string; description: string; icon: typeof GitBranch }> = [
  { kind: "execution", label: "Execution", description: "nodes, attempts, terminals", icon: GitBranch },
  { kind: "proof", label: "Proof", description: "admission, receipts, blockers", icon: ShieldCheck },
  { kind: "decision", label: "Human Decisions", description: "approvals, blockers, choices", icon: AlertTriangle },
  { kind: "control", label: "Control & Effects", description: "attention, routes, joins, results", icon: Route },
];

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function sequenceLeft(sequence: number | null, stepWidth: number): number | null {
  return sequence === null ? null : Math.max(0, sequence - 1) * stepWidth;
}

function clipButton(clip: TimelineClip, selected: boolean, stepWidth: number, onSelect: (subject: TimelineSubject) => void) {
  const Icon = clip.state === "accepted" ? CheckCircle2 : clip.state === "blocked" ? AlertTriangle : clip.state === "active" ? CircleDot : ShieldCheck;
  const left = sequenceLeft(clip.sequence, stepWidth);
  const style = left === null
    ? undefined
    : {
      "--timeline-left": `${left}px`,
      "--timeline-clip-width": `${clamp(stepWidth * 4, 148, 260)}px`,
    } as CSSProperties;
  return <button
    key={clip.id}
    type="button"
    className={`run-timeline__clip run-timeline__clip--${clip.state}${left === null ? "" : " run-timeline__clip--positioned"}${selected ? " is-selected" : ""}`}
    data-qid={clip.qid}
    data-qs-action={clip.action}
    data-event-id={clip.eventId}
    data-scale-offset={clip.offsetPercent === null ? "unpositioned" : clip.offsetPercent.toFixed(2)}
    data-step-left={left === null ? "unpositioned" : String(left)}
    data-step-width={stepWidth}
    data-timeline-kind={clip.kind}
    data-timeline-state={clip.state}
    title={clip.title}
    aria-label={clip.title}
    aria-pressed={selected}
    onClick={() => onSelect(clip.subject)}
  >
    <span className="run-timeline__clip-status"><Icon aria-hidden="true" size={14} />{clip.eyebrow}</span>
    <strong>{clip.label}</strong>
    <small>{clip.meta}</small>
    <span className="run-timeline__clip-scale" aria-hidden="true" style={style}><i /></span>
    <code>{clip.positionLabel}</code>
  </button>;
}

function SelectedTimelineEvent({ clip }: { clip: TimelineClip | null }) {
  if (!clip) {
    return <aside className="run-timeline__selected" data-qid="dag:timeline:selected-event" data-event-id="none">
      <strong>No timeline event selected</strong><span>Select a timeline clip to inspect its exact event identity.</span>
    </aside>;
  }
  return <aside
    className="run-timeline__selected"
    data-qid="dag:timeline:selected-event"
    data-event-id={clip.eventId}
    data-timeline-kind={clip.kind}
    data-timeline-state={clip.state}
  >
    <strong>{clip.eventId}</strong>
    <span>{clip.kind} - {clip.state} - {clip.subject.kind} {clip.subject.id}</span>
    <code>{clip.sequence !== null ? `sequence ${clip.sequence}` : "sequence unknown"} · {clip.receiptRefs.length} receipts · {clip.blockerCodes.length} blockers</code>
  </aside>;
}

function RoleSwimlanes({ lanes, stepWidth, trackWidth }: { lanes: TimelineRoleLane[]; stepWidth: number; trackWidth: number }) {
  return <section className="run-timeline__role-swimlanes" aria-label="Role and model swimlanes" data-qid="dag:timeline:role-swimlanes">
    <header>
      <div><strong>Role Swimlanes</strong><span>role, adapter, node duration</span></div>
    </header>
    <div className="run-timeline__role-stack">
      {lanes.length > 0
        ? lanes.map((lane) => <article className="run-timeline__role-lane" key={lane.id} data-qid={lane.qid}>
          <header><strong>{lane.label}</strong><span>{lane.sublabel}</span></header>
          <div>
            {lane.clips.map((clip) => {
              const left = sequenceLeft(clip.sequence, stepWidth);
              const durationLeft = clip.durationOffsetPercent === null ? left : (clip.durationOffsetPercent / 100) * trackWidth;
              const durationWidth = clip.durationWidthPercent === null ? null : (clip.durationWidthPercent / 100) * trackWidth;
              const style = {
                "--timeline-left": `${left ?? 0}px`,
                "--timeline-duration-left": `${durationLeft ?? 0}px`,
                "--timeline-duration-width": `${durationWidth ?? Math.max(36, stepWidth * 2)}px`,
              } as CSSProperties;
              return <span
                className={`run-timeline__role-clip run-timeline__role-clip--${clip.state}`}
                key={clip.id}
                data-qid={clip.qid}
                data-event-id={clip.eventId}
                data-duration-mode={clip.durationMode}
                data-edge-anchor={clip.edgeAnchor}
                data-timeline-state={clip.state}
                data-step-left={left === null ? "unpositioned" : String(left)}
                data-step-width={stepWidth}
                title={`${clip.label} - ${clip.durationLabel}`}
                style={style}
              >
                <i aria-hidden="true" />
                <b>{clip.label}</b>
                <code>{clip.durationLabel}</code>
              </span>;
            })}
          </div>
        </article>)
        : <span className="run-timeline__empty">No role/model lane data in this manifest.</span>}
    </div>
  </section>;
}

export function RunTimeline({
  manifest,
  snapshot,
  selectedId,
  selectedTimelineEventId,
  selectedSequence,
  onSelect,
  onSelectSequence,
}: {
  manifest: DagManifest;
  snapshot: DagSnapshot;
  selectedId: string | null;
  selectedTimelineEventId: string | null;
  selectedSequence: number | null;
  onSelect: (subject: TimelineSubject) => void;
  onSelectSequence: (sequence: number | null) => void;
}) {
  useRegisterAction("dag:timeline:action:select-execution", {
    action: "TAU_TIMELINE_SELECT_EXECUTION",
    label: "Select Execution Clip",
    description: "Select a Tau execution node or terminal from the timeline.",
  });
  useRegisterAction("dag:timeline:action:select-proof", {
    action: "TAU_TIMELINE_SELECT_PROOF",
    label: "Select Proof Clip",
    description: "Select admission, receipt, or blocker evidence from the timeline.",
  });
  useRegisterAction("dag:timeline:action:select-control", {
    action: "TAU_TIMELINE_SELECT_CONTROL",
    label: "Select Control Clip",
    description: "Select route, join, attention, correction, terminal, or result state from the timeline.",
  });
  useRegisterAction("dag:timeline:action:select-decision", {
    action: "TAU_TIMELINE_SELECT_DECISION",
    label: "Select Human Decision Clip",
    description: "Select an open human decision required by the Tau run.",
  });
  useRegisterAction("dag:timeline:zoom-out", {
    action: "TAU_TIMELINE_ZOOM_OUT",
    label: "Zoom Out Timeline Steps",
    description: "Decrease the pixel width of each Tau journal sequence step.",
  });
  useRegisterAction("dag:timeline:zoom-in", {
    action: "TAU_TIMELINE_ZOOM_IN",
    label: "Zoom In Timeline Steps",
    description: "Increase the pixel width of each Tau journal sequence step.",
  });
  useRegisterAction("dag:timeline:zoom-slider", {
    action: "TAU_TIMELINE_STEP_ZOOM",
    label: "Timeline Step Zoom",
    description: "Set the pixel width used for Tau journal sequence steps.",
  });
  useRegisterAction("dag:timeline:fit-view", {
    action: "TAU_TIMELINE_FIT_VIEW",
    label: "Fit Timeline View",
    description: "Use a compact sequence-step width for overview navigation.",
  });
  useRegisterAction("dag:timeline:detail-view", {
    action: "TAU_TIMELINE_DETAIL_VIEW",
    label: "Detail Timeline View",
    description: "Use a wider sequence-step width for detailed inspection.",
  });
  useRegisterAction("dag:timeline:playhead-scrub", {
    action: "TAU_TIMELINE_PLAYHEAD_SCRUB",
    label: "Scrub Timeline Playhead",
    description: "Move the Tau timeline playhead to a journal sequence step.",
  });

  const model = useMemo(() => buildTimelineModel(manifest, snapshot), [manifest, snapshot]);
  const roleLanes = useMemo(() => buildTimelineRoleLanes(manifest, model.tracks.execution), [manifest, model.tracks.execution]);
  const [stepWidth, setStepWidth] = useState(DEFAULT_STEP_WIDTH);
  const [isScrubbing, setIsScrubbing] = useState(false);
  const rulerRef = useRef<HTMLDivElement | null>(null);
  const selectedClip = model.clips.find((clip) => clip.eventId === selectedTimelineEventId)
    ?? model.clips.find((clip) => clip.subject.id === selectedId)
    ?? null;
  const activeCount = model.tracks.execution.filter((clip) => clip.state === "active").length;
  const acceptedCount = model.tracks.proof.filter((clip) => clip.state === "accepted").length;
  const decisionCount = model.tracks.decision.length;
  const controlCount = model.tracks.control.length;
  const maxSequence = Math.max(snapshot.journal_sequence, ...model.clips.map((clip) => clip.sequence ?? 0));
  const sequenceCount = Math.max(1, maxSequence);
  const activeSequence = clamp(selectedSequence ?? maxSequence, 1, sequenceCount);
  const sequenceScale = useMemo(() => Array.from({ length: sequenceCount }, (_, index) => index + 1), [sequenceCount]);
  const timelineTrackWidth = sequenceCount * stepWidth;
  const timelineCanvasWidth = timelineTrackWidth + TIMELINE_LABEL_WIDTH;
  const playheadLeft = TIMELINE_LABEL_WIDTH + (activeSequence - 1) * stepWidth + stepWidth / 2;
  const timelineStyle = {
    "--timeline-track-width": `${timelineTrackWidth}px`,
    "--timeline-canvas-width": `${timelineCanvasWidth}px`,
    "--timeline-step-width": `${stepWidth}px`,
    "--timeline-playhead-left": `${playheadLeft}px`,
  } as CSSProperties;
  const setClampedStepWidth = useCallback((value: number) => {
    setStepWidth(clamp(value, MIN_STEP_WIDTH, MAX_STEP_WIDTH));
  }, []);
  const sequenceFromClientX = useCallback((clientX: number): number | null => {
    const ruler = rulerRef.current;
    if (!ruler) return null;
    const rect = ruler.getBoundingClientRect();
    const offset = clamp(clientX - rect.left, 0, rect.width);
    return clamp(Math.floor(offset / stepWidth) + 1, 1, sequenceCount);
  }, [sequenceCount, stepWidth]);
  const scrubToClientX = useCallback((clientX: number) => {
    const sequence = sequenceFromClientX(clientX);
    if (sequence !== null) onSelectSequence(sequence);
  }, [onSelectSequence, sequenceFromClientX]);
  const startScrubbing = useCallback((event: ReactMouseEvent<HTMLElement>) => {
    event.preventDefault();
    setIsScrubbing(true);
    scrubToClientX(event.clientX);
    const handleMouseMove = (moveEvent: MouseEvent) => scrubToClientX(moveEvent.clientX);
    const handleMouseUp = () => {
      setIsScrubbing(false);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
  }, [scrubToClientX]);
  const handleScrubKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      onSelectSequence(clamp(activeSequence - 1, 1, sequenceCount));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      onSelectSequence(clamp(activeSequence + 1, 1, sequenceCount));
    } else if (event.key === "Home") {
      event.preventDefault();
      onSelectSequence(1);
    } else if (event.key === "End") {
      event.preventDefault();
      onSelectSequence(sequenceCount);
    }
  };

  return <section
    className="run-timeline"
    aria-label="Run timeline"
    data-qid="dag:timeline:run"
    data-sequence-count={maxSequence}
    data-min-canvas-px={timelineCanvasWidth}
    data-step-width={stepWidth}
    data-active-sequence={activeSequence}
    style={timelineStyle}
  >
    <header className="run-timeline__summary">
      <div>
        <strong>{manifest.workflow?.title ?? manifest.plan_id}</strong>
        <span>{manifest.goal.summary ?? manifest.goal.goal_hash ?? "goal unavailable"}</span>
      </div>
      <dl>
        <div><dt>Active</dt><dd>{activeCount}</dd></div>
        <div><dt>Accepted</dt><dd>{acceptedCount}</dd></div>
        {decisionCount > 0 && <div className="run-timeline__decision-alert" data-qid="dag:timeline:decision-required"><dt>Human decision required</dt><dd>{decisionCount}</dd></div>}
        <div><dt>Control</dt><dd>{controlCount}</dd></div>
      </dl>
    </header>
    <div className="run-timeline__zoom-toolbar" data-qid="dag:timeline:zoom-toolbar">
      <strong>Sequence zoom</strong>
      <button type="button" data-qid="dag:timeline:zoom-out" data-qs-action="TAU_TIMELINE_ZOOM_OUT" title="Zoom out timeline steps" onClick={() => setClampedStepWidth(stepWidth - 8)}>
        <Minus aria-hidden="true" size={13} />
      </button>
      <input
        type="range"
        min={MIN_STEP_WIDTH}
        max={MAX_STEP_WIDTH}
        step={2}
        value={stepWidth}
        data-qid="dag:timeline:zoom-slider"
        data-qs-action="TAU_TIMELINE_STEP_ZOOM"
        title="Set timeline sequence step width"
        aria-label="Timeline sequence step width"
        onChange={(event) => setClampedStepWidth(Number(event.currentTarget.value))}
      />
      <button type="button" data-qid="dag:timeline:zoom-in" data-qs-action="TAU_TIMELINE_ZOOM_IN" title="Zoom in timeline steps" onClick={() => setClampedStepWidth(stepWidth + 8)}>
        <Plus aria-hidden="true" size={13} />
      </button>
      <button type="button" data-qid="dag:timeline:fit-view" data-qs-action="TAU_TIMELINE_FIT_VIEW" title="Fit timeline overview" onClick={() => setClampedStepWidth(FIT_STEP_WIDTH)}>Fit View</button>
      <button type="button" data-qid="dag:timeline:detail-view" data-qs-action="TAU_TIMELINE_DETAIL_VIEW" title="Use detailed timeline zoom" onClick={() => setClampedStepWidth(DETAIL_STEP_WIDTH)}>Detail</button>
      <code data-qid="dag:timeline:zoom-value">{stepWidth}px / seq</code>
    </div>
    <div className="run-timeline__canvas-scroll" data-qid="dag:timeline:canvas-scroll">
      <div className="run-timeline__scale" data-qid="dag:timeline:scale" data-scale-mode={model.scale.mode}>
        <strong>Sequence Step</strong>
        <span>{model.scale.domainLabel}</span>
        <div
          className="run-timeline__ruler"
          data-qid="dag:timeline:playhead-scrub"
          data-qs-action="TAU_TIMELINE_PLAYHEAD_SCRUB"
          title="Click or drag to scrub the timeline playhead"
          role="slider"
          tabIndex={0}
          aria-label="Timeline playhead sequence"
          aria-valuemin={1}
          aria-valuemax={sequenceCount}
          aria-valuenow={activeSequence}
          ref={rulerRef}
          onMouseDown={startScrubbing}
          onKeyDown={handleScrubKeyDown}
        >
          {sequenceScale.map((sequence) => <i
            key={sequence}
            className={sequence === activeSequence ? "is-active" : ""}
            data-sequence={sequence}
          ><b>#{sequence}</b></i>)}
        </div>
      </div>
      <div className="run-timeline__tracks">
        <div
          className={`run-timeline__playhead${isScrubbing ? " is-scrubbing" : ""}`}
          data-qid="dag:timeline:playhead"
          data-active-sequence={activeSequence}
          title={`Timeline playhead at sequence ${activeSequence}`}
          onMouseDown={startScrubbing}
        >
          <span>#{activeSequence}</span>
        </div>
        <RoleSwimlanes lanes={roleLanes} stepWidth={stepWidth} trackWidth={timelineTrackWidth} />
        {trackDefinitions.filter(({ kind }) => kind !== "decision" || model.tracks.decision.length > 0).map(({ kind, label, description, icon: Icon }) => (
          <section className="run-timeline__track" key={kind} aria-label={`${label} track`}>
            <header>
              <Icon aria-hidden="true" size={15} />
              <div><strong>{label}</strong><span>{description}</span></div>
            </header>
            <div className="run-timeline__lane">
              {model.tracks[kind].length > 0
                ? model.tracks[kind].map((clip) => clipButton(clip, selectedClip?.eventId === clip.eventId, stepWidth, onSelect))
                : <span className="run-timeline__empty">No {label.toLowerCase()} projection in this prefix.</span>}
            </div>
          </section>
        ))}
      </div>
    </div>
    <SelectedTimelineEvent clip={selectedClip} />
  </section>;
}
