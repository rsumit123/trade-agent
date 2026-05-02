"use client";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { CostLedgerData } from "@/lib/types";

const USD_INR = 84;

export function CostLedger({ sessionId }: { sessionId: string }) {
  const [data, setData] = useState<CostLedgerData | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      api<CostLedgerData>(`/api/cost/${sessionId}?days=30`)
        .then((d) => !cancelled && setData(d))
        .catch(() => {});
    };
    tick();
    const t = setInterval(tick, 60000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [sessionId]);

  const sevenDay = useMemo(() => {
    if (!data?.daily) return 0;
    const cutoff = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
      .toISOString()
      .slice(0, 10);
    return data.daily
      .filter((d) => d.date >= cutoff)
      .reduce((s, d) => s + d.usd, 0);
  }, [data]);

  const projected = useMemo(() => {
    if (!data?.daily?.length) return null;
    const last7 = data.daily.slice(-7);
    if (last7.length === 0) return null;
    const avg = last7.reduce((s, d) => s + d.usd, 0) / last7.length;
    return avg * 30;
  }, [data]);

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">
          LLM Cost Ledger
        </h3>
        {data && (
          <span className="text-[10px] font-mono text-text-muted bg-bg-secondary px-2 py-0.5 rounded">
            {data.lifetime.cycles} cycles
          </span>
        )}
      </div>

      {!data ? (
        <div className="p-4 space-y-2">
          <div className="skeleton h-16 rounded-lg" />
          <div className="skeleton h-20 rounded-lg" />
        </div>
      ) : data.lifetime.cycles === 0 ? (
        <div className="text-center py-10 text-text-muted text-sm px-4">
          No LLM calls logged yet — cost tracking starts from the agent's next cycle
        </div>
      ) : (
        <div className="p-4 space-y-4">
          {/* Top stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <Stat label="Today" usd={data.today.usd} sub={`${data.today.cycles} cycles`} />
            <Stat label="7-day" usd={sevenDay} accent="#60a5fa" />
            <Stat label="Lifetime" usd={data.lifetime.usd} sub={`${(data.lifetime.input_tokens / 1000).toFixed(0)}k in / ${(data.lifetime.output_tokens / 1000).toFixed(0)}k out`} />
            <Stat
              label="~Monthly"
              usd={projected ?? 0}
              sub="based on last 7d"
              accent="#fbbf24"
            />
          </div>

          {/* Daily bar chart */}
          {data.daily.length > 0 && <DailyCostChart daily={data.daily} />}

          {/* By-model breakdown */}
          {data.by_model.length > 0 && (
            <div className="rounded-lg overflow-hidden" style={{ background: "#0a0e17", border: "1px solid #1e293b" }}>
              <div className="px-3 py-2 border-b" style={{ borderColor: "#1e293b" }}>
                <span className="text-[10px] uppercase tracking-wider font-semibold text-text-muted">By Model</span>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wider text-text-muted" style={{ borderBottom: "1px solid #1e293b" }}>
                    <th className="text-left px-3 py-1.5">Model</th>
                    <th className="text-right px-2 py-1.5">Cycles</th>
                    <th className="text-right px-2 py-1.5">Tokens</th>
                    <th className="text-right px-3 py-1.5">USD</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_model.map((m) => (
                    <tr key={m.model} style={{ borderBottom: "1px solid #0f172a" }}>
                      <td className="px-3 py-1.5 truncate max-w-[180px] text-text-primary">{m.model}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-text-secondary">{m.cycles}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-text-muted">
                        {((m.input_tokens + m.output_tokens) / 1000).toFixed(0)}k
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono text-text-primary">
                        ${m.usd.toFixed(4)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  usd,
  sub,
  accent,
}: {
  label: string;
  usd: number;
  sub?: string;
  accent?: string;
}) {
  const inr = usd * USD_INR;
  return (
    <div
      className="rounded-lg px-3 py-2"
      style={{ background: "#0a0e17", border: `1px solid ${accent ? `${accent}33` : "#1e293b"}` }}
    >
      <div className="text-[10px] uppercase tracking-wider font-semibold text-text-muted mb-0.5">
        {label}
      </div>
      <div className="font-mono text-sm font-semibold" style={{ color: accent || "#f1f5f9" }}>
        ₹{inr.toFixed(inr < 10 ? 2 : 0)}
        <span className="text-text-muted text-[10px] ml-1.5">${usd.toFixed(usd < 0.01 ? 5 : 3)}</span>
      </div>
      {sub && <div className="text-[10px] text-text-muted mt-0.5 truncate">{sub}</div>}
    </div>
  );
}

function DailyCostChart({ daily }: { daily: { date: string; usd: number }[] }) {
  const W = 600;
  const H = 120;
  const PAD_L = 36;
  const PAD_R = 8;
  const PAD_T = 8;
  const PAD_B = 22;

  if (daily.length === 0) return null;

  const maxUsd = Math.max(...daily.map((d) => d.usd), 0.001);
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const slot = innerW / Math.max(daily.length, 1);
  const barW = Math.max(2, Math.min(slot * 0.7, 18));

  const xCenter = (i: number) => PAD_L + slot * (i + 0.5);
  const y = (v: number) => PAD_T + ((maxUsd - v) / maxUsd) * innerH;

  const xTickIdx =
    daily.length <= 3
      ? daily.map((_, i) => i)
      : [0, Math.floor((daily.length - 1) / 2), daily.length - 1];

  return (
    <div className="rounded-lg p-3" style={{ background: "#0a0e17", border: "1px solid #1e293b" }}>
      <div className="text-[10px] uppercase tracking-wider font-semibold text-text-muted mb-2">
        Daily Cost (USD)
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full" style={{ height: H }}>
        {/* Y grid */}
        {[0, maxUsd / 2, maxUsd].map((v, i) => {
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
                ${v.toFixed(v < 0.01 ? 4 : 2)}
              </text>
            </g>
          );
        })}
        {daily.map((d, i) => {
          const yTop = y(d.usd);
          const h = Math.max(0.5, y(0) - yTop);
          return (
            <rect
              key={d.date}
              x={xCenter(i) - barW / 2}
              y={yTop}
              width={barW}
              height={h}
              fill="#60a5fa"
              opacity={0.85}
              rx={1}
            />
          );
        })}
        {xTickIdx.map((idx) => (
          <text
            key={idx}
            x={xCenter(idx)}
            y={H - 6}
            textAnchor={idx === 0 ? "start" : idx === daily.length - 1 ? "end" : "middle"}
            fontSize="9"
            fill="#64748b"
            fontFamily="monospace"
          >
            {daily[idx].date.slice(5)}
          </text>
        ))}
      </svg>
    </div>
  );
}
