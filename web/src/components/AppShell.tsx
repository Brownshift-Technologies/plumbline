import { useEffect, useRef, useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

export function AppShell() {
  const [navOpen, setNavOpen] = useState(false);
  const navTriggerRef = useRef<HTMLButtonElement>(null);

  // Lock page scroll while the slide-over nav is open.
  useEffect(() => {
    if (!navOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [navOpen]);

  return (
    <div className="app">
      <Sidebar
        open={navOpen}
        onClose={() => setNavOpen(false)}
        triggerRef={navTriggerRef}
      />
      <div className="main">
        <TopBar onOpenNav={() => setNavOpen(true)} navTriggerRef={navTriggerRef} />
        <Outlet />
      </div>
    </div>
  );
}
