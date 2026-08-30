import type { ReactNode } from "react";

/**
 * A table that stacks into cards below the STACK breakpoint (see
 * src/styles/responsive.css). Each `<td>` carries a `data-label` so the
 * stacked layout can show "field: value" without any JS re-render, and the
 * `primary` column becomes the card's heading row.
 */
export interface TableColumn<T> {
  key: string;
  header: ReactNode;
  /** Plain-text label used for the stacked data-label and card heading fallback. */
  label: string;
  render: (row: T) => ReactNode;
  /** This column's value becomes the heading of the stacked card. */
  primary?: boolean;
  width?: string;
}

export interface TableProps<T> {
  columns: TableColumn<T>[];
  rows: T[];
  getRowKey: (row: T) => string | number;
  onRowClick?: (row: T) => void;
  caption?: string;
}

export function Table<T>({
  columns,
  rows,
  getRowKey,
  onRowClick,
  caption,
}: TableProps<T>) {
  return (
    <table>
      {caption && <caption style={{ position: "absolute", left: -9999 }}>{caption}</caption>}
      <thead>
        <tr>
          {columns.map((col) => (
            <th key={col.key} style={col.width ? { width: col.width } : undefined}>
              {col.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const key = getRowKey(row);
          const clickable = Boolean(onRowClick);
          return (
            <tr
              key={key}
              tabIndex={clickable ? 0 : undefined}
              role={clickable ? "button" : undefined}
              onClick={clickable ? () => onRowClick?.(row) : undefined}
              onKeyDown={
                clickable
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick?.(row);
                      }
                    }
                  : undefined
              }
            >
              {columns.map((col) => (
                <td
                  key={col.key}
                  data-label={col.label}
                  data-primary={col.primary ? "" : undefined}
                >
                  {col.render(row)}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
