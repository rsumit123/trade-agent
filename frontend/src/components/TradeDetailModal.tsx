"use client";
import { useEffect } from "react";
import { fmt, cn } from "@/lib/api";
import type { ClosedTrade, SessionConfig } from "@/lib/types";

export function TradeDetailModal({
  trade,
  config,
  onClose,
}: {
  trade: ClosedTrade | null;
  config: SessionConfig | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!trade) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [trade, onClose]);

  if (!trade) return null;

  const sym = config?.currency_symbol || "$";
  const suffix = config?.ticker_suffix || ".NS";
  const pnl = trade.pnl || 0;
  const pnlPositive = pnl >= 0;

  // Compute hold time
  const entry = new Date(trade.entry_time);
  const exit = new Date(trade.exit_time);
  const holdMs = Math.max(0, exit.getTime() - entry.getTime());
  const holdLabel = formatDuration(holdMs);

  // Compute return %
  const grossEntry = trade.entry_price * trade.quantity;
  const returnPct =
    grossEntry > 0
      ? trade.direction === "long"
        ? ((trade.exit_price - trade.entry_price) / trade.entry_price) * 100
        : ((trade.entry_price - trade.exit_price) / trade.entry_price) * 100
      : 0;

  return (
    <div
      className="fixed inset-0 z-[10000] flex items-end md:items-center justify-center p-0 md:p-4"
      style={{ background: "rgba(0,0,0,0.7)", backdropFilter: "blur(4px)" }}
      onClick={onClose}
    >
      <div
        className="w-full md:max-w-2xl max-h-[92vh] overflow-y-auto rounded-t-2xl md:rounded-2xl"
        style={{
          background: "#0f172a",
          border: "1px solid #1e293b",
          paddingBottom: "env(safe-area-inset-bottom)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="sticky top-0 px-4 md:px-5 py-3 flex items-center justify-between border-b"
          style={{ background: "#0f172a", borderColor: "#1e293b", zIndex: 1 }}
        >
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-mono font-bold text-text-primary text-base">
              {trade.ticker.replace(suffix, "")}
            </span>
            <span
              className={cn(
                "text-[10px] uppercase font-bold px-1.5 py-0.5 rounded",
                trade.direction === "long"
                  ? "bg-accent-green/15 text-accent-green"
                  : "bg-accent-red/15 text-accent-red"
              )}
            >
              {trade.direction}
            </span>
            <span
              className="text-[10px] uppercase font-medium px-1.5 py-0.5 rounded"
              style={{ background: "#1e293b", color: "#94a3b8" }}
            >
              {trade.trade_type}
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary p-2 -mr-2"
            style={{ minHeight: 44, minWidth: 44 }}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* P&L hero */}
        <div className="px-4 md:px-5 py-4 border-b" style={{ borderColor: "#1e293b" }}>
          <div className="text-[10px] uppercase tracking-wider font-semibold text-text-muted mb-1">
            Realized P&L
          </div>
          <div className="flex items-baseline gap-3">
            <span
              className={cn(
                "font-mono text-2xl md:text-3xl font-bold",
                pnlPositive ? "text-accent-green" : "text-accent-red"
              )}
            >
              {pnlPositive ? "+" : ""}
              {fmt(pnl, sym, undefined, 0)}
            </span>
            <span
              className={cn(
                "font-mono text-sm font-semibold",
                pnlPositive ? "text-accent-green" : "text-accent-red"
              )}
            >
              {returnPct >= 0 ? "+" : ""}
              {returnPct.toFixed(2)}%
            </span>
          </div>
        </div>

        {/* Stats grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 divide-x divide-y md:divide-y-0 border-b" style={{ borderColor: "#1e293b" }}>
          <Stat label="Entry" value={fmt(trade.entry_price, sym, undefined, 2)} />
          <Stat label="Exit" value={fmt(trade.exit_price, sym, undefined, 2)} />
          <Stat label="Quantity" value={String(trade.quantity)} />
          <Stat label="Hold" value={holdLabel} />
        </div>

        {/* Times */}
        <div className="px-4 md:px-5 py-3 border-b text-xs space-y-1" style={{ borderColor: "#1e293b" }}>
          <div className="flex justify-between">
            <span className="text-text-muted">Entered</span>
            <span className="font-mono text-text-secondary">{formatTs(trade.entry_time)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-muted">Exited</span>
            <span className="font-mono text-text-secondary">{formatTs(trade.exit_time)}</span>
          </div>
        </div>

        {/* Entry thesis */}
        {trade.reason && (
          <Section title="Entry Thesis" accent="#60a5fa">
            <p className="text-sm text-text-secondary whitespace-pre-wrap leading-relaxed">
              {trade.reason}
            </p>
          </Section>
        )}

        {/* Exit reason */}
        {trade.exit_reason && (
          <Section
            title="Exit Reason"
            accent={
              /stop/i.test(trade.exit_reason)
                ? "#ef4444"
                : /target|profit/i.test(trade.exit_reason)
                ? "#22c55e"
                : "#f59e0b"
            }
          >
            <p className="text-sm text-text-secondary whitespace-pre-wrap leading-relaxed">
              {trade.exit_reason}
            </p>
          </Section>
        )}

        {/* Footer spacer for safe-area */}
        <div style={{ height: 12 }} />
      </div>
    </div>
  );
}

function Section({
  title,
  accent,
  children,
}: {
  title: string;
  accent: string;
  children: React.ReactNode;
}) {
  return (
    <div className="px-4 md:px-5 py-3 border-b" style={{ borderColor: "#1e293b" }}>
      <div className="flex items-center gap-2 mb-2">
        <span
          className="inline-block w-1 h-3 rounded"
          style={{ background: accent }}
        />
        <h4 className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: accent }}>
          {title}
        </h4>
      </div>
      {children}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-4 py-3" style={{ borderColor: "#1e293b" }}>
      <div className="text-[10px] uppercase tracking-wider font-semibold text-text-muted mb-0.5">
        {label}
      </div>
      <div className="font-mono text-sm font-semibold text-text-primary">{value}</div>
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms <= 0) return "0m";
  const totalMin = Math.floor(ms / 60000);
  if (totalMin < 60) return `${totalMin}m`;
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h < 24) return `${h}h ${m}m`;
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return `${d}d ${rh}h`;
}

function formatTs(ts: string): string {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return ts;
  }
}
