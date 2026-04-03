"use client";
import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, cn } from "@/lib/api";
import { MetricsRow } from "@/components/MetricsRow";
import { HoldingsTable } from "@/components/HoldingsTable";
import { TradesTable } from "@/components/TradesTable";
import { RiskPanel } from "@/components/RiskPanel";
import { WatchlistPanel } from "@/components/WatchlistPanel";
import { PerformancePanel } from "@/components/PerformancePanel";
import { LogViewer } from "@/components/LogViewer";
import { JournalPanel } from "@/components/JournalPanel";
import { AgentControl } from "@/components/AgentControl";
import type {
  PortfolioSummary, ClosedTrade, RiskStatus, Performance,
  WatchlistItem, SessionConfig, AgentStatus,
} from "@/lib/types";

export default function SessionDashboard() {
  const params = useParams();
  const sessionId = params.id as string;

  const [config, setConfig] = useState<SessionConfig | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null);
  const [trades, setTrades] = useState<ClosedTrade[]>([]);
  const [risk, setRisk] = useState<RiskStatus | null>(null);
  const [perf, setPerf] = useState<Performance | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [journal, setJournal] = useState("");
  const [agentStatus, setAgentStatus] = useState<AgentStatus>({ running: false, pid: null });

  const q = `?session=${sessionId}`;

  const loadAll = useCallback(() => {
    api<SessionConfig>(`/api/config${q}`).then(setConfig).catch(() => {});
    api<PortfolioSummary>(`/api/portfolio${q}`).then(setPortfolio).catch(() => {});
    api<ClosedTrade[]>(`/api/trades/closed${q}&limit=30`).then(setTrades).catch(() => {});
    api<RiskStatus>(`/api/risk${q}`).then(setRisk).catch(() => {});
    api<Performance>(`/api/performance${q}`).then(setPerf).catch(() => {});
    api<WatchlistItem[]>(`/api/watchlist${q}`).then((d) => { if (Array.isArray(d)) setWatchlist(d); }).catch(() => {});
    api<{ lines: string[] }>(`/api/logs${q}&lines=100`).then((d) => setLogs(d.lines || [])).catch(() => {});
    api<{ content: string }>(`/api/journal${q}`).then((d) => setJournal(d.content || "")).catch(() => {});
    api<AgentStatus>(`/api/agent/status/${sessionId}`).then(setAgentStatus).catch(() => {});
  }, [sessionId, q]);

  useEffect(() => {
    loadAll();
    const interval = setInterval(loadAll, 30000);
    return () => clearInterval(interval);
  }, [loadAll]);

  const marketBadge = config?.market_id === "crypto"
    ? "bg-accent-blue/15 text-accent-blue border-accent-blue/30"
    : "bg-accent-green/15 text-accent-green border-accent-green/30";

  return (
    <div className="px-4 md:px-6 py-4 md:py-6 max-w-7xl mx-auto">
      {/* Header Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6 animate-fade-in">
        <div>
          <h1 className="text-xl font-bold tracking-tight flex items-center gap-3">
            {config?.session_name || sessionId}
            <span className={cn("text-[10px] uppercase tracking-wider font-semibold px-2 py-0.5 rounded border", marketBadge)}>
              {config?.market_id || "nse"}
            </span>
          </h1>
          <p className="text-text-muted text-xs mt-0.5">
            {config?.market_name || ""} {config?.is_24x7 ? " \u00B7 24/7" : ""}
            {config?.llm_model ? ` \u00B7 ${config.llm_model.includes("/") ? config.llm_model.split("/").pop() : config.llm_model}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <AgentControl sessionId={sessionId} status={agentStatus} onStatusChange={loadAll} />
          <Link
            href={`/sessions/${sessionId}/settings`}
            className="flex items-center justify-center rounded-xl border border-border hover:border-border-accent hover:bg-bg-card transition-all text-text-muted hover:text-text-primary"
            style={{ width: 44, height: 44 }}
            title="Settings"
          >
            &#x2699;
          </Link>
        </div>
      </div>

      {/* Metrics Row */}
      <MetricsRow portfolio={portfolio} perf={perf} config={config} />

      {/* Holdings + Risk */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 md:gap-5 mb-4 md:mb-5">
        <div className="lg:col-span-2 animate-fade-in delay-2">
          <HoldingsTable holdings={portfolio?.holdings || []} config={config} />
        </div>
        <div className="animate-fade-in delay-3">
          <RiskPanel risk={risk} />
        </div>
      </div>

      {/* Trades + Watchlist */}
      <div id="trades" className="grid grid-cols-1 lg:grid-cols-2 gap-4 md:gap-5 mb-4 md:mb-5">
        <div className="animate-fade-in delay-3">
          <TradesTable trades={trades} config={config} />
        </div>
        <div className="animate-fade-in delay-4">
          <WatchlistPanel items={watchlist} config={config} />
        </div>
      </div>

      {/* Performance + Journal */}
      <div id="journal" className="grid grid-cols-1 lg:grid-cols-2 gap-4 md:gap-5 mb-4 md:mb-5">
        <div className="animate-fade-in delay-4">
          <PerformancePanel perf={perf} config={config} />
        </div>
        <div className="animate-fade-in delay-5">
          <JournalPanel content={journal} />
        </div>
      </div>

      {/* Logs */}
      <div className="mb-6 animate-fade-in delay-5">
        <LogViewer lines={logs} />
      </div>
    </div>
  );
}
