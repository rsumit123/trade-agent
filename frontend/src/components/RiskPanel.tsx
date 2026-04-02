"use client";
import { cn } from "@/lib/api";
import type { RiskStatus } from "@/lib/types";

export function RiskPanel({ risk }: { risk: RiskStatus | null }) {
  if (!risk) return null;

  const riskColor = (pct: number) => pct > 80 ? "accent-red" : pct > 50 ? "accent-amber" : "accent-green";
  const dailyPct = risk.daily_limit_used_pct;
  const positionPct = (risk.open_positions / risk.max_positions) * 100;
  const capitalPct = risk.max_trade_amount > 0
    ? Math.max(0, Math.min(100, ((risk.max_trade_amount - risk.cash_available) / risk.max_trade_amount) * 100))
    : 0;

  const bars = [
    { label: "Daily Loss Usage", value: dailyPct, display: `${dailyPct.toFixed(0)}%` },
    { label: "Position Slots", value: positionPct, display: `${risk.open_positions} / ${risk.max_positions}` },
    { label: "Capital Deployed", value: capitalPct, display: `${capitalPct.toFixed(0)}%` },
  ];

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">Risk Status</h3>
      </div>
      <div className="p-4 space-y-4">
        {bars.map((bar) => {
          const color = riskColor(bar.value);
          return (
            <div key={bar.label}>
              <div className="flex justify-between text-xs mb-1.5">
                <span className="text-text-secondary">{bar.label}</span>
                <span className={`font-mono text-${color}`}>{bar.display}</span>
              </div>
              <div className="h-1.5 bg-bg-primary rounded-full overflow-hidden">
                <div
                  className={cn("h-full rounded-full transition-all duration-700", `bg-${color}`)}
                  style={{ width: `${Math.min(bar.value, 100)}%` }}
                />
              </div>
            </div>
          );
        })}
        <div className={cn(
          "text-xs font-medium mt-2 px-2 py-1 rounded text-center",
          risk.can_trade ? "bg-accent-green/10 text-accent-green" : "bg-accent-red/10 text-accent-red"
        )}>
          {risk.can_trade ? "Trading Active" : "Trading Paused (Daily Limit)"}
        </div>
      </div>
    </div>
  );
}
