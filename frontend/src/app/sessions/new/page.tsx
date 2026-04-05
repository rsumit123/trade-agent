"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, cn } from "@/lib/api";
import type { MarketPreset, Session } from "@/lib/types";

export default function CreateSessionPage() {
  const router = useRouter();
  const [presets, setPresets] = useState<Record<string, MarketPreset>>({});
  const [sessions, setSessions] = useState<Session[]>([]);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  const [capitalDisplay, setCapitalDisplay] = useState("10,00,000");

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
    import_learnings_from: "",
  });

  useEffect(() => {
    api<Record<string, MarketPreset>>("/api/market-presets").then((p) => {
      setPresets(p);
      if (p.nse) {
        const cap = p.nse.default_starting_capital;
        setForm((f) => ({ ...f, starting_capital: cap }));
        setCapitalDisplay(cap.toLocaleString("en-IN"));
      }
    });
    api<Session[]>("/api/sessions").then(setSessions).catch(() => {});
  }, []);

  const selectMarket = (m: string) => {
    const preset = presets[m];
    const cap = preset?.default_starting_capital || form.starting_capital;
    setForm((f) => ({ ...f, market: m, starting_capital: cap }));
    setCapitalDisplay(cap.toLocaleString(preset?.currency === "INR" ? "en-IN" : "en-US"));
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
    <div className="px-4 md:px-8 py-4 md:py-8 max-w-3xl mx-auto">
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
              type="text"
              inputMode="numeric"
              value={capitalDisplay}
              onChange={(e) => {
                const raw = e.target.value.replace(/[^0-9]/g, "");
                const num = parseInt(raw) || 0;
                setForm((f) => ({ ...f, starting_capital: num }));
                setCapitalDisplay(raw ? num.toLocaleString("en-IN") : "");
              }}
              onBlur={() => setCapitalDisplay(form.starting_capital.toLocaleString("en-IN"))}
              placeholder="10,00,000"
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
              onChange={(e) => {
                const provider = e.target.value;
                const defaults: Record<string, { model: string; keyEnv: string }> = {
                  openrouter: { model: "anthropic/claude-haiku-4-5", keyEnv: "OPENROUTER_API_KEY" },
                  anthropic: { model: "claude-sonnet-4-5-20250929", keyEnv: "ANTHROPIC_API_KEY" },
                  openai: { model: "gpt-4o", keyEnv: "OPENAI_API_KEY" },
                };
                const d = defaults[provider] || defaults.openrouter;
                setForm({ ...form, llm_provider: provider, llm_model: d.model, api_key_env: d.keyEnv });
              }}
              className="w-full"
            >
              <option value="openrouter">OpenRouter</option>
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1.5">Model</label>
            {form.llm_provider === "openrouter" ? (
              <select
                value={form.llm_model}
                onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
                className="w-full text-sm"
              >
                <optgroup label="Anthropic">
                  <option value="anthropic/claude-haiku-4-5">Claude Haiku 4.5 — fast ($0.80/M)</option>
                </optgroup>
                <optgroup label="Google">
                  <option value="google/gemini-2.5-flash">Gemini 2.5 Flash — fast ($0.15/M)</option>
                </optgroup>
                <optgroup label="OpenAI">
                  <option value="openai/gpt-4o-mini">GPT-4o Mini ($0.15/M)</option>
                </optgroup>
                <optgroup label="Meta">
                  <option value="meta-llama/llama-4-maverick">Llama 4 Maverick ($0.20/M)</option>
                  <option value="meta-llama/llama-4-scout">Llama 4 Scout ($0.10/M)</option>
                </optgroup>
                <optgroup label="DeepSeek">
                  <option value="deepseek/deepseek-chat-v3-0324">DeepSeek V3 ($0.14/M)</option>
                  <option value="deepseek/deepseek-r1">DeepSeek R1 — reasoning ($0.55/M)</option>
                </optgroup>
              </select>
            ) : form.llm_provider === "anthropic" ? (
              <select
                value={form.llm_model}
                onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
                className="w-full text-sm"
              >
                <option value="claude-haiku-4-5-20250929">Claude Haiku 4.5</option>
                <option value="claude-sonnet-4-5-20250929">Claude Sonnet 4.5</option>
                <option value="claude-opus-4-0-20250514">Claude Opus 4</option>
              </select>
            ) : (
              <select
                value={form.llm_model}
                onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
                className="w-full text-sm"
              >
                <option value="gpt-4o">GPT-4o</option>
                <option value="gpt-4o-mini">GPT-4o Mini</option>
                <option value="o3-mini">o3-mini (reasoning)</option>
              </select>
            )}
          </div>
          <div className="col-span-1 sm:col-span-2">
            <label className="block text-xs text-text-muted mb-1.5">API Key (env var name)</label>
            <input
              value={form.api_key_env}
              onChange={(e) => setForm({ ...form, api_key_env: e.target.value })}
              placeholder={form.llm_provider === "openrouter" ? "OPENROUTER_API_KEY" : form.llm_provider === "anthropic" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY"}
              className="w-full font-mono text-sm"
            />
            <span className="text-[10px] text-text-muted">
              {form.api_key_env === "OPENROUTER_API_KEY" || form.api_key_env === "ANTHROPIC_API_KEY" || form.api_key_env === "OPENAI_API_KEY"
                ? `Uses the server's ${form.api_key_env} env variable. Leave as-is to use the default key.`
                : "Name of the environment variable containing your API key"}
            </span>
          </div>
        </div>
      </Section>

      {/* Import Learnings */}
      {sessions.length > 0 && (
        <Section title="Import Learnings" number={5}>
          <div>
            <label className="block text-xs text-text-muted mb-1.5">Copy learnings from an existing session</label>
            <select
              value={form.import_learnings_from}
              onChange={(e) => setForm({ ...form, import_learnings_from: e.target.value })}
              className="w-full"
            >
              <option value="">Start fresh (no import)</option>
              {sessions.map((s) => (
                <option key={s.session_id} value={s.session_id}>
                  {s.display_name} ({s.market}) — {s.total_trades ?? 0} trades, {s.win_rate != null ? `${s.win_rate}% WR` : "no data"}
                </option>
              ))}
            </select>
            <span className="text-[10px] text-text-muted block mt-1.5">
              {form.import_learnings_from ? (
                sessions.find((s) => s.session_id === form.import_learnings_from)?.market === form.market
                  ? "Same market — full journal will be copied (all trade entries + distilled rules)"
                  : "Different market — only distilled rules will be imported (market-specific entries skipped)"
              ) : (
                "New agents start with no learnings. Import from a session with good trade history to skip the learning curve."
              )}
            </span>
          </div>
        </Section>
      )}

      {/* Personality */}
      <Section title="Trading Personality" number={sessions.length > 0 ? 6 : 5}>
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
        <div style={{ marginBottom: 16, padding: 14, background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: 10, color: "#ef4444", fontSize: 14 }}>
          {error}
        </div>
      )}
      <div className="flex flex-col sm:flex-row gap-3 sm:justify-end mt-8 mb-12 animate-fade-in delay-5">
        <button
          onClick={() => handleSubmit(false)}
          disabled={creating}
          style={{
            width: "100%",
            maxWidth: "none",
            padding: "14px 24px",
            minHeight: 48,
            background: "#151d2e",
            border: "1px solid #1e293b",
            borderRadius: 10,
            color: "#e2e8f0",
            fontSize: 15,
            fontWeight: 500,
            opacity: creating ? 0.5 : 1,
            cursor: creating ? "not-allowed" : "pointer",
          }}
        >
          {creating ? "Creating..." : "Create Session"}
        </button>
        <button
          onClick={() => handleSubmit(true)}
          disabled={creating}
          style={{
            width: "100%",
            maxWidth: "none",
            padding: "14px 24px",
            minHeight: 48,
            background: "#22c55e",
            border: "none",
            borderRadius: 10,
            color: "#fff",
            fontSize: 15,
            fontWeight: 600,
            opacity: creating ? 0.5 : 1,
            cursor: creating ? "not-allowed" : "pointer",
          }}
        >
          {creating ? "Creating..." : "Create & Start Agent"}
        </button>
      </div>
    </div>
  );
}

function Section({ title, number, children }: { title: string; number: number; children: React.ReactNode }) {
  return (
    <div className={`animate-fade-in delay-${number}`} style={{ marginBottom: 24 }}>
      <div className="flex items-center gap-3 mb-4">
        <div
          className="flex items-center justify-center font-mono font-bold flex-shrink-0"
          style={{ width: 28, height: 28, borderRadius: "50%", background: "rgba(59,130,246,0.15)", color: "#3b82f6", fontSize: 12 }}
        >
          {number}
        </div>
        <h2 style={{ fontSize: 13, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "#94a3b8" }}>{title}</h2>
      </div>
      <div style={{ background: "#151d2e", border: "1px solid #1e293b", borderRadius: 12, padding: 20 }}>{children}</div>
    </div>
  );
}

function SliderField({ label, value, onChange, min, max, step, format }: {
  label: string; value: number; onChange: (v: number) => void;
  min: number; max: number; step: number; format: (v: number) => string;
}) {
  const fillPct = ((value - min) / (max - min)) * 100;
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="text-xs text-text-muted">{label}</label>
        <span className="text-xs font-mono font-semibold text-accent-blue">{format(value)}</span>
      </div>
      <input
        type="range"
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        min={min} max={max} step={step}
        className="w-full"
        style={{ "--range-fill": `${fillPct}%` } as React.CSSProperties}
      />
      <div className="flex justify-between mt-1">
        <span className="text-[10px] text-text-muted font-mono">{format(min)}</span>
        <span className="text-[10px] text-text-muted font-mono">{format(max)}</span>
      </div>
    </div>
  );
}
