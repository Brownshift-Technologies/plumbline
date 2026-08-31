import type { CSSProperties } from "react";

/**
 * A single shimmering placeholder block. `prefers-reduced-motion` disables
 * the shimmer animation via base.css's global `*{animation:none!important}`
 * rule -- nothing extra needed here.
 */
export function SkeletonBlock({
  width = "100%",
  height = 13,
  style,
}: {
  width?: string | number;
  height?: string | number;
  style?: CSSProperties;
}) {
  return <span className="skel" aria-hidden="true" style={{ width, height, ...style }} />;
}

/**
 * A block of shimmering lines standing in for a non-tabular panel (a card,
 * a timeline row, a form section) while its data loads. Kept out of the
 * accessibility tree (`aria-hidden`) -- the caller is responsible for a
 * `role="status"`/visually-hidden "Loading…" text alongside it, same as
 * `<Table skeletonRows>` does for tabular content.
 */
export function SkeletonLines({ count = 3, widths }: { count?: number; widths?: (string | number)[] }) {
  return (
    <div aria-hidden="true" style={{ display: "grid", gap: 10 }}>
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonBlock key={i} width={widths?.[i] ?? `${85 - i * 12}%`} />
      ))}
    </div>
  );
}
