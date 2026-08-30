import { useRef, type RefObject } from "react";
import { NavLink } from "react-router-dom";
import { Icon, type IconName } from "./Icon";
import { routes } from "../lib/routes";
import { useFocusTrap } from "../lib/useFocusTrap";

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  count?: number;
  badge?: string;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: routes.home, label: "Home", icon: "i-home", end: true },
  { to: routes.runs, label: "Runs", icon: "i-run", count: 18 },
  { to: routes.surface, label: "Surface map", icon: "i-map" },
  { to: routes.findings, label: "Findings", icon: "i-alert", count: 7 },
  { to: routes.behaviours, label: "Behaviours", icon: "i-grid" },
];

const NAV_ITEMS_2: NavItem[] = [
  { to: routes.agents, label: "Agents", icon: "i-agents", badge: "7" },
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

function NavList({ items }: { items: NavItem[] }) {
  return (
    <>
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          data-tip={item.label}
          aria-label={item.label}
          className={({ isActive }) => (isActive ? "on" : undefined)}
        >
          <Icon name={item.icon} />
          <span className="nav-label">
            {item.label}
            {item.count !== undefined && <span className="count n">{item.count}</span>}
            {item.badge !== undefined && <span className="badge">{item.badge}</span>}
          </span>
        </NavLink>
      ))}
    </>
  );
}

export function Sidebar({
  open,
  onClose,
  triggerRef,
  planUsed = 184,
  planTotal = 500,
  planResetDays = 12,
}: SidebarProps) {
  const asideRef = useRef<HTMLElement>(null);
  useFocusTrap(asideRef, open, onClose, triggerRef);
  const pct = Math.min(100, (planUsed / planTotal) * 100);

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
          <NavList items={NAV_ITEMS} />
          <div className="nav-rule" />
          <NavList items={NAV_ITEMS_2} />
        </nav>
        <div className="side-foot">
          <div className="plan-row">
            Runs this month
            <Icon name="i-help" size="xs" className="faint" label="Plan usage" />
            <span className="v n">
              {planUsed} / {planTotal}
            </span>
          </div>
          <div className="meter">
            <i style={{ width: `${pct}%` }} />
          </div>
          <small>Resets in {planResetDays} days</small>
          <a href="#" className="manage">
            Manage plan
          </a>
        </div>
      </aside>
    </>
  );
}
