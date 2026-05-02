"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

interface AdminStats {
  users_total: number;
  users_by_tier: { free: number; paid: number; admin: number };
  sessions_total: number;
  sessions_running: number;
  backtests_running: number;
  trades_total: number;
  trades_today: number;
  waitlist_count: number;
}

export function AdminStatsStrip() {
  const [s, setS] = useState<AdminStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchOnce = () =>
      api<AdminStats>("/api/admin/stats")
        .then((r) => !cancelled && setS(r))
        .catch(() => {});
    fetchOnce();
    const t = setInterval(fetchOnce, 15000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  return (
    <div className="px-4 md:px-6 pt-4">
      <div className="max-w-6xl mx-auto">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 md:gap-3">
          <Stat
            label="Users"
            value={s ? s.users_total : "—"}
            sub={
              s
                ? `${s.users_by_tier.paid} paid · ${s.users_by_tier.free} free`
                : ""
            }
            accent="#60a5fa"
            href="/admin/users"
          />
          <Stat
            label="Running"
            value={s ? `${s.sessions_running}/${s.sessions_total}` : "—"}
            sub={
              s
                ? `${s.backtests_running} backtests`
                : ""
            }
            accent="#22c55e"
          />
          <Stat
            label="Trades today"
            value={s ? s.trades_today.toLocaleString() : "—"}
            sub={s ? `${s.trades_total.toLocaleString()} total` : ""}
            accent="#a78bfa"
          />
          <Stat
            label="Waitlist"
            value={s ? s.waitlist_count : "—"}
            sub="upgrade interest"
            accent="#fbbf24"
            href="/admin/waitlist"
          />
        </div>
      </div>
    </div>
  );
}

function Stat({
  label, value, sub, accent, href,
}: { label: string; value: string | number; sub: string; accent: string; href?: string }) {
  const inner = (
    <div
      className="rounded-xl p-3 md:p-4 h-full"
      style={{
        background: "linear-gradient(180deg, #151d2e 0%, #0c1424 100%)",
        border: `1px solid ${accent}33`,
      }}
    >
      <div
        className="text-[10px] uppercase tracking-wider font-semibold mb-1"
        style={{ color: accent }}
      >
        {label}
      </div>
      <div className="text-xl md:text-2xl font-bold tabular-nums" style={{ color: "#e2e8f0" }}>
        {value}
      </div>
      {sub && (
        <div className="text-[10px] text-text-muted truncate mt-0.5">{sub}</div>
      )}
    </div>
  );
  return href ? (
    <Link href={href} className="block hover:opacity-90 transition-opacity">
      {inner}
    </Link>
  ) : (
    inner
  );
}
