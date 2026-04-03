"use client";
import { useEffect, useState } from "react";
import { api, cn } from "@/lib/api";

interface CategoryStat {
  total: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_pnl: number;
  total_pnl: number;
}

interface ExitStat {
  total: number;
  avg_pnl: number;
}

interface ConvictionStat {
  total: number;
  wins: number;
  win_rate: number;
  total_pnl: number;
}

interface DetailedPerformance {
  overall: {
    total_trades: number;
    wins: number;
    losses: number;
    win_rate: number;
    total_pnl: number;
    avg_win: number;
    avg_loss: number;
    best_trade: number;
    worst_trade: number;
  };
  by_direction: Record<string, CategoryStat>;
  by_type: Record<string, CategoryStat>;
  by_exit: Record<string, ExitStat>;
  by_conviction: Record<string, ConvictionStat>;
  distilled_rules: string;
}

export function LearningInsights({ sessionId, currencySymbol }: { sessionId: string; currencySymbol: string }) {
  const [data, setData] = useState<DetailedPerformance | null>(null);
  const [tab, setTab] = useState<"rules" | "stats">("rules");
  const sym = currencySymbol || "$";

  useEffect(() => {
    api<DetailedPerformance>(`/api/performance/detailed?session=${sessionId}`)
      .then(setData)
      .catch(() => {});
  }, [sessionId]);

  if (!data || data.overall.total_trades === 0) {
    return (
      <div style={{ background: "#151d2e", border: "1px solid #1e293b", borderRadius: 12, padding: 20 }}>
        <h3 className="text-sm font-semibold mb-3" style={{ color: "#e2e8f0" }}>Agent Learning</h3>
        <p style={{ color: "#64748b", fontSize: 13 }}>No closed trades yet — the agent needs to complete trades before insights appear.</p>
      </div>
    );
  }

  const { overall, by_direction, by_type, by_exit, by_conviction, distilled_rules } = data;

  return (
    <div style={{ background: "#151d2e", border: "1px solid #1e293b", borderRadius: 12, padding: 20 }}>
      {/* Header + Tabs */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold" style={{ color: "#e2e8f0" }}>Agent Learning</h3>
        <div className="flex gap-1" style={{ background: "#0a0e17", borderRadius: 8, padding: 2 }}>
          {(["rules", "stats"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{
                padding: "6px 14px",
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 500,
                background: tab === t ? "#1e293b" : "transparent",
                color: tab === t ? "#e2e8f0" : "#64748b",
                border: "none",
                cursor: "pointer",
                textTransform: "capitalize",
              }}
            >
              {t === "rules" ? "Distilled Rules" : "Performance Stats"}
            </button>
          ))}
        </div>
      </div>

      {tab === "rules" ? (
        /* ── Distilled Rules Tab ─── */
        <div>
          {distilled_rules ? (
            <div
              style={{ fontSize: 13, lineHeight: 1.7, color: "#94a3b8" }}
              className="space-y-1.5"
            >
              {distilled_rules
                .split("\n")
                .filter((line) => line.trim())
                .map((line, i) => {
                  // Skip the header line
                  if (line.startsWith("## ")) return null;
                  // Style bullet points
                  const isBullet = line.trim().startsWith("- ") || line.trim().startsWith("* ");
                  if (!isBullet) return <p key={i} style={{ color: "#64748b" }}>{line}</p>;

                  const text = line.replace(/^[\s]*[-*]\s*/, "");
                  // Highlight win rates in green, loss rates in red
                  const hasWin = /\b(?:7[0-9]|8[0-9]|9[0-9]|100)%/.test(text) || /reliable|work/i.test(text);
                  const hasLoss = /fail|avoid|don't|poor|weak/i.test(text);

                  return (
                    <div
                      key={i}
                      className="flex gap-2"
                      style={{
                        padding: "6px 10px",
                        borderRadius: 6,
                        background: hasWin
                          ? "rgba(34,197,94,0.06)"
                          : hasLoss
                          ? "rgba(239,68,68,0.06)"
                          : "transparent",
                        borderLeft: `3px solid ${hasWin ? "#22c55e" : hasLoss ? "#ef4444" : "#2d3a4f"}`,
                      }}
                    >
                      <span style={{ color: "#e2e8f0", fontSize: 13 }}>{text}</span>
                    </div>
                  );
                })}
            </div>
          ) : (
            <p style={{ color: "#64748b", fontSize: 13 }}>
              No distilled rules yet — rules are generated after the first daily review.
            </p>
          )}
        </div>
      ) : (
        /* ── Performance Stats Tab ─── */
        <div className="space-y-5">
          {/* Direction breakdown */}
          {Object.keys(by_direction).length > 0 && (
            <div>
              <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8, fontWeight: 600 }}>
                By Direction
              </div>
              <div className="grid grid-cols-2 gap-3">
                {Object.entries(by_direction).map(([dir, s]) => (
                  <StatCard key={dir} label={dir} stat={s} sym={sym} />
                ))}
              </div>
            </div>
          )}

          {/* Trade type breakdown */}
          {Object.keys(by_type).length > 0 && (
            <div>
              <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8, fontWeight: 600 }}>
                By Trade Type
              </div>
              <div className="grid grid-cols-2 gap-3">
                {Object.entries(by_type).map(([t, s]) => (
                  <StatCard key={t} label={t} stat={s} sym={sym} />
                ))}
              </div>
            </div>
          )}

          {/* Exit type breakdown */}
          {Object.keys(by_exit).length > 0 && (
            <div>
              <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8, fontWeight: 600 }}>
                Exit Types
              </div>
              <div className="flex flex-wrap gap-3">
                {Object.entries(by_exit).map(([t, s]) => (
                  <div
                    key={t}
                    style={{
                      background: "#0a0e17",
                      border: "1px solid #1e293b",
                      borderRadius: 8,
                      padding: "8px 14px",
                      fontSize: 12,
                    }}
                  >
                    <span style={{ color: "#94a3b8", textTransform: "capitalize" }}>
                      {t.replace("_", " ")}
                    </span>
                    <span style={{ color: "#e2e8f0", fontWeight: 600, marginLeft: 8 }}>{s.total}</span>
                    <span
                      className="font-mono"
                      style={{ color: s.avg_pnl >= 0 ? "#22c55e" : "#ef4444", marginLeft: 8, fontSize: 11 }}
                    >
                      avg {sym}{s.avg_pnl.toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Conviction breakdown */}
          {Object.keys(by_conviction).length > 0 && (
            <div>
              <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8, fontWeight: 600 }}>
                By Conviction Level
              </div>
              <div className="flex flex-wrap gap-3">
                {Object.entries(by_conviction).map(([level, s]) => (
                  <div
                    key={level}
                    style={{
                      background: "#0a0e17",
                      border: "1px solid #1e293b",
                      borderRadius: 8,
                      padding: "8px 14px",
                      fontSize: 12,
                    }}
                  >
                    <span style={{ color: "#f59e0b" }}>{"★".repeat(Number(level))}</span>
                    <span style={{ color: "#64748b" }}>{"☆".repeat(5 - Number(level))}</span>
                    <span style={{ color: "#e2e8f0", fontWeight: 600, marginLeft: 8 }}>
                      {s.total} trades
                    </span>
                    <span
                      className="font-mono"
                      style={{ color: s.win_rate >= 50 ? "#22c55e" : "#ef4444", marginLeft: 8 }}
                    >
                      {s.win_rate}% WR
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatCard({ label, stat, sym }: { label: string; stat: CategoryStat; sym: string }) {
  return (
    <div
      style={{
        background: "#0a0e17",
        border: "1px solid #1e293b",
        borderRadius: 8,
        padding: "10px 14px",
      }}
    >
      <div style={{ fontSize: 12, color: "#94a3b8", textTransform: "capitalize", marginBottom: 6 }}>
        {label}
      </div>
      <div className="flex items-baseline gap-2">
        <span
          className="font-mono"
          style={{ fontSize: 18, fontWeight: 600, color: stat.win_rate >= 50 ? "#22c55e" : "#ef4444" }}
        >
          {stat.win_rate}%
        </span>
        <span style={{ fontSize: 11, color: "#64748b" }}>WR</span>
      </div>
      <div className="flex gap-3 mt-1" style={{ fontSize: 11 }}>
        <span style={{ color: "#64748b" }}>{stat.total} trades</span>
        <span className="font-mono" style={{ color: stat.total_pnl >= 0 ? "#22c55e" : "#ef4444" }}>
          {sym}{stat.total_pnl.toLocaleString()}
        </span>
      </div>
    </div>
  );
}
