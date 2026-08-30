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
