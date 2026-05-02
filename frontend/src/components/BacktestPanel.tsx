"use client";
import { useEffect, useState, useCallback } from "react";
import { api, fmt, pct } from "@/lib/api";
import { useAppPrefix } from "@/lib/paths";
import { useToast } from "@/components/Toast";
import { useUser } from "@/lib/auth";
import { UpgradeLockButton } from "@/components/Runtime";
import type { BacktestProgress, BacktestPhase, SessionConfig } from "@/lib/types";

// ── Phase metadata: icon, color, label, description ──────────
const PHASE_META: Record<BacktestPhase, { icon: string; color: string; label: string; hint: string }> = {
  init:      { icon: "\u{1F680}", color: "#94a3b8", label: "Setting up",       hint: "Initializing agent and market data..." },
  scanning:  { icon: "\u{1F50D}", color: "#60a5fa", label: "Scanning market",  hint: "Filtering ~3,000 NSE stocks for movers" },
  selecting: { icon: "\u{1F3AF}", color: "#818cf8", label: "Picking stocks",   hint: "LLM selecting top 25 with thesis" },
  trading:   { icon: "\u{1F4B9}", color: "#a78bfa", label: "Trading",          hint: "Stepping through 15-min bars" },
  closing:   { icon: "\u{1F514}", color: "#fbbf24", label: "Closing day",      hint: "Force-closing intraday positions" },
  reviewing: { icon: "\u{1F9E0}", color: "#22d3ee", label: "Learning",         hint: "Reviewing day & updating distilled rules" },
  day_done:  { icon: "\u{2705}", color: "#22c55e", label: "Day complete",     hint: "Moving to next trading day..." },
};

function formatRelative(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

interface Props {
  sessionId: string;
  config: SessionConfig | null;
  onComplete?: () => void;
}

export function BacktestPanel({ sessionId, config, onComplete }: Props) {
  const { user } = useUser();
  const isFree = !!user && !user.is_admin;
  const [progress, setProgress] = useState<BacktestProgress | null>(null);
  const [starting, setStarting] = useState(false);
  const [goingLive, setGoingLive] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const sym = config?.currency_symbol || "$";
  const prefix = useAppPrefix();
  const toast = useToast();

  const fetchProgress = useCallback(() => {
    api<BacktestProgress>(`/api/backtest/status/${sessionId}`)
      .then((data) => {
        setProgress(data);
        if (data.status === "completed" && onComplete) {
          onComplete();
        }
      })
      .catch(() => {});
  }, [sessionId, onComplete]);

  useEffect(() => {
    fetchProgress();
    const interval = setInterval(fetchProgress, 3000);
    return () => clearInterval(interval);
  }, [fetchProgress]);

  const handleStart = async () => {
    if (!startDate || !endDate) return;
    setStarting(true);
    try {
      await api(`/api/backtest/start/${sessionId}`, {
        method: "POST",
        body: JSON.stringify({ start_date: startDate, end_date: endDate }),
      });
      fetchProgress();
    } catch {
      // handled by progress poll
    } finally {
      setStarting(false);
    }
  };

  // --- Free tier: locked card ---
  if (isFree && (!progress || progress.status === "not_started")) {
    return (
      <div
        style={{
          background: "linear-gradient(180deg, rgba(245,158,11,0.05), #151d2e)",
          border: "1px solid rgba(245,158,11,0.3)",
          borderRadius: 12,
          padding: 16,
        }}
      >
        <div className="flex items-center gap-2 mb-2">
          <span style={{ fontSize: 18 }}>🔒</span>
          <h3 style={{ fontSize: 13, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "#fcd34d" }}>
            Backtest — Paid feature
          </h3>
        </div>
        <p className="text-text-muted text-xs mb-3">
          Replay months of historical data and watch your AI trade through it. Available on the upgrade — coming soon.
        </p>
        <UpgradeLockButton label="Unlock backtests" fullWidth />
      </div>
    );
  }

  // --- Not started: show start form ---
  if (!progress || progress.status === "not_started") {
    // Estimate trading days between selected dates
    const tradingDayEstimate = (() => {
      if (!startDate || !endDate) return null;
      const s = new Date(startDate), e = new Date(endDate);
      let days = 0;
      for (let d = new Date(s); d <= e; d.setDate(d.getDate() + 1)) {
        const dow = d.getDay();
        if (dow !== 0 && dow !== 6) days++;
      }
      return days;
    })();

    return (
      <div
        style={{
          background: "#151d2e",
          border: "1px solid #1e293b",
          borderRadius: 12,
          padding: "16px",
        }}
      >
        <div className="flex items-center gap-2 mb-3">
          <span style={{ fontSize: 16 }}>&#x23F3;</span>
          <h3 style={{ fontSize: 13, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "#94a3b8" }}>
            Backtest
          </h3>
        </div>
        <p className="text-text-muted text-xs mb-4">
          Replay historical market data. The agent picks stocks each morning, trades through the day, and learns from results.
        </p>

        {/* Quick presets — full width on mobile */}
        <div className="grid grid-cols-4 gap-2 mb-4">
          {[
            { label: "1W", days: 7 },
            { label: "2W", days: 14 },
            { label: "1M", days: 30 },
            { label: "3M", days: 90 },
          ].map(({ label, days }) => (
            <button
              key={label}
              onClick={() => {
                const end = new Date();
                end.setDate(end.getDate() - 1);
                const start = new Date(end);
                start.setDate(start.getDate() - days);
                setStartDate(start.toISOString().split("T")[0]);
                setEndDate(end.toISOString().split("T")[0]);
              }}
              style={{
                minHeight: 44,
                background: "rgba(59,130,246,0.1)",
                border: "1px solid rgba(59,130,246,0.25)",
                borderRadius: 10,
                color: "#60a5fa",
                fontSize: 13,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label className="block text-[11px] text-text-muted mb-1">From</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="w-full font-mono"
              style={{
                background: "#0f172a",
                border: "1px solid #334155",
                borderRadius: 8,
                padding: "10px 12px",
                minHeight: 44,
                color: "#e2e8f0",
                fontSize: 16,
              }}
            />
          </div>
          <div>
            <label className="block text-[11px] text-text-muted mb-1">To</label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="w-full font-mono"
              style={{
                background: "#0f172a",
                border: "1px solid #334155",
                borderRadius: 8,
                padding: "10px 12px",
                minHeight: 44,
                color: "#e2e8f0",
                fontSize: 16,
              }}
            />
          </div>
        </div>

        {/* Estimate */}
        {tradingDayEstimate != null && tradingDayEstimate > 0 && (
          <p className="text-xs text-text-muted mb-4 text-center">
            ~{tradingDayEstimate} trading days · est. {Math.ceil(tradingDayEstimate * 0.75)} min
          </p>
        )}

        <button
          onClick={handleStart}
          disabled={!startDate || !endDate || starting}
          style={{
            width: "100%",
            padding: "12px 20px",
            minHeight: 48,
            background: !startDate || !endDate || starting ? "#334155" : "#8b5cf6",
            border: "none",
            borderRadius: 12,
            color: "#fff",
            fontSize: 15,
            fontWeight: 600,
            cursor: !startDate || !endDate || starting ? "not-allowed" : "pointer",
            opacity: starting ? 0.6 : 1,
          }}
        >
          {starting ? "Starting backtest..." : "Run Backtest"}
        </button>
      </div>
    );
  }

  // --- Running: show live progress ---
  if (progress.status === "running") {
    return <RunningView progress={progress} sym={sym} sessionId={sessionId} />;
  }

  // --- Completed: show results ---
  if (progress.status === "completed") {
    const stats = progress.final_stats;
    const totalDays = progress.daily_results?.length || 0;
    const profitDays = progress.daily_results?.filter((d) => d.daily_pnl > 0).length || 0;
    const totalPnl = stats?.total_pnl || 0;

    return (
      <div
        style={{
          background: "#151d2e",
          border: `1px solid ${totalPnl >= 0 ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)"}`,
          borderRadius: 12,
          padding: 24,
        }}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span style={{ fontSize: 18 }}>{totalPnl >= 0 ? "\u2705" : "\u26A0\uFE0F"}</span>
            <h3
              style={{
                fontSize: 13,
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                color: totalPnl >= 0 ? "#22c55e" : "#ef4444",
              }}
            >
              Backtest Complete
            </h3>
          </div>
          <span className="text-xs text-text-muted">
            {progress.start_date} to {progress.end_date}
          </span>
        </div>

        {/* Summary stats */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          <MiniStat
            label="Total P&L"
            value={fmt(totalPnl, sym)}
            color={totalPnl >= 0 ? "#22c55e" : "#ef4444"}
          />
          <MiniStat
            label="Win Rate"
            value={stats ? `${stats.win_rate}%` : "--"}
            color={stats && stats.win_rate >= 50 ? "#22c55e" : "#ef4444"}
          />
          <MiniStat
            label="W / L / BE"
            value={stats
              ? `${stats.wins}/${stats.losses}${stats.breakevens ? `/${stats.breakevens}` : ""}`
              : "--"}
          />
          <MiniStat
            label="Profitable Days"
            value={`${profitDays}/${totalDays}`}
            color={profitDays > totalDays / 2 ? "#22c55e" : "#ef4444"}
          />
        </div>

        {stats && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
            <MiniStat label="Avg Win" value={fmt(stats.avg_win, sym)} color="#22c55e" />
            <MiniStat label="Avg Loss" value={fmt(stats.avg_loss, sym)} color="#ef4444" />
            <MiniStat label="Best Trade" value={fmt(stats.best_trade, sym)} color="#22c55e" />
            <MiniStat label="Worst Trade" value={fmt(stats.worst_trade, sym)} color="#ef4444" />
          </div>
        )}

        {/* Daily results table */}
        {progress.daily_results && progress.daily_results.length > 0 && (
          <div style={{ maxHeight: 250, overflowY: "auto", marginBottom: 16 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #1e293b", position: "sticky", top: 0, background: "#151d2e" }}>
                  <th style={{ textAlign: "left", padding: "6px 8px", color: "#64748b", fontWeight: 500 }}>Date</th>
                  <th style={{ textAlign: "right", padding: "6px 8px", color: "#64748b", fontWeight: 500 }}>Trades</th>
                  <th style={{ textAlign: "right", padding: "6px 8px", color: "#64748b", fontWeight: 500 }}>P&L</th>
                  <th style={{ textAlign: "right", padding: "6px 8px", color: "#64748b", fontWeight: 500 }}>Return</th>
                  <th style={{ textAlign: "right", padding: "6px 8px", color: "#64748b", fontWeight: 500 }}>Win Rate</th>
                  <th style={{ textAlign: "right", padding: "6px 8px", color: "#64748b", fontWeight: 500 }}></th>
                </tr>
              </thead>
              <tbody>
                {progress.daily_results.map((d) => (
                  <tr key={d.date} style={{ borderBottom: "1px solid #0f172a" }}>
                    <td style={{ padding: "6px 8px", color: "#cbd5e1", fontFamily: "monospace" }}>
                      <a
                        href={`${prefix}/sessions/${sessionId}/replay/${d.date}`}
                        style={{ color: "#cbd5e1", textDecoration: "none" }}
                        onMouseEnter={(e) => (e.currentTarget.style.color = "#60a5fa")}
                        onMouseLeave={(e) => (e.currentTarget.style.color = "#cbd5e1")}
                      >
                        {d.date}
                      </a>
                    </td>
                    <td style={{ padding: "6px 8px", textAlign: "right", color: "#94a3b8" }}>{d.trades}</td>
                    <td
                      style={{
                        padding: "6px 8px",
                        textAlign: "right",
                        fontFamily: "monospace",
                        color: d.daily_pnl >= 0 ? "#22c55e" : "#ef4444",
                      }}
                    >
                      {fmt(d.daily_pnl, sym)}
                    </td>
                    <td
                      style={{
                        padding: "6px 8px",
                        textAlign: "right",
                        fontFamily: "monospace",
                        color: d.total_return_pct >= 0 ? "#22c55e" : "#ef4444",
                      }}
                    >
                      {pct(d.total_return_pct)}
                    </td>
                    <td
                      style={{
                        padding: "6px 8px",
                        textAlign: "right",
                        fontFamily: "monospace",
                        color: d.win_rate >= 50 ? "#22c55e" : "#ef4444",
                      }}
                    >
                      {d.win_rate}%
                    </td>
                    <td style={{ padding: "6px 8px", textAlign: "right" }}>
                      <a
                        href={`${prefix}/sessions/${sessionId}/replay/${d.date}`}
                        style={{
                          fontSize: 10,
                          textTransform: "uppercase",
                          letterSpacing: "0.05em",
                          fontWeight: 600,
                          color: "#60a5fa",
                          textDecoration: "none",
                          padding: "2px 6px",
                          borderRadius: 4,
                          background: "rgba(96,165,250,0.1)",
                        }}
                      >
                        Replay
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Go Live button */}
        <button
          onClick={async () => {
            setGoingLive(true);
            try {
              await api(`/api/backtest/go-live/${sessionId}`, { method: "POST" });
              toast.success("Switched to live trading!");
              window.location.href = `${prefix}/sessions/${sessionId}`;
            } catch (err) {
              const msg = err instanceof Error ? err.message : "Failed to go live";
              toast.error(msg);
            } finally {
              setGoingLive(false);
            }
          }}
          disabled={goingLive}
          style={{
            width: "100%",
            padding: "12px 20px",
            minHeight: 44,
            background: goingLive ? "#334155" : "#22c55e",
            border: "none",
            borderRadius: 10,
            color: "#fff",
            fontSize: 14,
            fontWeight: 600,
            cursor: goingLive ? "not-allowed" : "pointer",
            opacity: goingLive ? 0.6 : 1,
          }}
        >
          {goingLive ? "Starting live agent..." : "Start Live Trading with Learned Rules"}
        </button>
      </div>
    );
  }

  // --- Failed ---
  return (
    <div
      style={{
        background: "#151d2e",
        border: "1px solid rgba(239,68,68,0.3)",
        borderRadius: 12,
        padding: 24,
      }}
    >
      <div className="flex items-center gap-2 mb-3">
        <span style={{ fontSize: 18 }}>&#x274C;</span>
        <h3
          style={{
            fontSize: 13,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "#ef4444",
          }}
        >
          Backtest Failed
        </h3>
      </div>
      <p className="text-text-muted text-sm mb-4">
        {progress.error || "An unknown error occurred. Check the agent logs for details."}
      </p>
      <button
        onClick={async () => {
          setResetting(true);
          try {
            await api(`/api/backtest/reset/${sessionId}`, { method: "POST" });
            setProgress(null);
            setStartDate("");
            setEndDate("");
            toast.success("Backtest reset. Configure new dates and try again.");
          } catch (err) {
            const msg = err instanceof Error ? err.message : "Reset failed";
            toast.error(msg);
          } finally {
            setResetting(false);
          }
        }}
        disabled={resetting}
        style={{
          padding: "10px 20px",
          minHeight: 44,
          background: "rgba(239,68,68,0.15)",
          border: "1px solid rgba(239,68,68,0.3)",
          borderRadius: 10,
          color: "#ef4444",
          fontSize: 14,
          fontWeight: 500,
          cursor: resetting ? "not-allowed" : "pointer",
          opacity: resetting ? 0.6 : 1,
        }}
      >
        {resetting ? "Resetting..." : "Try Again"}
      </button>
    </div>
  );
}

function MiniStat({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div
      style={{
        background: "#0f172a",
        borderRadius: 8,
        padding: "10px 12px",
      }}
    >
      <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </div>
      <div
        style={{
          fontSize: 15,
          fontWeight: 600,
          fontFamily: "monospace",
          color: color || "#e2e8f0",
        }}
      >
        {value}
      </div>
    </div>
  );
}

// ── Running view: live transparency UI ────────────────────────
function RunningView({ progress, sym, sessionId }: { progress: BacktestProgress; sym: string; sessionId: string }) {
  const [picksOpen, setPicksOpen] = useState(false);
  const [pastDaysOpen, setPastDaysOpen] = useState(false);
  const prefix = useAppPrefix();
  // Force re-render every 5s so "elapsed" timer ticks
  const [, setTick] = useState(0);
  useEffect(() => {
    const i = setInterval(() => setTick((t) => t + 1), 5000);
    return () => clearInterval(i);
  }, []);

  const phase = (progress.current_phase || "init") as BacktestPhase;
  const meta = PHASE_META[phase] || PHASE_META.init;
  const totalDays = progress.trading_days || 0;
  const currentDay = progress.current_day || 0;
  const pctDone = totalDays ? Math.round((currentDay / totalDays) * 100) : 0;

  // ETA calculation: avg time per completed day × remaining days
  const completedDays = progress.daily_results?.length || 0;
  const elapsed = progress.started_at ? Date.now() / 1000 - progress.started_at : 0;
  const eta = completedDays > 0 && totalDays > completedDays
    ? Math.max(0, (elapsed / completedDays) * (totalDays - completedDays))
    : null;

  const dayElapsed = progress.day_started_at ? Date.now() / 1000 - progress.day_started_at : 0;
  const dayPnl = progress.day_pnl || 0;
  const dayTrades = progress.day_trades_count || 0;
  const picks = progress.current_picks || [];
  const recent = progress.recent_trades || [];

  return (
    <div style={{ background: "#151d2e", border: "1px solid rgba(139,92,246,0.3)", borderRadius: 12, padding: 14 }}>
      {/* HEADER: status pill + day counter */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span style={{
            display: "inline-block", width: 8, height: 8, borderRadius: "50%",
            background: meta.color, boxShadow: `0 0 0 4px ${meta.color}25`,
            animation: "pulse 1.5s ease-in-out infinite",
          }} />
          <span style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "#a78bfa" }}>
            Backtest Running
          </span>
        </div>
        <span style={{ fontSize: 12, fontFamily: "monospace", color: "#94a3b8" }}>
          Day {currentDay} of {totalDays}
        </span>
      </div>

      {/* Progress bar with ETA */}
      <div style={{ height: 4, background: "#1e293b", borderRadius: 2, overflow: "hidden", marginBottom: 6 }}>
        <div style={{
          height: "100%", width: `${pctDone}%`,
          background: "linear-gradient(90deg, #8b5cf6, #a78bfa)",
          transition: "width 0.6s ease",
        }} />
      </div>
      <div className="flex items-center justify-between" style={{ fontSize: 11, color: "#64748b", marginBottom: 14 }}>
        <span>{pctDone}% complete</span>
        {eta != null && <span>~{formatRelative(eta)} remaining</span>}
      </div>

      {/* BIG PHASE CARD — the centerpiece */}
      <div style={{
        background: `linear-gradient(135deg, ${meta.color}15 0%, ${meta.color}05 100%)`,
        border: `1px solid ${meta.color}33`,
        borderRadius: 12,
        padding: 14,
        marginBottom: 12,
        overflow: "hidden",
        position: "relative",
      }}>
        <div className="flex items-start gap-3">
          <span style={{
            fontSize: 28, lineHeight: 1, flexShrink: 0,
            animation: phase === "trading" || phase === "scanning" || phase === "selecting" ? "pulse 2s ease-in-out infinite" : undefined,
          }}>
            {meta.icon}
          </span>
          <div className="min-w-0 flex-1">
            <div style={{ fontSize: 16, fontWeight: 700, color: meta.color, lineHeight: 1.2, marginBottom: 2 }}>
              {meta.label}
              {phase === "trading" && progress.current_bar_time && (
                <span style={{ fontFamily: "monospace", marginLeft: 8, fontWeight: 600, color: "#e2e8f0" }}>
                  {progress.current_bar_time}
                </span>
              )}
            </div>
            <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.4 }}>
              {meta.hint}
            </div>
            {/* Sub-progress: e.g. "2,200 / 2,847 stocks scanned" with mini progress bar */}
            {progress.phase_progress != null && progress.phase_total != null && progress.phase_total > 0 && (
              <SubProgress
                current={progress.phase_progress}
                total={progress.phase_total}
                label={phase === "scanning" ? "stocks scanned" : phase === "trading" ? "bars processed" : "items"}
                color={meta.color}
              />
            )}
            {/* Phase detail text (e.g. "60 candidates passed Phase 1") */}
            {progress.phase_detail && (
              <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 6, fontStyle: "italic" }}>
                {progress.phase_detail}
              </div>
            )}
            {/* Day-level stats inline (only after scanning is done) */}
            {(phase === "trading" || phase === "closing" || phase === "reviewing" || phase === "day_done") && (
              <div className="flex items-center flex-wrap gap-x-3 gap-y-1 mt-2" style={{ fontSize: 11, fontFamily: "monospace" }}>
                <span style={{ color: "#cbd5e1" }}>{progress.current_date}</span>
                <span style={{ color: "#475569" }}>·</span>
                <span style={{ color: "#cbd5e1" }}>{dayTrades} trade{dayTrades !== 1 ? "s" : ""}</span>
                {dayPnl !== 0 && (
                  <>
                    <span style={{ color: "#475569" }}>·</span>
                    <span style={{ color: dayPnl >= 0 ? "#22c55e" : "#ef4444", fontWeight: 600 }}>
                      {dayPnl >= 0 ? "+" : ""}{fmt(dayPnl, sym)}
                    </span>
                  </>
                )}
                {dayElapsed > 0 && (
                  <>
                    <span style={{ color: "#475569" }}>·</span>
                    <span style={{ color: "#64748b" }}>{formatRelative(dayElapsed)}</span>
                  </>
                )}
              </div>
            )}
            {/* Show date for scanning/selecting too */}
            {(phase === "scanning" || phase === "selecting") && progress.current_date && (
              <div style={{ fontSize: 11, color: "#cbd5e1", fontFamily: "monospace", marginTop: 6 }}>
                {progress.current_date}
              </div>
            )}
          </div>
        </div>

        {/* Indeterminate shimmer for phases without sub-progress */}
        {(phase === "scanning" || phase === "selecting" || phase === "reviewing" || phase === "closing") && progress.phase_progress == null && (
          <div style={{
            position: "absolute", left: 0, right: 0, bottom: 0, height: 2,
            background: `linear-gradient(90deg, transparent 0%, ${meta.color} 50%, transparent 100%)`,
            backgroundSize: "200% 100%",
            animation: "shimmerSlide 1.8s linear infinite",
          }} />
        )}
      </div>

      {/* TODAY'S PICKS — collapsible */}
      {picks.length > 0 && (
        <div style={{ background: "#0f172a", borderRadius: 10, marginBottom: 12, overflow: "hidden" }}>
          <button
            onClick={() => setPicksOpen((v) => !v)}
            className="w-full flex items-center justify-between"
            style={{
              padding: "10px 12px", minHeight: 44,
              background: "transparent", border: "none", cursor: "pointer",
              color: "#cbd5e1", fontSize: 13, fontWeight: 600,
            }}
          >
            <span className="flex items-center gap-2">
              <span>🎯</span>
              <span>Today&apos;s picks</span>
              <span style={{ color: "#64748b", fontWeight: 400, fontSize: 12 }}>({picks.length})</span>
            </span>
            <span style={{ color: "#64748b", fontSize: 12, transform: picksOpen ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>▾</span>
          </button>
          {!picksOpen && (
            <div style={{ padding: "0 12px 10px", display: "flex", gap: 6, overflowX: "auto", whiteSpace: "nowrap" }}>
              {picks.slice(0, 12).map((p) => (
                <PickChip key={p.ticker} pick={p} compact />
              ))}
              {picks.length > 12 && (
                <span style={{ fontSize: 11, color: "#64748b", alignSelf: "center", paddingLeft: 4 }}>+{picks.length - 12}</span>
              )}
            </div>
          )}
          {picksOpen && (
            <div style={{ padding: "0 12px 12px", display: "flex", flexWrap: "wrap", gap: 6 }}>
              {picks.map((p) => <PickChip key={p.ticker} pick={p} />)}
            </div>
          )}
        </div>
      )}

      {/* RECENT TRADES — live feed */}
      {recent.length > 0 && (
        <div style={{ background: "#0f172a", borderRadius: 10, padding: "10px 12px", marginBottom: 12 }}>
          <div className="flex items-center gap-2 mb-2" style={{ fontSize: 11, fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            <span>Recent trades</span>
            <span style={{ color: "#475569" }}>·</span>
            <span style={{ color: "#94a3b8", textTransform: "none", letterSpacing: 0, fontWeight: 500 }}>last {recent.length}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {recent.slice(0, 6).map((t, i) => <TradeRow key={`${t.ticker}-${t.exit_time || i}`} trade={t} sym={sym} />)}
          </div>
        </div>
      )}

      {/* PAST DAYS — collapsed by default */}
      {progress.daily_results && progress.daily_results.length > 0 && (
        <div style={{ background: "#0f172a", borderRadius: 10, overflow: "hidden" }}>
          <button
            onClick={() => setPastDaysOpen((v) => !v)}
            className="w-full flex items-center justify-between"
            style={{
              padding: "10px 12px", minHeight: 44,
              background: "transparent", border: "none", cursor: "pointer",
              color: "#cbd5e1", fontSize: 13, fontWeight: 600,
            }}
          >
            <span className="flex items-center gap-2">
              <span>📊</span>
              <span>Past days</span>
              <span style={{ color: "#64748b", fontWeight: 400, fontSize: 12 }}>({progress.daily_results.length})</span>
            </span>
            <span style={{ color: "#64748b", fontSize: 12, transform: pastDaysOpen ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>▾</span>
          </button>
          {pastDaysOpen && (
            <div style={{ maxHeight: 240, overflowY: "auto", padding: "0 4px 8px" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #1e293b" }}>
                    <th style={{ textAlign: "left", padding: "6px 8px", color: "#64748b", fontWeight: 500 }}>Date</th>
                    <th style={{ textAlign: "right", padding: "6px 8px", color: "#64748b", fontWeight: 500 }}>Trades</th>
                    <th style={{ textAlign: "right", padding: "6px 8px", color: "#64748b", fontWeight: 500 }}>P&L</th>
                    <th style={{ textAlign: "right", padding: "6px 8px", color: "#64748b", fontWeight: 500 }}>Return</th>
                  </tr>
                </thead>
                <tbody>
                  {progress.daily_results.map((d) => (
                    <tr key={d.date} style={{ borderBottom: "1px solid #0a0f1c" }}>
                      <td style={{ padding: "6px 8px", fontFamily: "monospace" }}>
                        <a
                          href={`${prefix}/sessions/${sessionId}/replay/${d.date}`}
                          style={{ color: "#60a5fa", textDecoration: "none" }}
                        >
                          {d.date}
                        </a>
                      </td>
                      <td style={{ padding: "6px 8px", textAlign: "right", color: "#94a3b8" }}>{d.trades}</td>
                      <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "monospace", color: d.daily_pnl >= 0 ? "#22c55e" : "#ef4444" }}>
                        {fmt(d.daily_pnl, sym)}
                      </td>
                      <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "monospace", color: d.total_return_pct >= 0 ? "#22c55e" : "#ef4444" }}>
                        {pct(d.total_return_pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SubProgress({ current, total, label, color }: { current: number; total: number; label: string; color: string }) {
  const pct = total > 0 ? Math.min(100, (current / total) * 100) : 0;
  return (
    <div style={{ marginTop: 8 }}>
      <div className="flex items-baseline justify-between" style={{ fontSize: 11, fontFamily: "monospace", marginBottom: 4 }}>
        <span style={{ color: "#cbd5e1", fontWeight: 600 }}>
          <span style={{ color, fontWeight: 700 }}>{current.toLocaleString()}</span>
          <span style={{ color: "#475569" }}> / </span>
          <span style={{ color: "#94a3b8" }}>{total.toLocaleString()}</span>
          <span style={{ color: "#64748b", marginLeft: 6, fontWeight: 400 }}>{label}</span>
        </span>
        <span style={{ color, fontWeight: 600 }}>{Math.round(pct)}%</span>
      </div>
      <div style={{ height: 3, background: "#0f172a", borderRadius: 2, overflow: "hidden" }}>
        <div style={{
          height: "100%",
          width: `${pct}%`,
          background: color,
          transition: "width 0.4s ease",
          borderRadius: 2,
        }} />
      </div>
    </div>
  );
}

function PickChip({ pick, compact = false }: { pick: { ticker: string; direction: string; reason: string }; compact?: boolean }) {
  const isLong = pick.direction === "long";
  const cleanTicker = pick.ticker.replace(/\.NS$/, "");
  return (
    <span
      title={pick.reason}
      style={{
        display: "inline-flex", alignItems: "center", gap: 3,
        padding: compact ? "4px 8px" : "5px 10px",
        background: isLong ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
        border: `1px solid ${isLong ? "rgba(34,197,94,0.25)" : "rgba(239,68,68,0.25)"}`,
        color: isLong ? "#22c55e" : "#ef4444",
        borderRadius: 6,
        fontSize: 11,
        fontFamily: "monospace",
        fontWeight: 600,
        flexShrink: 0,
      }}
    >
      <span>{cleanTicker}</span>
      <span style={{ fontSize: 9 }}>{isLong ? "▲" : "▼"}</span>
    </span>
  );
}

function TradeRow({ trade, sym }: { trade: { ticker: string; action: string; direction: string; quantity: number; entry_price: number; exit_price: number | null; pnl: number | null; exit_time: string | null }; sym: string }) {
  const cleanTicker = trade.ticker.replace(/\.NS$/, "");
  const pnl = trade.pnl;
  const isProfit = pnl != null && pnl >= 0;
  const isShort = trade.direction === "short";
  const time = trade.exit_time ? new Date(trade.exit_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";

  return (
    <div className="flex items-center gap-2" style={{ fontSize: 12 }}>
      <span style={{
        fontSize: 10, fontWeight: 700,
        color: isShort ? "#ef4444" : "#22c55e",
        flexShrink: 0,
      }}>
        {isShort ? "▼" : "▲"}
      </span>
      <span style={{ fontFamily: "monospace", color: "#cbd5e1", fontWeight: 600, minWidth: 70 }}>
        {cleanTicker}
      </span>
      <span style={{ color: "#64748b", fontFamily: "monospace", flex: 1 }}>
        {trade.quantity} @ {fmt(trade.entry_price, sym)}
      </span>
      {pnl != null && (
        <span style={{
          fontFamily: "monospace", fontWeight: 600,
          color: isProfit ? "#22c55e" : "#ef4444",
          flexShrink: 0,
        }}>
          {isProfit ? "+" : ""}{fmt(pnl, sym)}
        </span>
      )}
      {time && (
        <span style={{ fontSize: 10, color: "#475569", fontFamily: "monospace", flexShrink: 0 }}>
          {time}
        </span>
      )}
    </div>
  );
}
