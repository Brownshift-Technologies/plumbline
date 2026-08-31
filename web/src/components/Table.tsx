import type { ReactNode } from "react";
import { SkeletonBlock } from "./Skeleton";

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
  /**
   * Loading state: renders this many shaped placeholder rows -- same
   * column count and widths as the real table -- instead of `rows`. Every
   * placeholder `<tr>` carries `data-skeleton-row` so a test can assert on
   * row-shaped DOM rather than on any particular loading text.
   */
  skeletonRows?: number;
}

export function Table<T>({
  columns,
  rows,
  getRowKey,
  onRowClick,
  caption,
  skeletonRows,
}: TableProps<T>) {
  const loading = skeletonRows !== undefined;

  return (
    <table aria-busy={loading || undefined}>
      {(caption || loading) && (
        <caption className="visually-hidden" role={loading ? "status" : undefined}>
          {loading ? (caption ?? "Loading…") : caption}
        </caption>
      )}
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
        {loading
          ? Array.from({ length: skeletonRows }).map((_, i) => (
              <tr key={i} data-skeleton-row="">
                {columns.map((col, ci) => (
                  <td key={col.key} data-label={col.label} data-primary={col.primary ? "" : undefined}>
                    <SkeletonBlock width={ci === 0 ? "60%" : `${75 - (i % 3) * 10}%`} />
                  </td>
                ))}
              </tr>
            ))
          : rows.map((row) => {
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
                    <td key={col.key} data-label={col.label} data-primary={col.primary ? "" : undefined}>
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
