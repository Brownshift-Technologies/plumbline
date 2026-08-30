import type { ReactNode } from "react";

export type PillKind = "fail" | "pass" | "warn" | "info" | "grey";

export interface PillProps {
  kind: PillKind;
  children: ReactNode;
  /** Set false to suppress the leading status dot (e.g. when a custom icon is used instead). */
  dot?: boolean;
}

export function Pill({ kind, children, dot = true }: PillProps) {
  return (
    <span className={`pill ${kind}`}>
      {dot && <span className="d" aria-hidden="true" />}
      {children}
    </span>
  );
}
