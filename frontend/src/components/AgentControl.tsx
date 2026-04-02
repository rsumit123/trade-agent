"use client";
import { useState } from "react";
import { api, cn } from "@/lib/api";
import { useToast } from "@/components/Toast";
import type { AgentStatus } from "@/lib/types";

export function AgentControl({ sessionId, status, onStatusChange }: {
  sessionId: string;
  status: AgentStatus;
  onStatusChange: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const toast = useToast();

  const toggle = async () => {
    setLoading(true);
    try {
      if (status.running) {
        await api(`/api/agent/stop/${sessionId}`, { method: "POST" });
        toast.success("Agent stopped successfully");
      } else {
        await api(`/api/agent/start/${sessionId}`, { method: "POST" });
        toast.success("Agent started successfully");
      }
      setTimeout(onStatusChange, 1500);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      toast.error(`Failed to ${status.running ? "stop" : "start"} agent: ${msg}`);
    }
    setLoading(false);
  };

  return (
    <div className="flex items-center gap-2 sm:gap-3">
      <div className="flex items-center gap-2">
        <span
          className={cn("rounded-full", status.running && "animate-pulse-dot")}
          style={{
            width: 10, height: 10,
            background: status.running ? "#22c55e" : "rgba(100,116,139,0.4)",
          }}
        />
        {/* Full status text on desktop, compact on mobile */}
        <span className="hidden sm:inline" style={{ fontSize: 14, color: "#94a3b8" }}>
          {status.running ? `Running (PID ${status.pid})` : "Stopped"}
        </span>
        <span className="sm:hidden" style={{ fontSize: 13, color: "#94a3b8" }}>
          {status.running ? "Live" : "Off"}
        </span>
      </div>
      <button
        onClick={toggle}
        disabled={loading}
        style={{
          padding: "10px 16px",
          minHeight: 44,
          borderRadius: 10,
          fontSize: 14,
          fontWeight: 500,
          border: status.running ? "1px solid rgba(239,68,68,0.3)" : "1px solid rgba(34,197,94,0.3)",
          color: status.running ? "#ef4444" : "#22c55e",
          background: "transparent",
          opacity: loading ? 0.5 : 1,
          cursor: loading ? "not-allowed" : "pointer",
        }}
      >
        {loading ? "..." : status.running ? "Stop" : "Start"}
      </button>
    </div>
  );
}
