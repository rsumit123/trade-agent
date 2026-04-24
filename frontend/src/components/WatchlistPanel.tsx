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

      {display.length === 0 ? (
        <div className="text-center py-10 text-text-muted text-sm px-4">No tickers in watchlist</div>
      ) : (
        <>
          {/* Mobile card list */}
          <div className="md:hidden divide-y divide-border/40 max-h-[420px] overflow-y-auto">
            {display.map((s) => {
              const change = s.change_pct || 0;
              const up = change >= 0;
              const ticker = s.ticker.replace(suffix, "");
              return (
                <div key={s.ticker} className="px-4 py-3">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-mono font-semibold text-text-primary text-sm truncate">{ticker}</span>
                    <span className={cn(
                      "font-mono text-sm font-semibold shrink-0",
                      up ? "text-accent-green" : "text-accent-red"
                    )}>
                      {up ? "+" : ""}{change.toFixed(2)}%
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] font-mono">
                    <span className="text-text-secondary">
                      {fmt(s.current_price, sym, undefined, 2)}
                    </span>
                    <div className="flex items-center gap-2">
                      {s.rsi_14 != null && (
                        <RSIChip value={s.rsi_14} />
                      )}
                      {s.price_vs_sma && (
                        <span className={cn(
                          "text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded",
                          s.price_vs_sma === "above"
                            ? "bg-accent-green/10 text-accent-green/90"
                            : "bg-accent-red/10 text-accent-red/90"
                        )}>
                          {s.price_vs_sma === "above" ? "↑ SMA" : "↓ SMA"}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Desktop compact rows */}
          <div className="hidden md:block max-h-[350px] overflow-y-auto">
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
                  {s.rsi_14 != null && <RSIChip value={s.rsi_14} />}
                  <span className={cn("font-mono text-xs font-medium min-w-[60px] text-right",
                    (s.change_pct || 0) >= 0 ? "text-accent-green" : "text-accent-red"
                  )}>
                    {(s.change_pct >= 0 ? "+" : "")}{s.change_pct?.toFixed(2) || "0.00"}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function RSIChip({ value }: { value: number }) {
  const color =
    value < 30 ? "text-accent-green bg-accent-green/10" :
    value > 70 ? "text-accent-red bg-accent-red/10" :
    "text-text-muted bg-bg-secondary/60";
  return (
    <span className={cn("text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded", color)}>
      RSI {value.toFixed(0)}
    </span>
  );
}
