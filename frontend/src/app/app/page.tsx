"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useUser } from "@/lib/auth";
import { SessionsListView } from "@/components/SessionsListView";

export default function UserAppHome() {
  const router = useRouter();
  const { user, loading } = useUser();

  useEffect(() => {
    if (loading) return;
    if (!user) router.push("/login?next=/app");
  }, [user, loading, router]);

  if (loading || !user) {
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
    <SessionsListView
      endpoint="user"
      newHref="/app/sessions/new"
      sessionHrefBase="/app/sessions"
      title="Your Sessions"
      subtitle={`Signed in as ${user.email}`}
    />
  );
}
