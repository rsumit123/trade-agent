"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, cn } from "@/lib/api";
import { SessionCard } from "@/components/SessionCard";
import type { Session } from "@/lib/types";

type Filter = "all" | "live" | "stopped";

function SkeletonCard() {
  return (
    <div className="bg-bg-card border border-border rounded-2xl p-5 space-y-4">
      <div className="flex items-start justify-between">
        <div className="space-y-2 flex-1">
          <div className="skeleton h-4 w-32" />
          <div className="skeleton h-3 w-20" />
        </div>
        <div className="skeleton h-9 w-9 rounded-lg" />
      </div>
      <div className="flex items-end justify-between">
        <div className="space-y-2 flex-1">
          <div className="skeleton h-3 w-16" />
          <div className="skeleton h-7 w-28" />
          <div className="skeleton h-3 w-20" />
        </div>
        <div className="skeleton h-8 w-20 rounded" />
      </div>
      <div className="skeleton h-11 w-full rounded-xl" />
    </div>
  );
}

function EmptyState() {
  return (
    <div className="relative overflow-hidden rounded-3xl border border-border bg-bg-card">
      {/* Ambient gradient */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: "radial-gradient(ellipse at top, rgba(34,197,94,0.08), transparent 60%), radial-gradient(ellipse at bottom right, rgba(59,130,246,0.06), transparent 60%)",
        }}
      />
      <div className="relative px-6 py-12 text-center">
        {/* Glyph */}
        <div className="mx-auto mb-5 flex items-center justify-center rounded-2xl border border-border/60"
          style={{ width: 64, height: 64, background: "linear-gradient(135deg, #151d2e, #0a0e17)" }}>
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
            <polyline points="16 7 22 7 22 13" />
          </svg>
        </div>
        <h2 className="text-lg font-semibold text-text-primary mb-1.5">No sessions yet</h2>
        <p className="text-text-muted text-sm mb-6 max-w-xs mx-auto">
          Spin up an AI agent to paper-trade a market. Each session learns independently.
        </p>

        {/* Quick-start presets */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-md mx-auto mb-5">
          <PresetCard
            href="/sessions/new?preset=nse-intraday"
            title="NSE Intraday"
            subtitle="Scalp Indian equities"
            accent="green"
            icon="▲"
          />
          <PresetCard
            href="/sessions/new?preset=crypto"
            title="Crypto 24/7"
            subtitle="Swing trade BTC & majors"
            accent="blue"
            icon="₿"
          />
        </div>

        <Link
          href="/sessions/new"
          className="inline-flex items-center gap-2 text-xs text-text-muted hover:text-text-primary transition-colors"
        >
          or customize from scratch →
        </Link>
      </div>
    </div>
  );
}

function PresetCard({ href, title, subtitle, accent, icon }: {
  href: string; title: string; subtitle: string; accent: "green" | "blue"; icon: string;
}) {
  const borderColor = accent === "green" ? "rgba(34,197,94,0.3)" : "rgba(59,130,246,0.3)";
  const iconColor = accent === "green" ? "#22c55e" : "#3b82f6";
  const iconBg = accent === "green" ? "rgba(34,197,94,0.12)" : "rgba(59,130,246,0.12)";
  return (
    <Link
      href={href}
      className="group flex items-center gap-3 p-3.5 rounded-xl border text-left transition-all hover:bg-bg-card-hover"
      style={{ borderColor, minHeight: 64 }}
    >
      <div
        className="flex items-center justify-center rounded-lg font-mono font-bold shrink-0"
        style={{ width: 40, height: 40, background: iconBg, color: iconColor, fontSize: 18 }}
      >
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-text-primary truncate">{title}</div>
        <div className="text-xs text-text-muted truncate">{subtitle}</div>
      </div>
      <span className="text-text-muted/50 group-hover:text-text-primary transition-colors">→</span>
    </Link>
  );
}

export default function HomePage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");

  const loadSessions = () => {
    api<Session[]>("/api/sessions")
      .then((data) => { setSessions(data); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    loadSessions();
    const interval = setInterval(loadSessions, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") loadSessions();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  const filtered = useMemo(() => {
    if (filter === "live") return sessions.filter((s) => s.is_running);
    if (filter === "stopped") return sessions.filter((s) => !s.is_running);
    return sessions;
  }, [sessions, filter]);

  const liveCount = sessions.filter((s) => s.is_running).length;
  const stoppedCount = sessions.length - liveCount;

  return (
    <div className="px-4 md:px-8 py-5 md:py-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 mb-5 md:mb-6 animate-fade-in">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl md:text-2xl font-bold tracking-tight">Sessions</h1>
          <p className="text-text-muted text-xs md:text-sm mt-0.5">
            {loading ? "Loading..." : sessions.length === 0 ? "No sessions yet" : `${liveCount} live · ${stoppedCount} stopped`}
          </p>
        </div>
        <Link
          href="/sessions/new"
          className="inline-flex items-center justify-center gap-1.5 px-4 rounded-xl bg-accent-blue hover:bg-accent-blue/80 text-white font-semibold text-sm transition-all shadow-lg shadow-accent-blue/20 shrink-0"
          style={{ minHeight: 44 }}
        >
          <span className="text-lg leading-none -mt-0.5">+</span>
          <span className="hidden sm:inline">New Session</span>
          <span className="sm:hidden">New</span>
        </Link>
      </div>

      {/* Filter pills */}
      {!loading && sessions.length > 0 && (
        <div
          className="flex items-center gap-1 mb-5 p-1 rounded-xl border border-border bg-bg-card w-fit animate-fade-in delay-1"
        >
          <FilterPill label="All" count={sessions.length} active={filter === "all"} onClick={() => setFilter("all")} />
          <FilterPill label="Live" count={liveCount} active={filter === "live"} onClick={() => setFilter("live")} accent="green" />
          <FilterPill label="Stopped" count={stoppedCount} active={filter === "stopped"} onClick={() => setFilter("stopped")} />
        </div>
      )}

      {/* Session Grid */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-5">
          {[1, 2, 3].map((i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : sessions.length === 0 ? (
        <EmptyState />
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-text-muted text-sm">
          No {filter} sessions.{" "}
          <button onClick={() => setFilter("all")} className="text-accent-blue hover:underline">Show all</button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-5">
          {filtered.map((s, i) => (
            <div key={s.session_id} className={`animate-fade-in delay-${Math.min(i + 1, 5)}`}>
              <SessionCard session={s} onDelete={loadSessions} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function FilterPill({ label, count, active, onClick, accent }: {
  label: string; count: number; active: boolean; onClick: () => void; accent?: "green";
}) {
  const activeColor = accent === "green" ? "text-accent-green" : "text-text-primary";
  return (
    <button
      onClick={onClick}
      className={cn(
        "px-3 rounded-lg text-xs font-semibold transition-all inline-flex items-center gap-1.5",
        active ? cn("bg-bg-secondary", activeColor) : "text-text-muted hover:text-text-primary"
      )}
      style={{ minHeight: 36 }}
    >
      <span>{label}</span>
      <span className={cn("font-mono text-[10px] px-1.5 py-0.5 rounded", active ? "bg-bg-primary/50" : "bg-bg-secondary/50")}>
        {count}
      </span>
    </button>
  );
}
