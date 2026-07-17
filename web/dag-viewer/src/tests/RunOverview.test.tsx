import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { RunOverview } from "../components/RunOverview";
import { manifest, snapshot } from "./fixtures";

test("renders workflow goal and the authoritative active node", () => {
  render(<RunOverview manifest={manifest} snapshot={snapshot} />);

  expect(screen.getByText("Repository Readiness")).toBeInTheDocument();
  expect(screen.getByText("LINEAR · repository-readiness")).toBeInTheDocument();
  expect(screen.getByText("Determine whether this checkout is ready for focused work.")).toBeInTheDocument();
  expect(screen.getByText("creator")).toBeInTheDocument();
});

test("renders an accepted final result and artifact references", () => {
  const settled = {
    ...snapshot,
    run_status: "PASS",
    run_summary: {
      active_node_ids: [],
      accepted_node_ids: ["creator", "publish"],
      highest_priority_blocker: null,
      final_result: {
        summary: "Repository is ready for focused work.",
        status: "READY",
        artifacts: [{ kind: "readiness_report", path_display: "results/repository-readiness.json", sha256: "sha256:report" }],
      },
    },
  };
  render(<RunOverview manifest={manifest} snapshot={settled} />);

  expect(screen.getByText("Run settled")).toBeInTheDocument();
  expect(screen.getByText("Repository is ready for focused work.")).toBeInTheDocument();
  expect(screen.getByText("READY")).toBeInTheDocument();
  expect(screen.getByText(/results\/repository-readiness.json/)).toBeInTheDocument();
});

test("renders exact blocker codes without a final result", () => {
  const blocked = {
    ...snapshot,
    run_status: "BLOCKED",
    run_summary: {
      active_node_ids: [],
      accepted_node_ids: ["creator"],
      highest_priority_blocker: { node_id: "validate-readiness", codes: ["dirty_repository"] },
      final_result: null,
    },
  };
  render(<RunOverview manifest={manifest} snapshot={blocked} />);

  expect(screen.getByText("dirty_repository")).toBeInTheDocument();
  expect(screen.getAllByText("No accepted final result").at(-1)).toBeInTheDocument();
  expect(document.querySelector('[data-qid="dag:overview:blocker"]')).toBeInTheDocument();
});

test("uses the retained source goal when the plan has only a hash binding", () => {
  render(<RunOverview
    manifest={{ ...manifest, goal: { kind: "hash_only", goal_hash: "sha256:goal" } }}
    snapshot={snapshot}
  />);

  expect(screen.getByText("Keep the human-owned goal immutable.")).toBeInTheDocument();
  expect(screen.queryByText("Goal summary unavailable")).not.toBeInTheDocument();
});
