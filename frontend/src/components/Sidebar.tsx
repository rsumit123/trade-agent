"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { api, cn } from "@/lib/api";
import type { Session } from "@/lib/types";

interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

export function Sidebar({ isOpen, onClose }: SidebarProps) {
  const pathname = usePathname();
  const [sessions, setSessions] = useState<Session[]>([]);

  useEffect(() => {
    api<Session[]>("/api/sessions").then(setSessions).catch(() => {});
    const interval = setInterval(() => {
      api<Session[]>("/api/sessions").then(setSessions).catch(() => {});
    }, 15000);
    return () => clearInterval(interval);
  }, []);

  const handleNavClick = () => {
    // Close sidebar on mobile when a link is clicked
    if (onClose) onClose();
  };

  const sidebarContent = (
    <aside className="fixed left-0 top-0 bottom-0 bg-bg-secondary border-r border-border flex flex-col z-50" style={{ width: 240 }}>
      {/* Logo */}
      <Link href="/" onClick={handleNavClick} className="flex items-center gap-3 px-5 py-5 border-b border-border hover:bg-bg-card transition-colors">
        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-accent-green to-accent-cyan flex items-center justify-center text-sm font-bold text-bg-primary font-mono">
          &alpha;A
        </div>
        <div>
          <div className="font-semibold text-text-primary text-sm tracking-tight">AlphaAgent</div>
          <div className="text-[11px] text-text-muted">AI Paper Trading</div>
        </div>
      </Link>

      {/* Close button - mobile only */}
      {onClose && (
        <button
          onClick={onClose}
          className="absolute top-4 right-3 md:hidden w-8 h-8 flex items-center justify-center rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-card transition-colors"
        >
          &times;
        </button>
      )}

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3">
        <div className="px-4 mb-1">
          <span className="text-[10px] uppercase tracking-widest text-text-muted font-semibold">Navigation</span>
        </div>
        <NavItem href="/" label="Sessions" icon="grid" active={pathname === "/"} onClick={handleNavClick} />
        <NavItem href="/sessions/new" label="New Session" icon="plus" active={pathname === "/sessions/new"} onClick={handleNavClick} />

        {sessions.length > 0 && (
          <>
            <div className="mx-4 my-3 border-t border-border" />
            <div className="px-4 mb-1">
              <span className="text-[10px] uppercase tracking-widest text-text-muted font-semibold">Sessions</span>
            </div>
            {sessions.map((s) => (
              <NavItem
                key={s.session_id}
                href={`/sessions/${s.session_id}`}
                label={s.display_name}
                icon={s.market === "crypto" ? "bitcoin" : "chart"}
                active={pathname === `/sessions/${s.session_id}`}
                badge={s.is_running ? "live" : undefined}
                onClick={handleNavClick}
              />
            ))}
          </>
        )}
      </nav>

      {/* Footer */}
      <div className="px-5 py-3 border-t border-border">
        <div className="text-[10px] text-text-muted">v2.0 Multi-Session</div>
      </div>
    </aside>
  );

  return (
    <>
      {/* Desktop sidebar: always visible, hidden on mobile */}
      <div className="hidden md:block">
        {sidebarContent}
      </div>

      {/* Mobile sidebar: overlay drawer */}
      {isOpen && (
        <div className="md:hidden fixed inset-0 z-50">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60"
            onClick={onClose}
            style={{ animation: "fade-in 0.2s ease forwards" }}
          />
          {/* Drawer */}
          <div style={{ animation: "slide-in-left 0.25s ease forwards" }}>
            {sidebarContent}
          </div>
        </div>
      )}
    </>
  );
}

/** Mobile top bar with hamburger + logo + context */
export function MobileTopBar({ onMenuClick }: { onMenuClick: () => void }) {
  const pathname = usePathname();

  let pageTitle = "Sessions";
  if (pathname === "/sessions/new") pageTitle = "New Session";
  else if (pathname.startsWith("/sessions/") && pathname.includes("/settings")) pageTitle = "Settings";
  else if (pathname.startsWith("/sessions/")) pageTitle = "Dashboard";

  return (
    <div className="md:hidden sticky top-0 z-40 bg-bg-secondary/95 backdrop-blur border-b border-border px-4 py-3 flex items-center gap-3">
      <button
        onClick={onMenuClick}
        className="w-9 h-9 rounded-lg border border-border hover:border-border-accent hover:bg-bg-card flex items-center justify-center transition-all"
        aria-label="Open menu"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <line x1="3" y1="6" x2="21" y2="6" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <line x1="3" y1="18" x2="21" y2="18" />
        </svg>
      </button>
      <div className="w-7 h-7 rounded-md bg-gradient-to-br from-accent-green to-accent-cyan flex items-center justify-center text-[10px] font-bold text-bg-primary font-mono">
        &alpha;A
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-semibold text-text-primary truncate">{pageTitle}</div>
      </div>
    </div>
  );
}

function NavItem({ href, label, icon, active, badge, onClick }: {
  href: string; label: string; icon: string; active: boolean; badge?: string; onClick?: () => void;
}) {
  return (
    <Link
      href={href}
      onClick={onClick}
      className={cn(
        "flex items-center gap-3 px-4 py-2.5 mx-2 rounded-lg text-sm transition-all",
        active
          ? "bg-bg-card text-text-primary border border-border-accent"
          : "text-text-secondary hover:text-text-primary hover:bg-bg-card/50"
      )}
    >
      <span className="text-base w-5 text-center opacity-60">{iconMap[icon] || ">"}</span>
      <span className="flex-1 truncate">{label}</span>
      {badge === "live" && (
        <span className="w-2 h-2 rounded-full bg-accent-green animate-pulse-dot" />
      )}
    </Link>
  );
}

const iconMap: Record<string, string> = {
  grid: "\u25A6",
  plus: "+",
  chart: "\u25B2",
  bitcoin: "\u20BF",
};
