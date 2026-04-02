"use client";
import { fmt, cn } from "@/lib/api";
import type { Holding, SessionConfig } from "@/lib/types";

export function HoldingsTable({ holdings, config }: { holdings: Holding[]; config: SessionConfig | null }) {
  const sym = config?.currency_symbol || "$";
  const suffix = config?.ticker_suffix || ".NS";

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">Open Holdings</h3>
        <span className="text-xs font-mono text-text-muted bg-bg-secondary px-2 py-0.5 rounded">{holdings.length}</span>
      </div>
      {holdings.length === 0 ? (
        <div className="text-center py-10 text-text-muted text-sm">No open positions</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[10px] uppercase tracking-wider text-text-muted border-b border-border">
                <th className="text-left px-4 py-2">Ticker</th>
                <th className="text-left px-2 py-2">Dir</th>
                <th className="text-right px-2 py-2">Qty</th>
                <th className="text-right px-2 py-2">Entry</th>
                <th className="text-right px-2 py-2">Current</th>
                <th className="text-right px-4 py-2">P&L</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((h) => (
                <tr key={h.trade_id} className="border-b border-border/50 hover:bg-bg-card-hover transition-colors">
                  <td className="px-4 py-2.5 font-semibold font-mono text-text-primary">{h.ticker.replace(suffix, "")}</td>
                  <td className="px-2 py-2.5">
                    <span className={cn(
                      "text-[10px] uppercase font-bold px-1.5 py-0.5 rounded",
                      h.direction === "long" ? "bg-accent-green/15 text-accent-green" : "bg-accent-red/15 text-accent-red"
                    )}>
                      {h.direction}
                    </span>
                  </td>
                  <td className="px-2 py-2.5 text-right font-mono text-text-secondary">{h.qty}</td>
                  <td className="px-2 py-2.5 text-right font-mono text-text-secondary">{fmt(h.entry_price, sym, undefined, 2)}</td>
                  <td className="px-2 py-2.5 text-right font-mono text-text-primary">{fmt(h.current_price, sym, undefined, 2)}</td>
                  <td className={cn("px-4 py-2.5 text-right font-mono font-medium", h.unrealized_pnl >= 0 ? "text-accent-green" : "text-accent-red")}>
                    {fmt(h.unrealized_pnl, sym, undefined, 0)}
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
