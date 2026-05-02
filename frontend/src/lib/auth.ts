"use client";
import { useEffect, useState } from "react";
import { api, setStoredToken, getStoredToken } from "./api";

export const GOOGLE_CLIENT_ID =
  process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ||
  "929006071236-abebnk4rpkvnbkqfhlf3vv5p0t59igaj.apps.googleusercontent.com";

export interface AuthUser {
  email: string;
  name: string;
  picture: string;
  is_admin: boolean;
  tier?: "free" | "admin";
  runtime_quota_seconds?: number;
  runtime_used_seconds?: number;
  runtime_remaining_seconds?: number;
  trial_ended?: boolean;
  has_running_session?: boolean;
}

const USER_KEY = "alphaagent_user";

function readCachedUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

function writeCachedUser(u: AuthUser | null) {
  if (typeof window === "undefined") return;
  try {
    if (u) window.localStorage.setItem(USER_KEY, JSON.stringify(u));
    else window.localStorage.removeItem(USER_KEY);
  } catch {}
}

export function useUser() {
  const [user, setUser] = useState<AuthUser | null>(() => readCachedUser());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getStoredToken()) {
      setUser(null);
      writeCachedUser(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    api<AuthUser>("/api/auth/me")
      .then((u) => {
        if (cancelled) return;
        setUser(u);
        writeCachedUser(u);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setUser(null);
        writeCachedUser(null);
        setStoredToken("");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { user, loading };
}

export async function signInWithCredential(credential: string): Promise<AuthUser> {
  const r = await api<{ token: string; user: AuthUser }>("/api/auth/google", {
    method: "POST",
    body: JSON.stringify({ credential }),
  });
  setStoredToken(r.token);
  writeCachedUser(r.user);
  return r.user;
}

export async function signOut() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {}
  setStoredToken("");
  writeCachedUser(null);
  if (typeof window !== "undefined") window.location.href = "/login";
}

// ── Google Identity Services loader ─────────────────────────

interface GsiCredentialResponse {
  credential: string;
  select_by?: string;
}

interface GsiButtonOptions {
  type?: "standard" | "icon";
  theme?: "outline" | "filled_blue" | "filled_black";
  size?: "large" | "medium" | "small";
  text?: "signin_with" | "signup_with" | "continue_with" | "signin";
  shape?: "rectangular" | "pill" | "circle" | "square";
  logo_alignment?: "left" | "center";
  width?: number;
  locale?: string;
}

interface GsiInitConfig {
  client_id: string;
  callback: (response: GsiCredentialResponse) => void;
  auto_select?: boolean;
  cancel_on_tap_outside?: boolean;
  use_fedcm_for_prompt?: boolean;
}

interface GoogleAccountsId {
  initialize: (config: GsiInitConfig) => void;
  renderButton: (parent: HTMLElement, options: GsiButtonOptions) => void;
  prompt: () => void;
  disableAutoSelect: () => void;
}

declare global {
  interface Window {
    google?: { accounts: { id: GoogleAccountsId } };
  }
}

let gsiPromise: Promise<void> | null = null;

export function loadGoogleScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.reject(new Error("SSR"));
  if (window.google?.accounts?.id) return Promise.resolve();
  if (gsiPromise) return gsiPromise;
  gsiPromise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      'script[src="https://accounts.google.com/gsi/client"]'
    );
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () =>
        reject(new Error("Failed to load Google script"))
      );
      return;
    }
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.defer = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Failed to load Google script"));
    document.head.appendChild(s);
  });
  return gsiPromise;
}
