import { Braces, FileCheck2, FileJson2, GitBranch, RadioTower, SquareDashedMousePointer } from "lucide-react";

export type InspectorTab = "node" | "source" | "plan" | "live" | "cause" | "receipt";
export type WorkspaceView = "timeline" | "topology";

export const inspectorTabs: Array<{ id: InspectorTab; label: string; icon: typeof Braces }> = [
  { id: "node", label: "Node", icon: SquareDashedMousePointer },
  { id: "source", label: "Source DAG", icon: FileJson2 },
  { id: "plan", label: "DagPlan", icon: Braces },
  { id: "live", label: "Live State", icon: RadioTower },
  { id: "cause", label: "Why", icon: GitBranch },
  { id: "receipt", label: "Receipt", icon: FileCheck2 },
];

export function parseWorkspaceView(parameters: URLSearchParams): WorkspaceView {
  return parameters.get("workspace_view") === "topology" ? "topology" : "timeline";
}
