"use client";
import { useEffect, useState, useRef } from "react";
import { api, getStoredToken } from "./api";

export interface RuntimeState {
  tier: "free" | "paid" | "admin";
  isAdmin: boolean;
  quotaSeconds: number;
  remainingSeconds: number;
  trialEnded: boolean;
  hasRunningSession: boolean;
  loading: boolean;
}

interface MeResponse {
  is_admin: boolean;
  tier: "free" | "paid" | "admin";
  runtime_quota_seconds: number;
  runtime_used_seconds: number;
  runtime_remaining_seconds: number;
  trial_ended: boolean;
  has_running_session: boolean;
}

const POLL_MS = 30_000;

/** Polls /api/auth/me every 30s. Ticks down each second locally while a
 *  session is running, so the chip stays smooth without hammering the API. */
export function useRuntime(): RuntimeState {
  const [state, setState] = useState<RuntimeState>({
    tier: "free",
    isAdmin: false,
    quotaSeconds: 86400,
    remainingSeconds: 86400,
    trialEnded: false,
    hasRunningSession: false,
    loading: true,
  });
  const lastFetchRef = useRef<number>(0);

  useEffect(() => {
    if (!getStoredToken()) {
      setState((s) => ({ ...s, loading: false }));
      return;
    }
    let cancelled = false;

    const fetchMe = async () => {
      try {
        const u = await api<MeResponse>("/api/auth/me");
        if (cancelled) return;
        lastFetchRef.current = Date.now();
        setState({
          tier: u.tier,
          isAdmin: u.is_admin,
          quotaSeconds: u.runtime_quota_seconds || 86400,
          remainingSeconds: u.runtime_remaining_seconds || 0,
          trialEnded: u.trial_ended,
          hasRunningSession: u.has_running_session,
          loading: false,
        });
      } catch {
        if (cancelled) return;
        setState((s) => ({ ...s, loading: false }));
      }
    };

    fetchMe();
    const poll = setInterval(fetchMe, POLL_MS);

    // Local 1s tick while a session is running
    const tick = setInterval(() => {
      setState((s) => {
        if (s.isAdmin || !s.hasRunningSession || s.remainingSeconds <= 0) return s;
        const next = Math.max(0, s.remainingSeconds - 1);
        return { ...s, remainingSeconds: next, trialEnded: next <= 0 };
      });
    }, 1000);

    return () => {
      cancelled = true;
      clearInterval(poll);
      clearInterval(tick);
    };
  }, []);

  return state;
}

export function formatRuntime(seconds: number): string {
  if (seconds <= 0) return "0m";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h >= 1) return `${h}h ${m}m`;
  if (m >= 1) return `${m}m`;
  return `${seconds}s`;
}

export function formatRuntimeLong(seconds: number): string {
  if (seconds <= 0) return "0 minutes";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h >= 1) return `${h} hour${h === 1 ? "" : "s"} ${m}m`;
  return `${m} minute${m === 1 ? "" : "s"}`;
}
