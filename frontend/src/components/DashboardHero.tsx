"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, fmt, pct, cn } from "@/lib/api";
import { useAppPrefix } from "@/lib/paths";
import { useToast } from "@/components/Toast";
import { Sparkline } from "@/components/Sparkline";
import type { AgentStatus, PortfolioSummary, Performance, SessionConfig, DailyPerformance } from "@/lib/types";

interface Props {
  sessionId: string;
  config: SessionConfig | null;
  portfolio: PortfolioSummary | null;
  perf: Performance | null;
  agentStatus: AgentStatus;
  isBacktest: boolean;
  onStatusChange: () => void;
  lastUpdated: Date | null;
}

export function DashboardHero({
  sessionId, config, portfolio, perf, agentStatus, isBacktest, onStatusChange, lastUpdated,
}: Props) {
  const toast = useToast();
  const prefix = useAppPrefix();
  const [daily, setDaily] = useState<DailyPerformance[]>([]);
  const [toggling, setToggling] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const prevPnlRef = useRef<number | null>(null);

  const sym = config?.currency_symbol || "$";
  const locale = config?.locale || "en-US";

  // Fetch daily performance for sparkline
  useEffect(() => {
    const load = () => {
      api<DailyPerformance[]>(`/api/performance/daily?session=${sessionId}&limit=14`)
        .then((d) => { if (Array.isArray(d)) setDaily(d); })
        .catch(() => {});
    };
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [sessionId]);

  // Flash P&L when it changes
  useEffect(() => {
    if (!portfolio) return;
    const cur = portfolio.today_pnl;
    const prev = prevPnlRef.current;
    if (prev != null && cur !== prev) {
      setFlash(cur > prev ? "up" : "down");
      const t = setTimeout(() => setFlash(null), 800);
      return () => clearTimeout(t);
    }
    prevPnlRef.current = cur;
  }, [portfolio]);

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  const handleToggle = async () => {
    if (toggling) return;
    setToggling(true);
    try {
      if (agentStatus.running) {
        await api(`/api/agent/stop/${sessionId}`, { method: "POST" });
        toast.success("Agent stopped");
      } else {
        await api(`/api/agent/start/${sessionId}`, { method: "POST" });
        toast.success("Agent started");
      }
      setTimeout(onStatusChange, 1500);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setToggling(false);
    }
  };

  const sparkValues = daily
    .slice()
    .reverse()
    .map((d) => d.total_value)
    .filter((v): v is number => v != null);

  const totalReturn = portfolio?.total_return ?? 0;
  const isProfit = totalReturn >= 0;
  const todayPnl = portfolio?.today_pnl ?? 0;
  const todayUp = todayPnl >= 0;

  const modelName = config?.llm_model?.includes("/")
    ? config.llm_model.split("/").pop()
    : config?.llm_model;

  const updatedAgo = lastUpdated
    ? Math.max(0, Math.floor((Date.now() - lastUpdated.getTime()) / 1000))
    : null;
  const updatedLabel = updatedAgo == null
    ? "—"
    : updatedAgo < 5 ? "just now"
    : updatedAgo < 60 ? `${updatedAgo}s ago`
    : `${Math.floor(updatedAgo / 60)}m ago`;

  const marketLabel = config?.market_id || "nse";
  const marketColor =
    marketLabel === "crypto"
      ? "bg-accent-blue/15 text-accent-blue border-accent-blue/30"
      : "bg-accent-green/15 text-accent-green border-accent-green/30";

  return (
    <div className="relative overflow-hidden rounded-2xl border border-border bg-bg-card mb-4 md:mb-5">
      {/* Ambient color tint */}
      <div
        className="absolute inset-0 pointer-events-none opacity-70"
        style={{
          background: isProfit
            ? "radial-gradient(ellipse at top, rgba(34,197,94,0.1), transparent 60%)"
            : "radial-gradient(ellipse at top, rgba(239,68,68,0.1), transparent 60%)",
        }}
      />
      {flash && (
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background: flash === "up" ? "rgba(34,197,94,0.08)" : "rgba(239,68,68,0.08)",
            animation: "fade-in 0.3s ease forwards",
          }}
        />
      )}

      <div className="relative p-4 md:p-5">
        {/* Row 1: live status + name + overflow */}
        <div className="flex items-start justify-between gap-2 mb-4">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <span
              className={cn("rounded-full shrink-0", agentStatus.running && "animate-pulse-dot")}
              style={{
                width: 8, height: 8,
                background: agentStatus.running ? "#22c55e" : "rgba(100,116,139,0.5)",
                boxShadow: agentStatus.running ? "0 0 0 4px rgba(34,197,94,0.15)" : "none",
              }}
            />
            <span className="text-[11px] uppercase tracking-wider font-semibold text-text-muted shrink-0">
              {isBacktest ? "Backtest" : agentStatus.running ? "Live" : "Stopped"}
            </span>
            <span className="text-text-muted/40">·</span>
            <h1 className="text-sm md:text-base font-semibold text-text-primary truncate">
              {config?.session_name || sessionId}
            </h1>
          </div>
          <div className="relative shrink-0" ref={menuRef}>
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="flex items-center justify-center rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-secondary transition-all"
              style={{ width: 36, height: 36 }}
              aria-label="More options"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                <circle cx="12" cy="5" r="1.5" />
                <circle cx="12" cy="12" r="1.5" />
                <circle cx="12" cy="19" r="1.5" />
              </svg>
            </button>
            {menuOpen && (
              <div className="absolute right-0 top-full mt-1 min-w-[180px] rounded-lg border border-border bg-bg-card shadow-lg shadow-black/40 z-20 overflow-hidden">
                <div className="px-3 py-2 text-[10px] uppercase tracking-wider font-semibold text-text-muted border-b border-border">
                  Session
                </div>
                <div className="px-3 py-2 text-xs text-text-muted">
                  <span className={cn("inline-block text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded border mr-1.5", marketColor)}>
                    {marketLabel}
                  </span>
                  {modelName && <span className="text-text-secondary">{modelName}</span>}
                </div>
                <Link
                  href={`${prefix}/sessions/${sessionId}/settings`}
                  className="block w-full text-left px-3 py-2.5 text-xs font-medium text-text-secondary hover:bg-bg-secondary transition-colors border-t border-border"
                  onClick={() => setMenuOpen(false)}
                >
                  ⚙  Session Settings
                </Link>
                <Link
                  href="/"
                  className="block w-full text-left px-3 py-2.5 text-xs font-medium text-text-secondary hover:bg-bg-secondary transition-colors"
                  onClick={() => setMenuOpen(false)}
                >
                  ←  All Sessions
                </Link>
              </div>
            )}
          </div>
        </div>

        {/* Row 2: Total return hero + sparkline */}
        <div className="flex items-end justify-between gap-3 mb-4">
          <div className="min-w-0 flex-1">
            <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1">Total Return</div>
            <div className={cn(
              "font-mono font-bold tracking-tight leading-none flex items-baseline gap-2",
              isProfit ? "text-accent-green" : "text-accent-red"
            )} style={{ fontSize: 32 }}>
              <span style={{ fontSize: 18, lineHeight: 1 }}>{isProfit ? "▲" : "▼"}</span>
              <span className="truncate">
                {portfolio ? `${isProfit ? "+" : ""}${fmt(portfolio.total_return, sym, locale)}` : "--"}
              </span>
            </div>
            <div className={cn("text-sm font-mono mt-1", isProfit ? "text-accent-green/80" : "text-accent-red/80")}>
              {portfolio ? pct(portfolio.total_return_pct) : "--"}
              <span className="text-text-muted"> · Portfolio {portfolio ? fmt(portfolio.total_value, sym, locale) : "--"}</span>
            </div>
          </div>
          <div className="shrink-0">
            <Sparkline values={sparkValues} width={100} height={44} positive={isProfit} />
          </div>
        </div>

        {/* Row 3: meta pills */}
        <div className="flex items-center gap-2 flex-wrap mb-4 pb-4 border-b border-border/60">
          <MetaPill
            label="Today"
            value={portfolio ? `${todayUp ? "+" : ""}${fmt(todayPnl, sym, locale)}` : "--"}
            color={todayUp ? "green" : "red"}
            highlight
          />
          <MetaPill
            label="Win Rate"
            value={perf?.total_trades ? `${perf.win_rate}%` : "--"}
            color={(perf?.win_rate ?? 0) >= 50 ? "green" : "red"}
          />
          <MetaPill
            label="Trades"
            value={String(perf?.total_trades ?? 0)}
          />
          <MetaPill
            label="Open"
            value={String(portfolio?.open_positions ?? 0)}
          />
          <MetaPill
            label="Cash"
            value={portfolio ? fmt(portfolio.cash, sym, locale) : "--"}
          />
        </div>

        {/* Row 4: primary action + updated */}
        <div className="flex items-center gap-3">
          {!isBacktest ? (
            <button
              onClick={handleToggle}
              disabled={toggling}
              className={cn(
                "flex-1 rounded-xl font-semibold text-sm transition-all disabled:opacity-60 inline-flex items-center justify-center gap-2",
                agentStatus.running
                  ? "bg-accent-red/10 hover:bg-accent-red/20 text-accent-red border border-accent-red/30"
                  : "bg-accent-green/15 hover:bg-accent-green/25 text-accent-green border border-accent-green/40 shadow-sm shadow-accent-green/10"
              )}
              style={{ minHeight: 48 }}
            >
              {toggling ? (
                <span className="opacity-70">...</span>
              ) : agentStatus.running ? (
                <>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
                  Stop Agent
                </>
              ) : (
                <>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
                  Start Agent
                </>
              )}
            </button>
          ) : (
            <div className="flex-1 rounded-xl px-4 py-3 text-center text-xs text-text-muted border border-dashed border-border">
              Backtest mode — see panel below
            </div>
          )}
          <div className="text-[10px] text-text-muted font-mono text-right leading-tight whitespace-nowrap">
            Updated<br />
            <span className="text-text-secondary">{updatedLabel}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function MetaPill({ label, value, color, highlight }: {
  label: string; value: string; color?: "green" | "red"; highlight?: boolean;
}) {
  const valColor = color === "green" ? "text-accent-green" : color === "red" ? "text-accent-red" : "text-text-primary";
  return (
    <div
      className={cn(
        "inline-flex flex-col rounded-lg px-2.5 py-1.5 border",
        highlight ? "border-border bg-bg-secondary/60" : "border-transparent bg-bg-secondary/30"
      )}
    >
      <span className="text-[9px] uppercase tracking-wider text-text-muted leading-none">{label}</span>
      <span className={cn("font-mono text-xs font-semibold mt-0.5", valColor)}>{value}</span>
    </div>
  );
}
