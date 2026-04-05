"use client";
import { useEffect, useState, useCallback } from "react";
import { api, cn } from "@/lib/api";
import { useToast } from "@/components/Toast";
import type { Directive } from "@/lib/types";

const QUICK_ACTIONS = [
  {
    label: "Be aggressive",
    text: "Be aggressive \u2014 increase position sizes, take more trades on moderate signals",
  },
  {
    label: "Reduce risk",
    text: "Reduce risk \u2014 only take high-conviction (5/5) trades, prefer smaller positions, prioritize capital preservation",
  },
  {
    label: "Close all",
    text: "Close all open positions at market price as soon as possible",
  },
] as const;

const EXPIRY_OPTIONS = [
  { value: "this_cycle", label: "This cycle only" },
  { value: "today", label: "Today" },
  { value: "until_cleared", label: "Until cleared" },
] as const;

const EXPIRY_LABELS: Record<string, string> = {
  this_cycle: "This cycle",
  today: "Today",
  until_cleared: "Until cleared",
};

export function DirectivePanel({ sessionId }: { sessionId: string }) {
  const toast = useToast();
  const [directives, setDirectives] = useState<Directive[]>([]);
  const [customText, setCustomText] = useState("");
  const [focusTicker, setFocusTicker] = useState("");
  const [targetPct, setTargetPct] = useState("");
  const [expiry, setExpiry] = useState<"this_cycle" | "today" | "until_cleared">("today");

  const q = `?session=${sessionId}`;

  const loadDirectives = useCallback(() => {
    api<{ directives: Directive[] }>(`/api/directives${q}`)
      .then((d) => setDirectives(d.directives || []))
      .catch(() => {});
  }, [q]);

  useEffect(() => {
    loadDirectives();
    const interval = setInterval(loadDirectives, 30000);
    return () => clearInterval(interval);
  }, [loadDirectives]);

  const addDirective = async (text: string, type: "quick" | "custom") => {
    try {
      await api(`/api/directives${q}`, {
        method: "POST",
        body: JSON.stringify({ text, type, expiry }),
      });
      toast.success("Directive added");
      loadDirectives();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Failed to add directive");
    }
  };

  const removeDirective = async (id: string) => {
    try {
      await api(`/api/directives/${id}${q}`, { method: "DELETE" });
      toast.success("Directive removed");
      loadDirectives();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Failed to remove directive");
    }
  };

  const handleFocus = () => {
    const ticker = focusTicker.trim();
    if (!ticker) return;
    addDirective(
      `Focus on ${ticker} \u2014 prioritize this ticker above all others for analysis and trading`,
      "quick"
    );
    setFocusTicker("");
  };

  const handleTarget = () => {
    const target = targetPct.trim();
    if (!target) return;
    addDirective(
      `Target ${target}% portfolio profit today \u2014 be opportunistic and take quality setups`,
      "quick"
    );
    setTargetPct("");
  };

  const handleCustom = () => {
    const text = customText.trim();
    if (!text) return;
    addDirective(text, "custom");
    setCustomText("");
  };

  return (
    <div style={{ background: "#151d2e", border: "1px solid #1e293b", borderRadius: 12 }}>
      {/* Header */}
      <div
        className="px-4 py-3"
        style={{ borderBottom: "1px solid #1e293b" }}
      >
        <h3
          className="text-xs uppercase tracking-wider font-semibold"
          style={{ color: "#94a3b8" }}
        >
          Live Directives
        </h3>
      </div>

      <div className="p-4 space-y-3">
        {/* Quick Action Buttons */}
        <div className="flex flex-wrap gap-2">
          {QUICK_ACTIONS.map((qa) => (
            <button
              key={qa.label}
              onClick={() => addDirective(qa.text, "quick")}
              className="rounded-full text-xs font-medium transition-colors"
              style={{
                background: "#151d2e",
                border: "1px solid #2d3a4f",
                color: "#94a3b8",
                padding: "6px 14px",
                minHeight: 44,
                cursor: "pointer",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.borderColor = "#3b82f6";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.borderColor = "#2d3a4f";
              }}
            >
              {qa.label}
            </button>
          ))}
        </div>

        {/* Focus Ticker Row */}
        <div className="flex gap-2">
          <input
            type="text"
            value={focusTicker}
            onChange={(e) => setFocusTicker(e.target.value)}
            placeholder="BTC-USD"
            className="flex-1 rounded-lg text-sm"
            style={{
              background: "#0a0e17",
              border: "1px solid #2d3a4f",
              color: "#e2e8f0",
              padding: "8px 12px",
              minHeight: 44,
              fontSize: 16,
              outline: "none",
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = "#3b82f6";
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = "#2d3a4f";
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleFocus();
            }}
          />
          <button
            onClick={handleFocus}
            className="rounded-lg text-xs font-semibold transition-colors"
            style={{
              background: "#2d3a4f",
              border: "1px solid #2d3a4f",
              color: "#e2e8f0",
              padding: "8px 16px",
              minHeight: 44,
              cursor: "pointer",
              whiteSpace: "nowrap",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "#3b4a5f";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "#2d3a4f";
            }}
          >
            Focus
          </button>
        </div>

        {/* Target Profit Row */}
        <div className="flex gap-2">
          <input
            type="text"
            value={targetPct}
            onChange={(e) => setTargetPct(e.target.value)}
            placeholder="5"
            className="rounded-lg text-sm"
            style={{
              background: "#0a0e17",
              border: "1px solid #2d3a4f",
              color: "#e2e8f0",
              padding: "8px 12px",
              minHeight: 44,
              fontSize: 16,
              width: 80,
              outline: "none",
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = "#3b82f6";
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = "#2d3a4f";
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleTarget();
            }}
          />
          <button
            onClick={handleTarget}
            className="rounded-lg text-xs font-semibold transition-colors"
            style={{
              background: "#2d3a4f",
              border: "1px solid #2d3a4f",
              color: "#e2e8f0",
              padding: "8px 16px",
              minHeight: 44,
              cursor: "pointer",
              whiteSpace: "nowrap",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "#3b4a5f";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "#2d3a4f";
            }}
          >
            Set Target %
          </button>
        </div>

        {/* Custom Text Area */}
        <textarea
          value={customText}
          onChange={(e) => setCustomText(e.target.value)}
          placeholder="Custom instruction for the agent..."
          rows={2}
          className="w-full rounded-lg text-sm resize-none"
          style={{
            background: "#0a0e17",
            border: "1px solid #2d3a4f",
            color: "#e2e8f0",
            padding: "10px 12px",
            fontSize: 16,
            outline: "none",
            lineHeight: 1.5,
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = "#3b82f6";
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = "#2d3a4f";
          }}
        />

        {/* Expiry Selector + Add Button */}
        <div className="flex gap-2">
          <select
            value={expiry}
            onChange={(e) =>
              setExpiry(e.target.value as "this_cycle" | "today" | "until_cleared")
            }
            className="rounded-lg text-sm"
            style={{
              background: "#0a0e17",
              border: "1px solid #2d3a4f",
              color: "#e2e8f0",
              padding: "8px 12px",
              minHeight: 44,
              fontSize: 16,
              outline: "none",
              cursor: "pointer",
            }}
          >
            {EXPIRY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <button
            onClick={handleCustom}
            className="flex-1 rounded-lg text-sm font-semibold transition-colors"
            style={{
              background: "#3b82f6",
              border: "none",
              color: "#ffffff",
              padding: "8px 16px",
              minHeight: 44,
              cursor: "pointer",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "#2563eb";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "#3b82f6";
            }}
          >
            Add Directive
          </button>
        </div>

        {/* Active Directives List */}
        {directives.length > 0 && (
          <div className="space-y-2 pt-2">
            <div
              className="text-xs uppercase tracking-wider font-semibold"
              style={{ color: "#64748b" }}
            >
              Active ({directives.length})
            </div>
            {directives.map((d) => (
              <div
                key={d.id}
                className="flex items-start gap-3 rounded-lg"
                style={{
                  background: "#0a0e17",
                  borderLeft: "3px solid #3b82f6",
                  padding: "10px 12px",
                }}
              >
                <div className="flex-1 min-w-0">
                  <div
                    className="text-sm leading-relaxed"
                    style={{ color: "#e2e8f0" }}
                  >
                    {d.text}
                  </div>
                  <div
                    className="text-xs mt-1"
                    style={{ color: "#64748b" }}
                  >
                    {EXPIRY_LABELS[d.expiry] || d.expiry}
                  </div>
                </div>
                <button
                  onClick={() => removeDirective(d.id)}
                  className="shrink-0 flex items-center justify-center rounded transition-colors"
                  style={{
                    color: "#64748b",
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                    width: 28,
                    height: 28,
                    fontSize: 16,
                    lineHeight: 1,
                    minHeight: 44,
                    minWidth: 44,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.color = "#ef4444";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.color = "#64748b";
                  }}
                  title="Remove directive"
                >
                  &times;
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
