"use client";
import { useEffect, useState, useMemo } from "react";
import { api, fmt, cn } from "@/lib/api";
import type { DailyPerformance, SessionConfig } from "@/lib/types";

export function DailyTracker({
  sessionId,
  config,
}: {
  sessionId: string;
  config: SessionConfig | null;
}) {
  const [rows, setRows] = useState<DailyPerformance[]>([]);
  const sym = config?.currency_symbol || "$";

  useEffect(() => {
    api<DailyPerformance[]>(
      `/api/performance/daily?session=${sessionId}&limit=30`
    )
      .then((d) => {
        if (Array.isArray(d)) setRows(d);
      })
      .catch(() => {});
  }, [sessionId]);

  const maxAbsPnl = useMemo(() => {
    if (rows.length === 0) return 1;
    return Math.max(...rows.map((r) => Math.abs(r.daily_pnl)), 1);
  }, [rows]);

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">
          Daily Performance
        </h3>
        {rows.length > 0 && (
          <span className="text-xs font-mono text-text-muted bg-bg-secondary px-2 py-0.5 rounded">
            {rows.length}d
          </span>
        )}
      </div>

      {rows.length === 0 ? (
        <div className="text-center py-10 text-text-muted text-sm px-4">
          No daily data yet — recorded after each daily review
        </div>
      ) : (
        <>
          {/* Mobile card list */}
          <div className="md:hidden divide-y divide-border/40 max-h-[420px] overflow-y-auto">
            {rows.map((r) => {
              const pnlPositive = r.daily_pnl >= 0;
              const barWidth = Math.min((Math.abs(r.daily_pnl) / maxAbsPnl) * 100, 100);
              return (
                <div key={r.date} className="px-4 py-3">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm font-semibold text-text-primary">
                        {r.date}
                      </span>
                      <span className="text-[10px] font-mono text-text-muted">
                        <span className="text-accent-green">{r.wins}W</span>
                        <span className="opacity-40"> · </span>
                        <span className="text-accent-red">{r.losses}L</span>
                        <span className="opacity-40"> · </span>
                        <span className="text-text-muted">{r.trades_taken}t</span>
                      </span>
                    </div>
                    <span
                      className={cn(
                        "font-mono text-sm font-semibold",
                        pnlPositive ? "text-accent-green" : "text-accent-red"
                      )}
                    >
                      {pnlPositive ? "+" : ""}
                      {fmt(r.daily_pnl, sym, "en-US", 0)}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <div
                      className="flex-1 h-1.5 rounded-full overflow-hidden"
                      style={{ background: "#0a0e17" }}
                    >
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{
                          width: `${barWidth}%`,
                          background: pnlPositive ? "#22c55e" : "#ef4444",
                        }}
                      />
                    </div>
                    <span
                      className={cn(
                        "font-mono text-[11px] font-medium min-w-[60px] text-right",
                        r.cumulative_return_pct == null
                          ? "text-text-muted"
                          : r.cumulative_return_pct >= 0
                          ? "text-accent-green"
                          : "text-accent-red"
                      )}
                    >
                      {r.cumulative_return_pct == null
                        ? "--"
                        : `${r.cumulative_return_pct >= 0 ? "+" : ""}${r.cumulative_return_pct.toFixed(2)}%`}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto max-h-[400px] overflow-y-auto">
            <table className="w-full">
              <thead className="sticky top-0 bg-bg-card z-10">
                <tr className="border-b border-border">
                  {["Date", "Trades", "W/L", "Daily P&L", "", "Cumulative %"].map((h) => (
                    <th
                      key={h}
                      className="text-left text-[10px] uppercase tracking-wider font-semibold text-text-muted px-4 py-2 whitespace-nowrap"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const pnlPositive = r.daily_pnl >= 0;
                  const barWidth = Math.min((Math.abs(r.daily_pnl) / maxAbsPnl) * 100, 100);
                  return (
                    <tr key={r.date} className="border-b border-border/50">
                      <td className="px-4 py-2.5 font-mono text-xs text-text-secondary whitespace-nowrap">{r.date}</td>
                      <td className="px-4 py-2.5 text-xs text-text-primary">{r.trades_taken}</td>
                      <td className="px-4 py-2.5 text-xs">
                        <span className="text-accent-green">{r.wins}</span>
                        <span className="text-text-muted">/</span>
                        <span className="text-accent-red">{r.losses}</span>
                      </td>
                      <td
                        className={cn(
                          "px-4 py-2.5 font-mono text-xs font-medium whitespace-nowrap",
                          pnlPositive ? "text-accent-green" : "text-accent-red"
                        )}
                      >
                        {pnlPositive ? "+" : ""}
                        {fmt(r.daily_pnl, sym, "en-US", 0)}
                      </td>
                      <td className="px-4 py-2.5" style={{ minWidth: 80 }}>
                        <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "#0a0e17" }}>
                          <div
                            className="h-full rounded-full transition-all duration-500"
                            style={{
                              width: `${barWidth}%`,
                              background: pnlPositive ? "#22c55e" : "#ef4444",
                            }}
                          />
                        </div>
                      </td>
                      <td
                        className={cn(
                          "px-4 py-2.5 font-mono text-xs font-medium whitespace-nowrap",
                          r.cumulative_return_pct == null
                            ? "text-text-muted"
                            : r.cumulative_return_pct >= 0
                            ? "text-accent-green"
                            : "text-accent-red"
                        )}
                      >
                        {r.cumulative_return_pct == null
                          ? "--"
                          : `${r.cumulative_return_pct >= 0 ? "+" : ""}${r.cumulative_return_pct.toFixed(2)}%`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
