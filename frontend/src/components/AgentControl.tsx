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
      // Wait a moment for the process to start/stop
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
        <span className={cn("w-2.5 h-2.5 rounded-full",
          status.running ? "bg-accent-green animate-pulse-dot" : "bg-text-muted/40"
        )} />
        {/* Full status text on desktop, compact on mobile */}
        <span className="text-sm text-text-secondary hidden sm:inline">
          {status.running ? `Running (PID ${status.pid})` : "Stopped"}
        </span>
        <span className="text-xs text-text-secondary sm:hidden">
          {status.running ? "Running" : "Stopped"}
        </span>
      </div>
      <button
        onClick={toggle}
        disabled={loading}
        className={cn(
          "px-3 sm:px-4 py-1.5 rounded-lg text-xs sm:text-sm font-medium transition-all border disabled:opacity-50",
          status.running
            ? "border-accent-red/30 text-accent-red hover:bg-accent-red/10"
            : "border-accent-green/30 text-accent-green hover:bg-accent-green/10"
        )}
      >
        {loading ? "..." : status.running ? "Stop" : "Start"}
      </button>
    </div>
  );
}
