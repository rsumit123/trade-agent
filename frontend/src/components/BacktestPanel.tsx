"use client";
import { useEffect, useState, useCallback } from "react";
import { api, fmt, pct } from "@/lib/api";
import { useToast } from "@/components/Toast";
import type { BacktestProgress, SessionConfig } from "@/lib/types";

interface Props {
  sessionId: string;
  config: SessionConfig | null;
  onComplete?: () => void;
}

export function BacktestPanel({ sessionId, config, onComplete }: Props) {
  const [progress, setProgress] = useState<BacktestProgress | null>(null);
  const [starting, setStarting] = useState(false);
  const [goingLive, setGoingLive] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const sym = config?.currency_symbol || "$";
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

  // --- Not started: show start form ---
  if (!progress || progress.status === "not_started") {
    return (
      <div
        style={{
          background: "#151d2e",
          border: "1px solid #1e293b",
          borderRadius: 12,
          padding: 24,
        }}
      >
        <div className="flex items-center gap-2 mb-4">
          <span style={{ fontSize: 18 }}>&#x23F3;</span>
          <h3
            style={{
              fontSize: 13,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              color: "#94a3b8",
            }}
          >
            Backtest
          </h3>
        </div>
        <p className="text-text-muted text-sm mb-5">
          Run a historical simulation to test your agent before going live.
          The agent will replay market data day-by-day, making trades and building its learning journal.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-5">
          <div>
            <label className="block text-xs text-text-muted mb-1.5">Start Date</label>
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
                color: "#e2e8f0",
                fontSize: 14,
              }}
            />
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1.5">End Date</label>
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
                color: "#e2e8f0",
                fontSize: 14,
              }}
            />
          </div>
        </div>
        {/* Quick presets */}
        <div className="flex flex-wrap gap-2 mb-5">
          {[
            { label: "1 Week", days: 7 },
            { label: "2 Weeks", days: 14 },
            { label: "1 Month", days: 30 },
            { label: "3 Months", days: 90 },
          ].map(({ label, days }) => (
            <button
              key={label}
              onClick={() => {
                const end = new Date();
                end.setDate(end.getDate() - 1); // Yesterday
                const start = new Date(end);
                start.setDate(start.getDate() - days);
                setStartDate(start.toISOString().split("T")[0]);
                setEndDate(end.toISOString().split("T")[0]);
              }}
              style={{
                padding: "6px 14px",
                background: "rgba(59,130,246,0.1)",
                border: "1px solid rgba(59,130,246,0.25)",
                borderRadius: 8,
                color: "#60a5fa",
                fontSize: 12,
                fontWeight: 500,
                cursor: "pointer",
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          onClick={handleStart}
          disabled={!startDate || !endDate || starting}
          style={{
            width: "100%",
            padding: "12px 20px",
            minHeight: 44,
            background: !startDate || !endDate || starting ? "#334155" : "#8b5cf6",
            border: "none",
            borderRadius: 10,
            color: "#fff",
            fontSize: 14,
            fontWeight: 600,
            cursor: !startDate || !endDate || starting ? "not-allowed" : "pointer",
            opacity: starting ? 0.6 : 1,
          }}
        >
          {starting ? "Starting..." : "Run Backtest"}
        </button>
      </div>
    );
  }

  // --- Running: show progress ---
  if (progress.status === "running") {
    const pctDone = progress.trading_days && progress.current_day
      ? Math.round((progress.current_day / progress.trading_days) * 100)
      : 0;
    const latestDay = progress.daily_results?.length
      ? progress.daily_results[progress.daily_results.length - 1]
      : null;
    const totalTrades = progress.daily_results?.reduce((s, d) => s + (d.trades || 0), 0) || 0;

    return (
      <div
        style={{
          background: "#151d2e",
          border: "1px solid rgba(139,92,246,0.3)",
          borderRadius: 12,
          padding: 24,
        }}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span
              style={{
                display: "inline-block",
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: "#8b5cf6",
                animation: "pulse 1.5s ease-in-out infinite",
              }}
            />
            <h3
              style={{
                fontSize: 13,
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                color: "#a78bfa",
              }}
            >
              Backtest Running
            </h3>
          </div>
          <span className="text-xs font-mono text-text-muted">
            Day {progress.current_day || 0}/{progress.trading_days || 0}
          </span>
        </div>

        {/* Progress bar */}
        <div
          style={{
            height: 6,
            background: "#1e293b",
            borderRadius: 3,
            overflow: "hidden",
            marginBottom: 16,
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${pctDone}%`,
              background: "linear-gradient(90deg, #8b5cf6, #a78bfa)",
              borderRadius: 3,
              transition: "width 0.5s ease",
            }}
          />
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          <MiniStat label="Progress" value={`${pctDone}%`} />
          <MiniStat label="Current Date" value={progress.current_date || "--"} />
          <MiniStat label="Trades" value={String(totalTrades)} />
          <MiniStat
            label="Latest P&L"
            value={latestDay ? fmt(latestDay.daily_pnl, sym) : "--"}
            color={latestDay && latestDay.daily_pnl >= 0 ? "#22c55e" : "#ef4444"}
          />
        </div>

        {/* Daily results table */}
        {progress.daily_results && progress.daily_results.length > 0 && (
          <div style={{ maxHeight: 200, overflowY: "auto" }}>
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
                  <tr key={d.date} style={{ borderBottom: "1px solid #0f172a" }}>
                    <td style={{ padding: "6px 8px", color: "#cbd5e1", fontFamily: "monospace" }}>{d.date}</td>
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
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
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
          <MiniStat label="Total Trades" value={String(stats?.total_trades || 0)} />
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
                </tr>
              </thead>
              <tbody>
                {progress.daily_results.map((d) => (
                  <tr key={d.date} style={{ borderBottom: "1px solid #0f172a" }}>
                    <td style={{ padding: "6px 8px", color: "#cbd5e1", fontFamily: "monospace" }}>{d.date}</td>
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
              window.location.href = `/sessions/${sessionId}`;
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
