"use client";
import { useMemo, useState } from "react";
import { fmt } from "@/lib/api";
import type { ClosedTrade, SessionConfig } from "@/lib/types";

interface Props {
  trades: ClosedTrade[];
  config: SessionConfig | null;
}

type Filter = "all" | "wins" | "losses";

/** A unified per-trade card: entry + exit + reasoning in one place.
 *  The data already exists in the trades DB — this just stitches the
 *  thesis (entry reason) and exit reasoning side-by-side. */
export function DecisionFeed({ trades, config }: Props) {
  const sym = config?.currency_symbol || "$";
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    let xs = trades;
    if (filter === "wins") xs = xs.filter((t) => (t.pnl ?? 0) > 0);
    if (filter === "losses") xs = xs.filter((t) => (t.pnl ?? 0) < 0);
    if (query) {
      const q = query.toLowerCase();
      xs = xs.filter(
        (t) =>
          t.ticker.toLowerCase().includes(q) ||
          (t.reason || "").toLowerCase().includes(q) ||
          (t.exit_reason || "").toLowerCase().includes(q),
      );
    }
    return xs;
  }, [trades, filter, query]);

  const wins = trades.filter((t) => (t.pnl ?? 0) > 0).length;
  const losses = trades.filter((t) => (t.pnl ?? 0) < 0).length;

  return (
    <div
      className="rounded-2xl"
      style={{
        background: "linear-gradient(180deg, #151d2e 0%, #0c1424 100%)",
        border: "1px solid #1e293b",
      }}
    >
      <div className="flex items-center justify-between gap-3 p-4 border-b border-border">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">
            Decision Feed
          </h3>
          <p className="text-[11px] text-text-muted mt-0.5">
            Every entry and exit, with the AI&rsquo;s reasoning.
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <FilterPill label={`All ${trades.length}`} active={filter === "all"} onClick={() => setFilter("all")} accent="#94a3b8" />
          <FilterPill label={`Wins ${wins}`} active={filter === "wins"} onClick={() => setFilter("wins")} accent="#22c55e" />
          <FilterPill label={`Losses ${losses}`} active={filter === "losses"} onClick={() => setFilter("losses")} accent="#ef4444" />
        </div>
      </div>

      <div className="px-4 pt-3 pb-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter by ticker or text in the thesis..."
          className="w-full text-sm"
          style={{
            background: "#0a0e17",
            border: "1px solid #1e293b",
            borderRadius: 10,
            padding: "10px 12px",
            color: "#e2e8f0",
            minHeight: 40,
            fontSize: 14,
          }}
        />
      </div>

      <div className="p-2 md:p-3 space-y-2">
        {filtered.length === 0 ? (
          <div className="px-3 py-10 text-center text-sm text-text-muted">
            {trades.length === 0
              ? "No completed trades yet."
              : "Nothing matches that filter."}
          </div>
        ) : (
          filtered.map((t) => <TradeCard key={t.id} t={t} sym={sym} />)
        )}
      </div>
    </div>
  );
}

function FilterPill({
  label, active, onClick, accent,
}: { label: string; active: boolean; onClick: () => void; accent: string }) {
  return (
    <button
      onClick={onClick}
      className="text-[11px] font-semibold rounded-full px-2.5 py-1 transition-colors"
      style={{
        background: active ? `${accent}1f` : "transparent",
        border: `1px solid ${active ? accent + "55" : "#1e293b"}`,
        color: active ? accent : "#94a3b8",
        minHeight: 28,
      }}
    >
      {label}
    </button>
  );
}

function TradeCard({ t, sym }: { t: ClosedTrade; sym: string }) {
  const isLong = (t.direction || "").toLowerCase() === "long" ||
                 (t.action || "").toUpperCase() === "BUY";
  const pnl = t.pnl ?? 0;
  const winning = pnl > 0;
  const losing = pnl < 0;
  const accent = winning ? "#22c55e" : losing ? "#ef4444" : "#94a3b8";
  const dirIcon = isLong ? "▲" : "▼";
  const dirLabel = isLong ? "LONG" : "SHORT";
  const entryTime = formatTime(t.entry_time);
  const exitTime = formatTime(t.exit_time);
  const holdMin = computeHoldMinutes(t.entry_time, t.exit_time);
  const pnlPct = t.entry_price && t.exit_price
    ? ((isLong ? 1 : -1) * (t.exit_price - t.entry_price) / t.entry_price) * 100
    : null;

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{
        background: "#0a0e17",
        border: `1px solid ${accent}22`,
      }}
    >
      {/* Header row */}
      <div
        className="flex items-center justify-between gap-3 px-3.5 py-2.5"
        style={{ borderBottom: "1px solid #1e293b", background: `${accent}08` }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="rounded px-1.5 py-0.5 text-[10px] font-bold"
            style={{
              background: `${accent}1f`,
              color: accent,
              border: `1px solid ${accent}55`,
            }}
          >
            {dirIcon} {dirLabel}
          </span>
          <span className="font-mono font-semibold text-sm text-text-primary truncate">
            {t.ticker.replace(".NS", "")}
          </span>
          <span className="text-[11px] text-text-muted">
            ×{t.quantity}
          </span>
          {t.conviction != null && (
            <span
              className="text-[10px] uppercase tracking-wider font-semibold rounded px-1.5 py-0.5"
              style={{
                background: "rgba(96,165,250,0.10)",
                color: "#60a5fa",
                border: "1px solid rgba(96,165,250,0.25)",
              }}
              title="Conviction at entry (1-5)"
            >
              {"★".repeat(t.conviction)}
              <span style={{ opacity: 0.3 }}>
                {"★".repeat(Math.max(0, 5 - (t.conviction || 0)))}
              </span>
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <div className="text-right">
            <div
              className="text-sm font-bold tabular-nums"
              style={{ color: accent }}
            >
              {pnl > 0 ? "+" : ""}{sym}{fmt(Math.abs(pnl))}
            </div>
            {pnlPct != null && (
              <div className="text-[10px] tabular-nums" style={{ color: accent, opacity: 0.85 }}>
                {pnlPct > 0 ? "+" : ""}{pnlPct.toFixed(2)}%
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Entry */}
      <div className="px-3.5 py-2.5 flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-3">
          <div className="text-[11px] text-text-muted shrink-0">
            <span style={{ color: "#94a3b8" }}>● {entryTime}</span> ·{" "}
            <span style={{ color: accent }}>
              {isLong ? "Bought" : "Shorted"}
            </span>{" "}
            @ {sym}{fmt(t.entry_price)}
          </div>
        </div>
        {t.reason && (
          <div className="text-[12px] leading-relaxed" style={{ color: "#cbd5e1" }}>
            <span className="text-text-muted text-[10px] uppercase tracking-wider mr-1.5">Thesis</span>
            {t.reason}
          </div>
        )}
      </div>

      {/* Exit */}
      <div className="px-3.5 py-2.5 flex flex-col gap-1.5" style={{ borderTop: "1px dashed #1e293b" }}>
        <div className="flex items-center justify-between gap-3">
          <div className="text-[11px] text-text-muted">
            <span style={{ color: "#94a3b8" }}>○ {exitTime}</span> ·{" "}
            <span style={{ color: accent }}>
              {isLong ? "Sold" : "Covered"}
            </span>{" "}
            @ {t.exit_price != null ? `${sym}${fmt(t.exit_price)}` : "—"}
            {holdMin != null && (
              <span className="text-text-muted"> · held {holdMin}</span>
            )}
            {t.exit_type && (
              <span
                className="ml-1.5 text-[10px] uppercase tracking-wider rounded px-1.5 py-0.5"
                style={{
                  background: exitTypeBg(t.exit_type),
                  color: exitTypeFg(t.exit_type),
                  border: `1px solid ${exitTypeFg(t.exit_type)}33`,
                }}
              >
                {t.exit_type.replace(/_/g, " ")}
              </span>
            )}
          </div>
        </div>
        {t.exit_reason && (
          <div className="text-[12px] leading-relaxed" style={{ color: "#cbd5e1" }}>
            <span className="text-text-muted text-[10px] uppercase tracking-wider mr-1.5">Exit</span>
            {t.exit_reason}
          </div>
        )}
      </div>

      {t.llm_model && (
        <div className="px-3.5 py-1.5 text-[10px] text-text-muted" style={{ borderTop: "1px solid #0f172a" }}>
          model: <span className="font-mono">{t.llm_model}</span>
        </div>
      )}
    </div>
  );
}

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleString("en-IN", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return ts;
  }
}

function computeHoldMinutes(entry: string, exit: string | null): string | null {
  if (!entry || !exit) return null;
  try {
    const a = new Date(entry).getTime();
    const b = new Date(exit).getTime();
    if (!isFinite(a) || !isFinite(b)) return null;
    const m = Math.round((b - a) / 60000);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  } catch {
    return null;
  }
}

function exitTypeBg(t: string): string {
  if (t === "target") return "rgba(34,197,94,0.10)";
  if (t === "stop") return "rgba(239,68,68,0.10)";
  if (t === "forced_close") return "rgba(245,158,11,0.10)";
  if (t === "manual") return "rgba(96,165,250,0.10)";
  return "rgba(148,163,184,0.10)";
}
function exitTypeFg(t: string): string {
  if (t === "target") return "#22c55e";
  if (t === "stop") return "#ef4444";
  if (t === "forced_close") return "#fbbf24";
  if (t === "manual") return "#60a5fa";
  return "#94a3b8";
}
