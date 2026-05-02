"use client";
import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { useUser } from "@/lib/auth";

export default function LegacySessionRedirect() {
  const router = useRouter();
  const params = useParams();
  const { user, loading } = useUser();
  const id = params.id as string;
  useEffect(() => {
    if (loading) return;
    if (!user) router.replace("/login?next=" + encodeURIComponent(`/sessions/${id}`));
    else router.replace((user.is_admin ? "/admin" : "/app") + `/sessions/${id}`);
  }, [user, loading, router, id]);
  return <div className="p-8 text-center text-text-muted text-sm">Redirecting…</div>;
}
