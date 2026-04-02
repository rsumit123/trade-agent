"use client";
import { fmt, cn } from "@/lib/api";
import type { WatchlistItem, SessionConfig } from "@/lib/types";

export function WatchlistPanel({ items, config }: { items: WatchlistItem[]; config: SessionConfig | null }) {
  const sym = config?.currency_symbol || "$";
  const suffix = config?.ticker_suffix || ".NS";
  const display = items.slice(0, 20);

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">Watchlist</h3>
        <span className="text-xs font-mono text-text-muted bg-bg-secondary px-2 py-0.5 rounded">{items.length}</span>
      </div>
      <div className="max-h-[350px] overflow-y-auto">
        {display.map((s) => (
          <div key={s.ticker} className="flex items-center justify-between px-4 py-2 border-b border-border/30 hover:bg-bg-card-hover transition-colors">
            <div className="flex items-center gap-3">
              <span className="font-mono text-sm font-semibold text-text-primary w-24 truncate">
                {s.ticker.replace(suffix, "")}
              </span>
              <span className="font-mono text-xs text-text-secondary">
                {fmt(s.current_price, sym, undefined, 2)}
              </span>
            </div>
            <div className="flex items-center gap-4">
              {s.rsi_14 != null && (
                <span className={cn("text-[10px] font-mono",
                  s.rsi_14 < 30 ? "text-accent-green" : s.rsi_14 > 70 ? "text-accent-red" : "text-text-muted"
                )}>
                  RSI {s.rsi_14.toFixed(0)}
                </span>
              )}
              <span className={cn("font-mono text-xs font-medium min-w-[60px] text-right",
                (s.change_pct || 0) >= 0 ? "text-accent-green" : "text-accent-red"
              )}>
                {(s.change_pct >= 0 ? "+" : "")}{s.change_pct?.toFixed(2) || "0.00"}%
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
