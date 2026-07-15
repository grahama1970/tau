import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { TransactionAttempts } from "../components/TransactionAttempts";

test("separates reviewer pass claim from Tau admission", () => {
  render(<TransactionAttempts transaction={{ current_attempt: 2, max_attempts: 2, state: "AWAITING_RECEIPT", attempts: [
    { attempt: 1, reviewer_verdict: "REVISE" },
    { attempt: 2, reviewer_verdict: "PASS" },
  ] }} />);
  expect(screen.getByText("PASS claim")).toBeInTheDocument();
  expect(screen.getByText(/Tau admission: AWAITING_RECEIPT/)).toBeInTheDocument();
});
