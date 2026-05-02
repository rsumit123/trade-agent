"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, cn, fmt } from "@/lib/api";
import { SessionCard } from "@/components/SessionCard";
import { Sparkline } from "@/components/Sparkline";
import type { Session } from "@/lib/types";

type Filter = "all" | "live" | "stopped";
type SortBy = "recent" | "return" | "today" | "winrate" | "trades";

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

function EmptyState({ newHref }: { newHref: string }) {
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
            href={`${newHref}?preset=nse-intraday`}
            title="NSE Intraday"
            subtitle="Scalp Indian equities"
            accent="green"
            icon="▲"
          />
          <PresetCard
            href={`${newHref}?preset=crypto`}
            title="Crypto 24/7"
            subtitle="Swing trade BTC & majors"
            accent="blue"
            icon="₿"
          />
        </div>

        <Link
          href={newHref}
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

interface SessionsListProps {
  endpoint?: "user" | "admin";
  newHref?: string;
  sessionHrefBase?: string; // "/app/sessions" or "/admin/sessions"
  title?: string;
  subtitle?: string;
}

export function SessionsListView({
  endpoint = "user",
  newHref = "/app/sessions/new",
  sessionHrefBase: _sessionHrefBase = "/app/sessions",
  title = "Sessions",
  subtitle,
}: SessionsListProps = {}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");
  const [sortBy, setSortBy] = useState<SortBy>("recent");

  const apiPath = endpoint === "admin" ? "/api/admin/sessions" : "/api/sessions";

  const loadSessions = () => {
    api<Session[]>(apiPath)
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
    let arr = sessions;
    if (filter === "live") arr = arr.filter((s) => s.is_running);
    else if (filter === "stopped") arr = arr.filter((s) => !s.is_running);

    const todayIso = new Date().toISOString().slice(0, 10);
    const todayPnl = (s: Session) => {
      const last = (s.daily ?? [])[0];
      return last?.date === todayIso ? (last.daily_pnl ?? 0) : 0;
    };

    const sorted = [...arr];
    if (sortBy === "return") {
      sorted.sort((a, b) => (b.portfolio?.total_return_pct ?? -Infinity) - (a.portfolio?.total_return_pct ?? -Infinity));
    } else if (sortBy === "today") {
      sorted.sort((a, b) => todayPnl(b) - todayPnl(a));
    } else if (sortBy === "winrate") {
      sorted.sort((a, b) => (b.win_rate ?? -1) - (a.win_rate ?? -1));
    } else if (sortBy === "trades") {
      sorted.sort((a, b) => (b.total_trades ?? 0) - (a.total_trades ?? 0));
    }
    return sorted;
  }, [sessions, filter, sortBy]);

  const rollup = useMemo(() => {
    const todayIso = new Date().toISOString().slice(0, 10);
    let totalReturn = 0;
    let totalValue = 0;
    let totalCapital = 0;
    let openPositions = 0;
    let trades = 0;
    let todayPnl = 0;
    let havePortfolio = 0;
    for (const s of sessions) {
      if (s.portfolio) {
        totalReturn += s.portfolio.total_return || 0;
        totalValue += s.portfolio.total_value || 0;
        totalCapital += s.starting_capital || 0;
        openPositions += s.portfolio.open_positions || 0;
        havePortfolio++;
      }
      trades += s.total_trades ?? 0;
      const last = (s.daily ?? [])[0];
      if (last?.date === todayIso) todayPnl += last.daily_pnl ?? 0;
    }
    const returnPct = totalCapital > 0 ? (totalReturn / totalCapital) * 100 : 0;
    return { totalReturn, totalValue, totalCapital, returnPct, openPositions, trades, todayPnl, havePortfolio };
  }, [sessions]);

  const liveCount = sessions.filter((s) => s.is_running).length;
  const stoppedCount = sessions.length - liveCount;

  return (
    <div className="px-4 md:px-8 py-5 md:py-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 mb-5 md:mb-6 animate-fade-in">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl md:text-2xl font-bold tracking-tight">{title}</h1>
          {subtitle && <p className="text-text-muted text-xs mt-0.5">{subtitle}</p>}
          <p className="text-text-muted text-xs md:text-sm mt-0.5">
            {loading ? "Loading..." : sessions.length === 0 ? "No sessions yet" : `${liveCount} live · ${stoppedCount} stopped`}
          </p>
        </div>
        <Link
          href={newHref}
          className="inline-flex items-center justify-center gap-1.5 px-4 rounded-xl bg-accent-blue hover:bg-accent-blue/80 text-white font-semibold text-sm transition-all shadow-lg shadow-accent-blue/20 shrink-0"
          style={{ minHeight: 44 }}
        >
          <span className="text-lg leading-none -mt-0.5">+</span>
          <span className="hidden sm:inline">New Session</span>
          <span className="sm:hidden">New</span>
        </Link>
      </div>

      {/* Aggregate rollup */}
      {!loading && sessions.length > 0 && rollup.havePortfolio > 0 && (
        <PortfolioRollup
          totalReturn={rollup.totalReturn}
          totalValue={rollup.totalValue}
          totalCapital={rollup.totalCapital}
          returnPct={rollup.returnPct}
          openPositions={rollup.openPositions}
          trades={rollup.trades}
          todayPnl={rollup.todayPnl}
          sessions={sessions}
          liveCount={liveCount}
        />
      )}

      {/* Filter pills + sort */}
      {!loading && sessions.length > 0 && (
        <div className="flex items-center gap-2 mb-5 flex-wrap animate-fade-in delay-1">
          <div className="flex items-center gap-1 p-1 rounded-xl border border-border bg-bg-card w-fit">
            <FilterPill label="All" count={sessions.length} active={filter === "all"} onClick={() => setFilter("all")} />
            <FilterPill label="Live" count={liveCount} active={filter === "live"} onClick={() => setFilter("live")} accent="green" />
            <FilterPill label="Stopped" count={stoppedCount} active={filter === "stopped"} onClick={() => setFilter("stopped")} />
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <label className="text-[10px] uppercase tracking-wider font-semibold text-text-muted">Sort</label>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortBy)}
              className="text-xs rounded-lg px-2.5 py-1 font-medium"
              style={{ background: "#0a0e17", border: "1px solid #1e293b", color: "#cbd5e1", minHeight: 32 }}
            >
              <option value="recent">Recent</option>
              <option value="return">Top Return %</option>
              <option value="today">Today&apos;s P&L</option>
              <option value="winrate">Win Rate</option>
              <option value="trades">Most Trades</option>
            </select>
          </div>
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
        <EmptyState newHref={newHref} />
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

function PortfolioRollup({
  totalReturn,
  totalValue,
  totalCapital,
  returnPct,
  openPositions,
  trades,
  todayPnl,
  sessions,
  liveCount,
}: {
  totalReturn: number;
  totalValue: number;
  totalCapital: number;
  returnPct: number;
  openPositions: number;
  trades: number;
  todayPnl: number;
  sessions: Session[];
  liveCount: number;
}) {
  const isProfit = totalReturn >= 0;
  const accent = isProfit ? "#22c55e" : "#ef4444";

  // Pick a single currency symbol (most common across sessions)
  const symCounts: Record<string, number> = {};
  sessions.forEach((s) => {
    const sym = s.currency_symbol || "$";
    symCounts[sym] = (symCounts[sym] || 0) + 1;
  });
  const sym = Object.entries(symCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "$";

  // Build combined daily total_value series for the watermark sparkline
  const byDate: Record<string, number> = {};
  sessions.forEach((s) => {
    (s.daily ?? []).forEach((d) => {
      if (d.total_value != null) byDate[d.date] = (byDate[d.date] || 0) + d.total_value;
    });
  });
  const series = Object.keys(byDate).sort().map((d) => byDate[d]);

  return (
    <div
      className="relative rounded-2xl p-5 md:p-6 mb-6 overflow-hidden animate-fade-in"
      style={{
        // Layered: deep base, ambient profit/loss radial glow, faint dotted texture
        background: `
          radial-gradient(ellipse 80% 60% at 100% 0%, ${accent}26 0%, transparent 60%),
          radial-gradient(ellipse 60% 40% at 0% 100%, ${accent}14 0%, transparent 60%),
          linear-gradient(135deg, #0c1424 0%, #0a0e17 100%)
        `,
        border: `1px solid ${accent}33`,
        boxShadow: `0 1px 0 0 ${accent}22 inset, 0 24px 48px -24px ${accent}33, 0 0 0 1px rgba(255,255,255,0.02) inset`,
      }}
    >
      {/* Subtle grid texture */}
      <div
        className="absolute inset-0 pointer-events-none opacity-[0.04]"
        style={{
          backgroundImage:
            "linear-gradient(to right, #ffffff 1px, transparent 1px), linear-gradient(to bottom, #ffffff 1px, transparent 1px)",
          backgroundSize: "32px 32px",
        }}
      />

      {/* Watermark sparkline behind content */}
      {series.length >= 2 && (
        <div
          className="absolute inset-x-0 bottom-0 pointer-events-none"
          style={{ height: "70%", opacity: 0.18 }}
        >
          <div className="absolute inset-0">
            <Sparkline
              values={series}
              width={800}
              height={200}
              positive={isProfit}
              fill
              strokeWidth={2}
              responsive
            />
          </div>
        </div>
      )}

      <div className="relative">
        {/* Top brand row */}
        <div className="flex items-center justify-between mb-4 md:mb-5">
          <div className="flex items-center gap-2">
            <span
              className="inline-block rounded-full"
              style={{
                width: 6,
                height: 6,
                background: accent,
                boxShadow: `0 0 0 4px ${accent}26`,
              }}
            />
            <span
              className="text-[10px] uppercase font-bold tracking-[0.2em]"
              style={{ color: "#cbd5e1", letterSpacing: "0.18em" }}
            >
              PORTFOLIO
            </span>
            <span className="text-[10px] uppercase tracking-wider text-text-muted">
              · {sessions.length} session{sessions.length === 1 ? "" : "s"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {liveCount > 0 && (
              <div
                className="flex items-center gap-1.5 px-2 py-1 rounded-md"
                style={{
                  background: "rgba(34,197,94,0.1)",
                  border: "1px solid rgba(34,197,94,0.25)",
                }}
              >
                <span
                  className="inline-block rounded-full animate-pulse-dot"
                  style={{
                    width: 6,
                    height: 6,
                    background: "#22c55e",
                    boxShadow: "0 0 0 3px rgba(34,197,94,0.2)",
                  }}
                />
                <span className="text-[10px] font-mono font-semibold text-accent-green">
                  {liveCount} LIVE
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Hero number — bigger, no monospace for warmth, with tabular-nums for alignment */}
        <div className="mb-1">
          <div
            className="font-bold tracking-tight leading-none flex items-baseline gap-2"
            style={{
              fontSize: 44,
              color: accent,
              fontVariantNumeric: "tabular-nums",
              fontFeatureSettings: '"tnum"',
              textShadow: `0 0 32px ${accent}33`,
            }}
          >
            <span style={{ fontSize: 22, lineHeight: 1 }}>{isProfit ? "▲" : "▼"}</span>
            <span>{isProfit ? "+" : ""}{fmt(totalReturn, sym, undefined, 0)}</span>
            <span
              className="font-semibold"
              style={{
                fontSize: 18,
                color: isProfit ? "rgba(34,197,94,0.7)" : "rgba(239,68,68,0.7)",
              }}
            >
              {returnPct >= 0 ? "+" : ""}{returnPct.toFixed(2)}%
            </span>
          </div>
        </div>
        <div className="text-[11px] uppercase tracking-wider text-text-muted mb-5">
          Total realized + unrealized P&amp;L across all agents
        </div>

        {/* KPI tiles */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 md:gap-3">
          <Tile
            label="Equity"
            value={fmt(totalValue, sym, undefined, 0)}
            sub={`Capital ${fmt(totalCapital, sym, undefined, 0)}`}
          />
          <Tile
            label="Today"
            value={`${todayPnl >= 0 ? "+" : ""}${fmt(todayPnl, sym, undefined, 0)}`}
            valueColor={todayPnl >= 0 ? "#22c55e" : "#ef4444"}
            sub={todayPnl === 0 ? "No moves yet" : todayPnl >= 0 ? "Profit today" : "Loss today"}
          />
          <Tile label="Open" value={String(openPositions)} sub={`${liveCount} agent${liveCount === 1 ? "" : "s"} live`} />
          <Tile label="Trades" value={String(trades)} sub="lifetime closed" />
        </div>
      </div>
    </div>
  );
}

function Tile({
  label,
  value,
  sub,
  valueColor,
}: {
  label: string;
  value: string;
  sub?: string;
  valueColor?: string;
}) {
  return (
    <div
      className="rounded-xl px-3 py-2.5"
      style={{
        background: "rgba(0,0,0,0.25)",
        border: "1px solid rgba(255,255,255,0.04)",
        backdropFilter: "blur(6px)",
      }}
    >
      <div className="text-[9px] uppercase tracking-[0.15em] font-semibold text-text-muted mb-1">
        {label}
      </div>
      <div
        className="font-mono font-semibold leading-none"
        style={{ fontSize: 16, color: valueColor || "#f1f5f9" }}
      >
        {value}
      </div>
      {sub && (
        <div className="text-[10px] text-text-muted mt-1 truncate">{sub}</div>
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
