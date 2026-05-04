"use client";
import { useState } from "react";
import { fmt } from "@/lib/api";
import type { ClosedTrade, SessionConfig } from "@/lib/types";

interface Props {
  trades: ClosedTrade[];
  config: SessionConfig | null;
  windowLabel?: string;
}

export function BacktestHistoryPanel({ trades, config, windowLabel }: Props) {
  const [open, setOpen] = useState(false);
  const sym = config?.currency_symbol || "$";

  if (!trades.length) return null;

  const totalPnl = trades.reduce((s, t) => s + (t.pnl ?? 0), 0);
  const wins = trades.filter((t) => (t.pnl ?? 0) > 0).length;
  const losses = trades.filter((t) => (t.pnl ?? 0) < 0).length;
  const winRate = trades.length ? Math.round((wins / trades.length) * 1000) / 10 : 0;
  const positive = totalPnl >= 0;

  return (
    <div
      className="rounded-xl mb-4"
      style={{ background: "#0c1424", border: "1px solid #1e293b", overflow: "hidden" }}
    >
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3"
        style={{ background: "transparent", border: "none", cursor: "pointer", minHeight: 48 }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span style={{ fontSize: 16 }}>📊</span>
          <div className="text-left min-w-0">
            <div className="text-[12px] font-semibold uppercase tracking-wider" style={{ color: "#94a3b8" }}>
              Backtest history{windowLabel ? ` · ${windowLabel}` : ""}
            </div>
            <div className="text-[11px] text-text-muted">
              {trades.length} trades · {wins}W / {losses}L · {winRate}% WR
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span
            className="font-mono font-semibold text-sm"
            style={{ color: positive ? "#22c55e" : "#ef4444" }}
          >
            {positive ? "+" : ""}{fmt(totalPnl, sym, undefined, 0)}
          </span>
          <span
            style={{
              display: "inline-block",
              transform: open ? "rotate(180deg)" : "rotate(0deg)",
              transition: "transform 150ms",
              color: "#94a3b8",
            }}
          >
            ▾
          </span>
        </div>
      </button>

      {open && (
        <div style={{ borderTop: "1px solid #1e293b", padding: 12 }}>
          <div className="text-[11px] text-text-muted mb-2 px-1">
            These trades happened during the backtest replay. Live trading metrics in the tabs below start fresh from Go-Live.
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-muted" style={{ borderBottom: "1px solid #1e293b" }}>
                  <th className="text-left px-2 py-1.5 font-medium">Date</th>
                  <th className="text-left px-2 py-1.5 font-medium">Ticker</th>
                  <th className="text-left px-2 py-1.5 font-medium">Side</th>
                  <th className="text-right px-2 py-1.5 font-medium">Qty</th>
                  <th className="text-right px-2 py-1.5 font-medium">Entry → Exit</th>
                  <th className="text-right px-2 py-1.5 font-medium">P&amp;L</th>
                  <th className="text-left px-2 py-1.5 font-medium">Exit</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => {
                  const pnl = t.pnl ?? 0;
                  const win = pnl > 0;
                  const date = (t.exit_time || t.entry_time || "").split("T")[0];
                  return (
                    <tr key={t.id} style={{ borderBottom: "1px solid #1e293b" }}>
                      <td className="px-2 py-1.5 text-text-muted font-mono">{date}</td>
                      <td className="px-2 py-1.5 font-medium">{t.ticker}</td>
                      <td className="px-2 py-1.5 text-text-muted">{t.direction || "long"}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{t.quantity}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-text-muted">
                        {sym}{Number(t.entry_price).toFixed(2)} → {sym}{Number(t.exit_price ?? 0).toFixed(2)}
                      </td>
                      <td
                        className="px-2 py-1.5 text-right font-mono font-semibold"
                        style={{ color: win ? "#22c55e" : "#ef4444" }}
                      >
                        {win ? "+" : ""}{fmt(pnl, sym, undefined, 0)}
                      </td>
                      <td className="px-2 py-1.5 text-text-muted">{t.exit_type || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
