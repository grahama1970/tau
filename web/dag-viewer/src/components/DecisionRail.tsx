import { GitFork, Merge } from "lucide-react";
import type { JoinProjection, RouteProjection } from "../types";

type Props = {
  routes: RouteProjection[];
  joins: JoinProjection[];
  onSelect: (kind: "ROUTE" | "JOIN", id: string) => void;
};

export function DecisionRail({ routes, joins, onSelect }: Props) {
  if (routes.length === 0 && joins.length === 0) return null;
  return <section className="decision-rail" aria-label="Route and join decisions" data-qid="dag:decisions:rail">
    {routes.map((route) => <button
      key={route.route_id}
      type="button"
      data-qid={`dag:decision:route:${route.route_id}`}
      onClick={() => onSelect("ROUTE", route.route_id)}
    >
      <GitFork aria-hidden="true" size={14} />
      <span><strong>{route.source_node_id}</strong><small>route · {route.state}</small></span>
      <code>{route.selected_edge_ids.length} selected · {route.skipped_edge_ids.length} skipped</code>
    </button>)}
    {joins.map((join) => <button
      key={join.join_node_id}
      type="button"
      data-qid={`dag:decision:join:${join.join_node_id}`}
      onClick={() => onSelect("JOIN", join.join_node_id)}
    >
      <Merge aria-hidden="true" size={14} />
      <span><strong>{join.join_node_id}</strong><small>join · {join.state}</small></span>
      <code>{join.incoming.length} contributions · {join.decision ?? "pending"}</code>
    </button>)}
  </section>;
}
