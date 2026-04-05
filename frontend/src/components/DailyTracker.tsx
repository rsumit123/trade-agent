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
    <div style={{ background: "#151d2e", border: "1px solid #1e293b", borderRadius: 12 }}>
      {/* Header */}
      <div className="px-4 py-3" style={{ borderBottom: "1px solid #1e293b" }}>
        <h3
          className="text-xs uppercase tracking-wider font-semibold"
          style={{ color: "#94a3b8" }}
        >
          Daily Performance
        </h3>
      </div>

      {rows.length === 0 ? (
        <div className="p-4">
          <p style={{ color: "#64748b", fontSize: 13 }}>
            No daily data yet &mdash; performance is recorded after each daily review
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full" style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid #1e293b" }}>
                {["Date", "Trades", "W/L", "Daily P&L", "", "Cumulative %"].map(
                  (h) => (
                    <th
                      key={h}
                      className="text-left text-xs font-semibold px-4 py-2"
                      style={{ color: "#64748b", whiteSpace: "nowrap" }}
                    >
                      {h}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const pnlPositive = r.daily_pnl >= 0;
                const barWidth = Math.min(
                  (Math.abs(r.daily_pnl) / maxAbsPnl) * 100,
                  100
                );

                return (
                  <tr
                    key={r.date}
                    style={{ borderBottom: "1px solid #1e293b" }}
                  >
                    {/* Date */}
                    <td
                      className="px-4 py-2.5 font-mono text-xs"
                      style={{ color: "#94a3b8", whiteSpace: "nowrap" }}
                    >
                      {r.date}
                    </td>

                    {/* Trades */}
                    <td
                      className="px-4 py-2.5 text-xs"
                      style={{ color: "#e2e8f0" }}
                    >
                      {r.trades_taken}
                    </td>

                    {/* W/L */}
                    <td className="px-4 py-2.5 text-xs">
                      <span style={{ color: "#22c55e" }}>{r.wins}</span>
                      <span style={{ color: "#64748b" }}>/</span>
                      <span style={{ color: "#ef4444" }}>{r.losses}</span>
                    </td>

                    {/* Daily P&L */}
                    <td
                      className="px-4 py-2.5 font-mono text-xs font-medium"
                      style={{
                        color: pnlPositive ? "#22c55e" : "#ef4444",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {pnlPositive ? "+" : ""}
                      {fmt(r.daily_pnl, sym, "en-US", 0)}
                    </td>

                    {/* Visual P&L bar */}
                    <td className="px-4 py-2.5" style={{ minWidth: 80 }}>
                      <div
                        style={{
                          height: 6,
                          borderRadius: 3,
                          background: "#0a0e17",
                          overflow: "hidden",
                        }}
                      >
                        <div
                          style={{
                            height: "100%",
                            width: `${barWidth}%`,
                            borderRadius: 3,
                            background: pnlPositive ? "#22c55e" : "#ef4444",
                            transition: "width 0.3s ease",
                          }}
                        />
                      </div>
                    </td>

                    {/* Cumulative % */}
                    <td
                      className="px-4 py-2.5 font-mono text-xs font-medium"
                      style={{
                        color:
                          r.cumulative_return_pct == null
                            ? "#64748b"
                            : r.cumulative_return_pct >= 0
                            ? "#22c55e"
                            : "#ef4444",
                        whiteSpace: "nowrap",
                      }}
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
      )}
    </div>
  );
}
