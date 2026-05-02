"use client";
import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useUser } from "@/lib/auth";
import { useToast } from "@/components/Toast";

interface AdminUser {
  email: string;
  name: string;
  picture: string;
  tier: "free" | "paid" | "admin";
  is_admin: boolean;
  created_at?: number;
  last_login?: number;
  trial_started_at?: number;
  runtime_quota_seconds: number;
  runtime_used_seconds: number;
  runtime_remaining_seconds: number;
  session_count: number;
  running_session_count: number;
}

const FREE_QUOTA = 24 * 3600;
const PAID_QUOTA = 5 * 24 * 3600;

function formatDur(seconds: number): string {
  if (seconds >= 10 ** 8) return "∞";
  if (seconds <= 0) return "0m";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h >= 24) {
    const d = Math.floor(h / 24);
    return `${d}d ${h % 24}h`;
  }
  if (h >= 1) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatRelative(ts?: number): string {
  if (!ts) return "—";
  const delta = Date.now() / 1000 - ts;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export default function AdminUsersPage() {
  const router = useRouter();
  const { user, loading } = useUser();
  const toast = useToast();
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState<string>("");

  useEffect(() => {
    if (loading) return;
    if (!user) router.push("/login?next=/admin/users");
    else if (!user.is_admin) router.push("/app");
  }, [user, loading, router]);

  const refresh = useCallback(() => {
    api<AdminUser[]>("/api/admin/users").then(setUsers).catch(() => {});
  }, []);

  useEffect(() => {
    if (user?.is_admin) refresh();
  }, [user, refresh]);

  const setTier = async (email: string, tier: "free" | "paid") => {
    setBusy(email);
    try {
      await api(`/api/admin/users/${encodeURIComponent(email)}`, {
        method: "PATCH",
        body: JSON.stringify({ tier }),
      });
      toast.success(`${email} → ${tier}`);
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy("");
    }
  };

  const grantRuntime = async (u: AdminUser, addSeconds: number) => {
    setBusy(u.email);
    try {
      await api(`/api/admin/users/${encodeURIComponent(u.email)}`, {
        method: "PATCH",
        body: JSON.stringify({
          runtime_quota_seconds: u.runtime_quota_seconds + addSeconds,
        }),
      });
      toast.success(`+${formatDur(addSeconds)} added`);
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy("");
    }
  };

  const resetUsed = async (email: string) => {
    setBusy(email);
    try {
      await api(`/api/admin/users/${encodeURIComponent(email)}`, {
        method: "PATCH",
        body: JSON.stringify({ runtime_seconds_used: 0 }),
      });
      toast.success(`Reset usage for ${email}`);
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy("");
    }
  };

  if (loading || !user || !user.is_admin) {
    return (
      <div className="px-4 py-8 max-w-6xl mx-auto">
        <div className="skeleton h-8 w-32 rounded mb-4" />
        <div className="skeleton h-64 w-full rounded-2xl" />
      </div>
    );
  }

  const filtered = (users || []).filter(
    (u) => !filter || u.email.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="px-4 md:px-6 py-5 md:py-7 max-w-6xl mx-auto">
      <div className="flex items-center gap-3 mb-1">
        <Link href="/admin" className="text-xs text-text-muted hover:text-text-primary">
          ← Admin
        </Link>
      </div>
      <h1 className="text-xl md:text-2xl font-bold tracking-tight mb-1">Users</h1>
      <p className="text-text-muted text-xs md:text-sm mb-4">
        Manage tiers and runtime grants. Admin tier is env-driven and can&rsquo;t be edited here.
      </p>

      <input
        type="text"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter by email..."
        className="mb-4 w-full max-w-sm"
        style={{
          background: "#0a0e17",
          border: "1px solid #1e293b",
          borderRadius: 10,
          padding: "10px 12px",
          color: "#e2e8f0",
          fontSize: 14,
          minHeight: 44,
        }}
      />

      {users === null ? (
        <div className="skeleton h-64 w-full rounded-2xl" />
      ) : filtered.length === 0 ? (
        <div className="text-text-muted text-sm">No users yet.</div>
      ) : (
        <div className="space-y-2">
          {filtered.map((u) => (
            <UserRow
              key={u.email}
              u={u}
              busy={busy === u.email}
              onSetTier={setTier}
              onGrant={grantRuntime}
              onResetUsed={resetUsed}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function UserRow({
  u, busy, onSetTier, onGrant, onResetUsed,
}: {
  u: AdminUser;
  busy: boolean;
  onSetTier: (email: string, tier: "free" | "paid") => void;
  onGrant: (u: AdminUser, addSeconds: number) => void;
  onResetUsed: (email: string) => void;
}) {
  const tierColor =
    u.tier === "admin" ? "#a78bfa" : u.tier === "paid" ? "#22c55e" : "#94a3b8";
  return (
    <div
      className="rounded-xl p-3 md:p-4"
      style={{
        background: "linear-gradient(180deg, #151d2e 0%, #0c1424 100%)",
        border: `1px solid ${tierColor}22`,
      }}
    >
      <div className="flex items-start gap-3 flex-wrap md:flex-nowrap">
        {u.picture ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={u.picture}
            alt=""
            style={{ width: 36, height: 36, borderRadius: "50%", flexShrink: 0 }}
          />
        ) : (
          <div
            style={{
              width: 36, height: 36, borderRadius: "50%", background: "#1e293b",
              display: "flex", alignItems: "center", justifyContent: "center",
              color: "#94a3b8", fontSize: 14, flexShrink: 0,
            }}
          >
            {u.email.slice(0, 1).toUpperCase()}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-text-primary truncate">
              {u.name || u.email}
            </span>
            <span
              className="text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded"
              style={{
                background: `${tierColor}22`,
                color: tierColor,
                border: `1px solid ${tierColor}55`,
              }}
            >
              {u.tier}
            </span>
            {u.running_session_count > 0 && (
              <span
                className="text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded"
                style={{
                  background: "rgba(34,197,94,0.12)",
                  color: "#22c55e",
                  border: "1px solid rgba(34,197,94,0.3)",
                }}
              >
                ● {u.running_session_count} live
              </span>
            )}
          </div>
          <div className="text-[11px] text-text-muted truncate">{u.email}</div>
          <div className="text-[11px] text-text-muted mt-1">
            Last login {formatRelative(u.last_login)} · {u.session_count} session
            {u.session_count === 1 ? "" : "s"} ·{" "}
            <span style={{ color: u.runtime_remaining_seconds > 0 ? "#94a3b8" : "#fca5a5" }}>
              {formatDur(u.runtime_remaining_seconds)} runtime left
            </span>{" "}
            (used {formatDur(u.runtime_used_seconds)} / {formatDur(u.runtime_quota_seconds)})
          </div>
        </div>
      </div>

      {!u.is_admin && (
        <div className="flex flex-wrap gap-2 mt-3">
          {u.tier === "free" ? (
            <ActionButton
              label={`Promote to Paid (${formatDur(PAID_QUOTA)})`}
              accent="#22c55e"
              busy={busy}
              onClick={() => onSetTier(u.email, "paid")}
            />
          ) : (
            <ActionButton
              label="Demote to Free"
              accent="#94a3b8"
              busy={busy}
              onClick={() => onSetTier(u.email, "free")}
            />
          )}
          <ActionButton
            label="+24h"
            accent="#60a5fa"
            busy={busy}
            onClick={() => onGrant(u, FREE_QUOTA)}
          />
          <ActionButton
            label="+5d"
            accent="#60a5fa"
            busy={busy}
            onClick={() => onGrant(u, PAID_QUOTA)}
          />
          <ActionButton
            label="Reset usage"
            accent="#fbbf24"
            busy={busy}
            onClick={() => onResetUsed(u.email)}
          />
        </div>
      )}
    </div>
  );
}

function ActionButton({
  label, accent, busy, onClick,
}: { label: string; accent: string; busy: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="text-[11px] font-semibold rounded-lg px-3 py-2 transition-opacity disabled:opacity-50"
      style={{
        background: `${accent}1a`,
        border: `1px solid ${accent}55`,
        color: accent,
        minHeight: 36,
      }}
    >
      {label}
    </button>
  );
}
