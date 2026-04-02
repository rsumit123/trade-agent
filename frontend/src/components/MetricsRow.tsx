"use client";
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
  const sym = config?.currency_symbol || "$";
  const locale = config?.locale || "en-US";

  const isLoading = !portfolio && !perf;

  const cards = [
    {
      label: "Portfolio Value",
      value: portfolio ? fmt(portfolio.total_value, sym, locale) : "--",
      sub: portfolio ? `${fmt(portfolio.total_return, sym, locale)} (${pct(portfolio.total_return_pct)})` : "",
      color: (portfolio?.total_return ?? 0) >= 0,
    },
    {
      label: "Today's P&L",
      value: portfolio ? fmt(portfolio.today_pnl, sym, locale) : "--",
      sub: "",
      color: (portfolio?.today_pnl ?? 0) >= 0,
    },
    {
      label: "Win Rate",
      value: perf?.total_trades ? `${perf.win_rate}%` : "--",
      sub: perf?.total_trades ? `${perf.total_trades} trades total` : "No trades yet",
      color: (perf?.win_rate ?? 0) >= 50,
    },
    {
      label: "Cash Available",
      value: portfolio ? fmt(portfolio.cash, sym, locale) : "--",
      sub: portfolio ? `${((portfolio.cash / portfolio.total_value) * 100).toFixed(1)}% of portfolio` : "",
      color: true,
    },
    {
      label: "Open Positions",
      value: portfolio ? String(portfolio.open_positions) : "0",
      sub: portfolio ? `${(config?.max_open_positions || 5) - portfolio.open_positions} slots free` : "",
      color: true,
    },
  ];

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 md:gap-4 mb-6">
        {[1, 2, 3, 4, 5].map((i) => (
          <MetricSkeleton key={i} />
        ))}
      </div>
    );
  }

  return (
    <div className="mb-6">
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 md:gap-4">
        {cards.map((card, i) => (
          <div
            key={card.label}
            className={cn(
              "bg-bg-card border border-border rounded-xl p-3 md:p-4 animate-fade-in",
              `delay-${i + 1}`,
              card.color ? "hover:border-accent-green/30" : "hover:border-accent-red/30",
              "transition-all"
            )}
          >
            <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1.5">{card.label}</div>
            <div className={cn(
              "font-mono text-base md:text-xl font-semibold tracking-tight leading-tight",
              card.label === "Open Positions" || card.label === "Win Rate"
                ? "text-text-primary"
                : card.color ? "text-accent-green" : "text-accent-red"
            )}>
              {card.value}
            </div>
            {card.sub && (
              <div className={cn(
                "text-[10px] md:text-xs mt-1 leading-tight",
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
    </div>
  );
}
