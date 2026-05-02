"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useUser } from "@/lib/auth";
import { SessionsListView } from "@/components/SessionsListView";
import { AdminStatsStrip } from "@/components/AdminStatsStrip";

export default function AdminHome() {
  const router = useRouter();
  const { user, loading } = useUser();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.push("/login?next=/admin");
    } else if (!user.is_admin) {
      router.push("/app");
    }
  }, [user, loading, router]);

  if (loading || !user || !user.is_admin) {
    return (
      <div className="px-4 py-8 max-w-6xl mx-auto">
        <div className="skeleton h-8 w-32 rounded mb-5" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton h-64 rounded-2xl" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <>
      <AdminStatsStrip />
      <SessionsListView
        endpoint="admin"
        newHref="/admin/sessions/new"
        sessionHrefBase="/admin/sessions"
        title="Admin · All Sessions"
        subtitle="Every session across every user. Be careful."
      />
    </>
  );
}
