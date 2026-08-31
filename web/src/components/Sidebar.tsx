import { useRef, type RefObject } from "react";
import { NavLink } from "react-router-dom";
import { Icon, type IconName } from "./Icon";
import { routes } from "../lib/routes";
import { useFocusTrap } from "../lib/useFocusTrap";
import { useAsync } from "../lib/useAsync";
import { api } from "../lib/api";
import type { BillingInfo, SummaryResponse } from "../lib/types";

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  countKey?: keyof SummaryResponse;
  end?: boolean;
}

// Counts come from `GET /api/summary`, not from here. They used to be
// literals lifted from the design prototype -- 18 runs, 7 findings, a "7"
// badge on Agents -- so every visitor saw the prototype's numbers instead of
// their own workspace's, including a brand-new sandbox with nothing in it.
// The agent count was wrong even as a constant: there are eleven.
const NAV_ITEMS: NavItem[] = [
  { to: routes.home, label: "Home", icon: "i-home", end: true },
  { to: routes.runs, label: "Runs", icon: "i-run", countKey: "runs" },
  { to: routes.surface, label: "Surface map", icon: "i-map" },
  { to: routes.findings, label: "Findings", icon: "i-alert", countKey: "findings" },
  { to: routes.behaviours, label: "Behaviours", icon: "i-grid", countKey: "behaviours" },
];

const NAV_ITEMS_2: NavItem[] = [
  { to: routes.agents, label: "Agents", icon: "i-agents", countKey: "agents" },
  { to: routes.policy, label: "Policy & gates", icon: "i-shield" },
  { to: routes.ledger, label: "Audit ledger", icon: "i-ledger" },
  { to: routes.settings, label: "Settings", icon: "i-settings" },
];

export interface SidebarProps {
  open: boolean;
  onClose: () => void;
  triggerRef: RefObject<HTMLElement | null>;
  planUsed?: number;
  planTotal?: number;
  planResetDays?: number;
}

function Logo() {
  return (
    <div className="logo">
      <svg width="20" height="24" viewBox="0 0 20 24" aria-hidden="true">
        <line
          x1="10"
          y1="1"
          x2="10"
          y2="13"
          stroke="#1F4FD8"
          strokeWidth="1.8"
          strokeLinecap="round"
        />
        <path d="M10 13 L15.5 18 L10 23 L4.5 18 Z" fill="#1F4FD8" />
      </svg>
      <b>Plumbline</b>
    </div>
  );
}

function NavList({ items, counts }: { items: NavItem[]; counts?: SummaryResponse }) {
  return (
    <>
      {items.map((item) => {
        const value = item.countKey && counts ? counts[item.countKey] : undefined;
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            data-tip={item.label}
            aria-label={
              value === undefined ? item.label : `${item.label}, ${value}`
            }
            className={({ isActive }) => (isActive ? "on" : undefined)}
          >
            <Icon name={item.icon} />
            {/* The label and the count are SIBLINGS, both direct flex children
                of the link. They used to be nested together inside
                .nav-label, so the count's `margin-left:auto` had no flex
                parent to push against and rendered as "Runs18", jammed
                against the word. */}
            <span className="nav-label">{item.label}</span>
            {value !== undefined && value > 0 && (
              <span className="count n" aria-hidden="true">
                {value > 99 ? "99+" : value}
              </span>
            )}
          </NavLink>
        );
      })}
    </>
  );
}

export function Sidebar({
  open,
  onClose,
  triggerRef,
  planUsed,
  planTotal,
  planResetDays,
}: SidebarProps) {
  const asideRef = useRef<HTMLElement>(null);
  useFocusTrap(asideRef, open, onClose, triggerRef);

  // One request for the nav counts. The sidebar renders on every screen and
  // AppShell keeps it mounted across route changes, so this runs once per
  // session rather than once per navigation.
  const summary = useAsync<SummaryResponse>(() => api.get<SummaryResponse>("/summary"), []);
  const counts = summary.status === "success" ? summary.data ?? undefined : undefined;

  // The plan meter read "184 / 500 runs, resets in 12 days" for everyone --
  // prop defaults from the prototype that nothing overrode. The props are
  // kept as overrides (the prototype and the component tests pass them), but
  // the workspace's own billing row is the default now.
  const billing = useAsync<BillingInfo>(() => api.get<BillingInfo>("/billing"), []);
  const live = billing.status === "success" ? billing.data ?? undefined : undefined;
  const used = planUsed ?? live?.runs_used ?? 0;
  const total = planTotal ?? live?.run_limit ?? 0;
  const resetDays =
    planResetDays ??
    (live?.renews_at
      ? Math.max(0, Math.ceil((live.renews_at * 1000 - Date.now()) / 86_400_000))
      : undefined);
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;

  return (
    <>
      <div
        className="side-backdrop"
        hidden={!open}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        id="sidebar"
        className={open ? "side side--open" : "side"}
        ref={asideRef}
        role={open ? "dialog" : undefined}
        aria-modal={open ? true : undefined}
        aria-label={open ? "Navigation" : undefined}
        tabIndex={-1}
      >
        <div className="side-drawer-head">
          <Logo />
          <button
            type="button"
            className="iconbtn side-close"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <Icon name="i-x" label="Close navigation" />
          </button>
        </div>
        <button className="create" type="button">
          <Icon name="i-plus" size="s" />
          <span className="nav-label">New run</span>
        </button>
        <nav className="nav" aria-label="Primary">
          <NavList items={NAV_ITEMS} counts={counts} />
          <div className="nav-rule" />
          <NavList items={NAV_ITEMS_2} counts={counts} />
        </nav>
        <div className="side-foot">
          <div className="plan-row">
            Runs this month
            <Icon name="i-help" size="xs" className="faint" label="Plan usage" />
            <span className="v n">
              {used} / {total}
            </span>
          </div>
          <div className="meter">
            <i style={{ width: `${pct}%` }} />
          </div>
          <small>
            {resetDays === undefined
              ? "\u00a0"
              : resetDays === 0
                ? "Resets today"
                : `Resets in ${resetDays} day${resetDays === 1 ? "" : "s"}`}
          </small>
          <a href="#" className="manage">
            Manage plan
          </a>
        </div>
      </aside>
    </>
  );
}
