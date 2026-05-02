"use client";
import { useMemo, useState } from "react";
import { fmt, cn } from "@/lib/api";
import { sectorOf, SECTOR_ORDER } from "@/lib/sectors";
import type { WatchlistItem, SessionConfig } from "@/lib/types";

type View = "sector" | "flat";

export function WatchlistPanel({ items, config }: { items: WatchlistItem[]; config: SessionConfig | null }) {
  const sym = config?.currency_symbol || "$";
  const suffix = config?.ticker_suffix || ".NS";
  const [view, setView] = useState<View>("sector");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const grouped = useMemo(() => {
    const map = new Map<string, WatchlistItem[]>();
    for (const it of items) {
      const sec = sectorOf(it.ticker);
      if (!map.has(sec)) map.set(sec, []);
      map.get(sec)!.push(it);
    }
    // Sort sectors by SECTOR_ORDER, with unknowns at the end
    const ordered: { sector: string; items: WatchlistItem[] }[] = [];
    for (const sec of SECTOR_ORDER) {
      if (map.has(sec)) {
        ordered.push({ sector: sec, items: map.get(sec)! });
        map.delete(sec);
      }
    }
    // Any leftover sectors not in SECTOR_ORDER
    for (const [sec, arr] of map) {
      ordered.push({ sector: sec, items: arr });
    }
    return ordered;
  }, [items]);

  const flat = useMemo(() => items.slice(0, 60), [items]);

  const toggle = (sec: string) => {
    const next = new Set(collapsed);
    if (next.has(sec)) next.delete(sec);
    else next.add(sec);
    setCollapsed(next);
  };

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border gap-2">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">Watchlist</h3>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-0.5 rounded-lg p-0.5" style={{ background: "#0a0e17", border: "1px solid #1e293b" }}>
            <ToggleBtn active={view === "sector"} onClick={() => setView("sector")}>Sector</ToggleBtn>
            <ToggleBtn active={view === "flat"} onClick={() => setView("flat")}>Flat</ToggleBtn>
          </div>
          <span className="text-xs font-mono text-text-muted bg-bg-secondary px-2 py-0.5 rounded">{items.length}</span>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="text-center py-10 text-text-muted text-sm px-4">No tickers in watchlist</div>
      ) : view === "sector" ? (
        <div className="max-h-[520px] overflow-y-auto">
          {grouped.map(({ sector, items: secItems }) => {
            const isCollapsed = collapsed.has(sector);
            const avgChange = secItems.reduce((s, x) => s + (x.change_pct || 0), 0) / secItems.length;
            const upCount = secItems.filter((x) => (x.change_pct || 0) >= 0).length;
            const sectorAccent =
              avgChange > 0.5 ? "#22c55e" : avgChange < -0.5 ? "#ef4444" : "#94a3b8";

            return (
              <div key={sector}>
                {/* Sector header */}
                <button
                  onClick={() => toggle(sector)}
                  className="w-full flex items-center justify-between px-4 py-2 transition-colors hover:bg-bg-card-hover sticky top-0"
                  style={{
                    background: "#0c1320",
                    borderBottom: "1px solid #1e293b",
                    minHeight: 36,
                    zIndex: 1,
                  }}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className="text-text-muted"
                      style={{
                        transform: isCollapsed ? "rotate(-90deg)" : "rotate(0)",
                        transition: "transform 0.15s",
                        fontSize: 9,
                      }}
                    >
                      ▼
                    </span>
                    <span className="text-[11px] uppercase font-bold tracking-[0.1em] text-text-primary">
                      {sector}
                    </span>
                    <span className="text-[10px] font-mono text-text-muted">
                      {secItems.length}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-[10px] font-mono text-text-muted">
                      <span className="text-accent-green">{upCount}</span>
                      <span className="opacity-40">/</span>
                      <span className="text-accent-red">{secItems.length - upCount}</span>
                    </span>
                    <span
                      className="font-mono text-xs font-semibold tabular-nums"
                      style={{ color: sectorAccent }}
                    >
                      {avgChange >= 0 ? "+" : ""}{avgChange.toFixed(2)}%
                    </span>
                  </div>
                </button>

                {/* Sector items */}
                {!isCollapsed && (
                  <div className="divide-y divide-border/30">
                    {secItems.map((s) => (
                      <TickerRow key={s.ticker} item={s} sym={sym} suffix={suffix} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="max-h-[520px] overflow-y-auto divide-y divide-border/40">
          {flat.map((s) => (
            <TickerRow key={s.ticker} item={s} sym={sym} suffix={suffix} />
          ))}
        </div>
      )}
    </div>
  );
}

function TickerRow({ item: s, sym, suffix }: { item: WatchlistItem; sym: string; suffix: string }) {
  const change = s.change_pct || 0;
  const up = change >= 0;
  const ticker = s.ticker.replace(suffix, "");
  return (
    <>
      {/* Mobile */}
      <div className="md:hidden px-4 py-2.5 hover:bg-bg-card-hover transition-colors">
        <div className="flex items-center justify-between mb-1">
          <span className="font-mono font-semibold text-text-primary text-sm truncate">{ticker}</span>
          <span className={cn(
            "font-mono text-sm font-semibold shrink-0 tabular-nums",
            up ? "text-accent-green" : "text-accent-red"
          )}>
            {up ? "+" : ""}{change.toFixed(2)}%
          </span>
        </div>
        <div className="flex items-center justify-between text-[11px] font-mono">
          <span className="text-text-secondary">{fmt(s.current_price, sym, undefined, 2)}</span>
          <div className="flex items-center gap-2">
            {s.rsi_14 != null && <RSIChip value={s.rsi_14} />}
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

      {/* Desktop */}
      <div className="hidden md:flex items-center justify-between px-4 py-1.5 hover:bg-bg-card-hover transition-colors">
        <div className="flex items-center gap-3 min-w-0">
          <span className="font-mono text-sm font-semibold text-text-primary w-24 truncate">
            {ticker}
          </span>
          <span className="font-mono text-xs text-text-secondary tabular-nums">
            {fmt(s.current_price, sym, undefined, 2)}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {s.rsi_14 != null && <RSIChip value={s.rsi_14} />}
          <span className={cn(
            "font-mono text-xs font-medium min-w-[64px] text-right tabular-nums",
            up ? "text-accent-green" : "text-accent-red"
          )}>
            {up ? "+" : ""}{change.toFixed(2)}%
          </span>
        </div>
      </div>
    </>
  );
}

function ToggleBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "px-2.5 py-1 text-[10px] uppercase tracking-wider font-semibold rounded-md transition-colors",
        active ? "text-text-primary" : "text-text-muted hover:text-text-secondary"
      )}
      style={{
        background: active ? "#1e293b" : "transparent",
        minHeight: 28,
      }}
    >
      {children}
    </button>
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
