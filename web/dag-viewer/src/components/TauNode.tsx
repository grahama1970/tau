import { memo, useEffect, useState } from "react";
import { Bot, Braces, CircleCheck, Clock3, GitBranch, UserRound, Workflow } from "lucide-react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { LiveNode } from "../types";

export type TauNodeData = {
  label: string;
  role: string;
  kind: string;
  live: LiveNode | null;
};

const kindIcon = (kind: string) => {
  if (kind.includes("human")) return UserRound;
  if (kind.includes("route") || kind.includes("join")) return GitBranch;
  if (kind.includes("agent") || kind.includes("provider")) return Bot;
  if (kind.includes("command") || kind.includes("code")) return Braces;
  return Workflow;
};

const activeTimingStates = new Set(["ready", "running", "validating", "committing", "retry_pending", "reconciliation_required"]);

function parseTime(value: string | null | undefined) {
  if (!value) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function formatDurationSeconds(value: number) {
  const totalSeconds = Math.max(0, Math.floor(value));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes.toString().padStart(2, "0")}m`;
  if (minutes > 0) return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
  return `${seconds}s`;
}

function nodeTiming(live: LiveNode | null, scheduler: string, nowMs: number) {
  const result = live?.result;
  if (!result) return null;
  if (typeof result.duration_seconds === "number") {
    return { label: "duration", value: formatDurationSeconds(result.duration_seconds) };
  }
  const startedAt = parseTime(result.started_at);
  const finishedAt = parseTime(result.finished_at);
  if (startedAt === null) return null;
  if (finishedAt !== null) {
    return { label: "duration", value: formatDurationSeconds((finishedAt - startedAt) / 1000) };
  }
  if (!activeTimingStates.has(scheduler)) return null;
  return { label: "elapsed", value: formatDurationSeconds((nowMs - startedAt) / 1000) };
}

function TauNodeComponent({ data, selected }: NodeProps) {
  const value = data as unknown as TauNodeData;
  const live = value.live;
  const scheduler = live?.scheduler.state ?? "pending";
  const admission = live?.admission.state ?? "not_started";
  const Icon = kindIcon(value.kind);
  const accepted = live?.admission.accepted === true && scheduler === "settled";
  const tone = accepted ? "accepted" : scheduler;
  const [nowMs, setNowMs] = useState(() => Date.now());
  const startedAt = parseTime(live?.result?.started_at);
  const shouldTick = startedAt !== null
    && live?.result?.finished_at === null
    && typeof live?.result?.duration_seconds !== "number"
    && activeTimingStates.has(scheduler);
  useEffect(() => {
    if (!shouldTick) return undefined;
    setNowMs(Date.now());
    const interval = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [scheduler, shouldTick, startedAt]);
  const timing = nodeTiming(live, scheduler, nowMs);
  return (
    <article
      className={`tau-node tau-node--${tone} ${selected ? "tau-node--selected" : ""}`}
      data-qid={`dag:node:${value.label}`}
      data-state={scheduler}
      data-node-state={scheduler}
      data-admission-state={admission}
      aria-label={`${value.label}, ${scheduler}, ${admission}`}
    >
      <Handle id="input" type="target" position={Position.Left} className="tau-handle" />
      <header className="tau-node__header">
        <Icon aria-hidden="true" size={16} />
        <strong>{value.label}</strong>
      </header>
      <div className="tau-node__role">{value.role}</div>
      <div className="tau-node__states">
        <span><Clock3 aria-hidden="true" size={13} />{scheduler}</span>
        <span><CircleCheck aria-hidden="true" size={13} />{admission}</span>
        {live?.correction && <span data-qid={`dag:node:${value.label}:correction`}>correction {live.correction.state.toLowerCase()}</span>}
      </div>
      {timing && (
        <div className="tau-node__timing" data-qid={`dag:node:${value.label}:timing`}>
          <Clock3 aria-hidden="true" size={12} />
          <span>{timing.label}</span>
          <strong>{timing.value}</strong>
        </div>
      )}
      {live?.result?.summary && (
        <div className="tau-node__result">{live.result.summary}</div>
      )}
      {live?.result?.blocker_codes[0] && (
        <div
          className="tau-node__blocker"
          data-qid={`dag:node:${value.label}:blocker`}
        >
          {live.result.blocker_codes[0]}
        </div>
      )}
      <footer>
        <span>attempt {live?.scheduler.attempt ?? 0}/{live?.scheduler.max_attempts ?? 1}</span>
        <span>{live?.runtime.state ?? "UNKNOWN"}</span>
      </footer>
      <Handle id="output" type="source" position={Position.Right} className="tau-handle" />
    </article>
  );
}

export const TauNode = memo(TauNodeComponent);
