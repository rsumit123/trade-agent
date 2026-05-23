"use client";
import { useEffect, useMemo, useState } from "react";
import { getCategories, updateUniverse } from "@/lib/api";
import type { Category, SessionConfig } from "@/lib/types";

export function TradingUniverseCard({
  sessionId,
  config,
  onSaved,
}: {
  sessionId: string;
  config: SessionConfig | null;
  onSaved?: () => void;
}) {
  const [cats, setCats] = useState<Category[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set(config?.universe ?? []));
  const [mode, setMode] = useState<"all" | "specific">(
    (config?.universe?.length ?? 0) > 0 ? "specific" : "all",
  );
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");

  // Sync once the config loads in.
  useEffect(() => {
    setSelected(new Set(config?.universe ?? []));
    setMode((config?.universe?.length ?? 0) > 0 ? "specific" : "all");
  }, [config?.universe]);

  useEffect(() => {
    getCategories(config?.market_id || "nse")
      .then((r) => setCats(r.categories))
      .catch(() => setCats([]));
  }, [config?.market_id]);

  const tickersOf = (name: string) => cats.find((c) => c.name === name)?.tickers ?? [];
  const sectorFullySelected = (name: string) =>
    tickersOf(name).length > 0 && tickersOf(name).every((t) => selected.has(t));

  const toggleSector = (name: string) => {
    const next = new Set(selected);
    const tks = tickersOf(name);
    if (sectorFullySelected(name)) tks.forEach((t) => next.delete(t));
    else tks.forEach((t) => next.add(t));
    setSelected(next);
  };

  const addTicker = (raw: string) => {
    const t = raw.trim().toUpperCase();
    if (!t) return;
    const ticker = t.endsWith(".NS") ? t : `${t}.NS`;
    setSelected(new Set(selected).add(ticker));
    setSearch("");
  };

  const removeTicker = (t: string) => {
    const next = new Set(selected);
    next.delete(t);
    setSelected(next);
  };

  const list = useMemo(() => Array.from(selected), [selected]);
  const canSave = mode === "all" || list.length > 0;

  const save = async () => {
    setSaving(true);
    try {
      await updateUniverse(sessionId, mode === "all" ? [] : list);
      setSavedMsg(
        mode === "all"
          ? "Saved. The agent will scan all NSE sectors at the next open."
          : `Saved. Starting next open the agent will trade ${list.length} stock${list.length === 1 ? "" : "s"}. Today's positions are unaffected.`,
      );
      onSaved?.();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mb-4 rounded-xl overflow-hidden" style={{ background: "#0c1320", border: "1px solid #1e293b" }}>
      <div className="px-4 py-3 border-b" style={{ borderColor: "#1e293b" }}>
        <h3 className="text-xs uppercase tracking-wider font-semibold" style={{ color: "#94a3b8" }}>Trading Universe</h3>
        <p className="text-xs mt-0.5" style={{ color: "#64748b" }}>Which stocks should the agent trade?</p>
      </div>

      <div className="p-4">
        <div className="flex flex-col gap-2 mb-3">
          <label className="flex items-center gap-2 text-sm" style={{ color: "#e2e8f0", minHeight: 44 }}>
            <input type="radio" checked={mode === "all"} onChange={() => setMode("all")} />
            All sectors — agent scans the market daily
          </label>
          <label className="flex items-center gap-2 text-sm" style={{ color: "#e2e8f0", minHeight: 44 }}>
            <input type="radio" checked={mode === "specific"} onChange={() => setMode("specific")} />
            Choose specific sectors / stocks
          </label>
        </div>

        {mode === "specific" && (
          <>
            <div className="flex flex-wrap gap-2 mb-3">
              {cats.map((c) => {
                const on = sectorFullySelected(c.name);
                return (
                  <button
                    key={c.name}
                    onClick={() => toggleSector(c.name)}
                    className="text-xs px-2 rounded-lg border"
                    style={{
                      minHeight: 44,
                      background: on ? "#1d4ed8" : "#0a0e17",
                      borderColor: on ? "#3b82f6" : "#1e293b",
                      color: on ? "#fff" : "#94a3b8",
                    }}
                  >
                    {c.name} ({c.count}) {on ? "✓" : "+"}
                  </button>
                );
              })}
              {cats.length === 0 && (
                <span className="text-xs" style={{ color: "#64748b" }}>
                  Sector list temporarily unavailable — pick individual stocks below.
                </span>
              )}
            </div>

            <div className="flex gap-2 mb-3">
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addTicker(search)}
                placeholder="Add a stock (e.g. RELIANCE)"
                className="flex-1 text-sm px-2 rounded-lg"
                style={{ minHeight: 44, fontSize: 16, background: "#0a0e17", border: "1px solid #1e293b", color: "#e2e8f0" }}
              />
              <button onClick={() => addTicker(search)} className="text-sm px-3 rounded-lg" style={{ minHeight: 44, background: "#1e293b", color: "#e2e8f0" }}>
                Add
              </button>
            </div>

            <div className="mb-3">
              <div className="text-xs mb-1" style={{ color: "#64748b" }}>In your universe ({list.length}):</div>
              <div className="flex flex-wrap gap-1.5">
                {list.map((t) => (
                  <span key={t} className="text-xs px-2 py-1 rounded-md flex items-center gap-1" style={{ background: "#0a0e17", border: "1px solid #1e293b", color: "#e2e8f0" }}>
                    {t.replace(".NS", "")}
                    <button onClick={() => removeTicker(t)} style={{ opacity: 0.6 }}>×</button>
                  </span>
                ))}
                {list.length === 0 && <span className="text-xs" style={{ color: "#64748b" }}>No stocks yet — add a sector or a ticker.</span>}
              </div>
            </div>
          </>
        )}

        <p className="text-xs mb-2" style={{ color: "#64748b" }}>⏱ Takes effect at the next market open. Today&apos;s open positions are unaffected.</p>
        <button
          onClick={save}
          disabled={!canSave || saving}
          className="text-sm px-4 rounded-lg font-medium"
          style={{ minHeight: 44, background: canSave ? "#1d4ed8" : "#1e293b", color: "#fff", opacity: saving ? 0.6 : 1 }}
        >
          {saving ? "Saving…" : "Save universe"}
        </button>
        {savedMsg && <p className="text-xs mt-2" style={{ color: "#22c55e" }}>{savedMsg}</p>}
      </div>
    </div>
  );
}
