"use client";
import { useEffect, useState } from "react";
import { api, parseServerTs } from "@/lib/api";
import type { ThinkingEntry, SessionConfig } from "@/lib/types";

export function ThinkingLog({
  sessionId,
  config,
}: {
  sessionId: string;
  config: SessionConfig | null;
}) {
  const [entries, setEntries] = useState<ThinkingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const sym = config?.currency_symbol || "$";

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      api<{ entries: ThinkingEntry[] }>(`/api/thinking/${sessionId}?limit=40`)
        .then((d) => {
          if (cancelled) return;
          setEntries(d.entries || []);
          setLoading(false);
        })
        .catch(() => !cancelled && setLoading(false));
    };
    load();
    const t = setInterval(load, 30000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [sessionId]);

  const toggle = (i: number) => {
    const next = new Set(expanded);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    setExpanded(next);
  };

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div>
          <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">
            Agent Thinking
          </h3>
          <p className="text-[10px] text-text-muted mt-0.5">
            What the agent considered each cycle, newest first
          </p>
        </div>
        {entries.length > 0 && (
          <span className="text-xs font-mono text-text-muted bg-bg-secondary px-2 py-0.5 rounded">
            {entries.length}
          </span>
        )}
      </div>

      {loading ? (
        <div className="p-4 space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton h-16 rounded-lg" />
          ))}
        </div>
      ) : entries.length === 0 ? (
        <div className="text-center py-10 text-text-muted text-sm px-4">
          No reasoning logged yet — appears after the agent's next cycle
        </div>
      ) : (
        <div className="divide-y divide-border/40 max-h-[600px] overflow-y-auto">
          {entries.map((e, idx) => {
            const isOpen = expanded.has(idx);
            const phaseColor =
              e.phase === "executed"
                ? "#22c55e"
                : e.phase === "rejected"
                ? "#f59e0b"
                : "#64748b";
            const summary = buildSummary(e);
            return (
              <div key={idx} className="px-4 py-3">
                <button
                  onClick={() => toggle(idx)}
                  className="w-full text-left flex items-start justify-between gap-3 group"
                  style={{ minHeight: 44 }}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span
                        className="inline-block w-1.5 h-1.5 rounded-full"
                        style={{ background: phaseColor }}
                      />
                      <span
                        className="text-[10px] uppercase tracking-wider font-semibold"
                        style={{ color: phaseColor }}
                      >
                        {e.phase}
                      </span>
                      <span className="text-[10px] font-mono text-text-muted">
                        {fmtTs(e.ts)}
                      </span>
                      {e.iterations > 1 && (
                        <span className="text-[10px] font-mono text-text-muted">
                          · {e.iterations} steps
                        </span>
                      )}
                    </div>
                    <div className="text-sm text-text-primary line-clamp-2">
                      {summary}
                    </div>
                    {e.placed.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {e.placed.map((p, j) => (
                          <span
                            key={j}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono"
                            style={{
                              background:
                                p.action === "BUY" || p.action === "COVER"
                                  ? "rgba(34,197,94,0.12)"
                                  : "rgba(239,68,68,0.12)",
                              color:
                                p.action === "BUY" || p.action === "COVER"
                                  ? "#22c55e"
                                  : "#ef4444",
                              border: `1px solid ${
                                p.action === "BUY" || p.action === "COVER"
                                  ? "rgba(34,197,94,0.25)"
                                  : "rgba(239,68,68,0.25)"
                              }`,
                            }}
                          >
                            {p.action} {p.ticker?.replace(".NS", "") ?? ""}
                            {p.qty ? ` × ${p.qty}` : ""}
                            {p.price ? ` @ ${sym}${p.price}` : ""}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <span
                    className="text-text-muted text-xs flex-shrink-0 mt-0.5"
                    style={{
                      transform: isOpen ? "rotate(90deg)" : "rotate(0)",
                      transition: "transform 0.15s",
                    }}
                  >
                    ▶
                  </span>
                </button>

                {isOpen && (
                  <div className="mt-3 space-y-2 pl-3.5 border-l-2 border-border/60">
                    {e.trail.length === 0 ? (
                      <p className="text-xs text-text-muted italic">
                        No reasoning text recorded.
                      </p>
                    ) : (
                      e.trail.map((it, i) => (
                        <div key={i} className="space-y-1.5">
                          {it.text && (
                            <p className="text-xs text-text-secondary whitespace-pre-wrap leading-relaxed">
                              {it.text}
                            </p>
                          )}
                          {it.tool_calls.length > 0 && (
                            <div className="flex flex-wrap gap-1">
                              {it.tool_calls.map((tc, j) => (
                                <span
                                  key={j}
                                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono"
                                  style={{
                                    background: "#0a0e17",
                                    border: "1px solid #1e293b",
                                    color: "#94a3b8",
                                  }}
                                >
                                  <span style={{ color: "#60a5fa" }}>{tc.name}</span>
                                  <span>·</span>
                                  <span className="truncate max-w-[200px]">
                                    {summarizeToolInput(tc.input)}
                                  </span>
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function buildSummary(e: ThinkingEntry): string {
  // Prefer last iteration's text (the actual conclusion)
  for (let i = e.trail.length - 1; i >= 0; i--) {
    const t = e.trail[i].text;
    if (t) return t;
  }
  if (e.placed.length > 0) {
    return `Placed ${e.placed.length} trade${e.placed.length > 1 ? "s" : ""}`;
  }
  return "Held — no trade taken";
}

function summarizeToolInput(input: Record<string, unknown>): string {
  if (!input || typeof input !== "object") return "";
  const t = input.ticker || input.symbol;
  const a = input.action;
  const q = input.query;
  if (a && t) return `${a} ${String(t).replace(".NS", "")}`;
  if (t) return String(t).replace(".NS", "");
  if (q) return `"${String(q).slice(0, 30)}"`;
  // Fall back to first key=value
  const keys = Object.keys(input);
  if (keys.length === 0) return "";
  const k = keys[0];
  return `${k}=${String(input[k]).slice(0, 30)}`;
}

function fmtTs(ts: string): string {
  if (!ts) return "";
  try {
    const d = parseServerTs(ts);
    if (!d || isNaN(d.getTime())) return ts;
    const today = new Date();
    const sameDay =
      d.getFullYear() === today.getFullYear() &&
      d.getMonth() === today.getMonth() &&
      d.getDate() === today.getDate();
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
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
