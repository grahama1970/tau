import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { DagWorkspace } from "../components/DagWorkspace";
import { manifest, snapshot } from "./fixtures";

test("keeps runtime-alive awaiting-receipt node non-green", () => {
  render(<div style={{ width: 900, height: 500 }}><DagWorkspace manifest={manifest} snapshot={snapshot} selectedId={null} onSelect={vi.fn()} /></div>);
  const node = screen.getByLabelText("creator, running, awaiting_receipt");
  expect(node).toHaveAttribute("data-admission-state", "awaiting_receipt");
  expect(node.className).not.toContain("accepted");
});
