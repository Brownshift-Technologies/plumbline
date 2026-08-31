import { render, screen } from "@testing-library/react";
import { Table, type TableColumn } from "./Table";

interface Row {
  id: string;
  name: string;
}

const columns: TableColumn<Row>[] = [
  { key: "name", header: "Name", label: "Name", primary: true, render: (r) => r.name },
  { key: "id", header: "ID", label: "ID", render: (r) => r.id },
];

const rows: Row[] = [{ id: "42", name: "Ada" }];

test("the primary column is marked so narrow layouts can promote it to a card heading", () => {
  render(<Table columns={columns} rows={rows} getRowKey={(r) => r.id} />);
  const primaryCell = screen.getByText("Ada").closest("td");
  expect(primaryCell).toHaveAttribute("data-primary");
  const idCell = screen.getByText("42").closest("td");
  expect(idCell).toHaveAttribute("data-label", "ID");
  expect(idCell).not.toHaveAttribute("data-primary");
});

test("a row without a click handler is not exposed as a button", () => {
  render(<Table columns={columns} rows={rows} getRowKey={(r) => r.id} />);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

test("skeletonRows renders shaped placeholder rows matching the real column count, not the real data", () => {
  render(<Table columns={columns} rows={rows} getRowKey={(r) => r.id} skeletonRows={4} />);
  const skeletonRows = document.querySelectorAll("tr[data-skeleton-row]");
  expect(skeletonRows).toHaveLength(4);
  // Each skeleton row has one shimmer block per real column.
  expect(skeletonRows[0].querySelectorAll(".skel")).toHaveLength(columns.length);
  // The real row's data never renders while skeletonRows is set.
  expect(screen.queryByText("Ada")).not.toBeInTheDocument();
  expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
});
