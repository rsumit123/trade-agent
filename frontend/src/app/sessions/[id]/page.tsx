"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, cn } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { MetricsRow } from "@/components/MetricsRow";
import { HoldingsTable } from "@/components/HoldingsTable";
import { TradesTable } from "@/components/TradesTable";
import { RiskPanel } from "@/components/RiskPanel";
import { WatchlistPanel } from "@/components/WatchlistPanel";
import { PerformancePanel } from "@/components/PerformancePanel";
import { LogViewer } from "@/components/LogViewer";
import { JournalPanel } from "@/components/JournalPanel";
import { AgentControl } from "@/components/AgentControl";
import { LearningInsights } from "@/components/LearningInsights";
import { DirectivePanel } from "@/components/DirectivePanel";
import { DailyTracker } from "@/components/DailyTracker";
import { BacktestPanel } from "@/components/BacktestPanel";
import type {
  PortfolioSummary, ClosedTrade, RiskStatus, Performance,
  WatchlistItem, SessionConfig, AgentStatus,
} from "@/lib/types";

function PanelSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <div className="skeleton h-3 w-24" />
      </div>
      <div className="p-4 space-y-3">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex justify-between items-center">
            <div className="skeleton h-3 w-20" />
            <div className="skeleton h-3 w-16" />
          </div>
        ))}
      </div>
    </div>
  );
}

export default function SessionDashboard() {
  const params = useParams();
  const sessionId = params.id as string;
  const toast = useToast();

  const [config, setConfig] = useState<SessionConfig | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null);
  const [trades, setTrades] = useState<ClosedTrade[]>([]);
  const [risk, setRisk] = useState<RiskStatus | null>(null);
  const [perf, setPerf] = useState<Performance | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [journal, setJournal] = useState("");
  const [agentStatus, setAgentStatus] = useState<AgentStatus>({ running: false, pid: null });
  const [initialLoading, setInitialLoading] = useState(true);

  // Track if we've already shown an error toast to avoid spamming on every poll
  const hasErrored = useRef(false);

  const q = `?session=${sessionId}`;

  const loadAll = useCallback((isInitial = false) => {
    const calls = [
      api<SessionConfig>(`/api/config${q}`).then(setConfig),
      api<PortfolioSummary>(`/api/portfolio${q}`).then(setPortfolio),
      api<ClosedTrade[]>(`/api/trades/closed${q}&limit=30`).then(setTrades),
      api<RiskStatus>(`/api/risk${q}`).then(setRisk),
      api<Performance>(`/api/performance${q}`).then(setPerf),
      api<WatchlistItem[]>(`/api/watchlist${q}`).then((d) => { if (Array.isArray(d)) setWatchlist(d); }),
      api<{ lines: string[] }>(`/api/logs${q}&lines=100`).then((d) => setLogs(d.lines || [])),
      api<{ content: string }>(`/api/journal${q}`).then((d) => setJournal(d.content || "")),
      api<AgentStatus>(`/api/agent/status/${sessionId}`).then(setAgentStatus),
    ];

    Promise.allSettled(calls).then((results) => {
      if (isInitial) setInitialLoading(false);

      const failures = results.filter((r) => r.status === "rejected");
      if (failures.length > 0 && !hasErrored.current) {
        hasErrored.current = true;
        toast.error("Failed to load some dashboard data");
      }
      // Reset error flag once all calls succeed, so we can show error again if it recurs
      if (failures.length === 0) {
        hasErrored.current = false;
      }
    });
  }, [sessionId, q, toast]);

  useEffect(() => {
    loadAll(true);
    const interval = setInterval(() => loadAll(false), 30000);
    return () => clearInterval(interval);
  }, [loadAll]);

  // Refetch when user returns to the tab
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") loadAll(false);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [loadAll]);

  const isBacktest = config?.backtest_mode === true;

  const marketBadge = config?.market_id === "crypto"
    ? "bg-accent-blue/15 text-accent-blue border-accent-blue/30"
    : "bg-accent-green/15 text-accent-green border-accent-green/30";

  // Show skeleton layout during initial load
  if (initialLoading) {
    return (
      <div className="px-4 md:px-6 py-4 md:py-6 max-w-7xl mx-auto">
        {/* Header skeleton */}
        <div className="flex items-center justify-between mb-6">
          <div className="space-y-2">
            <div className="skeleton h-6 w-48" />
            <div className="skeleton h-3 w-32" />
          </div>
          <div className="flex gap-3">
            <div className="skeleton h-11 w-24 rounded-xl" />
            <div className="skeleton h-11 w-11 rounded-xl" />
          </div>
        </div>

        {/* Metrics skeleton */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 md:gap-4 mb-6">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="bg-bg-card border border-border rounded-xl p-4">
              <div className="skeleton h-3 w-20 mb-3" />
              <div className="skeleton h-6 w-28 mb-2" />
              <div className="skeleton h-3 w-24" />
            </div>
          ))}
        </div>

        {/* Holdings + Risk skeleton */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 md:gap-5 mb-4 md:mb-5">
          <div className="lg:col-span-2"><PanelSkeleton rows={4} /></div>
          <div><PanelSkeleton rows={3} /></div>
        </div>

        {/* Trades + Watchlist skeleton */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 md:gap-5 mb-4 md:mb-5">
          <PanelSkeleton rows={5} />
          <PanelSkeleton rows={5} />
        </div>

        {/* Performance + Journal skeleton */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 md:gap-5 mb-4 md:mb-5">
          <PanelSkeleton rows={6} />
          <PanelSkeleton rows={4} />
        </div>
      </div>
    );
  }

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
            {isBacktest && (
              <span
                className="text-[10px] uppercase tracking-wider font-semibold px-2 py-0.5 rounded border"
                style={{
                  background: "rgba(139,92,246,0.15)",
                  color: "#a78bfa",
                  borderColor: "rgba(139,92,246,0.3)",
                }}
              >
                backtest
              </span>
            )}
          </h1>
          <p className="text-text-muted text-xs mt-0.5">
            {config?.market_name || ""} {config?.is_24x7 ? " \u00B7 24/7" : ""}
            {config?.llm_model ? ` \u00B7 ${config.llm_model.includes("/") ? config.llm_model.split("/").pop() : config.llm_model}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {!isBacktest && (
            <AgentControl sessionId={sessionId} status={agentStatus} onStatusChange={loadAll} />
          )}
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

      {/* Backtest Panel (shown for backtest sessions) */}
      {isBacktest && (
        <div className="mb-4 md:mb-5 animate-fade-in delay-1">
          <BacktestPanel sessionId={sessionId} config={config} onComplete={loadAll} />
        </div>
      )}

      {/* Live Directives (only for live sessions) */}
      {!isBacktest && (
        <div className="mb-4 md:mb-5 animate-fade-in delay-1">
          <DirectivePanel sessionId={sessionId} />
        </div>
      )}

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

      {/* Daily Performance */}
      <div className="mb-4 md:mb-5 animate-fade-in delay-5">
        <DailyTracker sessionId={sessionId} config={config} />
      </div>

      {/* Agent Learning Insights */}
      <div className="mb-4 md:mb-5 animate-fade-in delay-5">
        <LearningInsights sessionId={sessionId} currencySymbol={config?.currency_symbol || "$"} />
      </div>

      {/* Logs */}
      <div className="mb-6 animate-fade-in delay-5">
        <LogViewer lines={logs} />
      </div>
    </div>
  );
}
