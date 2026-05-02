const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const TOKEN_KEY = "alphaagent_token";

export function getStoredToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setStoredToken(token: string) {
  if (typeof window === "undefined") return;
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {}
}

export async function api<T = unknown>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const token = getStoredToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(url, {
    ...options,
    credentials: "include", // also send cookie
    headers,
  });
  if (res.status === 401) {
    // Auto-redirect to login on auth failure (skip on the login page itself)
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      setStoredToken("");
      window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
    }
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API error: ${res.status}`);
  }
  return res.json();
}

export function fmt(
  n: number | null | undefined,
  symbol: string = "$",
  locale: string = "en-US",
  decimals: number = 0
): string {
  if (n == null || isNaN(n)) return "--";
  return (
    symbol +
    Number(n).toLocaleString(locale, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })
  );
}

export function pct(n: number | null | undefined): string {
  if (n == null || isNaN(n)) return "--%";
  return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
}

export function cn(...classes: (string | false | undefined | null)[]): string {
  return classes.filter(Boolean).join(" ");
}
