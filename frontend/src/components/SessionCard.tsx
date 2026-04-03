"use client";
import Link from "next/link";
import { api, fmt, pct, cn } from "@/lib/api";
import { useToast } from "@/components/Toast";
import type { Session, PortfolioSummary } from "@/lib/types";
import { useEffect, useState } from "react";

/** Pretty-print model name: "anthropic/claude-haiku-4-5" → "Claude Haiku 4.5" */
function formatModel(model?: string): string {
  if (!model) return "";
  // Remove provider prefix (anthropic/, google/, openai/, etc.)
  const short = model.includes("/") ? model.split("/").pop()! : model;
  return short
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/(\d) (\d)/g, "$1.$2"); // "4 5" → "4.5"
}

export function SessionCard({ session }: { session: Session }) {
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null);
  const toast = useToast();

  useEffect(() => {
    api<PortfolioSummary>(`/api/portfolio?session=${session.session_id}`)
      .then(setPortfolio)
      .catch(() => {});
  }, [session.session_id]);

  const isProfit = (portfolio?.total_return ?? 0) >= 0;
  const marketColors: Record<string, string> = {
    nse: "bg-accent-green/15 text-accent-green border-accent-green/30",
    crypto: "bg-accent-blue/15 text-accent-blue border-accent-blue/30",
  };

  const handleToggle = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      if (session.is_running) {
        await api(`/api/agent/stop/${session.session_id}`, { method: "POST" });
        toast.success(`Agent "${session.display_name}" stopped`);
      } else {
        await api(`/api/agent/start/${session.session_id}`, { method: "POST" });
        toast.success(`Agent "${session.display_name}" started`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      toast.error(`Failed to ${session.is_running ? "stop" : "start"} agent: ${msg}`);
    }
  };

  const modelName = formatModel(session.llm_model);

  return (
    <Link href={`/sessions/${session.session_id}`}>
      <div className="bg-bg-card border border-border rounded-xl p-5 hover:border-border-accent hover:bg-bg-card-hover transition-all cursor-pointer group">
        {/* Header */}
        <div className="flex items-start justify-between mb-3">
          <div>
            <h3 className="font-semibold text-text-primary group-hover:text-white transition-colors">
              {session.display_name}
            </h3>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              <span className={cn("inline-block text-[10px] uppercase tracking-wider font-semibold px-2 py-0.5 rounded border", marketColors[session.market] || "bg-bg-secondary text-text-muted border-border")}>
                {session.market}
              </span>
              {modelName && (
                <span className="inline-block text-[10px] text-text-muted font-mono truncate max-w-[140px]" title={session.llm_model}>
                  {modelName}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={cn("w-2.5 h-2.5 rounded-full", session.is_running ? "bg-accent-green animate-pulse-dot" : "bg-text-muted/40")} />
            <span className="text-xs text-text-muted">{session.is_running ? "Live" : "Stopped"}</span>
          </div>
        </div>

        {/* Portfolio value */}
        <div className="mb-3">
          <div className="text-xs text-text-muted mb-1">Portfolio Value</div>
          <div className="font-mono text-xl font-semibold tracking-tight">
            {portfolio ? fmt(portfolio.total_value, session.currency_symbol) : "--"}
          </div>
        </div>

        {/* Return + Win Rate + Start/Stop */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className={cn("font-mono text-sm font-medium", isProfit ? "text-accent-green" : "text-accent-red")}>
              {portfolio ? fmt(portfolio.total_return, session.currency_symbol) : "--"}
              {portfolio ? ` (${pct(portfolio.total_return_pct)})` : ""}
            </span>
            {session.win_rate != null && (
              <span className={cn(
                "text-xs font-mono font-medium",
                session.win_rate >= 50 ? "text-accent-green" : "text-accent-red"
              )}>
                {session.win_rate}% W
              </span>
            )}
            {session.total_trades != null && session.total_trades > 0 && session.win_rate == null && (
              <span className="text-xs text-text-muted font-mono">{session.total_trades}t</span>
            )}
          </div>
          <button
            onClick={handleToggle}
            className={cn(
              "px-3 py-1.5 rounded-lg text-xs font-medium transition-all border",
              session.is_running
                ? "border-accent-red/30 text-accent-red hover:bg-accent-red/10"
                : "border-accent-green/30 text-accent-green hover:bg-accent-green/10"
            )}
          >
            {session.is_running ? "Stop" : "Start"}
          </button>
        </div>
      </div>
    </Link>
  );
}
