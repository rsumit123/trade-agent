"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api, fmt, cn } from "@/lib/api";
import { useToast } from "@/components/Toast";
import type { ReplayDayData, SessionConfig, ThinkingEntry, ReplayTrade } from "@/lib/types";

const AUTOPLAY_MS = 1500;

export default function ReplayDayPage() {
  const params = useParams();
  const router = useRouter();
  const toast = useToast();
  const sessionId = params.id as string;
  const date = params.date as string;

  const [data, setData] = useState<ReplayDayData | null>(null);
  const [config, setConfig] = useState<SessionConfig | null>(null);
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const touchStartX = useRef<number | null>(null);
  const touchStartY = useRef<number | null>(null);

  const sym = config?.currency_symbol || "$";
  const suffix = config?.ticker_suffix || ".NS";

  // Load data + config
  useEffect(() => {
    Promise.all([
      api<ReplayDayData>(`/api/replay/${sessionId}?date=${date}`),
      api<{ config: SessionConfig }>(`/api/dashboard/${sessionId}`).catch(() => null),
    ])
      .then(([d, c]) => {
        setData(d);
        if (c?.config) setConfig(c.config);
        setLoading(false);
      })
      .catch((e) => {
        toast.error(e instanceof Error ? e.message : "Failed to load replay");
        setLoading(false);
      });
  }, [sessionId, date, toast]);

  const cycles = data?.cycles || [];
  const total = cycles.length;
  const current = cycles[idx];

  // Trades cumulative up to current cycle
  const tradesByCycleIdx = useMemo(() => {
    if (!data) return [];
    const out: ReplayTrade[][] = [];
    let upto: ReplayTrade[] = [];
    cycles.forEach((c) => {
      const cycleTs = c.ts;
      // Trades whose entry_time <= cycleTs (and same date) are now visible
      const visible = data.trades.filter(
        (t) => (t.entry_time || "") <= cycleTs && (t.entry_time || "").startsWith(date)
      );
      upto = visible;
      out.push(upto);
    });
    return out;
  }, [data, cycles, date]);

  // Cumulative day P&L from completed trades up to current cycle
  const cumulativePnl = useMemo(() => {
    if (!data || idx >= cycles.length) return 0;
    const cycleTs = cycles[idx]?.ts || "";
    return data.trades
      .filter((t) => (t.exit_time || "") && (t.exit_time || "") <= cycleTs)
      .reduce((s, t) => s + (t.pnl || 0), 0);
  }, [data, cycles, idx, date]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-play
  useEffect(() => {
    if (!playing || total === 0) return;
    if (idx >= total - 1) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setIdx((i) => Math.min(total - 1, i + 1)), AUTOPLAY_MS);
    return () => clearTimeout(t);
  }, [playing, idx, total]);

  // Keyboard nav
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") setIdx((i) => Math.max(0, i - 1));
      else if (e.key === "ArrowRight") setIdx((i) => Math.min(total - 1, i + 1));
      else if (e.key === " ") {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key === "Escape") {
        router.push(`/sessions/${sessionId}`);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [total, router, sessionId]);

  // Touch swipe gestures (mobile)
  const onTouchStart = (e: React.TouchEvent) => {
    touchStartX.current = e.touches[0].clientX;
    touchStartY.current = e.touches[0].clientY;
  };
  const onTouchEnd = (e: React.TouchEvent) => {
    if (touchStartX.current == null || touchStartY.current == null) return;
    const dx = e.changedTouches[0].clientX - touchStartX.current;
    const dy = e.changedTouches[0].clientY - touchStartY.current;
    // Horizontal swipe with min distance and dominantly horizontal
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
      if (dx < 0) setIdx((i) => Math.min(total - 1, i + 1));
      else setIdx((i) => Math.max(0, i - 1));
    }
    touchStartX.current = null;
    touchStartY.current = null;
  };

  // Pretty time of current cycle
  const cycleTime = useMemo(() => {
    if (!current) return "";
    const ts = current.ts || "";
    return ts.slice(11, 16); // HH:MM
  }, [current]);

  if (loading) {
    return (
      <div className="px-4 py-6 max-w-3xl mx-auto">
        <div className="skeleton h-12 w-full rounded-xl mb-3" />
        <div className="skeleton h-2 w-full rounded mb-4" />
        <div className="skeleton h-40 rounded-xl mb-3" />
        <div className="skeleton h-32 rounded-xl" />
      </div>
    );
  }

  if (!data || total === 0) {
    return (
      <div className="px-4 py-12 max-w-md mx-auto text-center">
        <h1 className="text-lg font-semibold mb-2">No replay data for {date}</h1>
        <p className="text-sm text-text-muted mb-6">
          This day has no captured reasoning cycles. Replay only works for days
          run after the thinking-log feature was deployed.
        </p>
        <button
          onClick={() => router.push(`/sessions/${sessionId}`)}
          className="px-4 py-2 rounded-lg bg-bg-secondary hover:bg-bg-card-hover text-sm"
          style={{ minHeight: 44 }}
        >
          Back to session
        </button>
      </div>
    );
  }

  const phase = current?.phase || "observed";
  const phaseColor =
    phase === "executed" ? "#22c55e" : phase === "rejected" ? "#f59e0b" : "#64748b";
  const progress = total > 1 ? (idx / (total - 1)) * 100 : 100;
  const visibleTrades = tradesByCycleIdx[idx] || [];

  return (
    <div
      ref={containerRef}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      className="min-h-screen flex flex-col"
      style={{ background: "#0a0e17" }}
    >
      {/* Sticky top bar */}
      <header
        className="sticky top-0 z-20 px-3 md:px-4 py-2.5 flex items-center justify-between gap-2"
        style={{
          background: "rgba(10,14,23,0.92)",
          backdropFilter: "blur(8px)",
          borderBottom: "1px solid #1e293b",
          paddingTop: "max(10px, env(safe-area-inset-top))",
        }}
      >
        <button
          onClick={() => router.push(`/sessions/${sessionId}`)}
          className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-sm text-text-secondary hover:bg-bg-card-hover"
          style={{ minHeight: 44 }}
          aria-label="Back to session"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
          <span className="hidden sm:inline">Back</span>
        </button>
        <div className="flex flex-col items-center min-w-0 flex-1">
          <span className="text-[10px] uppercase tracking-wider text-text-muted">Replay</span>
          <span className="font-mono text-sm font-semibold text-text-primary truncate">
            {date}
          </span>
        </div>
        <div className="font-mono text-xs text-text-muted tabular-nums" style={{ minWidth: 56, textAlign: "right" }}>
          {idx + 1}/{total}
        </div>
      </header>

      {/* Progress strip */}
      <div className="h-1" style={{ background: "#1e293b" }}>
        <div
          className="h-full transition-all"
          style={{
            width: `${progress}%`,
            background: `linear-gradient(90deg, #60a5fa, ${phaseColor})`,
          }}
        />
      </div>

      {/* Day stats strip */}
      {data.day_summary && (
        <div
          className="grid grid-cols-3 divide-x text-center"
          style={{ borderBottom: "1px solid #1e293b", borderColor: "#1e293b" }}
        >
          <DayStat
            label="Day P&L"
            value={`${data.day_summary.daily_pnl >= 0 ? "+" : ""}${fmt(data.day_summary.daily_pnl, sym, undefined, 0)}`}
            color={data.day_summary.daily_pnl >= 0 ? "#22c55e" : "#ef4444"}
          />
          <DayStat label="Trades" value={String(data.day_summary.trades || 0)} />
          <DayStat
            label="Win Rate"
            value={`${data.day_summary.win_rate ?? 0}%`}
            color={(data.day_summary.win_rate ?? 0) >= 50 ? "#22c55e" : "#ef4444"}
          />
        </div>
      )}

      {/* Main scrollable content */}
      <main className="flex-1 px-3 md:px-4 py-4 pb-32 md:pb-24 max-w-3xl mx-auto w-full">
        {current && (
          <>
            {/* Cycle hero */}
            <div
              className="rounded-xl p-4 mb-4"
              style={{
                background: "#151d2e",
                border: `1px solid ${phaseColor}33`,
                boxShadow: `0 0 24px -8px ${phaseColor}33`,
              }}
            >
              <div className="flex items-baseline justify-between gap-3 mb-2">
                <div className="flex items-baseline gap-3">
                  <span
                    className="font-mono font-bold tabular-nums leading-none"
                    style={{ fontSize: 32, color: phaseColor, textShadow: `0 0 16px ${phaseColor}33` }}
                  >
                    {cycleTime}
                  </span>
                  <span
                    className="text-[10px] uppercase tracking-wider font-bold px-2 py-1 rounded"
                    style={{
                      background: `${phaseColor}1a`,
                      color: phaseColor,
                      border: `1px solid ${phaseColor}40`,
                    }}
                  >
                    {phase}
                  </span>
                </div>
                <div className="text-right">
                  <div className="text-[9px] uppercase tracking-wider text-text-muted">P&L so far</div>
                  <div
                    className="font-mono font-semibold text-sm tabular-nums"
                    style={{ color: cumulativePnl >= 0 ? "#22c55e" : "#ef4444" }}
                  >
                    {cumulativePnl >= 0 ? "+" : ""}{fmt(cumulativePnl, sym, undefined, 0)}
                  </div>
                </div>
              </div>

              {current.placed.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {current.placed.map((p, j) => {
                    const buy = p.action === "BUY" || p.action === "COVER";
                    return (
                      <span
                        key={j}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] font-mono font-semibold"
                        style={{
                          background: buy ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)",
                          color: buy ? "#22c55e" : "#ef4444",
                          border: `1px solid ${buy ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)"}`,
                        }}
                      >
                        {p.action} {p.ticker?.replace(suffix, "")}
                        {p.qty ? ` × ${p.qty}` : ""}
                        {p.price ? ` @ ${sym}${p.price}` : ""}
                      </span>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Reasoning trail */}
            <div className="rounded-xl overflow-hidden mb-4" style={{ background: "#151d2e", border: "1px solid #1e293b" }}>
              <div className="px-4 py-2.5" style={{ borderBottom: "1px solid #1e293b" }}>
                <span className="text-[10px] uppercase tracking-wider font-semibold text-text-muted">
                  Agent Reasoning · {current.iterations} step{current.iterations === 1 ? "" : "s"}
                </span>
              </div>
              <div className="p-4 space-y-4 max-h-[60vh] overflow-y-auto">
                {current.trail.length === 0 ? (
                  <p className="text-sm text-text-muted italic">No reasoning text recorded.</p>
                ) : (
                  current.trail.map((it, i) => (
                    <div key={i} className="space-y-2 pb-3" style={{ borderBottom: i < current.trail.length - 1 ? "1px solid #1e293b" : "none" }}>
                      {it.text && (
                        <p className="text-sm text-text-secondary whitespace-pre-wrap leading-relaxed">
                          {it.text}
                        </p>
                      )}
                      {it.tool_calls.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {it.tool_calls.map((tc, j) => (
                            <span
                              key={j}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10px] font-mono"
                              style={{ background: "#0a0e17", border: "1px solid #1e293b", color: "#94a3b8" }}
                            >
                              <span style={{ color: "#60a5fa" }}>{tc.name}</span>
                              <span className="opacity-50">·</span>
                              <span className="truncate max-w-[200px]">{summarizeInput(tc.input)}</span>
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Open positions snapshot */}
            {visibleTrades.length > 0 && (
              <div className="rounded-xl overflow-hidden mb-4" style={{ background: "#151d2e", border: "1px solid #1e293b" }}>
                <div className="px-4 py-2.5 flex items-center justify-between" style={{ borderBottom: "1px solid #1e293b" }}>
                  <span className="text-[10px] uppercase tracking-wider font-semibold text-text-muted">
                    Trades So Far
                  </span>
                  <span className="text-[10px] font-mono text-text-muted">{visibleTrades.length}</span>
                </div>
                <div className="divide-y divide-border/30 max-h-72 overflow-y-auto">
                  {visibleTrades.map((t) => (
                    <TradeRow key={t.id} trade={t} sym={sym} suffix={suffix} cycleTs={current.ts} />
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </main>

      {/* Sticky bottom controls */}
      <footer
        className="fixed bottom-0 inset-x-0 z-30"
        style={{
          background: "rgba(10,14,23,0.95)",
          backdropFilter: "blur(8px)",
          borderTop: "1px solid #1e293b",
          paddingBottom: "max(8px, env(safe-area-inset-bottom))",
        }}
      >
        <div className="max-w-3xl mx-auto px-3 py-2 flex items-center gap-2">
          <button
            onClick={() => setIdx((i) => Math.max(0, i - 1))}
            disabled={idx === 0}
            className="flex-1 flex items-center justify-center gap-1.5 rounded-xl text-sm font-medium disabled:opacity-40"
            style={{
              background: "#151d2e",
              border: "1px solid #1e293b",
              color: "#cbd5e1",
              minHeight: 48,
            }}
            aria-label="Previous cycle"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
            <span className="hidden sm:inline">Prev</span>
          </button>
          <button
            onClick={() => setPlaying((p) => !p)}
            disabled={idx === total - 1 && !playing}
            className={cn(
              "flex items-center justify-center gap-1.5 rounded-xl text-sm font-semibold disabled:opacity-40 transition-colors",
            )}
            style={{
              background: playing ? "rgba(245,158,11,0.15)" : "rgba(96,165,250,0.15)",
              color: playing ? "#fbbf24" : "#60a5fa",
              border: `1px solid ${playing ? "rgba(245,158,11,0.4)" : "rgba(96,165,250,0.4)"}`,
              minHeight: 48,
              minWidth: 96,
              padding: "0 16px",
            }}
            aria-label={playing ? "Pause" : "Auto-play"}
          >
            {playing ? (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>
                <span>Pause</span>
              </>
            ) : (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                <span>Play</span>
              </>
            )}
          </button>
          <button
            onClick={() => setIdx((i) => Math.min(total - 1, i + 1))}
            disabled={idx === total - 1}
            className="flex-1 flex items-center justify-center gap-1.5 rounded-xl text-sm font-medium disabled:opacity-40"
            style={{
              background: "#151d2e",
              border: "1px solid #1e293b",
              color: "#cbd5e1",
              minHeight: 48,
            }}
            aria-label="Next cycle"
          >
            <span className="hidden sm:inline">Next</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18l6-6-6-6"/></svg>
          </button>
        </div>
        <div className="text-[10px] text-text-muted text-center pb-1">
          <span className="hidden md:inline">← / → to navigate · space to play · esc to exit</span>
          <span className="md:hidden">Swipe ← → to navigate</span>
        </div>
      </footer>
    </div>
  );
}

function DayStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="px-2 py-2" style={{ borderColor: "#1e293b" }}>
      <div className="text-[9px] uppercase tracking-wider text-text-muted">{label}</div>
      <div className="font-mono text-sm font-semibold tabular-nums" style={{ color: color || "#f1f5f9" }}>
        {value}
      </div>
    </div>
  );
}

function TradeRow({
  trade,
  sym,
  suffix,
  cycleTs,
}: {
  trade: ReplayTrade;
  sym: string;
  suffix: string;
  cycleTs: string;
}) {
  const ticker = trade.ticker.replace(suffix, "");
  const closed = trade.exit_time && trade.exit_time <= cycleTs;
  const pnl = closed ? (trade.pnl || 0) : 0;
  return (
    <div className="px-4 py-2.5 flex items-center justify-between gap-2">
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono text-sm font-semibold text-text-primary">{ticker}</span>
        <span
          className={cn(
            "text-[10px] uppercase font-bold px-1.5 py-0.5 rounded",
            trade.direction === "long" ? "bg-accent-green/15 text-accent-green" : "bg-accent-red/15 text-accent-red"
          )}
        >
          {trade.direction}
        </span>
        <span className="text-[10px] font-mono text-text-muted">
          {(trade.entry_time || "").slice(11, 16)}
          {closed && (
            <>
              <span className="opacity-50 mx-1">→</span>
              {(trade.exit_time || "").slice(11, 16)}
            </>
          )}
        </span>
      </div>
      <div className="text-right">
        {closed ? (
          <span className={cn("font-mono text-sm font-semibold tabular-nums", pnl >= 0 ? "text-accent-green" : "text-accent-red")}>
            {pnl >= 0 ? "+" : ""}{fmt(pnl, sym, undefined, 0)}
          </span>
        ) : (
          <span className="text-[10px] uppercase tracking-wider text-fbbf24" style={{ color: "#fbbf24" }}>OPEN</span>
        )}
      </div>
    </div>
  );
}

function summarizeInput(input: Record<string, unknown>): string {
  if (!input || typeof input !== "object") return "";
  const t = input.ticker || input.symbol;
  const a = input.action;
  if (a && t) return `${a} ${String(t).replace(".NS", "")}`;
  if (t) return String(t).replace(".NS", "");
  if (input.query) return `"${String(input.query).slice(0, 30)}"`;
  const keys = Object.keys(input);
  return keys.length ? `${keys[0]}=${String(input[keys[0]]).slice(0, 30)}` : "";
}
