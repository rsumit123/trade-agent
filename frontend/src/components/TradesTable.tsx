"use client";
import { fmt, cn } from "@/lib/api";
import type { ClosedTrade, SessionConfig } from "@/lib/types";

export function TradesTable({ trades, config }: { trades: ClosedTrade[]; config: SessionConfig | null }) {
  const sym = config?.currency_symbol || "$";
  const suffix = config?.ticker_suffix || ".NS";

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">Recent Trades</h3>
        <span className="text-xs font-mono text-text-muted bg-bg-secondary px-2 py-0.5 rounded">{trades.length}</span>
      </div>
      {trades.length === 0 ? (
        <div className="text-center py-10 text-text-muted text-sm">No closed trades yet</div>
      ) : (
        <>
          {/* Mobile card list */}
          <div className="md:hidden divide-y divide-border/40 max-h-[420px] overflow-y-auto">
            {trades.map((t) => {
              const pnl = t.pnl || 0;
              const ticker = t.ticker.replace(suffix, "");
              return (
                <div key={t.id} className="px-4 py-3 hover:bg-bg-card-hover transition-colors">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="font-mono font-semibold text-text-primary text-sm">{ticker}</span>
                    <span className={cn(
                      "text-[10px] uppercase font-bold px-1.5 py-0.5 rounded",
                      t.direction === "long" ? "bg-accent-green/15 text-accent-green" : "bg-accent-red/15 text-accent-red"
                    )}>
                      {t.direction}
                    </span>
                    <span className="text-[10px] uppercase font-medium px-1.5 py-0.5 rounded bg-bg-secondary text-text-muted">
                      {t.trade_type}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono text-text-muted">
                      {fmt(t.entry_price, sym, undefined, 2)}
                      <span className="mx-1.5 opacity-40">→</span>
                      {fmt(t.exit_price, sym, undefined, 2)}
                    </span>
                    <span className={cn("text-sm font-mono font-semibold", pnl >= 0 ? "text-accent-green" : "text-accent-red")}>
                      {fmt(pnl, sym, undefined, 0)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto max-h-[400px] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-bg-card z-10">
                <tr className="text-[10px] uppercase tracking-wider text-text-muted border-b border-border">
                  <th className="text-left px-4 py-2">Ticker</th>
                  <th className="text-left px-2 py-2">Dir</th>
                  <th className="text-left px-2 py-2">Type</th>
                  <th className="text-right px-2 py-2">Entry</th>
                  <th className="text-right px-2 py-2">Exit</th>
                  <th className="text-right px-4 py-2">P&L</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} className="border-b border-border/50 hover:bg-bg-card-hover transition-colors">
                    <td className="px-4 py-2 font-semibold font-mono text-text-primary">{t.ticker.replace(suffix, "")}</td>
                    <td className="px-2 py-2">
                      <span className={cn(
                        "text-[10px] uppercase font-bold px-1.5 py-0.5 rounded",
                        t.direction === "long" ? "bg-accent-green/15 text-accent-green" : "bg-accent-red/15 text-accent-red"
                      )}>
                        {t.direction}
                      </span>
                    </td>
                    <td className="px-2 py-2">
                      <span className="text-[10px] uppercase font-medium px-1.5 py-0.5 rounded bg-bg-secondary text-text-muted">
                        {t.trade_type}
                      </span>
                    </td>
                    <td className="px-2 py-2 text-right font-mono text-text-secondary">{fmt(t.entry_price, sym, undefined, 2)}</td>
                    <td className="px-2 py-2 text-right font-mono text-text-primary">{fmt(t.exit_price, sym, undefined, 2)}</td>
                    <td className={cn("px-4 py-2 text-right font-mono font-medium", (t.pnl || 0) >= 0 ? "text-accent-green" : "text-accent-red")}>
                      {fmt(t.pnl, sym, undefined, 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
