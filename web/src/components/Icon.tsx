/**
 * One icon sprite, lifted verbatim from plumbline/design/preview.html.
 * All symbols share the 20px grid / 1.5 stroke convention. Render
 * <IconSprite/> once at the app root, then reference icons with <Icon name="i-home" />.
 */

export function IconSprite() {
  return (
    <svg
      width="0"
      height="0"
      style={{ position: "absolute" }}
      aria-hidden="true"
    >
      <defs>
        <symbol id="i-home" viewBox="0 0 20 20">
          <path d="M3.2 8.4 10 3.1l6.8 5.3v7.3a1.2 1.2 0 0 1-1.2 1.2h-3.4v-4.7H7.8v4.7H4.4a1.2 1.2 0 0 1-1.2-1.2V8.4Z" />
        </symbol>
        <symbol id="i-run" viewBox="0 0 20 20">
          <circle cx="10" cy="10" r="7.2" />
          <path d="M10 5.9v4.3l2.9 1.7" />
        </symbol>
        <symbol id="i-map" viewBox="0 0 20 20">
          <path d="m3 6.1 4.7-2.3L12.3 6 17 3.7v10.2l-4.7 2.3-4.6-2.2L3 16.3V6.1Z" />
          <path d="M7.7 3.8v10.2M12.3 6v10.2" />
        </symbol>
        <symbol id="i-alert" viewBox="0 0 20 20">
          <path d="M10 3.2 17 15.4H3L10 3.2Z" />
          <path d="M10 8.3v2.9" />
          <circle cx="10" cy="13.2" r=".85" fill="currentColor" stroke="none" />
        </symbol>
        <symbol id="i-grid" viewBox="0 0 20 20">
          <rect x="3.2" y="3.2" width="6" height="6" rx="1.6" />
          <rect x="10.8" y="3.2" width="6" height="6" rx="1.6" />
          <rect x="3.2" y="10.8" width="6" height="6" rx="1.6" />
          <rect x="10.8" y="10.8" width="6" height="6" rx="1.6" />
        </symbol>
        <symbol id="i-agents" viewBox="0 0 20 20">
          <circle cx="7.4" cy="7.1" r="2.7" />
          <circle cx="13.6" cy="7.1" r="2.2" />
          <path d="M2.9 15.9c0-2.3 2-3.9 4.5-3.9s4.5 1.6 4.5 3.9M13 12.2c2.3.1 4.1 1.6 4.1 3.7" />
        </symbol>
        <symbol id="i-shield" viewBox="0 0 20 20">
          <path d="M10 3 15.9 5.4v4.4c0 3.4-2.4 6.2-5.9 7.3-3.5-1.1-5.9-3.9-5.9-7.3V5.4L10 3Z" />
          <path d="m7.7 10 1.7 1.7 3.1-3.4" />
        </symbol>
        <symbol id="i-ledger" viewBox="0 0 20 20">
          <rect x="3.6" y="2.9" width="12.8" height="14.2" rx="2.2" />
          <path d="M6.9 7h6.2M6.9 10h6.2M6.9 13h3.5" />
        </symbol>
        <symbol id="i-settings" viewBox="0 0 20 20">
          <path d="M4 5.4h12M4 10h12M4 14.6h12" />
          <circle cx="7.6" cy="5.4" r="1.9" fill="#fff" />
          <circle cx="12.4" cy="10" r="1.9" fill="#fff" />
          <circle cx="7.6" cy="14.6" r="1.9" fill="#fff" />
        </symbol>
        <symbol id="i-plus" viewBox="0 0 20 20">
          <path d="M10 4.2v11.6M4.2 10h11.6" />
        </symbol>
        <symbol id="i-search" viewBox="0 0 20 20">
          <circle cx="9" cy="9" r="5.6" />
          <path d="m13.3 13.3 3.2 3.2" />
        </symbol>
        <symbol id="i-chev-d" viewBox="0 0 20 20">
          <path d="m6 8.2 4 4 4-4" />
        </symbol>
        <symbol id="i-chev-l" viewBox="0 0 20 20">
          <path d="m12 5 -5 5 5 5" />
        </symbol>
        <symbol id="i-chev-r" viewBox="0 0 20 20">
          <path d="m8 5 5 5-5 5" />
        </symbol>
        <symbol id="i-spark" viewBox="0 0 20 20">
          <path
            d="M10 2.4 11.9 7 16.5 8.9 11.9 10.8 10 15.4 8.1 10.8 3.5 8.9 8.1 7 10 2.4Z"
            fill="currentColor"
            stroke="none"
          />
        </symbol>
        <symbol id="i-mic" viewBox="0 0 20 20">
          <rect x="7.4" y="2.7" width="5.2" height="8.6" rx="2.6" />
          <path d="M4.6 9.4a5.4 5.4 0 0 0 10.8 0M10 14.8v2.5" />
        </symbol>
        <symbol id="i-up" viewBox="0 0 20 20">
          <path d="M10 15.6V4.4M5.4 9 10 4.4 14.6 9" />
        </symbol>
        <symbol id="i-check" viewBox="0 0 20 20">
          <path d="m4.6 10.4 3.4 3.4 7.4-7.6" />
        </symbol>
        <symbol id="i-checkbox" viewBox="0 0 20 20">
          <rect x="3.2" y="3.2" width="13.6" height="13.6" rx="3.4" />
          <path d="m7.2 10.2 2 2 3.6-4" />
        </symbol>
        <symbol id="i-layers" viewBox="0 0 20 20">
          <rect x="3" y="4.2" width="14" height="4" rx="1.7" />
          <rect x="3" y="11.8" width="14" height="4" rx="1.7" />
        </symbol>
        <symbol id="i-bolt" viewBox="0 0 20 20">
          <path d="M11.6 2.6 4.9 11h4l-.4 6.4L15.1 9h-4l.5-6.4Z" />
        </symbol>
        <symbol id="i-cal" viewBox="0 0 20 20">
          <rect x="3.2" y="4.4" width="13.6" height="12.4" rx="2.4" />
          <path d="M3.2 8.2h13.6M7 2.8v3M13 2.8v3" />
        </symbol>
        <symbol id="i-import" viewBox="0 0 20 20">
          <path d="M3.6 6.6 10 3.2l6.4 3.4L10 10 3.6 6.6Z" />
          <path d="m3.6 10.6 6.4 3.4 6.4-3.4" />
        </symbol>
        <symbol id="i-star" viewBox="0 0 20 20">
          <path
            d="M10 2.6 12.3 7.2l5 .7-3.6 3.5.9 5-4.6-2.4L5.4 16.4l.9-5-3.6-3.5 5-.7L10 2.6Z"
            fill="currentColor"
            stroke="none"
          />
        </symbol>
        <symbol id="i-filter" viewBox="0 0 20 20">
          <path d="M3.2 5h13.6M5.6 10h8.8M8.2 15h3.6" />
        </symbol>
        <symbol id="i-bell" viewBox="0 0 20 20">
          <path d="M6 8.6a4 4 0 0 1 8 0c0 3 1.3 4.2 1.3 4.2H4.7S6 11.6 6 8.6Z" />
          <path d="M8.6 15.4a1.6 1.6 0 0 0 2.8 0" />
        </symbol>
        <symbol id="i-help" viewBox="0 0 20 20">
          <circle cx="10" cy="10" r="7.2" />
          <path d="M8.2 8.1a1.9 1.9 0 1 1 2.5 1.8c-.5.2-.7.6-.7 1.1v.4" />
          <circle cx="10" cy="13.6" r=".85" fill="currentColor" stroke="none" />
        </symbol>
        <symbol id="i-sort" viewBox="0 0 20 20">
          <path d="M10 4.6v10.8M6.6 12 10 15.4 13.4 12" />
        </symbol>
        <symbol id="i-back" viewBox="0 0 20 20">
          <path d="M16 10H4.6M9 4.6 3.6 10 9 15.4" />
        </symbol>
        <symbol id="i-git" viewBox="0 0 20 20">
          <circle cx="6" cy="5" r="2.1" />
          <circle cx="6" cy="15" r="2.1" />
          <circle cx="14" cy="10" r="2.1" />
          <path d="M6 7.1v5.8M8.1 5h1.8a2 2 0 0 1 2 2v1" />
        </symbol>
        <symbol id="i-lock" viewBox="0 0 20 20">
          <rect x="4.2" y="8.6" width="11.6" height="8.2" rx="2.2" />
          <path d="M6.9 8.6V6.4a3.1 3.1 0 0 1 6.2 0v2.2" />
        </symbol>
        <symbol id="i-menu" viewBox="0 0 20 20">
          <path d="M3.5 5.5h13M3.5 10h13M3.5 14.5h13" />
        </symbol>
        <symbol id="i-x" viewBox="0 0 20 20">
          <path d="m5 5 10 10M15 5 5 15" />
        </symbol>
        <symbol id="i-kebab" viewBox="0 0 20 20">
          <circle cx="10" cy="4.5" r="1.4" fill="currentColor" stroke="none" />
          <circle cx="10" cy="10" r="1.4" fill="currentColor" stroke="none" />
          <circle cx="10" cy="15.5" r="1.4" fill="currentColor" stroke="none" />
        </symbol>
      </defs>
    </svg>
  );
}

export type IconName =
  | "i-home"
  | "i-run"
  | "i-map"
  | "i-alert"
  | "i-grid"
  | "i-agents"
  | "i-shield"
  | "i-ledger"
  | "i-settings"
  | "i-plus"
  | "i-search"
  | "i-chev-d"
  | "i-chev-l"
  | "i-chev-r"
  | "i-spark"
  | "i-mic"
  | "i-up"
  | "i-check"
  | "i-checkbox"
  | "i-layers"
  | "i-bolt"
  | "i-cal"
  | "i-import"
  | "i-star"
  | "i-filter"
  | "i-bell"
  | "i-help"
  | "i-sort"
  | "i-back"
  | "i-git"
  | "i-lock"
  | "i-menu"
  | "i-x"
  | "i-kebab";

type IconSize = "xs" | "s" | "md";

export interface IconProps {
  name: IconName;
  size?: IconSize;
  className?: string;
  /** Set when the icon carries meaning on its own (no adjacent visible label). */
  label?: string;
}

export function Icon({ name, size = "md", className, label }: IconProps) {
  const sizeClass = size === "md" ? "" : ` ${size}`;
  return (
    <svg
      className={`icon${sizeClass}${className ? ` ${className}` : ""}`}
      aria-hidden={label ? undefined : true}
      role={label ? "img" : undefined}
      aria-label={label}
    >
      <use href={`#${name}`} />
    </svg>
  );
}
