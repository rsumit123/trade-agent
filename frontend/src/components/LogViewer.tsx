"use client";
import { useRef, useEffect } from "react";
import { cn } from "@/lib/api";

export function LogViewer({ lines }: { lines: string[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines]);

  const colorLine = (line: string) => {
    if (line.includes("[ERROR]")) return "text-accent-red";
    if (line.includes("[WARNING]")) return "text-accent-amber";
    if (line.includes("✅") || line.includes("🎯")) return "text-accent-green";
    if (line.includes("🛑") || line.includes("❌")) return "text-accent-red";
    if (line.includes("🔧") || line.includes("📐")) return "text-accent-cyan";
    if (line.includes("📰") || line.includes("🧠")) return "text-accent-blue";
    if (line.includes("⏰") || line.includes("⚠️")) return "text-accent-amber";
    return "text-text-secondary";
  };

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">Agent Logs</h3>
      </div>
      <div
        ref={containerRef}
        className="p-4 max-h-[350px] overflow-y-auto font-mono text-xs leading-relaxed"
      >
        {lines.length === 0 ? (
          <div className="text-text-muted text-center py-8">No logs available</div>
        ) : (
          lines.map((line, i) => (
            <div
              key={i}
              className={cn("py-0.5 break-all whitespace-pre-wrap", colorLine(line))}
            >
              {line}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
