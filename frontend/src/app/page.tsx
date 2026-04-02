"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { SessionCard } from "@/components/SessionCard";
import type { Session } from "@/lib/types";

function SkeletonCard() {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-5 space-y-4">
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <div className="skeleton h-5 w-32" />
          <div className="skeleton h-4 w-16" />
        </div>
        <div className="skeleton h-3 w-3 rounded-full" />
      </div>
      <div className="space-y-2">
        <div className="skeleton h-3 w-24" />
        <div className="skeleton h-7 w-36" />
      </div>
      <div className="flex items-center justify-between">
        <div className="skeleton h-4 w-28" />
        <div className="skeleton h-8 w-16 rounded-lg" />
      </div>
    </div>
  );
}

export default function HomePage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);

  const loadSessions = () => {
    api<Session[]>("/api/sessions")
      .then((data) => { setSessions(data); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    loadSessions();
    const interval = setInterval(loadSessions, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="p-4 md:p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8 animate-fade-in">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Trading Sessions</h1>
          <p className="text-text-secondary text-sm mt-1">Manage your AI trading agents across multiple markets</p>
        </div>
        <Link
          href="/sessions/new"
          className="flex items-center justify-center gap-2 px-5 py-2.5 bg-accent-blue hover:bg-accent-blue/80 text-white rounded-lg font-medium text-sm transition-all hover:shadow-lg hover:shadow-accent-blue/20 w-full sm:w-auto"
        >
          <span className="text-lg leading-none">+</span>
          New Session
        </Link>
      </div>

      {/* Session Grid */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {[1, 2, 3].map((i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : sessions.length === 0 ? (
        <div className="text-center py-20">
          <div className="text-5xl mb-4 opacity-30">&#x25A6;</div>
          <h2 className="text-lg font-semibold text-text-secondary mb-2">No sessions yet</h2>
          <p className="text-text-muted text-sm mb-6">Create your first trading session to get started</p>
          <Link
            href="/sessions/new"
            className="inline-flex items-center gap-2 px-6 py-3 bg-accent-blue hover:bg-accent-blue/80 text-white rounded-lg font-medium transition-all"
          >
            Create Session
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {sessions.map((s, i) => (
            <div key={s.session_id} className={`animate-fade-in delay-${Math.min(i + 1, 5)}`}>
              <SessionCard session={s} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
