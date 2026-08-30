import type { CSSProperties, ReactNode } from "react";

export interface PanelProps {
  title?: ReactNode;
  headerExtra?: ReactNode;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function Panel({ title, headerExtra, children, className, style }: PanelProps) {
  return (
    <div className={className ? `panel ${className}` : "panel"} style={style}>
      {(title || headerExtra) && (
        <header>
          {title && <h3>{title}</h3>}
          <span className="sp" />
          {headerExtra}
        </header>
      )}
      {children}
    </div>
  );
}
