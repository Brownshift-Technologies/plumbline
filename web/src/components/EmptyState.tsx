import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

export type EmptyStateVariant = "empty" | "error" | "loading";

export interface EmptyStateProps {
  variant?: EmptyStateVariant;
  icon?: IconName;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}

const TILE_BG: Record<EmptyStateVariant, string> = {
  empty: "var(--brand-w)",
  error: "var(--fail-w)",
  loading: "#F1EFEA",
};

const ICON_COLOR: Record<EmptyStateVariant, string> = {
  empty: "var(--brand)",
  error: "var(--fail)",
  loading: "var(--muted)",
};

const DEFAULT_ICON: Record<EmptyStateVariant, IconName> = {
  empty: "i-checkbox",
  error: "i-alert",
  loading: "i-run",
};

export function EmptyState({
  variant = "empty",
  icon,
  title,
  description,
  actions,
}: EmptyStateProps) {
  return (
    <div
      role={variant === "error" ? "alert" : undefined}
      aria-busy={variant === "loading" ? true : undefined}
      style={{
        padding: "56px 24px",
        textAlign: "center",
        maxWidth: 440,
        margin: "0 auto",
      }}
    >
      <div
        className="tile"
        style={{
          background: TILE_BG[variant],
          margin: "0 auto 14px",
          width: 44,
          height: 44,
        }}
      >
        <span style={{ color: ICON_COLOR[variant] }}>
          <Icon
            name={icon ?? DEFAULT_ICON[variant]}
            className={variant === "loading" ? "spin" : undefined}
            label={variant === "loading" ? "Loading" : undefined}
          />
        </span>
      </div>
      <h3 style={{ fontSize: 17, fontWeight: 600 }}>{title}</h3>
      {description && (
        <p
          style={{
            marginTop: 7,
            fontSize: 14,
            color: "var(--muted)",
            lineHeight: 1.6,
          }}
        >
          {description}
        </p>
      )}
      {actions && (
        <div
          style={{
            marginTop: 16,
            display: "flex",
            gap: 8,
            justifyContent: "center",
          }}
        >
          {actions}
        </div>
      )}
    </div>
  );
}
