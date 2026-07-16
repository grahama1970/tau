import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { SequenceNavigator } from "../components/SequenceNavigator";

test("navigates actual committed sequence identifiers and returns live", () => {
  const onSelect = vi.fn();
  const { rerender } = render(
    <SequenceNavigator sequences={[2, 5, 11]} selectedSequence={5} onSelect={onSelect} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Previous committed sequence" }));
  expect(onSelect).toHaveBeenCalledWith(2);
  fireEvent.click(screen.getByRole("button", { name: "Next committed sequence" }));
  expect(onSelect).toHaveBeenCalledWith(11);
  fireEvent.click(screen.getByRole("button", { name: "Return live" }));
  expect(onSelect).toHaveBeenCalledWith(null);

  rerender(
    <SequenceNavigator sequences={[2, 5, 11]} selectedSequence={null} onSelect={onSelect} />,
  );
  expect(screen.getByText("following journal head")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Return live" })).not.toBeInTheDocument();
});
