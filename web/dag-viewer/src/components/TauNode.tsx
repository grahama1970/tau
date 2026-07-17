import { memo } from "react";
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

function TauNodeComponent({ data, selected }: NodeProps) {
  const value = data as unknown as TauNodeData;
  const live = value.live;
  const scheduler = live?.scheduler.state ?? "pending";
  const admission = live?.admission.state ?? "not_started";
  const Icon = kindIcon(value.kind);
  const accepted = live?.admission.accepted === true && scheduler === "settled";
  const tone = accepted ? "accepted" : scheduler;
  return (
    <article
      className={`tau-node tau-node--${tone} ${selected ? "tau-node--selected" : ""}`}
      data-qid={`dag:node:${value.label}`}
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
