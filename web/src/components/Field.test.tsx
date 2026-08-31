import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { Field } from "./Field";

test("a neutral hint is associated with the input but not announced as an alert", () => {
  render(<Field label="New password" hint="Use a passphrase. Length beats symbols." />);
  const input = screen.getByLabelText("New password");
  const hint = screen.getByText("Use a passphrase. Length beats symbols.");
  expect(input).toHaveAttribute("aria-describedby", hint.id);
  expect(hint).not.toHaveAttribute("role");
  expect(input).not.toHaveAttribute("aria-invalid");
});

test("an invalid field's hint is announced immediately via role=alert, not only on focus", () => {
  render(<Field label="Work email" hint="Enter a valid work email." invalid />);
  const input = screen.getByLabelText("Work email");
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByRole("alert")).toHaveTextContent("Enter a valid work email.");
});
