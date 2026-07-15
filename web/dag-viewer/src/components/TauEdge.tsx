import { memo } from "react";
import { BaseEdge, getSmoothStepPath, type EdgeProps } from "@xyflow/react";

function TauEdgeComponent(props: EdgeProps) {
  const [path] = getSmoothStepPath(props);
  const state = String((props.data as { state?: string } | undefined)?.state ?? "pending");
  return <BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} className={`tau-edge tau-edge--${state}`} />;
}

export const TauEdge = memo(TauEdgeComponent);
