"use client";
import { useEffect, useMemo, useState } from "react";
import { api, fmt, cn } from "@/lib/api";
import { useToast } from "./Toast";
import type { ComparisonStatus, ComparisonChild, SessionConfig } from "@/lib/types";

const AVAILABLE_MODELS: { id: string; label: string; tag?: string; price?: string }[] = [
  { id: "google/gemini-2.5-flash",       label: "Gemini 2.5 Flash",  tag: "Cheapest", price: "$0.15/M" },
  { id: "openai/gpt-4o-mini",            label: "GPT-4o Mini",       tag: "Cheapest", price: "$0.15/M" },
  { id: "anthropic/claude-haiku-4-5",    label: "Claude Haiku 4.5",  tag: "Fastest",  price: "$0.80/M" },
  { id: "deepseek/deepseek-chat-v3-0324",label: "DeepSeek V3",       tag: "Cheapest", price: "$0.14/M" },
  { id: "deepseek/deepseek-r1",          label: "DeepSeek R1",       tag: "Reasoning", price: "$0.55/M" },
  { id: "meta-llama/llama-4-maverick",   label: "Llama 4 Maverick",  tag: "Balanced", price: "$0.20/M" },
];

const COLORS = ["#60a5fa", "#22c55e", "#f59e0b", "#a78bfa", "#ef4444"];

interface Props {
  baseSessionId: string;
  config: SessionConfig | null;
  defaultStart?: string;
  defaultEnd?: string;
}

export function BacktestCompare({ baseSessionId, config, defaultStart, defaultEnd }: Props) {
  const toast = useToast();
  const [status, setStatus] = useState<ComparisonStatus | null>(null);
  const [picker, setPicker] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set([
    "google/gemini-2.5-flash",
    "anthropic/claude-haiku-4-5",
  ]));
  const [startDate, setStartDate] = useState(defaultStart || "");
  const [endDate, setEndDate] = useState(defaultEnd || "");
  const [busy, setBusy] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const sym = config?.currency_symbol || "$";

  // Poll status
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      api<ComparisonStatus>(`/api/backtest/compare/status/${baseSessionId}`)
        .then((d) => !cancelled && setStatus(d))
        .catch(() => {});
    };
    tick();
    const t = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [baseSessionId]);

  const isRunning = status?.status === "running";
  const isDone = status?.status === "completed";
  const isFailed = status?.status?.startsWith("failed:");
  const hasState = !!status && status.status !== "not_started";

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const handleStart = async () => {
    if (selected.size < 2) {
      toast.error("Pick at least 2 models");
      return;
    }
    if (!startDate || !endDate) {
      toast.error("Set start and end dates");
      return;
    }
    setBusy(true);
    try {
      await api(`/api/backtest/compare/start/${baseSessionId}`, {
        method: "POST",
        body: JSON.stringify({
          models: Array.from(selected),
          start_date: startDate,
          end_date: endDate,
          interval: "15minute",
          llm_provider: "openrouter",
        }),
      });
      toast.success(`Comparing ${selected.size} models`);
      setPicker(false);
      // Fetch immediately
      const d = await api<ComparisonStatus>(`/api/backtest/compare/status/${baseSessionId}`);
      setStatus(d);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to start");
    } finally {
      setBusy(false);
    }
  };

  const handleCleanup = async () => {
    setCleaning(true);
    try {
      await api(`/api/backtest/compare/cleanup/${baseSessionId}`, { method: "POST" });
      toast.success("Comparison cleared");
      setStatus({ status: "not_started" });
    } catch {
      toast.error("Cleanup failed");
    } finally {
      setCleaning(false);
    }
  };

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ background: "#151d2e", border: "1px solid #1e293b" }}
    >
      <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "#1e293b" }}>
        <div className="flex items-center gap-2">
          <span style={{ fontSize: 16 }}>⚖️</span>
          <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">
            Model Comparison
          </h3>
          {isRunning && (
            <span className="text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: "#1e293b", color: "#fbbf24" }}>
              RUNNING
            </span>
          )}
          {isDone && (
            <span className="text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: "rgba(34,197,94,0.15)", color: "#22c55e" }}>
              DONE
            </span>
          )}
        </div>
        {hasState && !isRunning && (
          <button
            onClick={handleCleanup}
            disabled={cleaning}
            className="text-[10px] uppercase tracking-wider font-medium px-2 py-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-secondary"
            style={{ minHeight: 28 }}
          >
            {cleaning ? "..." : "Clear"}
          </button>
        )}
      </div>

      {!hasState && !picker && (
        <div className="px-4 py-5 text-center">
          <p className="text-xs text-text-muted mb-3">
            Run the same backtest with multiple models to see which one performs best on this strategy.
          </p>
          <button
            onClick={() => setPicker(true)}
            className="text-sm font-medium px-4 py-2.5 rounded-lg"
            style={{
              background: "rgba(96,165,250,0.12)",
              color: "#60a5fa",
              border: "1px solid rgba(96,165,250,0.3)",
              minHeight: 44,
            }}
          >
            Set up comparison
          </button>
        </div>
      )}

      {picker && (
        <div className="p-4 space-y-4 border-b" style={{ borderColor: "#1e293b" }}>
          <div>
            <div className="text-[11px] uppercase tracking-wider font-semibold text-text-muted mb-2">
              Pick models (2-5)
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {AVAILABLE_MODELS.map((m) => {
                const isSelected = selected.has(m.id);
                return (
                  <button
                    key={m.id}
                    onClick={() => toggle(m.id)}
                    className={cn(
                      "text-left rounded-lg px-3 py-2.5 transition-colors",
                      isSelected ? "" : "hover:bg-bg-card-hover"
                    )}
                    style={{
                      background: isSelected ? "rgba(96,165,250,0.1)" : "#0a0e17",
                      border: `1px solid ${isSelected ? "#60a5fa" : "#1e293b"}`,
                      minHeight: 56,
                    }}
                  >
                    <div className="flex items-center justify-between mb-0.5">
                      <span className="text-sm font-medium text-text-primary">{m.label}</span>
                      {isSelected && (
                        <span style={{ color: "#60a5fa", fontSize: 12 }}>✓</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 text-[10px]">
                      {m.tag && (
                        <span className="text-text-muted uppercase tracking-wider">{m.tag}</span>
                      )}
                      {m.price && (
                        <span className="text-text-muted font-mono">{m.price}</span>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[11px] text-text-muted mb-1">Start</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full text-sm"
                style={{ minHeight: 44, fontSize: 16 }}
              />
            </div>
            <div>
              <label className="block text-[11px] text-text-muted mb-1">End</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full text-sm"
                style={{ minHeight: 44, fontSize: 16 }}
              />
            </div>
          </div>
          <div className="text-[10px] text-text-muted">
            Comparisons run sequentially (~3-5 min per model per backtest day). With {selected.size} models and a 5-day window, expect ~{selected.size * 5 * 4}min total.
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleStart}
              disabled={busy || selected.size < 2 || !startDate || !endDate}
              className="flex-1 text-sm font-semibold rounded-lg disabled:opacity-50"
              style={{
                background: "#22c55e",
                color: "#fff",
                minHeight: 44,
              }}
            >
              {busy ? "Starting..." : `Start (${selected.size} models)`}
            </button>
            <button
              onClick={() => setPicker(false)}
              disabled={busy}
              className="px-4 text-sm rounded-lg text-text-muted"
              style={{ background: "#0a0e17", border: "1px solid #1e293b", minHeight: 44 }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {hasState && status?.children && (
        <div className="p-4 space-y-3">
          {/* Per-child rows */}
          {status.children.map((child, i) => (
            <ChildRow key={child.session_id} child={child} color={COLORS[i % COLORS.length]} sym={sym} />
          ))}

          {/* Overlay equity curve when at least 2 children completed */}
          {isDone && (
            <ComparisonChart children={status.children} colors={COLORS} />
          )}

          {/* Final stats table */}
          {isDone && <StatsTable children={status.children} colors={COLORS} sym={sym} />}

          {isFailed && (
            <p className="text-xs" style={{ color: "#ef4444" }}>
              {status?.status}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function ChildRow({ child, color, sym }: { child: ComparisonChild; color: string; sym: string }) {
  const phase = child.progress?.current_phase || "—";
  const day = child.progress?.current_day;
  const total = child.progress?.total_days;
  const pct = day && total ? (day / total) * 100 : 0;
  const summary = child.summary;
  const isRunning = child.status === "running";
  const isDone = child.status === "completed";
  const isFailed = (child.status || "").startsWith("failed");

  return (
    <div
      className="rounded-lg p-3"
      style={{
        background: "#0a0e17",
        border: `1px solid ${isRunning ? color : "#1e293b"}`,
      }}
    >
      <div className="flex items-center justify-between mb-2 gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="inline-block w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
          <span className="text-sm font-medium text-text-primary truncate">{child.model}</span>
        </div>
        <span
          className="text-[10px] uppercase tracking-wider font-mono px-1.5 py-0.5 rounded flex-shrink-0"
          style={{
            background: isDone ? "rgba(34,197,94,0.15)" : isFailed ? "rgba(239,68,68,0.15)" : "rgba(245,158,11,0.15)",
            color: isDone ? "#22c55e" : isFailed ? "#ef4444" : "#fbbf24",
          }}
        >
          {child.status}
        </span>
      </div>

      {isRunning && (
        <>
          <div className="flex items-center justify-between text-[10px] text-text-muted mb-1">
            <span>{phase}{child.progress?.phase_detail ? ` · ${child.progress.phase_detail}` : ""}</span>
            {day && total && <span className="font-mono">Day {day}/{total}</span>}
          </div>
          <div className="h-1 rounded-full overflow-hidden" style={{ background: "#1e293b" }}>
            <div className="h-full transition-all" style={{ width: `${pct}%`, background: color }} />
          </div>
        </>
      )}

      {isDone && summary && (
        <div className="grid grid-cols-4 gap-2 text-xs">
          <Stat label="Return" value={summary.total_return_pct != null ? `${summary.total_return_pct >= 0 ? "+" : ""}${summary.total_return_pct.toFixed(2)}%` : "—"} positive={(summary.total_return_pct ?? 0) >= 0} />
          <Stat label="P&L" value={`${summary.total_pnl >= 0 ? "+" : ""}${fmt(summary.total_pnl, sym, undefined, 0)}`} positive={summary.total_pnl >= 0} />
          <Stat label="Trades" value={String(summary.total_trades)} />
          <Stat label="Win %" value={summary.win_rate != null ? `${summary.win_rate}%` : "—"} positive={(summary.win_rate ?? 0) >= 50} />
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wider text-text-muted">{label}</div>
      <div
        className="font-mono text-xs font-semibold"
        style={{ color: positive == null ? "#f1f5f9" : positive ? "#22c55e" : "#ef4444" }}
      >
        {value}
      </div>
    </div>
  );
}

// ── Overlay equity curve ─────────────────────────────────
function ComparisonChart({ children, colors }: { children: ComparisonChild[]; colors: string[] }) {
  const W = 600;
  const H = 200;
  const PAD_L = 40;
  const PAD_R = 8;
  const PAD_T = 8;
  const PAD_B = 22;

  const series = useMemo(() => {
    const out: { model: string; color: string; pts: number[]; dates: string[] }[] = [];
    children.forEach((c, i) => {
      if (!c.summary?.days?.length) return;
      const days = [...c.summary.days].sort((a, b) => a.date.localeCompare(b.date));
      out.push({
        model: c.model,
        color: colors[i % colors.length],
        pts: days.map((d) => d.total_return_pct || 0),
        dates: days.map((d) => d.date),
      });
    });
    return out;
  }, [children, colors]);

  if (series.length < 1) return null;

  const allVals = series.flatMap((s) => s.pts).concat([0]);
  const minV = Math.min(...allVals);
  const maxV = Math.max(...allVals);
  const span = maxV - minV || 1;
  const yMin = minV - span * 0.1;
  const yMax = maxV + span * 0.1;
  const yRange = yMax - yMin;

  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const longest = series.reduce((m, s) => Math.max(m, s.pts.length), 0);
  const x = (i: number) => (longest <= 1 ? PAD_L + innerW / 2 : PAD_L + (i / (longest - 1)) * innerW);
  const y = (v: number) => PAD_T + ((yMax - v) / yRange) * innerH;

  const yTicks = [yMin, (yMin + yMax) / 2, yMax];

  return (
    <div className="rounded-lg p-3" style={{ background: "#0a0e17", border: "1px solid #1e293b" }}>
      <div className="text-[10px] uppercase tracking-wider font-semibold text-text-muted mb-2">
        Equity Curves (Cumulative Return %)
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full" style={{ height: H }}>
        {/* Y grid */}
        {yTicks.map((v, i) => {
          const yy = y(v);
          return (
            <g key={i}>
              <line
                x1={PAD_L}
                x2={W - PAD_R}
                y1={yy}
                y2={yy}
                stroke="#1e293b"
                strokeDasharray="2,3"
                strokeWidth={0.5}
              />
              <text x={PAD_L - 5} y={yy + 3} textAnchor="end" fontSize="9" fill="#64748b" fontFamily="monospace">
                {v >= 0 ? "+" : ""}{v.toFixed(1)}%
              </text>
            </g>
          );
        })}
        {/* Zero line */}
        {y(0) >= PAD_T && y(0) <= PAD_T + innerH && (
          <line x1={PAD_L} x2={W - PAD_R} y1={y(0)} y2={y(0)} stroke="#475569" strokeWidth={0.7} />
        )}
        {/* Series */}
        {series.map((s) => {
          const pts = s.pts.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
          if (pts.length === 0) return null;
          return (
            <g key={s.model}>
              <path
                d={`M ${pts.join(" L ")}`}
                fill="none"
                stroke={s.color}
                strokeWidth={1.8}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              <circle cx={x(s.pts.length - 1)} cy={y(s.pts[s.pts.length - 1])} r={2.5} fill={s.color} />
            </g>
          );
        })}
      </svg>
      {/* Legend */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1.5">
        {series.map((s) => (
          <div key={s.model} className="flex items-center gap-1.5 text-[10px]">
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: s.color }} />
            <span className="text-text-secondary truncate max-w-[180px]">{s.model}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function StatsTable({ children, colors, sym }: { children: ComparisonChild[]; colors: string[]; sym: string }) {
  const completed = children.filter((c) => c.summary);
  if (completed.length === 0) return null;
  const sorted = [...completed].sort((a, b) => (b.summary!.total_pnl || 0) - (a.summary!.total_pnl || 0));

  return (
    <div className="rounded-lg overflow-hidden" style={{ background: "#0a0e17", border: "1px solid #1e293b" }}>
      <div className="px-3 py-2 border-b" style={{ borderColor: "#1e293b" }}>
        <span className="text-[10px] uppercase tracking-wider font-semibold text-text-muted">Leaderboard</span>
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider text-text-muted" style={{ borderBottom: "1px solid #1e293b" }}>
            <th className="text-left px-3 py-1.5">#</th>
            <th className="text-left px-2 py-1.5">Model</th>
            <th className="text-right px-2 py-1.5">Return</th>
            <th className="text-right px-2 py-1.5">P&L</th>
            <th className="text-right px-2 py-1.5">Trades</th>
            <th className="text-right px-3 py-1.5">Win %</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c, rank) => {
            const s = c.summary!;
            const colorIdx = children.findIndex((cc) => cc.session_id === c.session_id);
            const color = colors[colorIdx % colors.length];
            return (
              <tr key={c.session_id} style={{ borderBottom: "1px solid #0f172a" }}>
                <td className="px-3 py-2 font-mono text-text-muted">{rank + 1}</td>
                <td className="px-2 py-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="inline-block w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
                    <span className="text-text-primary truncate">{c.model}</span>
                  </div>
                </td>
                <td className="px-2 py-2 text-right font-mono" style={{ color: (s.total_return_pct ?? 0) >= 0 ? "#22c55e" : "#ef4444" }}>
                  {s.total_return_pct != null ? `${s.total_return_pct >= 0 ? "+" : ""}${s.total_return_pct.toFixed(2)}%` : "—"}
                </td>
                <td className="px-2 py-2 text-right font-mono" style={{ color: s.total_pnl >= 0 ? "#22c55e" : "#ef4444" }}>
                  {s.total_pnl >= 0 ? "+" : ""}{fmt(s.total_pnl, sym, undefined, 0)}
                </td>
                <td className="px-2 py-2 text-right font-mono text-text-secondary">{s.total_trades}</td>
                <td className="px-3 py-2 text-right font-mono" style={{ color: (s.win_rate ?? 0) >= 50 ? "#22c55e" : "#ef4444" }}>
                  {s.win_rate != null ? `${s.win_rate}%` : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
