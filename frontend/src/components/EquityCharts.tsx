"use client";
import { useEffect, useMemo, useState } from "react";
import { api, fmt, cn } from "@/lib/api";
import type { DailyPerformance, SessionConfig } from "@/lib/types";

type View = "equity" | "drawdown" | "pnl";

const W = 600; // viewBox width — scales via preserveAspectRatio
const H = 180;
const PAD_L = 38;
const PAD_R = 8;
const PAD_T = 8;
const PAD_B = 22;

interface Props {
  sessionId: string;
  config: SessionConfig | null;
  // Optionally pre-bundled daily series; if not given, fetch
  daily?: DailyPerformance[];
}

export function EquityCharts({ sessionId, config, daily }: Props) {
  const [rows, setRows] = useState<DailyPerformance[]>(daily || []);
  const [view, setView] = useState<View>("equity");
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const sym = config?.currency_symbol || "$";

  useEffect(() => {
    if (daily && daily.length) {
      setRows(daily);
      return;
    }
    api<DailyPerformance[]>(`/api/performance/daily?session=${sessionId}&limit=60`)
      .then((d) => Array.isArray(d) && setRows(d))
      .catch(() => {});
  }, [sessionId, daily]);

  const stats = useMemo(() => {
    if (rows.length === 0) return null;
    // rows come oldest → newest from API; verify and sort
    const sorted = [...rows].sort((a, b) => a.date.localeCompare(b.date));

    // Equity series — prefer cumulative_return_pct; fallback to total_value normalized
    const startCap = config?.starting_capital || 0;
    const equityPct: number[] = sorted.map((r, i) => {
      if (r.cumulative_return_pct != null) return r.cumulative_return_pct;
      if (r.total_value != null && startCap > 0) return ((r.total_value - startCap) / startCap) * 100;
      // fallback: cumulative sum of daily_pnl as % of startCap
      const sum = sorted.slice(0, i + 1).reduce((s, x) => s + (x.daily_pnl || 0), 0);
      return startCap > 0 ? (sum / startCap) * 100 : 0;
    });

    // Drawdown series (always ≤ 0) — running max minus current
    const dd: number[] = [];
    let peak = equityPct[0] ?? 0;
    for (const v of equityPct) {
      if (v > peak) peak = v;
      dd.push(v - peak); // 0 or negative
    }

    const dailyPnl = sorted.map((r) => r.daily_pnl || 0);

    const last = equityPct[equityPct.length - 1] ?? 0;
    const maxDd = Math.min(...dd, 0);
    const totalPnl = sorted.reduce((s, r) => s + (r.daily_pnl || 0), 0);
    const wins = sorted.filter((r) => (r.daily_pnl || 0) > 0).length;
    const losses = sorted.filter((r) => (r.daily_pnl || 0) < 0).length;

    return { sorted, equityPct, dd, dailyPnl, last, maxDd, totalPnl, wins, losses };
  }, [rows, config?.starting_capital]);

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border gap-3">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">
          Performance
        </h3>
        <div className="flex items-center gap-1 bg-bg-secondary rounded-lg p-0.5">
          {(
            [
              { id: "equity", label: "Equity" },
              { id: "drawdown", label: "Drawdown" },
              { id: "pnl", label: "Daily P&L" },
            ] as { id: View; label: string }[]
          ).map((t) => (
            <button
              key={t.id}
              onClick={() => setView(t.id)}
              className={cn(
                "px-2.5 py-1.5 text-[11px] font-medium rounded-md transition-colors",
                view === t.id
                  ? "bg-bg-card text-text-primary"
                  : "text-text-muted hover:text-text-secondary"
              )}
              style={{ minHeight: 28 }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Stats row */}
      {stats && (
        <div className="grid grid-cols-3 divide-x divide-border/40 border-b border-border/40">
          <Stat
            label={view === "equity" ? "Total Return" : view === "drawdown" ? "Max DD" : "Total P&L"}
            value={
              view === "equity"
                ? `${stats.last >= 0 ? "+" : ""}${stats.last.toFixed(2)}%`
                : view === "drawdown"
                ? `${stats.maxDd.toFixed(2)}%`
                : `${stats.totalPnl >= 0 ? "+" : ""}${fmt(stats.totalPnl, sym, "en-US", 0)}`
            }
            positive={
              view === "drawdown"
                ? stats.maxDd > -2
                : view === "equity"
                ? stats.last >= 0
                : stats.totalPnl >= 0
            }
          />
          <Stat label="Days" value={String(stats.sorted.length)} muted />
          <Stat
            label="W / L"
            value={
              <>
                <span className="text-accent-green">{stats.wins}</span>
                <span className="text-text-muted"> / </span>
                <span className="text-accent-red">{stats.losses}</span>
              </>
            }
          />
        </div>
      )}

      {/* Chart */}
      <div className="px-2 py-3 md:px-4">
        {!stats || stats.sorted.length < 1 ? (
          <div className="text-center py-12 text-text-muted text-sm">
            No daily data yet — recorded after each daily review
          </div>
        ) : view === "equity" ? (
          <LineChart
            values={stats.equityPct}
            dates={stats.sorted.map((r) => r.date)}
            yFormat={(v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`}
            colorize
            zeroLine
            hoverIdx={hoverIdx}
            onHover={setHoverIdx}
          />
        ) : view === "drawdown" ? (
          <LineChart
            values={stats.dd}
            dates={stats.sorted.map((r) => r.date)}
            yFormat={(v) => `${v.toFixed(2)}%`}
            negativeOnly
            hoverIdx={hoverIdx}
            onHover={setHoverIdx}
          />
        ) : (
          <BarChart
            values={stats.dailyPnl}
            dates={stats.sorted.map((r) => r.date)}
            yFormat={(v) => `${v >= 0 ? "+" : ""}${fmt(v, sym, "en-US", 0)}`}
            hoverIdx={hoverIdx}
            onHover={setHoverIdx}
          />
        )}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  positive,
  muted,
}: {
  label: string;
  value: React.ReactNode;
  positive?: boolean;
  muted?: boolean;
}) {
  return (
    <div className="px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wider font-semibold text-text-muted mb-0.5">
        {label}
      </div>
      <div
        className={cn(
          "font-mono text-sm font-semibold",
          muted
            ? "text-text-secondary"
            : positive == null
            ? "text-text-primary"
            : positive
            ? "text-accent-green"
            : "text-accent-red"
        )}
      >
        {value}
      </div>
    </div>
  );
}

// ── Line chart (equity / drawdown) ──────────────────────────
function LineChart({
  values,
  dates,
  yFormat,
  colorize,
  zeroLine,
  negativeOnly,
  hoverIdx,
  onHover,
}: {
  values: number[];
  dates: string[];
  yFormat: (v: number) => string;
  colorize?: boolean;
  zeroLine?: boolean;
  negativeOnly?: boolean;
  hoverIdx: number | null;
  onHover: (i: number | null) => void;
}) {
  if (values.length === 0) return null;

  const minV = Math.min(...values, negativeOnly ? 0 : values[0]);
  const maxV = Math.max(...values, negativeOnly ? 0 : 0);
  // Pad y range slightly
  const span = (maxV - minV) || 1;
  const yMin = minV - span * 0.08;
  const yMax = maxV + span * 0.08;
  const yRange = yMax - yMin || 1;

  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const x = (i: number) =>
    values.length === 1 ? PAD_L + innerW / 2 : PAD_L + (i / (values.length - 1)) * innerW;
  const y = (v: number) => PAD_T + ((yMax - v) / yRange) * innerH;
  const yZero = y(0);

  const last = values[values.length - 1];
  const isPositive = colorize ? last >= 0 : false;
  const stroke = negativeOnly ? "#ef4444" : isPositive ? "#22c55e" : "#ef4444";

  const linePts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  const pathD = `M ${linePts.join(" L ")}`;
  // Area: clamped to zero baseline (or chart bottom for drawdown)
  const baselineY = negativeOnly ? y(0) : Math.min(Math.max(yZero, PAD_T), PAD_T + innerH);
  const areaD = `${pathD} L ${x(values.length - 1).toFixed(1)},${baselineY.toFixed(1)} L ${x(0).toFixed(1)},${baselineY.toFixed(1)} Z`;

  const gradId = `eq-grad-${negativeOnly ? "dd" : isPositive ? "up" : "dn"}`;

  // Y ticks (3 lines)
  const yTicks = [yMin + (yMax - yMin) * 0.0, yMin + (yMax - yMin) * 0.5, yMax];

  // X ticks — first, mid, last date
  const xTickIdx =
    values.length <= 3
      ? values.map((_, i) => i)
      : [0, Math.floor((values.length - 1) / 2), values.length - 1];

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height: H, touchAction: "pan-y" }}
        onMouseLeave={() => onHover(null)}
        onMouseMove={(e) => {
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - rect.left) / rect.width) * W;
          if (px < PAD_L || px > PAD_L + innerW) {
            onHover(null);
            return;
          }
          const ratio = (px - PAD_L) / innerW;
          const idx = Math.round(ratio * (values.length - 1));
          onHover(Math.max(0, Math.min(values.length - 1, idx)));
        }}
        onTouchMove={(e) => {
          const t = e.touches[0];
          if (!t) return;
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((t.clientX - rect.left) / rect.width) * W;
          if (px < PAD_L || px > PAD_L + innerW) return;
          const ratio = (px - PAD_L) / innerW;
          const idx = Math.round(ratio * (values.length - 1));
          onHover(Math.max(0, Math.min(values.length - 1, idx)));
        }}
        onTouchEnd={() => onHover(null)}
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Y grid lines + labels */}
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
              <text
                x={PAD_L - 5}
                y={yy + 3}
                textAnchor="end"
                fontSize="9"
                fill="#64748b"
                fontFamily="ui-monospace, monospace"
              >
                {yFormat(v)}
              </text>
            </g>
          );
        })}

        {/* Zero line */}
        {zeroLine && yZero >= PAD_T && yZero <= PAD_T + innerH && (
          <line
            x1={PAD_L}
            x2={W - PAD_R}
            y1={yZero}
            y2={yZero}
            stroke="#475569"
            strokeWidth={0.7}
          />
        )}

        {/* Area */}
        <path d={areaD} fill={`url(#${gradId})`} />
        {/* Line */}
        <path d={pathD} fill="none" stroke={stroke} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />

        {/* End dot */}
        <circle cx={x(values.length - 1)} cy={y(last)} r={2.5} fill={stroke} />

        {/* X labels */}
        {xTickIdx.map((idx) => (
          <text
            key={idx}
            x={x(idx)}
            y={H - 6}
            textAnchor={idx === 0 ? "start" : idx === values.length - 1 ? "end" : "middle"}
            fontSize="9"
            fill="#64748b"
            fontFamily="ui-monospace, monospace"
          >
            {shortDate(dates[idx])}
          </text>
        ))}

        {/* Hover crosshair */}
        {hoverIdx != null && hoverIdx < values.length && (
          <g>
            <line
              x1={x(hoverIdx)}
              x2={x(hoverIdx)}
              y1={PAD_T}
              y2={PAD_T + innerH}
              stroke="#64748b"
              strokeWidth={0.6}
              strokeDasharray="2,2"
            />
            <circle cx={x(hoverIdx)} cy={y(values[hoverIdx])} r={3.5} fill={stroke} stroke="#0f172a" strokeWidth={1.5} />
          </g>
        )}
      </svg>

      {/* Tooltip */}
      {hoverIdx != null && hoverIdx < values.length && (
        <div
          className="absolute pointer-events-none px-2 py-1 rounded-md text-[11px] font-mono"
          style={{
            background: "#0f172a",
            border: "1px solid #1e293b",
            color: "#f1f5f9",
            top: 4,
            left: `${(x(hoverIdx) / W) * 100}%`,
            transform:
              hoverIdx > values.length / 2 ? "translateX(-105%)" : "translateX(5%)",
            whiteSpace: "nowrap",
            zIndex: 5,
          }}
        >
          <div style={{ color: "#94a3b8", fontSize: 9 }}>{dates[hoverIdx]}</div>
          <div style={{ color: stroke }}>{yFormat(values[hoverIdx])}</div>
        </div>
      )}
    </div>
  );
}

// ── Bar chart (daily P&L) ──────────────────────────────────
function BarChart({
  values,
  dates,
  yFormat,
  hoverIdx,
  onHover,
}: {
  values: number[];
  dates: string[];
  yFormat: (v: number) => string;
  hoverIdx: number | null;
  onHover: (i: number | null) => void;
}) {
  if (values.length === 0) return null;

  const maxAbs = Math.max(...values.map((v) => Math.abs(v)), 1);
  const yMin = -maxAbs * 1.1;
  const yMax = maxAbs * 1.1;
  const yRange = yMax - yMin;

  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const slot = innerW / values.length;
  const barW = Math.max(2, Math.min(slot * 0.7, 20));
  const xCenter = (i: number) => PAD_L + slot * (i + 0.5);
  const y = (v: number) => PAD_T + ((yMax - v) / yRange) * innerH;
  const yZero = y(0);

  const yTicks = [yMin, 0, yMax];

  const xTickIdx =
    values.length <= 3
      ? values.map((_, i) => i)
      : [0, Math.floor((values.length - 1) / 2), values.length - 1];

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height: H, touchAction: "pan-y" }}
        onMouseLeave={() => onHover(null)}
        onMouseMove={(e) => {
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - rect.left) / rect.width) * W;
          const idx = Math.floor((px - PAD_L) / slot);
          if (idx < 0 || idx >= values.length) {
            onHover(null);
            return;
          }
          onHover(idx);
        }}
        onTouchMove={(e) => {
          const t = e.touches[0];
          if (!t) return;
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((t.clientX - rect.left) / rect.width) * W;
          const idx = Math.floor((px - PAD_L) / slot);
          if (idx < 0 || idx >= values.length) return;
          onHover(idx);
        }}
        onTouchEnd={() => onHover(null)}
      >
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
                stroke={v === 0 ? "#475569" : "#1e293b"}
                strokeDasharray={v === 0 ? "0" : "2,3"}
                strokeWidth={v === 0 ? 0.7 : 0.5}
              />
              <text
                x={PAD_L - 5}
                y={yy + 3}
                textAnchor="end"
                fontSize="9"
                fill="#64748b"
                fontFamily="ui-monospace, monospace"
              >
                {yFormat(v)}
              </text>
            </g>
          );
        })}

        {/* Bars */}
        {values.map((v, i) => {
          const isPos = v >= 0;
          const yTop = isPos ? y(v) : yZero;
          const h = Math.abs(y(v) - yZero) || 0.5;
          const color = isPos ? "#22c55e" : "#ef4444";
          const isHover = hoverIdx === i;
          return (
            <rect
              key={i}
              x={xCenter(i) - barW / 2}
              y={yTop}
              width={barW}
              height={h}
              fill={color}
              opacity={isHover ? 1 : hoverIdx == null ? 0.85 : 0.5}
              rx={1}
            />
          );
        })}

        {/* X labels */}
        {xTickIdx.map((idx) => (
          <text
            key={idx}
            x={xCenter(idx)}
            y={H - 6}
            textAnchor={idx === 0 ? "start" : idx === values.length - 1 ? "end" : "middle"}
            fontSize="9"
            fill="#64748b"
            fontFamily="ui-monospace, monospace"
          >
            {shortDate(dates[idx])}
          </text>
        ))}
      </svg>

      {hoverIdx != null && hoverIdx < values.length && (
        <div
          className="absolute pointer-events-none px-2 py-1 rounded-md text-[11px] font-mono"
          style={{
            background: "#0f172a",
            border: "1px solid #1e293b",
            color: "#f1f5f9",
            top: 4,
            left: `${(xCenter(hoverIdx) / W) * 100}%`,
            transform:
              hoverIdx > values.length / 2 ? "translateX(-105%)" : "translateX(5%)",
            whiteSpace: "nowrap",
            zIndex: 5,
          }}
        >
          <div style={{ color: "#94a3b8", fontSize: 9 }}>{dates[hoverIdx]}</div>
          <div style={{ color: values[hoverIdx] >= 0 ? "#22c55e" : "#ef4444" }}>
            {yFormat(values[hoverIdx])}
          </div>
        </div>
      )}
    </div>
  );
}

function shortDate(s: string): string {
  // YYYY-MM-DD → MM/DD
  if (!s || s.length < 10) return s;
  return `${s.slice(5, 7)}/${s.slice(8, 10)}`;
}
