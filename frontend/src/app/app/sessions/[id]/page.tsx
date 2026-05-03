"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { DashboardHero } from "@/components/DashboardHero";
import { TabStrip } from "@/components/TabStrip";
import { Collapsible } from "@/components/Collapsible";
import { HoldingsTable } from "@/components/HoldingsTable";
import { TradesTable } from "@/components/TradesTable";
import { RiskPanel } from "@/components/RiskPanel";
import { WatchlistPanel } from "@/components/WatchlistPanel";
import { PerformancePanel } from "@/components/PerformancePanel";
import { LogViewer } from "@/components/LogViewer";
import { JournalPanel } from "@/components/JournalPanel";
import { LearningInsights } from "@/components/LearningInsights";
import { DirectivePanel } from "@/components/DirectivePanel";
import { DailyTracker } from "@/components/DailyTracker";
import { EquityCharts } from "@/components/EquityCharts";
import { ThinkingLog } from "@/components/ThinkingLog";
import { CostLedger } from "@/components/CostLedger";
import { BacktestSection } from "@/components/BacktestSection";
import { DecisionFeed } from "@/components/DecisionFeed";
import type {
  PortfolioSummary, ClosedTrade, RiskStatus, Performance,
  WatchlistItem, SessionConfig, AgentStatus,
} from "@/lib/types";

type Tab = "overview" | "decisions" | "activity" | "insights" | "logs";

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

function HeroSkeleton() {
  return (
    <div className="rounded-2xl border border-border bg-bg-card p-4 md:p-5 mb-5">
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="skeleton h-2 w-2 rounded-full" />
          <div className="skeleton h-3 w-14" />
          <div className="skeleton h-3 w-24" />
        </div>
        <div className="skeleton h-9 w-9 rounded-lg" />
      </div>
      <div className="flex items-end justify-between gap-3 mb-4">
        <div className="space-y-2 flex-1">
          <div className="skeleton h-3 w-16" />
          <div className="skeleton h-8 w-40" />
          <div className="skeleton h-3 w-32" />
        </div>
        <div className="skeleton h-11 w-24 rounded" />
      </div>
      <div className="flex gap-2 mb-4 pb-4 border-b border-border/60">
        {[1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="skeleton h-11 w-16 rounded-lg" />
        ))}
      </div>
      <div className="skeleton h-12 w-full rounded-xl" />
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
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [tab, setTab] = useState<Tab>("overview");

  // Sync tab with URL hash so mobile bottom-nav deep links work
  useEffect(() => {
    const validTabs: Tab[] = ["overview", "decisions", "activity", "insights", "logs"];
    const apply = () => {
      const h = (typeof window !== "undefined" ? window.location.hash.replace("#", "") : "") as Tab;
      if (validTabs.includes(h)) setTab(h);
    };
    apply();
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
  }, []);

  const hasErrored = useRef(false);

  interface DashboardBundle {
    config: SessionConfig;
    portfolio: PortfolioSummary;
    trades: ClosedTrade[];
    risk: RiskStatus;
    performance: Performance;
    watchlist: WatchlistItem[];
    logs: { lines: string[] };
    journal: { content: string };
    agent_status: AgentStatus;
  }

  const loadAll = useCallback((isInitial = false) => {
    api<DashboardBundle>(`/api/dashboard/${sessionId}`)
      .then((data) => {
        setConfig(data.config);
        setPortfolio(data.portfolio);
        setTrades(data.trades);
        setRisk(data.risk);
        setPerf(data.performance);
        if (Array.isArray(data.watchlist)) setWatchlist(data.watchlist);
        setLogs(data.logs?.lines || []);
        setJournal(data.journal?.content || "");
        setAgentStatus(data.agent_status);
        if (isInitial) setInitialLoading(false);
        setLastUpdated(new Date());
        hasErrored.current = false;
      })
      .catch(() => {
        if (isInitial) setInitialLoading(false);
        if (!hasErrored.current) {
          hasErrored.current = true;
          toast.error("Failed to load dashboard data");
        }
      });
  }, [sessionId, toast]);

  useEffect(() => {
    loadAll(true);
    const interval = setInterval(() => loadAll(false), 30000);
    return () => clearInterval(interval);
  }, [loadAll]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") loadAll(false);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [loadAll]);

  const isBacktest = config?.backtest_mode === true;

  if (initialLoading) {
    return (
      <div className="px-4 md:px-6 py-4 md:py-6 max-w-7xl mx-auto">
        <HeroSkeleton />
        <div className="skeleton h-10 w-full rounded-xl mb-5" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 md:gap-5 mb-5">
          <div className="lg:col-span-2"><PanelSkeleton rows={4} /></div>
          <div><PanelSkeleton rows={3} /></div>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 md:gap-5">
          <PanelSkeleton rows={5} />
          <PanelSkeleton rows={5} />
        </div>
      </div>
    );
  }

  const tabs: { id: Tab; label: string; badge?: string | number }[] = [
    { id: "overview", label: "Overview" },
    { id: "decisions", label: "Decisions", badge: trades.length || undefined },
    { id: "activity", label: "Activity" },
    { id: "insights", label: "Insights" },
    { id: "logs", label: "Logs" },
  ];

  return (
    <div className="px-4 md:px-6 py-4 md:py-6 max-w-7xl mx-auto">
      {/* Hero */}
      <div className="animate-fade-in">
        <DashboardHero
          sessionId={sessionId}
          config={config}
          portfolio={portfolio}
          perf={perf}
          agentStatus={agentStatus}
          isBacktest={isBacktest}
          onStatusChange={() => loadAll(false)}
          lastUpdated={lastUpdated}
        />
      </div>

      {/* Backtest panel always above tabs */}
      {isBacktest && (
        <div className="mb-4 md:mb-5 animate-fade-in delay-1">
          <BacktestSection
            sessionId={sessionId}
            config={config}
            defaultStart={config?.backtest_start_date || ""}
            defaultEnd={config?.backtest_end_date || ""}
            onComplete={loadAll}
          />
        </div>
      )}

      {/* Tabs */}
      <TabStrip
        tabs={tabs}
        active={tab}
        onChange={(id) => {
          setTab(id as Tab);
          if (typeof window !== "undefined") {
            history.replaceState(null, "", `#${id}`);
          }
        }}
      />

      {/* Tab content */}
      {tab === "overview" && (
        <div className="space-y-4 md:space-y-5 animate-fade-in">
          {/* Performance charts */}
          <EquityCharts sessionId={sessionId} config={config} />

          {/* Holdings + Risk */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 md:gap-5">
            <div className="lg:col-span-2">
              <HoldingsTable
                holdings={portfolio?.holdings || []}
                config={config}
                sessionId={sessionId}
                onLiquidated={() => loadAll(false)}
              />
            </div>
            <div>
              <RiskPanel risk={risk} />
            </div>
          </div>

          {/* Watchlist — hidden during backtest (picks rotate daily, prices are simulated) */}
          {!isBacktest && <WatchlistPanel items={watchlist} config={config} />}

          {/* Directives (accordion, collapsed by default) */}
          {!isBacktest && (
            <Collapsible
              title="Instruct Agent"
              subtitle="Send live directives to adjust behavior"
              icon="🎯"
            >
              <DirectivePanel sessionId={sessionId} />
            </Collapsible>
          )}
        </div>
      )}

      {tab === "decisions" && (
        <div className="animate-fade-in">
          <DecisionFeed trades={trades} config={config} />
        </div>
      )}

      {tab === "activity" && (
        <div className="space-y-4 md:space-y-5 animate-fade-in">
          <ThinkingLog sessionId={sessionId} config={config} />
          <TradesTable trades={trades} config={config} />
          <DailyTracker sessionId={sessionId} config={config} />
        </div>
      )}

      {tab === "insights" && (
        <div className="space-y-4 md:space-y-5 animate-fade-in">
          <PerformancePanel perf={perf} config={config} />
          <CostLedger sessionId={sessionId} />
          <LearningInsights sessionId={sessionId} currencySymbol={config?.currency_symbol || "$"} />
          <JournalPanel content={journal} />
        </div>
      )}

      {tab === "logs" && (
        <div className="animate-fade-in">
          <LogViewer lines={logs} />
        </div>
      )}
    </div>
  );
}
