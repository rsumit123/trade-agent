"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useUser } from "@/lib/auth";
import { useToast } from "@/components/Toast";

interface WaitlistEntry {
  email: string;
  created_at: number;
  source: string;
}

export default function AdminWaitlistPage() {
  const router = useRouter();
  const { user, loading } = useUser();
  const toast = useToast();
  const [rows, setRows] = useState<WaitlistEntry[] | null>(null);

  useEffect(() => {
    if (loading) return;
    if (!user) router.push("/login?next=/admin/waitlist");
    else if (!user.is_admin) router.push("/app");
  }, [user, loading, router]);

  useEffect(() => {
    if (!user?.is_admin) return;
    api<WaitlistEntry[]>("/api/admin/waitlist").then(setRows).catch(() => {});
  }, [user]);

  const copyAll = () => {
    if (!rows) return;
    const text = rows.map((r) => r.email).join(", ");
    navigator.clipboard.writeText(text).then(
      () => toast.success(`Copied ${rows.length} emails`),
      () => toast.error("Copy failed"),
    );
  };

  if (loading || !user || !user.is_admin) {
    return (
      <div className="px-4 py-8 max-w-4xl mx-auto">
        <div className="skeleton h-8 w-32 rounded mb-4" />
        <div className="skeleton h-64 w-full rounded-2xl" />
      </div>
    );
  }

  return (
    <div className="px-4 md:px-6 py-5 md:py-7 max-w-4xl mx-auto">
      <Link href="/admin" className="text-xs text-text-muted hover:text-text-primary">
        ← Admin
      </Link>
      <div className="flex items-center justify-between gap-3 mb-1 mt-1">
        <h1 className="text-xl md:text-2xl font-bold tracking-tight">Waitlist</h1>
        {rows && rows.length > 0 && (
          <button
            onClick={copyAll}
            className="text-xs font-semibold rounded-lg px-3 py-2"
            style={{
              background: "rgba(96,165,250,0.10)",
              border: "1px solid rgba(96,165,250,0.3)",
              color: "#60a5fa",
              minHeight: 36,
            }}
          >
            Copy all
          </button>
        )}
      </div>
      <p className="text-text-muted text-xs md:text-sm mb-4">
        Emails captured from the upgrade prompts.
      </p>

      {rows === null ? (
        <div className="skeleton h-64 w-full rounded-2xl" />
      ) : rows.length === 0 ? (
        <div className="text-text-muted text-sm">No signups yet.</div>
      ) : (
        <div
          className="rounded-xl overflow-hidden"
          style={{
            background: "#0c1424",
            border: "1px solid #1e293b",
          }}
        >
          {rows.map((r, i) => (
            <div
              key={r.email + i}
              className="flex items-center justify-between gap-3 px-4 py-3"
              style={{
                borderBottom: i < rows.length - 1 ? "1px solid #1e293b" : "none",
              }}
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm font-mono truncate text-text-primary">
                  {r.email}
                </div>
                <div className="text-[11px] text-text-muted">
                  {new Date(r.created_at * 1000).toLocaleString()}
                  {r.source ? ` · via ${r.source}` : ""}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
