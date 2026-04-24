"use client";
import Link from "next/link";
import { api, fmt, pct, cn } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { Sparkline } from "@/components/Sparkline";
import type { Session, PortfolioSummary, DailyPerformance } from "@/lib/types";
import { useEffect, useRef, useState } from "react";

/** Pretty-print model name: "anthropic/claude-haiku-4-5" → "Claude Haiku 4.5" */
function formatModel(model?: string): string {
  if (!model) return "";
  const short = model.includes("/") ? model.split("/").pop()! : model;
  return short
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/(\d) (\d)/g, "$1.$2");
}

export function SessionCard({ session, onDelete }: { session: Session; onDelete?: () => void }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [toggling, setToggling] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const toast = useToast();

  // Use inline data from the sessions list API (no extra API calls)
  const portfolio = session.portfolio ?? null;
  const daily = session.daily ?? [];

  // Close overflow menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  const isProfit = (portfolio?.total_return ?? 0) >= 0;
  const hasPortfolio = portfolio != null;
  const sparkValues = daily
    .slice()
    .reverse()
    .map((d) => d.total_value)
    .filter((v): v is number => v != null);

  const marketColors: Record<string, string> = {
    nse: "bg-accent-green/15 text-accent-green border-accent-green/30",
    crypto: "bg-accent-blue/15 text-accent-blue border-accent-blue/30",
  };

  const handleToggle = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (toggling) return;
    setToggling(true);
    try {
      if (session.is_running) {
        await api(`/api/agent/stop/${session.session_id}`, { method: "POST" });
        toast.success(`"${session.display_name}" stopped`);
      } else {
        await api(`/api/agent/start/${session.session_id}`, { method: "POST" });
        toast.success(`"${session.display_name}" started`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      toast.error(`Failed to ${session.is_running ? "stop" : "start"}: ${msg}`);
    } finally {
      setToggling(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setDeleting(true);
    try {
      await api(`/api/sessions/${session.session_id}`, { method: "DELETE" });
      toast.success(`"${session.display_name}" deleted`);
      onDelete?.();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      toast.error(`Delete failed: ${msg}`);
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
      setMenuOpen(false);
    }
  };

  const handleCancelDelete = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setConfirmDelete(false);
  };

  const stopMenuPropagation = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const modelName = formatModel(session.llm_model);

  return (
    <Link href={`/sessions/${session.session_id}`} className="block">
      <div className="relative bg-bg-card border border-border rounded-2xl p-4 md:p-5 hover:border-border-accent transition-all group overflow-hidden">
        {/* Ambient gradient for profit/loss */}
        {hasPortfolio && (
          <div
            className="absolute inset-x-0 top-0 h-24 pointer-events-none opacity-60"
            style={{
              background: isProfit
                ? "linear-gradient(180deg, rgba(34,197,94,0.08) 0%, transparent 100%)"
                : "linear-gradient(180deg, rgba(239,68,68,0.08) 0%, transparent 100%)",
            }}
          />
        )}

        {/* Confirm delete bar */}
        {confirmDelete && (
          <div
            onClick={stopMenuPropagation}
            className="relative flex items-center justify-between gap-2 mb-3 p-2.5 rounded-lg border border-accent-red/30 bg-accent-red/10"
          >
            <span className="text-xs text-accent-red font-medium truncate">Delete this session?</span>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={handleCancelDelete}
                className="px-3 rounded-lg text-xs font-medium border border-border text-text-muted hover:bg-bg-secondary transition-all"
                style={{ minHeight: 36 }}
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-3 rounded-lg text-xs font-medium bg-accent-red text-white hover:bg-accent-red/80 transition-all disabled:opacity-50"
                style={{ minHeight: 36 }}
              >
                {deleting ? "..." : "Delete"}
              </button>
            </div>
          </div>
        )}

        {/* Top row: live status + name + overflow */}
        <div className="relative flex items-start justify-between mb-3">
          <div className="flex items-start gap-2.5 min-w-0 flex-1">
            <span
              className={cn(
                "rounded-full shrink-0 mt-1.5",
                session.is_running && "animate-pulse-dot"
              )}
              style={{
                width: 8,
                height: 8,
                background: session.is_running ? "#22c55e" : "rgba(100,116,139,0.5)",
                boxShadow: session.is_running ? "0 0 0 4px rgba(34,197,94,0.15)" : "none",
              }}
            />
            <div className="min-w-0 flex-1">
              <h3 className="font-semibold text-text-primary text-[15px] leading-tight truncate">
                {session.display_name}
              </h3>
              <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                <span className={cn("inline-block text-[11px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded border", marketColors[session.market] || "bg-bg-secondary text-text-muted border-border")}>
                  {session.market}
                </span>
                {session.backtest_mode && (
                  <span className="inline-block text-[11px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded border"
                    style={{ background: "rgba(139,92,246,0.15)", color: "#a78bfa", borderColor: "rgba(139,92,246,0.3)" }}>
                    {session.backtest_status?.startsWith("running") ? "backtesting..." :
                     session.backtest_status?.startsWith("completed") ? "backtest done" :
                     session.backtest_status?.startsWith("failed") ? "backtest failed" : "backtest"}
                  </span>
                )}
                {modelName && !session.backtest_mode && (
                  <span className="text-[11px] text-text-muted truncate max-w-[160px]" title={session.llm_model}>
                    {modelName}
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Overflow menu */}
          <div className="relative shrink-0" ref={menuRef}>
            <button
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); setMenuOpen((v) => !v); }}
              className="flex items-center justify-center rounded-lg text-text-muted/60 hover:text-text-primary hover:bg-bg-secondary transition-all"
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
              <div
                onClick={stopMenuPropagation}
                className="absolute right-0 top-full mt-1 min-w-[140px] rounded-lg border border-border bg-bg-card shadow-lg shadow-black/40 z-20 overflow-hidden"
              >
                <button
                  onClick={handleDelete}
                  className="w-full text-left px-3 py-2.5 text-xs font-medium text-accent-red hover:bg-accent-red/10 transition-colors flex items-center gap-2"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3 6 5 6 21 6" />
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                  </svg>
                  Delete session
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Hero: P&L + sparkline */}
        <div className="relative flex items-end justify-between gap-3 mb-3">
          <div className="min-w-0 flex-1">
            <div className="text-[10px] uppercase tracking-wider text-text-muted mb-0.5">Total Return</div>
            <div className={cn(
              "font-mono font-bold tracking-tight leading-none flex items-baseline gap-1.5",
              isProfit ? "text-accent-green" : "text-accent-red"
            )} style={{ fontSize: 26 }}>
              <span style={{ fontSize: 16, lineHeight: 1 }}>{isProfit ? "▲" : "▼"}</span>
              {portfolio
                ? `${isProfit ? "+" : ""}${fmt(portfolio.total_return, session.currency_symbol)}`
                : "--"}
            </div>
            <div className={cn("text-xs font-mono mt-1", isProfit ? "text-accent-green/80" : "text-accent-red/80")}>
              {portfolio ? pct(portfolio.total_return_pct) : "--"}
              <span className="text-text-muted"> · {portfolio ? fmt(portfolio.total_value, session.currency_symbol) : "--"}</span>
            </div>
          </div>
          <div className="shrink-0">
            <Sparkline values={sparkValues} width={90} height={34} positive={isProfit} />
          </div>
        </div>

        {/* Meta row */}
        <div className="relative flex items-center gap-3 text-[11px] text-text-muted mb-3 pt-3 border-t border-border/50">
          {session.win_rate != null ? (
            <span className={cn("font-mono font-semibold", session.win_rate >= 50 ? "text-accent-green" : "text-accent-red")}>
              {session.win_rate}% WR
            </span>
          ) : (
            <span className="font-mono">-- WR</span>
          )}
          <span className="opacity-40">·</span>
          <span className="font-mono">{session.total_trades ?? 0} trades</span>
          {portfolio && (
            <>
              <span className="opacity-40">·</span>
              <span className="font-mono">{portfolio.open_positions} open</span>
            </>
          )}
        </div>

        {/* Primary action */}
        {session.backtest_mode ? (
          <div
            className="relative w-full rounded-xl text-sm text-center font-medium border"
            style={{
              minHeight: 44,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "rgba(139,92,246,0.08)",
              borderColor: "rgba(139,92,246,0.25)",
              color: "#a78bfa",
            }}
          >
            {session.backtest_status?.startsWith("running") ? "Backtest in progress..." :
             session.backtest_status?.startsWith("completed") ? "View Results" :
             session.backtest_status?.startsWith("failed") ? "Backtest failed — retry" :
             "Configure Backtest"}
          </div>
        ) : (
          <button
            onClick={handleToggle}
            disabled={toggling}
            className={cn(
              "relative w-full rounded-xl font-semibold text-sm transition-all disabled:opacity-60",
              session.is_running
                ? "bg-accent-red/10 hover:bg-accent-red/20 text-accent-red border border-accent-red/30"
                : "bg-accent-green/15 hover:bg-accent-green/25 text-accent-green border border-accent-green/30"
            )}
            style={{ minHeight: 44 }}
          >
            <span className="inline-flex items-center justify-center gap-2">
              {toggling ? (
                <span className="opacity-70">...</span>
              ) : session.is_running ? (
                <>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
                  Stop Agent
                </>
              ) : (
                <>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
                  Start Agent
                </>
              )}
            </span>
          </button>
        )}
      </div>
    </Link>
  );
}
