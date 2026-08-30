import { useEffect, useRef, useState, type RefObject } from "react";
import { useNavigate } from "react-router-dom";
import { Icon } from "./Icon";
import { routes } from "../lib/routes";

export interface TopBarProps {
  onOpenNav: () => void;
  navTriggerRef: RefObject<HTMLButtonElement | null>;
  userInitials?: string;
  userName?: string;
  workspace?: string;
  planUsed?: number;
  planTotal?: number;
  planResetDays?: number;
}

function AvatarMenu({
  userInitials,
  userName,
  planUsed,
  planTotal,
  planResetDays,
}: {
  userInitials: string;
  userName: string;
  planUsed: number;
  planTotal: number;
  planResetDays: number;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const pct = Math.min(100, (planUsed / planTotal) * 100);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="avatar-menu-root" ref={rootRef}>
      <button
        type="button"
        className="avatar"
        title={userName}
        aria-label={`Account: ${userName}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {userInitials}
      </button>
      {open && (
        <div className="avatar-menu" role="menu" aria-label="Account">
          <div className="plan-block">
            <div className="plan-row">
              Runs this month
              <span className="v n">
                {planUsed} / {planTotal}
              </span>
            </div>
            <div className="meter">
              <i style={{ width: `${pct}%` }} />
            </div>
            <small>Resets in {planResetDays} days</small>
          </div>
          <button
            type="button"
            role="menuitem"
            className="avatar-menu-item"
            onClick={() => {
              setOpen(false);
              navigate(routes.settings);
            }}
          >
            Settings
          </button>
          <button
            type="button"
            role="menuitem"
            className="avatar-menu-item"
            onClick={() => {
              setOpen(false);
              navigate(routes.signin);
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function TopBar({
  onOpenNav,
  navTriggerRef,
  userInitials = "RK",
  userName = "Roger Koranteng",
  workspace = "acme / storefront",
  planUsed = 184,
  planTotal = 500,
  planResetDays = 12,
}: TopBarProps) {
  return (
    <div className="topbar">
      <button
        type="button"
        className="iconbtn nav-toggle"
        onClick={onOpenNav}
        ref={navTriggerRef}
        aria-label="Open navigation"
        aria-controls="sidebar"
        aria-expanded={false}
      >
        <Icon name="i-menu" label="Open navigation" />
      </button>
      <button type="button" className="ws" aria-label={`Switch workspace, current: ${workspace}`}>
        <span className="sq" aria-hidden="true">
          {workspace.charAt(0).toUpperCase()}
        </span>
        <span className="ws-label">{workspace}</span>
        <Icon name="i-chev-d" size="s" className="faint" />
      </button>
      <span className="sp" />
      <button type="button" className="tb" aria-label="Search">
        <Icon name="i-search" size="s" />
        <span className="tb-label">Search</span>
        <kbd>⌘K</kbd>
      </button>
      <button type="button" className="iconbtn" title="Notifications">
        <Icon name="i-bell" label="Notifications" />
      </button>
      <button type="button" className="iconbtn" title="Help and docs">
        <Icon name="i-help" label="Help and docs" />
      </button>
      <AvatarMenu
        userInitials={userInitials}
        userName={userName}
        planUsed={planUsed}
        planTotal={planTotal}
        planResetDays={planResetDays}
      />
    </div>
  );
}
