"use client";
import { useState } from "react";
import { fmt, pct, cn } from "@/lib/api";
import type { PortfolioSummary, Performance, SessionConfig } from "@/lib/types";

function MetricSkeleton() {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-4">
      <div className="skeleton h-3 w-20 mb-3" />
      <div className="skeleton h-6 w-28 mb-2" />
      <div className="skeleton h-3 w-24" />
    </div>
  );
}

export function MetricsRow({ portfolio, perf, config }: {
  portfolio: PortfolioSummary | null;
  perf: Performance | null;
  config: SessionConfig | null;
}) {
  const [showAll, setShowAll] = useState(false);
  const sym = config?.currency_symbol || "$";
  const locale = config?.locale || "en-US";

  const isLoading = !portfolio && !perf;

  const cards = [
    {
      label: "Portfolio Value",
      value: portfolio ? fmt(portfolio.total_value, sym, locale) : "--",
      sub: portfolio ? `${fmt(portfolio.total_return, sym, locale)} (${pct(portfolio.total_return_pct)})` : "",
      color: (portfolio?.total_return ?? 0) >= 0,
      priority: true,
    },
    {
      label: "Today's P&L",
      value: portfolio ? fmt(portfolio.today_pnl, sym, locale) : "--",
      sub: "",
      color: (portfolio?.today_pnl ?? 0) >= 0,
      priority: true,
    },
    {
      label: "Win Rate",
      value: perf?.total_trades ? `${perf.win_rate}%` : "--",
      sub: perf?.total_trades ? `${perf.total_trades} trades total` : "No trades yet",
      color: (perf?.win_rate ?? 0) >= 50,
      priority: true,
    },
    {
      label: "Cash Available",
      value: portfolio ? fmt(portfolio.cash, sym, locale) : "--",
      sub: portfolio ? `${((portfolio.cash / portfolio.total_value) * 100).toFixed(1)}% of portfolio` : "",
      color: true,
      priority: false,
    },
    {
      label: "Open Positions",
      value: portfolio ? String(portfolio.open_positions) : "0",
      sub: portfolio ? `${(config?.max_open_positions || 5) - portfolio.open_positions} slots free` : "",
      color: true,
      priority: false,
    },
  ];

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 mb-6">
        {[1, 2, 3, 4, 5].map((i) => (
          <MetricSkeleton key={i} />
        ))}
      </div>
    );
  }

  return (
    <div className="mb-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        {cards.map((card, i) => (
          <div
            key={card.label}
            className={cn(
              "bg-bg-card border border-border rounded-xl p-4 animate-fade-in",
              `delay-${i + 1}`,
              card.color ? "hover:border-accent-green/30" : "hover:border-accent-red/30",
              "transition-all",
              // On mobile, hide non-priority cards unless "show all" is toggled
              !card.priority && !showAll && "hidden sm:block"
            )}
          >
            <div className="text-[11px] uppercase tracking-wider text-text-muted mb-2">{card.label}</div>
            <div className={cn(
              "font-mono text-xl font-semibold tracking-tight",
              card.label === "Open Positions" || card.label === "Win Rate"
                ? "text-text-primary"
                : card.color ? "text-accent-green" : "text-accent-red"
            )}>
              {card.value}
            </div>
            {card.sub && (
              <div className={cn(
                "text-xs mt-1",
                card.label === "Portfolio Value" && portfolio
                  ? (portfolio.total_return >= 0 ? "text-accent-green/70" : "text-accent-red/70")
                  : "text-text-muted"
              )}>
                {card.sub}
              </div>
            )}
          </div>
        ))}
      </div>
      {/* Show more toggle - mobile only */}
      {!showAll && (
        <button
          onClick={() => setShowAll(true)}
          className="sm:hidden mt-3 w-full py-2 text-xs text-text-muted border border-border rounded-lg hover:bg-bg-card hover:text-text-secondary transition-all"
        >
          Show more metrics
        </button>
      )}
      {showAll && (
        <button
          onClick={() => setShowAll(false)}
          className="sm:hidden mt-3 w-full py-2 text-xs text-text-muted border border-border rounded-lg hover:bg-bg-card hover:text-text-secondary transition-all"
        >
          Show less
        </button>
      )}
    </div>
  );
}
