"use client";
import { useState } from "react";
import { api, fmt, cn } from "@/lib/api";
import { useToast } from "./Toast";
import type { Holding, SessionConfig } from "@/lib/types";

export function HoldingsTable({
  holdings,
  config,
  sessionId,
  onLiquidated,
}: {
  holdings: Holding[];
  config: SessionConfig | null;
  sessionId?: string;
  onLiquidated?: () => void;
}) {
  const sym = config?.currency_symbol || "$";
  const suffix = config?.ticker_suffix || ".NS";
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const [liquidating, setLiquidating] = useState(false);

  const handleLiquidate = async () => {
    if (!sessionId) return;
    setLiquidating(true);
    try {
      const r = await api<{ closed: number; agent_stopped: boolean; errors?: { ticker: string }[] }>(
        `/api/sessions/${sessionId}/liquidate`,
        { method: "POST" }
      );
      const errCount = r.errors?.length || 0;
      if (errCount > 0) {
        toast.error(`Closed ${r.closed}, ${errCount} failed`);
      } else {
        toast.success(
          `Liquidated ${r.closed} position${r.closed === 1 ? "" : "s"}${
            r.agent_stopped ? " · agent stopped" : ""
          }`
        );
      }
      onLiquidated?.();
    } catch {
      toast.error("Liquidation failed");
    } finally {
      setLiquidating(false);
      setConfirming(false);
    }
  };

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border gap-2">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">Open Holdings</h3>
        <div className="flex items-center gap-2">
          {sessionId && holdings.length > 0 && (
            confirming ? (
              <div className="flex items-center gap-1">
                <button
                  onClick={handleLiquidate}
                  disabled={liquidating}
                  className="text-[10px] uppercase tracking-wider font-bold px-2 py-1 rounded transition-colors disabled:opacity-60"
                  style={{
                    background: "rgba(239,68,68,0.15)",
                    color: "#ef4444",
                    border: "1px solid rgba(239,68,68,0.4)",
                    minHeight: 28,
                  }}
                >
                  {liquidating ? "Closing..." : "Confirm"}
                </button>
                <button
                  onClick={() => setConfirming(false)}
                  disabled={liquidating}
                  className="text-[10px] uppercase tracking-wider font-medium px-2 py-1 rounded text-text-muted hover:text-text-primary"
                  style={{ minHeight: 28 }}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirming(true)}
                title="Stop agent and close all positions at market"
                className="text-[10px] uppercase tracking-wider font-medium px-2 py-1 rounded text-accent-red/80 hover:text-accent-red hover:bg-accent-red/10 transition-colors"
                style={{ minHeight: 28 }}
              >
                Liquidate All
              </button>
            )
          )}
          <span className="text-xs font-mono text-text-muted bg-bg-secondary px-2 py-0.5 rounded">{holdings.length}</span>
        </div>
      </div>
      {holdings.length === 0 ? (
        <div className="text-center py-10 text-text-muted text-sm">No open positions</div>
      ) : (
        <>
          {/* Mobile card list */}
          <div className="md:hidden divide-y divide-border/40 max-h-[420px] overflow-y-auto">
            {holdings.map((h) => {
              const pnl = h.unrealized_pnl || 0;
              const ticker = h.ticker.replace(suffix, "");
              return (
                <div key={h.trade_id} className="px-4 py-3 hover:bg-bg-card-hover transition-colors">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="font-mono font-semibold text-text-primary text-sm">{ticker}</span>
                    <span className={cn(
                      "text-[10px] uppercase font-bold px-1.5 py-0.5 rounded",
                      h.direction === "long" ? "bg-accent-green/15 text-accent-green" : "bg-accent-red/15 text-accent-red"
                    )}>
                      {h.direction}
                    </span>
                    <span className="text-[10px] font-mono text-text-muted ml-auto">
                      {h.qty} shares
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono text-text-muted">
                      {fmt(h.entry_price, sym, undefined, 2)}
                      <span className="mx-1.5 opacity-40">→</span>
                      {fmt(h.current_price, sym, undefined, 2)}
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
        </>
      )}
    </div>
  );
}
