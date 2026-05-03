"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { cn } from "@/lib/api";

function HomeIcon({ active }: { active: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={active ? 2.5 : 2} strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  );
}
function TradesIcon({ active }: { active: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={active ? 2.5 : 2} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
      <polyline points="16 7 22 7 22 13" />
    </svg>
  );
}
function JournalIcon({ active }: { active: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={active ? 2.5 : 2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
    </svg>
  );
}
function SettingsIcon({ active }: { active: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={active ? 2.5 : 2} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
    </svg>
  );
}

function SessionBottomNav({ prefix, sessionId, isSettings, navStyle }: {
  prefix: string; sessionId: string; isSettings: boolean; navStyle: React.CSSProperties;
}) {
  const [hash, setHash] = useState("");
  useEffect(() => {
    const apply = () => setHash(window.location.hash.replace("#", ""));
    apply();
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
  }, []);
  const onSession = !isSettings;
  const isOverview = onSession && (hash === "" || hash === "overview");
  const isDecisions = onSession && hash === "decisions";
  const isActivity = onSession && hash === "activity";
  return (
    <nav className="md:hidden fixed bottom-0 inset-x-0 bottom-nav flex items-start justify-around" style={navStyle}>
      <NavTab
        href={`${prefix}/sessions/${sessionId}#overview`}
        label="Overview"
        active={isOverview}
        icon={<HomeIcon active={isOverview} />}
      />
      <NavTab
        href={`${prefix}/sessions/${sessionId}#decisions`}
        label="Decisions"
        active={isDecisions}
        icon={<JournalIcon active={isDecisions} />}
      />
      <NavTab
        href={`${prefix}/sessions/${sessionId}#activity`}
        label="Trades"
        active={isActivity}
        icon={<TradesIcon active={isActivity} />}
      />
      <NavTab
        href={`${prefix}/sessions/${sessionId}/settings`}
        label="Settings"
        active={isSettings}
        icon={<SettingsIcon active={isSettings} />}
      />
    </nav>
  );
}

function NavTab({ href, label, active, icon }: { href: string; label: string; active: boolean; icon: React.ReactNode }) {
  return (
    <Link
      href={href}
      className={cn(
        "flex flex-col items-center justify-center gap-1 flex-1 pt-2",
        active ? "text-accent-blue" : "text-text-muted"
      )}
      style={{ minHeight: 56, fontSize: 10, fontWeight: active ? 600 : 400 }}
    >
      {icon}
      <span>{label}</span>
    </Link>
  );
}

export function BottomNav() {
  const pathname = usePathname();

  // Hide the global bottom nav on the immersive replay screen (which has its own controls)
  if (pathname.includes("/replay/")) return null;
  // Hide on marketing + login
  if (pathname === "/" || pathname.startsWith("/login")) return null;

  // Determine app prefix and session id
  const isAdmin = pathname.startsWith("/admin");
  const prefix = isAdmin ? "/admin" : "/app";
  // Match either /app/sessions/<id> or /admin/sessions/<id>
  const sessionMatch = pathname.match(/^\/(app|admin)\/sessions\/([^/]+)/);
  const sessionId = sessionMatch ? sessionMatch[2] : null;
  const isSessionPage = sessionId && sessionId !== "new";
  const isSettings = pathname.endsWith("/settings");

  const navStyle = {
    background: "rgba(17,24,39,0.97)",
    backdropFilter: "blur(12px)",
    WebkitBackdropFilter: "blur(12px)",
    borderTop: "1px solid #1e293b",
    zIndex: 9990,
  } as React.CSSProperties;

  // On a session page: show session-aware tabs
  if (isSessionPage) {
    return <SessionBottomNav prefix={prefix} sessionId={sessionId} isSettings={isSettings} navStyle={navStyle} />;
  }

  // On home / create page: just show Home + New Session
  return (
    <nav className="md:hidden fixed bottom-0 inset-x-0 bottom-nav flex items-start justify-around" style={navStyle}>
      <NavTab
        href={prefix}
        label="Sessions"
        active={pathname === prefix}
        icon={<HomeIcon active={pathname === prefix} />}
      />
      <NavTab
        href={`${prefix}/sessions/new`}
        label="New Session"
        active={pathname === `${prefix}/sessions/new`}
        icon={
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth={pathname === `${prefix}/sessions/new` ? 2.5 : 2} strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="16" />
            <line x1="8" y1="12" x2="16" y2="12" />
          </svg>
        }
      />
    </nav>
  );
}
