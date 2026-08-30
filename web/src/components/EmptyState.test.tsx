import { render, screen } from "@testing-library/react";
import { EmptyState } from "./EmptyState";

test("an error state is announced as an alert", () => {
  render(<EmptyState variant="error" title="Couldn't load runs" description="Network error" />);
  expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load runs");
  expect(screen.getByText("Network error")).toBeInTheDocument();
});

test("a loading state is marked busy and an empty state is not", () => {
  const { rerender } = render(<EmptyState variant="loading" title="Loading runs…" />);
  expect(screen.getByText("Loading runs…").closest("[aria-busy]")).toBeInTheDocument();

  rerender(<EmptyState variant="empty" title="No runs yet" />);
  expect(screen.getByText("No runs yet").closest("[aria-busy]")).not.toBeInTheDocument();
});
