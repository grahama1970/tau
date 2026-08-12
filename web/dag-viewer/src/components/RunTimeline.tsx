import { AlertTriangle, CheckCircle2, CircleDot, GitBranch, Route, ShieldCheck } from "lucide-react";
import { useMemo, type CSSProperties } from "react";
import type { DagManifest, DagSnapshot } from "../types";
import { useRegisterAction } from "../useRegisterAction";
import { buildTimelineModel, type TimelineClip, type TimelineKind, type TimelineSubject } from "./runTimelineModel";
import { buildTimelineRoleLanes, type TimelineRoleLane } from "./runTimelineSwimlanes";

const trackDefinitions: Array<{ kind: TimelineKind; label: string; description: string; icon: typeof GitBranch }> = [
  { kind: "execution", label: "Execution", description: "nodes, attempts, terminals", icon: GitBranch },
  { kind: "proof", label: "Proof", description: "admission, receipts, blockers", icon: ShieldCheck },
  { kind: "decision", label: "Human Decisions", description: "approvals, blockers, choices", icon: AlertTriangle },
  { kind: "control", label: "Control & Effects", description: "attention, routes, joins, results", icon: Route },
];

function clipButton(clip: TimelineClip, selected: boolean, onSelect: (subject: TimelineSubject) => void) {
  const Icon = clip.state === "accepted" ? CheckCircle2 : clip.state === "blocked" ? AlertTriangle : clip.state === "active" ? CircleDot : ShieldCheck;
  const style = clip.offsetPercent === null ? undefined : { "--timeline-offset": `${clip.offsetPercent}%` } as CSSProperties;
  return <button
    key={clip.id}
    type="button"
    className={`run-timeline__clip run-timeline__clip--${clip.state}${selected ? " is-selected" : ""}`}
    data-qid={clip.qid}
    data-qs-action={clip.action}
    data-event-id={clip.eventId}
    data-scale-offset={clip.offsetPercent === null ? "unpositioned" : clip.offsetPercent.toFixed(2)}
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

function RoleSwimlanes({ lanes }: { lanes: TimelineRoleLane[] }) {
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
              const style = {
                "--timeline-offset": `${clip.offsetPercent ?? 0}%`,
                "--timeline-duration-offset": `${clip.durationOffsetPercent ?? clip.offsetPercent ?? 0}%`,
                "--timeline-duration-width": `${clip.durationWidthPercent ?? 1.5}%`,
              } as CSSProperties;
              return <span
                className={`run-timeline__role-clip run-timeline__role-clip--${clip.state}`}
                key={clip.id}
                data-qid={clip.qid}
                data-event-id={clip.eventId}
                data-duration-mode={clip.durationMode}
                data-edge-anchor={clip.edgeAnchor}
                data-timeline-state={clip.state}
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
  onSelect,
}: {
  manifest: DagManifest;
  snapshot: DagSnapshot;
  selectedId: string | null;
  selectedTimelineEventId: string | null;
  onSelect: (subject: TimelineSubject) => void;
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

  const model = useMemo(() => buildTimelineModel(manifest, snapshot), [manifest, snapshot]);
  const roleLanes = useMemo(() => buildTimelineRoleLanes(manifest, model.tracks.execution), [manifest, model.tracks.execution]);
  const selectedClip = model.clips.find((clip) => clip.eventId === selectedTimelineEventId)
    ?? model.clips.find((clip) => clip.subject.id === selectedId)
    ?? null;
  const activeCount = model.tracks.execution.filter((clip) => clip.state === "active").length;
  const acceptedCount = model.tracks.proof.filter((clip) => clip.state === "accepted").length;
  const decisionCount = model.tracks.decision.length;
  const controlCount = model.tracks.control.length;

  return <section className="run-timeline" aria-label="Run timeline" data-qid="dag:timeline:run">
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
    <div className="run-timeline__scale" data-qid="dag:timeline:scale" data-scale-mode={model.scale.mode}>
      <strong>{model.scale.label}</strong>
      <span>{model.scale.domainLabel}</span>
      <div aria-hidden="true">
        {model.scale.ticks.map((tick) => <i key={tick.id} style={{ left: `${tick.offsetPercent}%` }}><b>{tick.label}</b></i>)}
      </div>
    </div>
    <div className="run-timeline__tracks">
      <RoleSwimlanes lanes={roleLanes} />
      {trackDefinitions.filter(({ kind }) => kind !== "decision" || model.tracks.decision.length > 0).map(({ kind, label, description, icon: Icon }) => (
        <section className="run-timeline__track" key={kind} aria-label={`${label} track`}>
          <header>
            <Icon aria-hidden="true" size={15} />
            <div><strong>{label}</strong><span>{description}</span></div>
          </header>
          <div className="run-timeline__lane">
            {model.tracks[kind].length > 0
              ? model.tracks[kind].map((clip) => clipButton(clip, selectedClip?.eventId === clip.eventId, onSelect))
              : <span className="run-timeline__empty">No {label.toLowerCase()} projection in this prefix.</span>}
          </div>
        </section>
      ))}
    </div>
    <SelectedTimelineEvent clip={selectedClip} />
  </section>;
}
