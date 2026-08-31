import { useEffect, useRef, useState, type RefObject } from "react";
import { useNavigate } from "react-router-dom";
import { Icon } from "./Icon";
import { routes } from "../lib/routes";
import { useCurrentUser } from "../lib/useCurrentUser";

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
  userPhoto,
  planUsed,
  planTotal,
  planResetDays,
}: {
  userPhoto?: string;
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
        {userPhoto ? <img src={userPhoto} alt="" className="avatar-img" /> : userInitials}
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
  userInitials,
  userName,
  workspace = "acme / storefront",
  planUsed = 184,
  planTotal = 500,
  planResetDays = 12,
}: TopBarProps) {
  // The account menu used to render the literals "RK" and "Roger
  // Koranteng" -- prop defaults from the design prototype that nothing
  // ever overrode, because AppShell renders <TopBar> with no user props at
  // all. Every visitor, including every demo visitor, saw somebody else's
  // name and initials in the corner of every screen. The props are kept as
  // overrides (the prototype and the component tests pass them), but the
  // real session is the default now.
  const { data } = useCurrentUser();
  const name = userName ?? data?.name ?? "";
  const initials =
    userInitials ??
    (name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]!.toUpperCase())
      .join("") || "?");

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
        userInitials={initials}
        userName={name}
        userPhoto={data?.photo_url ?? ""}
        planUsed={planUsed}
        planTotal={planTotal}
        planResetDays={planResetDays}
      />
    </div>
  );
}
