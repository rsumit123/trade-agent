"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { useUser, signOut } from "@/lib/auth";
import type { Session } from "@/lib/types";

interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

export function Sidebar({ isOpen, onClose }: SidebarProps) {
  const pathname = usePathname() || "";
  const [sessions, setSessions] = useState<Session[]>([]);
  const { user } = useUser();
  const isAdmin = pathname.startsWith("/admin");
  const prefix: "/app" | "/admin" = isAdmin ? "/admin" : "/app";
  const apiPath = isAdmin ? "/api/admin/sessions" : "/api/sessions";

  const fetchSessions = () => api<Session[]>(apiPath).then(setSessions).catch(() => {});

  useEffect(() => {
    if (!user) return;
    fetchSessions();
    const interval = setInterval(fetchSessions, 15000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, apiPath]);

  // Refetch when user returns to the tab
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") fetchSessions();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  const handleNavClick = () => {
    if (onClose) onClose();
  };

  const sidebarContent = (
    <aside
      className="fixed left-0 top-0 bottom-0 flex flex-col"
      style={{ width: 240, background: "#111827", borderRight: "1px solid #1e293b", zIndex: 9998 }}
    >
      {/* Logo */}
      <Link href={prefix} onClick={handleNavClick} className="flex items-center gap-3 hover:opacity-80 transition-opacity" style={{ padding: "18px 20px", borderBottom: "1px solid #1e293b" }}>
        <div
          className="rounded-lg flex items-center justify-center font-bold font-mono"
          style={{ width: 40, height: 40, background: "linear-gradient(135deg, #22c55e, #06b6d4)", color: "#0a0e17", fontSize: 14 }}
        >
          &alpha;A
        </div>
        <div>
          <div className="font-semibold" style={{ color: "#e2e8f0", fontSize: 14 }}>AlphaAgent</div>
          <div style={{ color: "#64748b", fontSize: 11 }}>AI Paper Trading</div>
        </div>
      </Link>

      {/* Close button - mobile only */}
      {onClose && (
        <button
          onClick={onClose}
          className="absolute md:hidden flex items-center justify-center rounded-lg transition-colors"
          style={{ top: 12, right: 12, width: 44, height: 44, color: "#94a3b8", fontSize: 28, background: "transparent", border: "none" }}
        >
          &times;
        </button>
      )}

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto" style={{ paddingTop: 12 }}>
        <div style={{ padding: "0 16px", marginBottom: 8 }}>
          <span style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 600 }}>Navigation</span>
        </div>
        <NavItem href={prefix} label={isAdmin ? "Admin · Sessions" : "Sessions"} icon="grid" active={pathname === prefix} onClick={handleNavClick} />
        <NavItem href={`${prefix}/sessions/new`} label="New Session" icon="plus" active={pathname === `${prefix}/sessions/new`} onClick={handleNavClick} />
        {user?.is_admin && !isAdmin && (
          <NavItem href="/admin" label="Admin Panel" icon="grid" active={false} onClick={handleNavClick} />
        )}

        {sessions.length > 0 && (
          <>
            <div style={{ margin: "12px 16px", borderTop: "1px solid #1e293b" }} />
            <div style={{ padding: "0 16px", marginBottom: 8 }}>
              <span style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 600 }}>Sessions</span>
            </div>
            {sessions.map((s) => (
              <NavItem
                key={s.session_id}
                href={`${prefix}/sessions/${s.session_id}`}
                label={s.display_name}
                icon={s.market === "crypto" ? "bitcoin" : "chart"}
                active={pathname === `${prefix}/sessions/${s.session_id}`}
                badge={s.is_running ? "live" : undefined}
                onClick={handleNavClick}
              />
            ))}
          </>
        )}
      </nav>

      {/* Footer — user identity + sign out */}
      <div style={{ padding: "12px 12px", borderTop: "1px solid #1e293b" }}>
        {user ? (
          <div className="flex items-center gap-2 rounded-lg p-2" style={{ background: "#0a0e17" }}>
            {user.picture ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={user.picture} alt="" style={{ width: 28, height: 28, borderRadius: "50%" }} />
            ) : (
              <div style={{ width: 28, height: 28, borderRadius: "50%", background: "#1e293b" }} />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate text-[11px] font-semibold text-text-primary">{user.name || user.email}</div>
              <div className="truncate text-[10px] text-text-muted">{user.is_admin ? "Admin" : user.email}</div>
            </div>
            <button
              onClick={() => signOut()}
              className="text-[10px] uppercase tracking-wider font-semibold px-1.5 py-1 rounded text-text-muted hover:text-accent-red hover:bg-accent-red/10"
              style={{ minHeight: 28 }}
              aria-label="Sign out"
            >
              ⤴
            </button>
          </div>
        ) : (
          <div style={{ fontSize: 10, color: "#64748b" }}>Not signed in</div>
        )}
      </div>
    </aside>
  );

  return (
    <>
      {/* Desktop sidebar: always visible */}
      <div className="hidden md:block">
        {sidebarContent}
      </div>

      {/* Mobile sidebar: overlay drawer */}
      {isOpen && (
        <div className="md:hidden fixed inset-0" style={{ zIndex: 9998 }}>
          {/* Backdrop */}
          <div
            className="absolute inset-0"
            onClick={onClose}
            style={{ background: "rgba(0,0,0,0.75)", animation: "fade-in 0.2s ease forwards" }}
          />
          {/* Drawer */}
          <div className="relative" style={{ animation: "slide-in-left 0.25s ease forwards", zIndex: 9999 }}>
            {sidebarContent}
          </div>
        </div>
      )}
    </>
  );
}

/** Mobile top bar — no hamburger, bottom nav handles navigation */
export function MobileTopBar() {
  const pathname = usePathname();

  let pageTitle = "Sessions";
  if (pathname.startsWith("/admin")) pageTitle = "Admin";
  if (pathname.endsWith("/sessions/new")) pageTitle = "New Session";
  else if (pathname.includes("/sessions/") && pathname.includes("/settings")) pageTitle = "Settings";
  else if (pathname.includes("/sessions/") && !pathname.endsWith("/sessions/new")) pageTitle = "Dashboard";

  return (
    <div
      className="md:hidden sticky top-0 flex items-center gap-3"
      style={{
        zIndex: 40,
        background: "rgba(17,24,39,0.97)",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
        borderBottom: "1px solid #1e293b",
        padding: "10px 16px",
      }}
    >
      <div
        className="rounded-md flex items-center justify-center font-bold font-mono"
        style={{ width: 34, height: 34, background: "linear-gradient(135deg, #22c55e, #06b6d4)", color: "#0a0e17", fontSize: 12, flexShrink: 0 }}
      >
        &alpha;A
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-semibold truncate" style={{ color: "#e2e8f0", fontSize: 16 }}>{pageTitle}</div>
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
      className="flex items-center gap-3 rounded-lg transition-all"
      style={{
        padding: "14px 16px",
        margin: "2px 8px",
        minHeight: 48,
        fontSize: 14,
        color: active ? "#e2e8f0" : "#94a3b8",
        background: active ? "#151d2e" : "transparent",
        border: active ? "1px solid #2d3a4f" : "1px solid transparent",
        textDecoration: "none",
      }}
    >
      <span style={{ width: 20, textAlign: "center", opacity: 0.6, fontSize: 16 }}>{iconMap[icon] || ">"}</span>
      <span className="flex-1 truncate">{label}</span>
      {badge === "live" && (
        <span className="animate-pulse-dot" style={{ width: 10, height: 10, borderRadius: "50%", background: "#22c55e", display: "inline-block" }} />
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
