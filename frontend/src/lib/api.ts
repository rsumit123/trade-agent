const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export async function api<T = unknown>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
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
