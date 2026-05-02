"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useUser } from "@/lib/auth";

export default function LegacySessionsRedirect() {
  const router = useRouter();
  const { user, loading } = useUser();
  useEffect(() => {
    if (loading) return;
    if (!user) router.replace("/login");
    else router.replace(user.is_admin ? "/admin" : "/app");
  }, [user, loading, router]);
  return <div className="p-8 text-center text-text-muted text-sm">Redirecting…</div>;
}
