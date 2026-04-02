"use client";
import { fmt, cn } from "@/lib/api";
import type { Performance, SessionConfig } from "@/lib/types";

export function PerformancePanel({ perf, config }: { perf: Performance | null; config: SessionConfig | null }) {
  const sym = config?.currency_symbol || "$";
  if (!perf || !perf.total_trades) return null;

  const rows = [
    { label: "Total Trades", value: String(perf.total_trades) },
    { label: "Wins", value: String(perf.wins), color: "text-accent-green" },
    { label: "Losses", value: String(perf.losses), color: "text-accent-red" },
    { label: "Win Rate", value: `${perf.win_rate}%`, color: perf.win_rate >= 50 ? "text-accent-green" : "text-accent-red" },
    { label: "Total P&L", value: fmt(perf.total_pnl, sym), color: perf.total_pnl >= 0 ? "text-accent-green" : "text-accent-red" },
    { label: "Avg Win", value: fmt(perf.avg_win, sym), color: "text-accent-green" },
    { label: "Avg Loss", value: fmt(perf.avg_loss, sym), color: "text-accent-red" },
    { label: "Best Trade", value: fmt(perf.best_trade, sym), color: "text-accent-green" },
    { label: "Worst Trade", value: fmt(perf.worst_trade, sym), color: "text-accent-red" },
  ];

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">Performance</h3>
      </div>
      <div className="p-4 space-y-2">
        {rows.map((r) => (
          <div key={r.label} className="flex justify-between items-center py-1">
            <span className="text-xs text-text-muted">{r.label}</span>
            <span className={cn("font-mono text-sm font-medium", r.color || "text-text-primary")}>{r.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
