import { AlertTriangle, CheckCircle2, CircleDot, GitBranch, Route, ShieldCheck } from "lucide-react";
import { useMemo } from "react";
import type { AttentionItem, DagManifest, DagSnapshot, JsonValue, LiveNode } from "../types";
import { useRegisterAction } from "../useRegisterAction";

type TimelineKind = "execution" | "proof" | "control";
type TimelineState = "active" | "accepted" | "blocked" | "pending" | "warning" | "neutral";

type TimelineClip = {
  id: string;
  qid: string;
  action: string;
  title: string;
  label: string;
  eyebrow: string;
  meta: string;
  state: TimelineState;
  subject: { kind: string; id: string };
  sequence: number | null;
};

const countFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

function qidPart(value: string): string {
  return value.replace(/[^a-zA-Z0-9:_-]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
}

function stateForNode(node: LiveNode | null): TimelineState {
  if (!node) return "pending";
  const states = [
    node.scheduler.state,
    node.runtime.state,
    node.admission.state,
    ...node.result.blocker_codes,
  ].map((value) => value.toLowerCase());
  if (node.admission.accepted || states.includes("accepted") || states.includes("settled")) return "accepted";
  if (states.some((state) => ["blocked", "failed", "timed_out", "rejected"].includes(state))) return "blocked";
  if (states.some((state) => ["running", "alive", "validating", "committing", "awaiting_receipt"].includes(state))) return "active";
  if (states.some((state) => ["retry_pending", "scheduled", "warning"].includes(state))) return "warning";
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
  const blockers = node.result.blocker_codes.join(", ");
  if (blockers) return `blockers: ${blockers}`;
  if (node.result.summary) return node.result.summary;
  return `${receiptCount} receipt${receiptCount === 1 ? "" : "s"} - ${node.admission.state}`;
}

function stateForAttentionSeverity(severity: AttentionItem["severity"]): TimelineState {
  if (severity === "BLOCKER") return "blocked";
  if (severity === "ACTION_REQUIRED") return "active";
  return "warning";
}

function clipButton(clip: TimelineClip, selected: boolean, onSelect: (subject: { kind: string; id: string }) => void) {
  const Icon = clip.state === "accepted"
    ? CheckCircle2
    : clip.state === "blocked"
      ? AlertTriangle
      : clip.state === "active"
        ? CircleDot
        : ShieldCheck;
  return <button
    key={clip.id}
    type="button"
    className={`run-timeline__clip run-timeline__clip--${clip.state}${selected ? " is-selected" : ""}`}
    data-qid={clip.qid}
    data-qs-action={clip.action}
    title={clip.title}
    aria-label={clip.title}
    aria-pressed={selected}
    onClick={() => onSelect(clip.subject)}
  >
    <span className="run-timeline__clip-status"><Icon aria-hidden="true" size={14} />{clip.eyebrow}</span>
    <strong>{clip.label}</strong>
    <small>{clip.meta}</small>
    {clip.sequence !== null && <code>#{clip.sequence}</code>}
  </button>;
}

export function RunTimeline({
  manifest,
  snapshot,
  selectedId,
  onSelect,
}: {
  manifest: DagManifest;
  snapshot: DagSnapshot;
  selectedId: string | null;
  onSelect: (subject: { kind: string; id: string }) => void;
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

  const tracks = useMemo(() => {
    const liveNodes = new Map(snapshot.nodes.map((node) => [node.node_id, node]));
    const terminalStates = new Map(snapshot.terminals.map((terminal) => [terminal.terminal_id, terminal]));
    const execution: TimelineClip[] = [
      ...manifest.graph.nodes.map((node) => {
        const live = liveNodes.get(node.node_id) ?? null;
        return {
          id: `execution:${node.node_id}`,
          qid: `dag:timeline:execution:${qidPart(node.node_id)}`,
          action: "TAU_TIMELINE_SELECT_EXECUTION",
          title: `Select execution node ${node.node_id}`,
          label: node.node_id,
          eyebrow: live?.scheduler.state ?? "planned",
          meta: executionMeta(live, node.role, node.retry_policy.max_attempts),
          state: stateForNode(live),
          subject: { kind: "NODE", id: node.node_id },
          sequence: live?.updated_sequence ?? null,
        };
      }),
      ...manifest.graph.terminals.map((terminal) => {
        const live = terminalStates.get(terminal.terminal_id);
        return {
          id: `execution-terminal:${terminal.terminal_id}`,
          qid: `dag:timeline:execution:${qidPart(terminal.terminal_id)}`,
          action: "TAU_TIMELINE_SELECT_EXECUTION",
          title: `Select terminal ${terminal.terminal_id}`,
          label: terminal.terminal_id,
          eyebrow: live?.state ?? "terminal",
          meta: `${terminal.kind} - ${terminal.origin}`,
          state: stateForText(live?.state),
          subject: { kind: "TERMINAL", id: terminal.terminal_id },
          sequence: null,
        };
      }),
    ];

    const proof: TimelineClip[] = manifest.graph.nodes.map((node) => {
      const live = liveNodes.get(node.node_id) ?? null;
      return {
        id: `proof:${node.node_id}`,
        qid: `dag:timeline:proof:${qidPart(node.node_id)}`,
        action: "TAU_TIMELINE_SELECT_PROOF",
        title: `Inspect proof state for ${node.node_id}`,
        label: node.node_id,
        eyebrow: live?.admission.state ?? "not projected",
        meta: proofMeta(live),
        state: stateForNode(live),
        subject: { kind: "NODE", id: node.node_id },
        sequence: live?.updated_sequence ?? null,
      };
    });

    const control: TimelineClip[] = [
      ...snapshot.attention_items.map((item: AttentionItem) => ({
        id: `attention:${item.attention_id}`,
        qid: `dag:timeline:control:attention:${qidPart(item.attention_id)}`,
        action: "TAU_TIMELINE_SELECT_CONTROL",
        title: `Inspect attention item ${item.attention_id}`,
        label: item.reason_code,
        eyebrow: item.severity,
        meta: `${item.state.toLowerCase()} - ${item.required_action_code}`,
        state: stateForAttentionSeverity(item.severity),
        subject: { kind: "ATTENTION", id: item.attention_id },
        sequence: item.opened_sequence,
      })),
      ...snapshot.routes.map((route) => ({
        id: `route:${route.route_id}`,
        qid: `dag:timeline:control:route:${qidPart(route.route_id)}`,
        action: "TAU_TIMELINE_SELECT_CONTROL",
        title: `Inspect route ${route.route_id}`,
        label: route.route_id,
        eyebrow: route.state,
        meta: route.reason_code,
        state: stateForText(route.state),
        subject: { kind: "ROUTE", id: route.route_id },
        sequence: route.decision_sequence,
      })),
      ...snapshot.joins.map((join) => ({
        id: `join:${join.join_node_id}`,
        qid: `dag:timeline:control:join:${qidPart(join.join_node_id)}`,
        action: "TAU_TIMELINE_SELECT_CONTROL",
        title: `Inspect join ${join.join_node_id}`,
        label: join.join_node_id,
        eyebrow: join.state,
        meta: join.decision ?? join.reason_code,
        state: stateForText(join.state),
        subject: { kind: "JOIN", id: join.join_node_id },
        sequence: join.decision_sequence,
      })),
      ...snapshot.corrections.map((correction) => ({
        id: `correction:${correction.incident_id}`,
        qid: `dag:timeline:control:correction:${qidPart(correction.incident_id)}`,
        action: "TAU_TIMELINE_SELECT_CONTROL",
        title: `Inspect correction ${correction.incident_id}`,
        label: correction.incident_id,
        eyebrow: correction.state,
        meta: shortJson(correction.intent) || shortJson(correction.verification) || "correction",
        state: stateForText(correction.state),
        subject: { kind: "CORRECTION", id: correction.incident_id },
        sequence: correction.journal_sequence,
      })),
      ...snapshot.terminals.map((terminal) => ({
        id: `terminal:${terminal.terminal_id}`,
        qid: `dag:timeline:control:terminal:${qidPart(terminal.terminal_id)}`,
        action: "TAU_TIMELINE_SELECT_CONTROL",
        title: `Inspect terminal ${terminal.terminal_id}`,
        label: terminal.terminal_id,
        eyebrow: terminal.state,
        meta: "external boundary",
        state: stateForText(terminal.state),
        subject: { kind: "TERMINAL", id: terminal.terminal_id },
        sequence: null,
      })),
    ];

    if (snapshot.run_summary.final_result) {
      control.push({
        id: "final-result",
        qid: "dag:timeline:control:final-result",
        action: "TAU_TIMELINE_SELECT_CONTROL",
        title: "Inspect final result",
        label: "final result",
        eyebrow: snapshot.run_verdict ?? snapshot.run_status,
        meta: shortJson(snapshot.run_summary.final_result),
        state: stateForText(snapshot.run_verdict ?? snapshot.run_status),
        subject: { kind: "RUN", id: snapshot.run_id },
        sequence: snapshot.journal_sequence,
      });
    }

    return { execution, proof, control } satisfies Record<TimelineKind, TimelineClip[]>;
  }, [manifest, snapshot]);

  const activeCount = tracks.execution.filter((clip) => clip.state === "active").length;
  const acceptedCount = tracks.proof.filter((clip) => clip.state === "accepted").length;
  const controlCount = tracks.control.length;

  return <section className="run-timeline" aria-label="Run timeline" data-qid="dag:timeline:run">
    <header className="run-timeline__summary">
      <div>
        <strong>{manifest.workflow?.title ?? manifest.plan_id}</strong>
        <span>{manifest.goal.summary ?? manifest.goal.goal_hash ?? "goal unavailable"}</span>
      </div>
      <dl>
        <div><dt>Active</dt><dd>{activeCount}</dd></div>
        <div><dt>Accepted</dt><dd>{acceptedCount}</dd></div>
        <div><dt>Control</dt><dd>{controlCount}</dd></div>
      </dl>
    </header>
    <div className="run-timeline__tracks">
      {([
        ["execution", "Execution", "nodes, attempts, terminals", GitBranch],
        ["proof", "Proof", "admission, receipts, blockers", ShieldCheck],
        ["control", "Control & Effects", "attention, routes, joins, results", Route],
      ] as const).map(([kind, label, description, Icon]) => (
        <section className="run-timeline__track" key={kind} aria-label={`${label} track`}>
          <header>
            <Icon aria-hidden="true" size={15} />
            <div><strong>{label}</strong><span>{description}</span></div>
          </header>
          <div className="run-timeline__lane">
            {tracks[kind].length > 0
              ? tracks[kind].map((clip) => clipButton(clip, selectedId === clip.subject.id, onSelect))
              : <span className="run-timeline__empty">No {label.toLowerCase()} projection in this prefix.</span>}
          </div>
        </section>
      ))}
    </div>
  </section>;
}
