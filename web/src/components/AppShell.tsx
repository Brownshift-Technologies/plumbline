import { useEffect, useRef, useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { DemoBanner } from "./DemoBanner";

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
        <DemoBanner />
        {/* The one <main> on the page. Without it axe reported
            landmark-one-main on every screen, and `region` for every block
            of page content -- all of it sat outside any landmark, so a
            screen-reader user had no way to jump past the nav to the
            actual page. `.content` only restores what the bare <Outlet />
            inherited as a flex child of `.main`. */}
        <main className="content" id="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
