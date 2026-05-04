"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { BacktestPanel } from "@/components/BacktestPanel";
import { BacktestCompare } from "@/components/BacktestCompare";
import type { SessionConfig } from "@/lib/types";

type Tab = "single" | "compare";
type RunStatus = "running" | "idle";

interface SingleStatus { status: string }
interface CompareStatus { status: string }

interface Props {
  sessionId: string;
  config: SessionConfig | null;
  defaultStart?: string;
  defaultEnd?: string;
  onComplete?: () => void;
}

/** One-stop backtest UI: tabs for Single Run / Compare Models. Only one
 *  can run at a time — if either side is in-progress the other tab is
 *  disabled and we auto-switch to whichever is live. */
export function BacktestSection({
  sessionId, config, defaultStart, defaultEnd, onComplete,
}: Props) {
  const [tab, setTab] = useState<Tab>("single");
  const [single, setSingle] = useState<RunStatus>("idle");
  const [compare, setCompare] = useState<RunStatus>("idle");

  // Poll both sides so the wrapper knows what's in progress. Only run the
  // interval while at least one side is "running"; otherwise we just do a
  // single fetch on mount and re-arm if the user starts a new run (the child
  // panels call their own start endpoints which the next tick of this
  // component picks up via the dependency).
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await api<SingleStatus>(`/api/backtest/status/${sessionId}`);
        if (!cancelled) {
          setSingle(s.status === "running" ? "running" : "idle");
        }
      } catch {}
      try {
        const c = await api<CompareStatus>(`/api/backtest/compare/status/${sessionId}`);
        if (!cancelled) {
          setCompare(c.status === "running" ? "running" : "idle");
        }
      } catch {}
    };
    tick();
    const isActive = single === "running" || compare === "running";
    if (!isActive) return () => { cancelled = true; };
    const t = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [sessionId, single, compare]);

  // Auto-switch to whichever is running — user can always manually flip back
  useEffect(() => {
    if (single === "running" && tab !== "single") setTab("single");
    else if (compare === "running" && tab !== "compare") setTab("compare");
    // Only auto-switch when entering a running state, not on idle
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [single === "running", compare === "running"]);

  const singleLocked = compare === "running";
  const compareLocked = single === "running";

  return (
    <div
      style={{
        background: "#0c1424",
        border: "1px solid #1e293b",
        borderRadius: 12,
        padding: 4,
      }}
    >
      <div className="flex gap-1 p-1">
        <TabButton
          label="Single run"
          active={tab === "single"}
          locked={singleLocked}
          running={single === "running"}
          onClick={() => !singleLocked && setTab("single")}
        />
        <TabButton
          label="Compare models"
          active={tab === "compare"}
          locked={compareLocked}
          running={compare === "running"}
          onClick={() => !compareLocked && setTab("compare")}
        />
      </div>
      <div className="p-1">
        {tab === "single" ? (
          <BacktestPanel
            sessionId={sessionId}
            config={config}
            onComplete={onComplete}
          />
        ) : (
          <BacktestCompare
            baseSessionId={sessionId}
            config={config}
            defaultStart={defaultStart}
            defaultEnd={defaultEnd}
          />
        )}
      </div>
    </div>
  );
}

function TabButton({
  label, active, locked, running, onClick,
}: { label: string; active: boolean; locked: boolean; running: boolean; onClick: () => void }) {
  const accent = active ? "#22c55e" : "#94a3b8";
  return (
    <button
      onClick={onClick}
      disabled={locked}
      className="flex-1 text-sm font-semibold transition-all"
      style={{
        padding: "10px 14px",
        borderRadius: 8,
        background: active
          ? "rgba(34,197,94,0.10)"
          : locked
          ? "rgba(148,163,184,0.05)"
          : "transparent",
        border: `1px solid ${active ? "rgba(34,197,94,0.35)" : "transparent"}`,
        color: locked ? "#475569" : accent,
        cursor: locked ? "not-allowed" : "pointer",
        minHeight: 40,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
      }}
      title={locked ? "Another backtest is in progress — wait for it to finish or reset" : ""}
    >
      <span>{locked ? "🔒" : ""} {label}</span>
      {running && (
        <span
          className="animate-pulse-dot"
          style={{
            display: "inline-block",
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: "#22c55e",
          }}
        />
      )}
    </button>
  );
}
