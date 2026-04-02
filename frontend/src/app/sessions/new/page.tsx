"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, cn } from "@/lib/api";
import type { MarketPreset } from "@/lib/types";

export default function CreateSessionPage() {
  const router = useRouter();
  const [presets, setPresets] = useState<Record<string, MarketPreset>>({});
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  const [form, setForm] = useState({
    session_id: "",
    display_name: "",
    market: "nse",
    starting_capital: 0,
    max_position_pct: 0.20,
    max_open_positions: 5,
    daily_loss_limit_pct: 0.02,
    per_trade_loss_limit_pct: 0.01,
    llm_provider: "openrouter",
    llm_model: "anthropic/claude-haiku-4-5",
    api_key_env: "OPENROUTER_API_KEY",
    personality: "",
  });

  useEffect(() => {
    api<Record<string, MarketPreset>>("/api/market-presets").then((p) => {
      setPresets(p);
      if (p.nse) setForm((f) => ({ ...f, starting_capital: p.nse.default_starting_capital }));
    });
  }, []);

  const selectMarket = (m: string) => {
    const preset = presets[m];
    setForm((f) => ({
      ...f,
      market: m,
      starting_capital: preset?.default_starting_capital || f.starting_capital,
    }));
  };

  const handleSubmit = async (startAfter: boolean = false) => {
    setError("");
    if (!form.session_id.trim()) { setError("Session ID is required"); return; }
    if (!/^[a-z0-9_-]+$/.test(form.session_id)) { setError("Session ID: lowercase letters, numbers, hyphens, underscores only"); return; }

    setCreating(true);
    try {
      await api("/api/sessions", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          display_name: form.display_name || form.session_id,
        }),
      });
      if (startAfter) {
        await api(`/api/agent/start/${form.session_id}`, { method: "POST" });
      }
      router.push(`/sessions/${form.session_id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create session");
      setCreating(false);
    }
  };

  const preset = presets[form.market];

  return (
    <div className="p-4 md:p-8 max-w-3xl mx-auto">
      <div className="mb-8 animate-fade-in">
        <h1 className="text-2xl font-bold tracking-tight">Create Trading Session</h1>
        <p className="text-text-secondary text-sm mt-1">Configure a new AI trading agent</p>
      </div>

      {/* Market Selection */}
      <Section title="Market" number={1}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {Object.entries(presets).map(([id, p]) => (
            <button
              key={id}
              onClick={() => selectMarket(id)}
              className={cn(
                "p-5 rounded-xl border-2 text-left transition-all",
                form.market === id
                  ? "border-accent-blue bg-accent-blue/5 shadow-lg shadow-accent-blue/10"
                  : "border-border hover:border-border-accent bg-bg-card"
              )}
            >
              <div className="text-2xl mb-2">{id === "crypto" ? "\u20BF" : "\u25B2"}</div>
              <div className="font-semibold text-text-primary">{p.display_name}</div>
              <div className="text-xs text-text-muted mt-1">
                {p.currency_symbol}{p.default_starting_capital.toLocaleString()} default
                {p.is_24x7 ? " \u00B7 24/7" : ""}
                {" \u00B7 "}{p.default_watchlist_count} assets
              </div>
            </button>
          ))}
        </div>
      </Section>

      {/* Basics */}
      <Section title="Basics" number={2}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-text-muted mb-1.5">Session ID</label>
            <input
              value={form.session_id}
              onChange={(e) => setForm({ ...form, session_id: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '') })}
              placeholder="crypto_aggressive"
              className="w-full"
            />
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1.5">Display Name</label>
            <input
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              placeholder={form.session_id || "My Session"}
              className="w-full"
            />
          </div>
          <div className="col-span-1 sm:col-span-2">
            <label className="block text-xs text-text-muted mb-1.5">
              Starting Capital ({preset?.currency_symbol || "$"})
            </label>
            <input
              type="number"
              value={form.starting_capital}
              onChange={(e) => setForm({ ...form, starting_capital: parseFloat(e.target.value) || 0 })}
              className="w-full font-mono"
            />
          </div>
        </div>
      </Section>

      {/* Risk */}
      <Section title="Risk Limits" number={3}>
        <div className="space-y-5">
          <SliderField
            label="Max Position Size"
            value={form.max_position_pct}
            onChange={(v) => setForm({ ...form, max_position_pct: v })}
            min={0.05} max={0.50} step={0.05}
            format={(v) => `${(v * 100).toFixed(0)}% of portfolio`}
          />
          <SliderField
            label="Daily Loss Limit"
            value={form.daily_loss_limit_pct}
            onChange={(v) => setForm({ ...form, daily_loss_limit_pct: v })}
            min={0.01} max={0.10} step={0.01}
            format={(v) => `${(v * 100).toFixed(0)}% of capital`}
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-text-muted mb-1.5">Max Open Positions</label>
              <input
                type="number"
                value={form.max_open_positions}
                onChange={(e) => setForm({ ...form, max_open_positions: parseInt(e.target.value) || 1 })}
                min={1} max={20}
                className="w-full font-mono"
              />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1.5">Per-Trade Stop Loss</label>
              <input
                type="number"
                value={(form.per_trade_loss_limit_pct * 100).toFixed(1)}
                onChange={(e) => setForm({ ...form, per_trade_loss_limit_pct: (parseFloat(e.target.value) || 1) / 100 })}
                step={0.5} min={0.5} max={10}
                className="w-full font-mono"
              />
              <span className="text-[10px] text-text-muted">%</span>
            </div>
          </div>
        </div>
      </Section>

      {/* LLM */}
      <Section title="LLM Configuration" number={4}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-text-muted mb-1.5">Provider</label>
            <select
              value={form.llm_provider}
              onChange={(e) => setForm({ ...form, llm_provider: e.target.value })}
              className="w-full"
            >
              <option value="openrouter">OpenRouter</option>
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1.5">Model</label>
            <input
              value={form.llm_model}
              onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
              placeholder="anthropic/claude-haiku-4-5"
              className="w-full text-sm"
            />
          </div>
          <div className="col-span-1 sm:col-span-2">
            <label className="block text-xs text-text-muted mb-1.5">API Key (env var name)</label>
            <input
              value={form.api_key_env}
              onChange={(e) => setForm({ ...form, api_key_env: e.target.value })}
              placeholder="OPENROUTER_API_KEY"
              className="w-full font-mono text-sm"
            />
            <span className="text-[10px] text-text-muted">Name of the environment variable containing your API key</span>
          </div>
        </div>
      </Section>

      {/* Personality */}
      <Section title="Trading Personality" number={5}>
        <textarea
          value={form.personality}
          onChange={(e) => setForm({ ...form, personality: e.target.value })}
          placeholder="Be aggressive. Target 2% daily returns. Focus on momentum plays. Cut losers fast at 1% stop."
          rows={4}
          className="w-full resize-none"
        />
        <span className="text-[10px] text-text-muted">Custom instructions injected into the LLM system prompt. Leave empty for default balanced behavior.</span>
      </Section>

      {/* Actions */}
      {error && (
        <div className="mb-4 p-3 bg-accent-red/10 border border-accent-red/30 rounded-lg text-accent-red text-sm">
          {error}
        </div>
      )}
      <div className="flex flex-col sm:flex-row gap-3 sm:justify-end mt-8 mb-12 animate-fade-in delay-5">
        <button
          onClick={() => handleSubmit(false)}
          disabled={creating}
          className="w-full sm:w-auto px-6 py-2.5 bg-bg-card border border-border hover:border-border-accent rounded-lg text-sm font-medium transition-all disabled:opacity-50"
        >
          {creating ? "Creating..." : "Create Session"}
        </button>
        <button
          onClick={() => handleSubmit(true)}
          disabled={creating}
          className="w-full sm:w-auto px-6 py-2.5 bg-accent-green hover:bg-accent-green-dim text-white rounded-lg text-sm font-medium transition-all hover:shadow-lg hover:shadow-accent-green/20 disabled:opacity-50"
        >
          {creating ? "Creating..." : "Create & Start Agent"}
        </button>
      </div>
    </div>
  );
}

function Section({ title, number, children }: { title: string; number: number; children: React.ReactNode }) {
  return (
    <div className={`mb-8 animate-fade-in delay-${number}`}>
      <div className="flex items-center gap-3 mb-4">
        <div className="w-7 h-7 rounded-full bg-accent-blue/15 text-accent-blue text-xs font-bold flex items-center justify-center font-mono">
          {number}
        </div>
        <h2 className="text-sm font-semibold uppercase tracking-wider text-text-secondary">{title}</h2>
      </div>
      <div className="bg-bg-card border border-border rounded-xl p-5">{children}</div>
    </div>
  );
}

function SliderField({ label, value, onChange, min, max, step, format }: {
  label: string; value: number; onChange: (v: number) => void;
  min: number; max: number; step: number; format: (v: number) => string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="text-xs text-text-muted">{label}</label>
        <span className="text-xs font-mono text-accent-blue">{format(value)}</span>
      </div>
      <input
        type="range"
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        min={min} max={max} step={step}
        className="w-full"
      />
    </div>
  );
}
